"""艾宾浩斯日程表 - Tkinter 桌面界面 (Python 自带, 免安装, 双击即用)。

采用 Tkinter 而非 Kivy: 原生 Windows 输入法, 拼音候选框正常工作。
核心调度逻辑复用 models.py (纯 Python, 已单元测试)。

界面结构:
- 顶部标题栏 + 刷新按钮
- 中间三个页面 (复习表 / 今日所学 / 完成 list), 底部导航切换
- 复习表: 每个"未完成的复习事件"一张卡片 (一条任务最多 4 张);
  今日/过期到期的卡片用红色边框标记, 右侧方框打钩即完成。
- 完成 list: 每个"已完成的复习事件"一张卡片, 方框已打钩, 取消勾选即还原。
- 点开任意卡片 -> 详情弹窗: 展示首次学习 + 四次复习, 每行带方框可完成/还原,
  已完成的文字灰色 + 删除线; 每行可"改期"; 底部可"删除任务"。
"""

import os
import sys
import time
import ctypes
import tkinter as tk
from tkinter import ttk, font as tkfont, messagebox
from datetime import date, datetime, timedelta

import notifier
from models import Store, fmt_date, PLAN_KINDS

try:
    import tray
except Exception:
    tray = None

# 后台定点提醒的整点 (0 即 24 点)
TRIGGER_HOURS = [0, 7, 9, 13, 17, 20]

# 单实例互斥名: 已在运行时, 新启动的进程只唤起既有窗口后退出
_MUTEX_NAME = "EbbinghausScheduleAppSingleton"
_ERROR_ALREADY_EXISTS = 183


def _enable_dpi_awareness():
    """让进程按真实 DPI 渲染, 避免高分屏下 Windows 位图拉伸造成的字体发虚/毛躁。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# ---- 配色 ----
ACCENT = "#00897b"       # 主色 (青绿)
RED = "#d81f1f"          # 到期 / 危险
GRAY = "#9e9e9e"         # 已完成灰
GREEN = "#2e7d32"
BG = "#f4f6f8"           # 页面背景
CARD_BG = "#ffffff"      # 卡片背景
CARD_DONE_BG = "#eceff1"  # 已完成卡片背景
BORDER = "#e0e0e0"       # 普通卡片边框
TEXT = "#212121"
SUBTLE = "#6b6b6b"
NAV_BG = "#ffffff"
NAV_OFF = "#9099a1"


def data_dir():
    """数据目录: 默认在脚本同级的 appdata/ (便携), 可用环境变量覆盖。"""
    path = os.environ.get(
        "EBBINGHAUS_DATA_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "appdata"),
    )
    os.makedirs(path, exist_ok=True)
    return path


def pick_font_family(root):
    """选一个可显示中文的字体。"""
    available = set(tkfont.families(root))
    for f in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun", "NSimSun"):
        if f in available:
            return f
    return "TkDefaultFont"


class DatePicker(tk.Toplevel):
    """三个 Spinbox 组成的模态日期选择器, 返回 date 或 None。"""

    def __init__(self, parent, initial, title="选择日期"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.result = None
        initial = initial or date.today()

        wrap = tk.Frame(self, bg=BG, padx=18, pady=18)
        wrap.pack(fill="both", expand=True)
        row = tk.Frame(wrap, bg=BG)
        row.pack()
        self.y = tk.IntVar(value=initial.year)
        self.m = tk.IntVar(value=initial.month)
        self.d = tk.IntVar(value=initial.day)
        tk.Spinbox(row, from_=2000, to=2100, width=6, textvariable=self.y).pack(side="left")
        tk.Label(row, text="年", bg=BG).pack(side="left", padx=(2, 10))
        tk.Spinbox(row, from_=1, to=12, width=4, textvariable=self.m).pack(side="left")
        tk.Label(row, text="月", bg=BG).pack(side="left", padx=(2, 10))
        tk.Spinbox(row, from_=1, to=31, width=4, textvariable=self.d).pack(side="left")
        tk.Label(row, text="日", bg=BG).pack(side="left", padx=(2, 0))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(pady=(16, 0))
        tk.Button(btns, text="确定", width=8, command=self._ok,
                  bg=ACCENT, fg="white", activebackground=ACCENT,
                  relief="flat", cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="取消", width=8, command=self.destroy,
                  relief="flat", cursor="hand2").pack(side="left", padx=6)

        self.grab_set()
        parent.wait_window(self)

    def _ok(self):
        try:
            self.result = date(self.y.get(), self.m.get(), self.d.get())
        except ValueError:
            messagebox.showerror("日期无效", "请输入正确的日期", parent=self)
            return
        self.destroy()


class App:
    def __init__(self, root, scale=1.0, notify_on_start=False):
        self.root = root
        self.scale = scale
        self.notify_on_start = notify_on_start
        self.today = date.today()
        self.store = Store(os.path.join(data_dir(), "data.json"))
        # 启动时执行一次"未打钩则后移"的追赶; 之后手动改期不再被覆盖
        self.store.run_daily_postpone(self.today)

        # 标题栏提示语 / 托盘 / 定点通知的状态
        self._last_due_count = None
        self._header_phrase = ""
        self._last_notify_key = None
        self.tray = None

        # 搜索窗口 / 旋转动画的状态
        self._search_win = None
        self._search_entry = None
        self._search_list = None
        self._search_spinner = None
        self._spinner_job = None
        self._search_seq = 0

        fam = pick_font_family(root)
        self.f_title = tkfont.Font(family=fam, size=12, weight="bold")
        self.f_title_strike = tkfont.Font(family=fam, size=12, weight="bold", overstrike=1)
        self.f_body = tkfont.Font(family=fam, size=10)
        self.f_strike = tkfont.Font(family=fam, size=10, overstrike=1)
        self.f_sub = tkfont.Font(family=fam, size=9)
        self.f_nav = tkfont.Font(family=fam, size=10)
        self.f_h1 = tkfont.Font(family=fam, size=14, weight="bold")
        # 让所有默认控件 (Entry/Spinbox/Button/Label) 都用中文字体
        for nm in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                   "TkHeadingFont", "TkFixedFont"):
            try:
                tkfont.nametofont(nm).configure(family=fam, size=10)
            except tk.TclError:
                pass

        root.title("艾宾浩斯日程表")
        root.geometry(f"{self.px(448)}x{self.px(724)}")
        root.minsize(self.px(380), self.px(560))
        root.configure(bg=BG)

        self._build_layout()
        self.show_page("home")
        self._setup_background()

    def px(self, v):
        """把设计像素按屏幕缩放换算成物理像素 (点数类字体交由 tk scaling 处理)。"""
        return max(1, int(round(v * self.scale)))

    # ---------------- 布局 ----------------
    def _build_layout(self):
        self.header = tk.Frame(self.root, bg=ACCENT, height=self.px(52))
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)
        self.header_label = tk.Label(self.header, text="学习表", bg=ACCENT,
                                     fg="white", font=self.f_h1)
        self.header_label.pack(side="left", padx=(self.px(16), self.px(8)))
        # 右上角: 搜索按钮 (Canvas 手绘放大镜图标, 不依赖任何图片)
        # 图标画在青绿底上, 与原来的白色"刷新"字样一样用白色描边以保证可见
        self.search_btn = tk.Canvas(self.header, width=self.px(30), height=self.px(30),
                                    bg=ACCENT, highlightthickness=0, bd=0,
                                    cursor="hand2")
        self.search_btn.pack(side="right", padx=self.px(10), pady=self.px(11))
        self._draw_search_icon(self.search_btn)
        self.search_btn.bind("<Button-1>", lambda e: self.open_search())
        self.header_task = tk.Label(self.header, text="", bg=ACCENT, fg="#d7f0ec",
                                    font=self.f_nav, anchor="w")
        self.header_task.pack(side="left", padx=(0, self.px(8)))

        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill="both", expand=True, side="top")

        self.nav = tk.Frame(self.root, bg=NAV_BG, height=self.px(56),
                            highlightbackground=BORDER, highlightthickness=1)
        self.nav.pack(fill="x", side="bottom")
        self.nav.pack_propagate(False)
        self.nav_buttons = {}
        for key, label in (("home", "学习表"), ("create", "今日所学"), ("completed", "完成")):
            b = tk.Button(self.nav, text=label, command=lambda k=key: self.show_page(k),
                          relief="flat", bd=0, bg=NAV_BG, fg=NAV_OFF,
                          activebackground="#f0f2f4", activeforeground=ACCENT,
                          font=self.f_nav, cursor="hand2")
            b.pack(side="left", fill="both", expand=True)
            self.nav_buttons[key] = b

        self.pages = {}
        self._build_home_page()
        self._build_create_page()
        self._build_completed_page()

    def _draw_search_icon(self, cv):
        """在 Canvas 上画一个放大镜 (一个圆 + 一条斜线), 不依赖外部图片。"""
        size = self.px(30)
        r = self.px(8)
        cx = cy = self.px(12)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                       outline="white", width=self.px(2))
        cv.create_line(cx + r * 0.7, cy + r * 0.7, size - self.px(5), size - self.px(5),
                       fill="white", width=self.px(2), capstyle="round")

    def _make_scrollable(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win, width=e.width))

        def wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _build_home_page(self):
        page = tk.Frame(self.container, bg=BG)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        list_area = tk.Frame(page, bg=BG)
        list_area.pack(side="top", fill="both", expand=True)
        self.home_list = self._make_scrollable(list_area)
        self._build_home_progress_bar(page)
        self.pages["home"] = page

    def _build_home_progress_bar(self, page):
        """复习表页底部的「今日完成进度」条 (卡片列表与底部导航之间, 仅首页显示)。

        绿色(主色) = 今日已完成占比, 灰色 = 尚未完成部分;
        今天没有任何到期任务时按 100% 显示。
        """
        strip = tk.Frame(page, bg=NAV_BG, height=self.px(40),
                         highlightbackground=BORDER, highlightthickness=1)
        strip.pack(side="bottom", fill="x")
        strip.pack_propagate(False)

        tk.Label(strip, text="今日完成", bg=NAV_BG, fg=SUBTLE,
                 font=self.f_sub).pack(side="left", padx=(self.px(14), self.px(8)))

        track = tk.Frame(strip, bg=BORDER, height=self.px(10))
        track.pack(side="left", fill="x", expand=True, padx=(self.px(2), self.px(10)))
        self._home_bar_fill = tk.Frame(track, bg=ACCENT)
        self._home_bar_fill.place(x=0, y=0, relheight=1.0, relwidth=0.0, anchor="nw")

        self._home_pct_label = tk.Label(strip, text="0%", bg=NAV_BG, fg=ACCENT,
                                        font=self.f_nav)
        self._home_pct_label.pack(side="right", padx=(self.px(4), self.px(14)))

    def _today_progress(self):
        """今日完成百分比: 分母 = 今日到期且未完成(复习+学习计划) + 今天内完成的到期任务;
        今天没有到期任务 (total == 0) 时按 100% 处理。"""
        done = 0
        pending = 0
        for s in self.store.series:
            if s.kind in PLAN_KINDS:
                if not s.done_learn:
                    if s.learn_date <= self.today:
                        pending += 1
                else:
                    if (s.done_learn_date is not None and s.done_learn_date == self.today
                            and s.learn_date <= self.today):
                        done += 1
                continue
            for r in s.reviews:
                if r.date <= self.today:
                    if not r.done:
                        pending += 1
                    elif r.done_date is not None and r.done_date == self.today:
                        done += 1
        total = pending + done
        pct = 100 if total == 0 else int(round(done * 100.0 / total))
        return pct

    def _update_progress(self):
        """刷新进度条的百分比文字与绿色填充宽度 (用内存数据实时计算)。"""
        pct = self._today_progress()
        self._home_pct_label.config(text=f"{pct}%")
        self._home_bar_fill.place_configure(relwidth=min(1.0, pct / 100.0))

    def _build_completed_page(self):
        page = tk.Frame(self.container, bg=BG)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.completed_list = self._make_scrollable(page)
        self.pages["completed"] = page

    def _build_create_page(self):
        page = tk.Frame(self.container, bg=BG)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        wrap = tk.Frame(page, bg=BG)
        wrap.pack(fill="x", padx=self.px(22), pady=self.px(22))

        # 类型选择 (三个选项互斥同组):
        #   第一行: 今日所学（创建复习计划）
        #   第二行: 学习计划（需复习） 与 学习计划（不复习） 并排
        self.create_kind = tk.StringVar(value="review")
        kind_box = tk.Frame(wrap, bg=BG)
        kind_box.pack(anchor="w", pady=(0, self.px(14)))
        row1 = tk.Frame(kind_box, bg=BG)
        row1.pack(anchor="w")
        tk.Radiobutton(row1, text="今日所学（创建复习计划）", value="review",
                       variable=self.create_kind, bg=BG, activebackground=BG,
                       fg=TEXT, font=self.f_body, anchor="w", cursor="hand2",
                       selectcolor=CARD_BG).pack(side="left")
        row2 = tk.Frame(kind_box, bg=BG)
        row2.pack(anchor="w", pady=(self.px(2), 0))
        # 占位块: 宽度约等于单选钮指示器, 使第二行缩进与第一行对齐
        tk.Frame(row2, bg=BG, width=self.px(16)).pack(side="left")
        tk.Radiobutton(row2, text="学习计划（需复习）", value="plan",
                       variable=self.create_kind, bg=BG, activebackground=BG,
                       fg=TEXT, font=self.f_body, anchor="w", cursor="hand2",
                       selectcolor=CARD_BG).pack(side="left")
        tk.Radiobutton(row2, text="学习计划（不复习）", value="simple_plan",
                       variable=self.create_kind, bg=BG, activebackground=BG,
                       fg=TEXT, font=self.f_body, anchor="w", cursor="hand2",
                       selectcolor=CARD_BG).pack(side="left", padx=(self.px(8), 0))

        tk.Label(wrap, text="学习内容", bg=BG, fg=TEXT, font=self.f_body).pack(anchor="w")
        self.name_entry = tk.Entry(wrap, font=self.f_body, relief="solid", bd=1)
        self.name_entry.pack(fill="x", pady=(self.px(4), self.px(16)), ipady=self.px(5))
        self.name_entry.bind("<Return>", self.on_create)

        tk.Label(wrap, text="学习 / 应完成日期", bg=BG, fg=TEXT,
                 font=self.f_body).pack(anchor="w")
        drow = tk.Frame(wrap, bg=BG)
        drow.pack(anchor="w", pady=(self.px(4), self.px(22)))
        t = date.today()
        self.cy = tk.IntVar(value=t.year)
        self.cm = tk.IntVar(value=t.month)
        self.cd = tk.IntVar(value=t.day)
        tk.Spinbox(drow, from_=2000, to=2100, width=6, textvariable=self.cy).pack(side="left")
        tk.Label(drow, text="年", bg=BG).pack(side="left", padx=(2, 10))
        tk.Spinbox(drow, from_=1, to=12, width=4, textvariable=self.cm).pack(side="left")
        tk.Label(drow, text="月", bg=BG).pack(side="left", padx=(2, 10))
        tk.Spinbox(drow, from_=1, to=31, width=4, textvariable=self.cd).pack(side="left")
        tk.Label(drow, text="日", bg=BG).pack(side="left", padx=(2, 0))

        tk.Button(wrap, text="创建学习日程", command=self.on_create,
                  bg=ACCENT, fg="white", activebackground=ACCENT,
                  activeforeground="white", relief="flat", font=self.f_body,
                  cursor="hand2").pack(anchor="w", ipadx=self.px(12), ipady=self.px(6))
        self.create_hint = tk.Label(wrap, text="输入内容后按回车即可快速添加，可连续录入多条",
                                    bg=BG, fg=SUBTLE, font=self.f_sub, anchor="w",
                                    wraplength=self.px(380), justify="left")
        self.create_hint.pack(anchor="w", pady=(self.px(10), 0))
        self.pages["create"] = page

    # ---------------- 页面切换 ----------------
    def show_page(self, key):
        self.pages[key].lift()
        titles = {"home": "学习表", "create": "今日所学", "completed": "完成 list"}
        self.header_label.config(text=titles[key])
        for k, b in self.nav_buttons.items():
            b.config(fg=ACCENT if k == key else NAV_OFF)
        if key in ("home", "completed"):
            self.refresh_lists()
        if key == "create":
            self.name_entry.focus_set()

    # ---------------- 列表刷新 ----------------
    def refresh_lists(self):
        self.today = date.today()
        self._build_home_cards()
        self._build_completed_cards()
        self._update_status(self._due_count())
        self._update_progress()

    def _due_count(self):
        """今日待办总数 = 到期未完成复习 + 到期未完成学习计划(绿卡),
        与顶部提示 / 托盘 / 系统通知口径一致。"""
        return self.store.due_task_count(self.today)

    def _update_status(self, count):
        """刷新标题栏提示语与托盘 tooltip; 提示语仅在待办数目变化时重新随机。"""
        if count != self._last_due_count:
            self._last_due_count = count
            self._header_phrase = notifier.task_phrase(count)
        self.header_task.config(text=self._header_phrase)
        if self.tray:
            try:
                self.tray.set_tip(self._tray_tip(count))
            except Exception:
                pass

    @staticmethod
    def _tray_tip(count):
        if count > 0:
            return f"艾宾浩斯日程表 - 今天有 {count} 个任务没完成"
        return "艾宾浩斯日程表 - 今天任务已全部完成"

    def _clear(self, box):
        for w in box.winfo_children():
            w.destroy()

    def _empty(self, box, text):
        tk.Label(box, text=text, bg=BG, fg=SUBTLE, font=self.f_body,
                 wraplength=self.px(360), justify="center").pack(pady=self.px(60))

    def _card_frame(self, parent, border_color, bg):
        outer = tk.Frame(parent, bg=border_color)
        outer.pack(fill="x", padx=self.px(12), pady=self.px(6))
        inner = tk.Frame(outer, bg=bg)
        bw = self.px(2) if border_color != BORDER else self.px(1)
        inner.pack(fill="both", expand=True, padx=bw, pady=bw)
        return inner

    def _build_home_cards(self):
        self._clear(self.home_list)
        # 复习事件卡 + 学习计划(绿卡) 统一入列。
        # 注意: 绿卡"完成学习"后会生成 4 次复习, 此时它不再以绿卡出现,
        # 而是像普通复习任务一样逐次展开成复习卡片, 必须在这里一起列出。
        entries = []  # (series, review_or_None, 排序日期)
        for s in self.store.series:
            if s.kind in PLAN_KINDS and not s.done_learn:
                entries.append((s, None, s.learn_date))
                continue
            for r in s.reviews:
                if not r.done:
                    entries.append((s, r, r.date))
        if not entries:
            self._empty(self.home_list, "暂无卡片，去「今日所学」添加吧")
            return
        entries.sort(key=lambda e: self._home_entry_key(e))

        for s, r, _d in entries:
            if r is None:
                self._make_plan_card(s)
            else:
                self._make_pending_card(s, r)

    def _home_entry_key(self, e):
        """学习表卡片排序: 今日/过期块在最前; 时间先排, 同一天内学习计划放最后。

        - 今日/过期块 (日期<=今天): 复习在前、学习计划在后, 块内再按时间;
        - 未来块 (日期>今天): 严格按日期排, 同一天内复习在前、学习计划在后。
        (已"完成学习"的绿卡按复习事件参与排序, 不再视为学习计划卡。)
        """
        s, r, d = e
        is_plan_card = r is None
        due = d <= self.today
        if due:
            return (0, 1 if is_plan_card else 0, d.toordinal(), s.created_at)
        return (1, d.toordinal(), 1 if is_plan_card else 0, s.created_at)

    def _make_pending_card(self, s, r):
        due = r.date <= self.today
        inner = self._card_frame(self.home_list,
                                 RED if due else BORDER, CARD_BG)

        left = tk.Frame(inner, bg=CARD_BG, cursor="hand2")
        left.pack(side="left", fill="both", expand=True, padx=self.px(12), pady=self.px(9))
        name = tk.Label(left, text=s.name, bg=CARD_BG, fg=TEXT,
                        font=self.f_title, anchor="w", justify="left")
        name.pack(anchor="w", fill="x")
        status = "今日到期" if due else "待复习"
        line2 = tk.Label(left, text=f"第 {r.n} 次复习    · {status}", bg=CARD_BG,
                         fg=RED if due else GRAY, font=self.f_sub, anchor="w")
        line2.pack(anchor="w")
        line3 = tk.Label(left,
                         text=f"复习日期：{fmt_date(r.date)}    学习日期：{fmt_date(s.learn_date)}",
                         bg=CARD_BG, fg=SUBTLE, font=self.f_sub, anchor="w")
        line3.pack(anchor="w")
        for w in (left, name, line2, line3):
            w.bind("<Button-1>", lambda e, ss=s: self.open_detail(ss))

        var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(inner, variable=var, bg=CARD_BG, activebackground=CARD_BG,
                            cursor="hand2",
                            command=lambda ss=s, rr=r, v=var: self._toggle_pending(ss, rr, v))
        cb.pack(side="right", padx=self.px(10))

    def _toggle_pending(self, s, r, var):
        if var.get():
            s.complete_review(r.n, self.today)
            self.store.save()
            self.refresh_lists()

    @staticmethod
    def _plan_kind_text(s):
        """绿卡第二行的类型文字: 学习计划 / 学习计划（不复习）。"""
        return "学习计划（不复习）" if s.kind == "simple_plan" else "学习计划"

    def _make_plan_card(self, s):
        """学习计划卡: 一个任务一张卡, 显示应完成日期, 右侧方框=完成学习。

        边框: 只有到期/逾期 (应完成日期 <= 今天) 才用绿色突出;
        未来未到期与普通"待复习"卡片一样用灰色边框。
        """
        due = s.learn_date <= self.today
        inner = self._card_frame(self.home_list,
                                 GREEN if due else BORDER, CARD_BG)

        left = tk.Frame(inner, bg=CARD_BG, cursor="hand2")
        left.pack(side="left", fill="both", expand=True, padx=self.px(12), pady=self.px(9))
        name = tk.Label(left, text=s.name, bg=CARD_BG, fg=TEXT,
                        font=self.f_title, anchor="w", justify="left")
        name.pack(anchor="w", fill="x")
        if s.learn_date < self.today:
            tag, tag_col = f"拖欠 {(self.today - s.learn_date).days} 天", RED
        elif s.learn_date == self.today:
            tag, tag_col = "今日到期", RED
        else:
            tag, tag_col = "待学习", GRAY
        line2 = tk.Label(left,
                         text=f"{self._plan_kind_text(s)}    · {tag}", bg=CARD_BG,
                         fg=tag_col, font=self.f_sub, anchor="w")
        line2.pack(anchor="w")
        line3 = tk.Label(left,
                         text=f"应完成日期：{fmt_date(s.learn_date)}",
                         bg=CARD_BG, fg=SUBTLE, font=self.f_sub, anchor="w")
        line3.pack(anchor="w")
        for w in (left, name, line2, line3):
            w.bind("<Button-1>", lambda e, ss=s: self.open_detail(ss))

        var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(inner, variable=var, bg=CARD_BG, activebackground=CARD_BG,
                            cursor="hand2",
                            command=lambda ss=s, v=var: self._complete_plan(ss, v))
        cb.pack(side="right", padx=self.px(10))

    def _complete_plan(self, s, var):
        """绿卡右侧勾选 = 完成学习: 自动生成 +1/+4/+14/+28 的 4 次复习。"""
        if var.get():
            s.complete_learning(self.today)
            self.store.save()
            self.refresh_lists()

    def _build_completed_cards(self):
        self._clear(self.completed_list)
        # 已完成复习卡片 + 已完成学习的绿卡记录, 统一按完成日期排序 (最近在前)
        entries = [(r.done_date or r.date, "review", s, r)
                   for s, r in self.store.completed_cards()]
        entries += [(s.done_learn_date or s.learn_date, "learn", s, None)
                    for s in self.store.completed_learnings()]
        if not entries:
            self._empty(self.completed_list, "还没有完成记录")
            return
        entries.sort(key=lambda e: e[0], reverse=True)
        for _, typ, s, r in entries:
            if typ == "learn":
                self._make_completed_learning_card(s)
            else:
                self._make_completed_card(s, r)

    def _make_completed_card(self, s, r):
        inner = self._card_frame(self.completed_list, BORDER, CARD_DONE_BG)

        left = tk.Frame(inner, bg=CARD_DONE_BG, cursor="hand2")
        left.pack(side="left", fill="both", expand=True, padx=self.px(12), pady=self.px(9))
        name = tk.Label(left, text=s.name, bg=CARD_DONE_BG, fg=GRAY,
                        font=self.f_title_strike, anchor="w", justify="left")
        name.pack(anchor="w", fill="x")
        dd = f"{fmt_date(r.done_date)} 完成" if r.done_date else "已完成"
        sub = tk.Label(left,
                       text=f"第 {r.n} 次复习    · {dd}    复习日 {fmt_date(r.date)}",
                       bg=CARD_DONE_BG, fg=GRAY, font=self.f_sub, anchor="w")
        sub.pack(anchor="w")
        for w in (left, name, sub):
            w.bind("<Button-1>", lambda e, ss=s: self.open_detail(ss))

        var = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, variable=var, bg=CARD_DONE_BG,
                            activebackground=CARD_DONE_BG, cursor="hand2",
                            command=lambda ss=s, rr=r, v=var: self._toggle_completed(ss, rr, v))
        cb.pack(side="right", padx=self.px(10))

    def _toggle_completed(self, s, r, var):
        if not var.get():
            s.restore_review(r.n, self.today)
            self.store.save()
            self.refresh_lists()

    def _make_completed_learning_card(self, s):
        """已完成学习的绿卡记录 (完成 list): 取消勾选 = 还原学习。"""
        inner = self._card_frame(self.completed_list, BORDER, CARD_DONE_BG)

        left = tk.Frame(inner, bg=CARD_DONE_BG, cursor="hand2")
        left.pack(side="left", fill="both", expand=True, padx=self.px(12), pady=self.px(9))
        name = tk.Label(left, text=s.name, bg=CARD_DONE_BG, fg=GRAY,
                        font=self.f_title_strike, anchor="w", justify="left")
        name.pack(anchor="w", fill="x")
        dd = f"{fmt_date(s.done_learn_date)} 完成" if s.done_learn_date else "已完成"
        sub = tk.Label(left,
                       text=f"完成学习    · {dd}    应完成 {fmt_date(s.learn_date)}",
                       bg=CARD_DONE_BG, fg=GRAY, font=self.f_sub, anchor="w")
        sub.pack(anchor="w")
        for w in (left, name, sub):
            w.bind("<Button-1>", lambda e, ss=s: self.open_detail(ss))

        var = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(inner, variable=var, bg=CARD_DONE_BG,
                            activebackground=CARD_DONE_BG, cursor="hand2",
                            command=lambda ss=s, v=var: self._toggle_completed_learning(ss, v))
        cb.pack(side="right", padx=self.px(10))

    def _toggle_completed_learning(self, s, var):
        if not var.get():
            if s.reviews and any(r.done for r in s.reviews):
                if not messagebox.askyesno(
                        "还原学习",
                        f"确定撤销「{s.name}」的完成学习？将同时删除已生成的复习计划。",
                        parent=self.root):
                    var.set(True)
                    return
            s.undo_learning()
            self.store.save()
            self.refresh_lists()

    # ---------------- 详情弹窗 ----------------
    def open_detail(self, s):
        top = tk.Toplevel(self.root)
        top.title(s.name)
        top.transient(self.root)
        top.configure(bg=BG)
        top.geometry(f"{self.px(430)}x{self.px(470)}")
        top.minsize(self.px(360), self.px(400))

        head = tk.Frame(top, bg=ACCENT, height=self.px(46))
        head.pack(fill="x")
        head.pack_propagate(False)
        head_lb = tk.Label(head, text=s.name, bg=ACCENT, fg="white",
                           font=self.f_h1, anchor="w")
        head_lb.pack(side="left", padx=self.px(14))

        body = tk.Frame(top, bg=BG)
        body.pack(fill="both", expand=True, padx=self.px(18), pady=self.px(14))

        # 顶部行: 首次学习 / 应完成学习 + 改期 (与下方复习行的"改期"同一列对齐);
        # 绿卡(学习计划)在"应完成学习"左侧带勾选框: 勾选=完成学习, 取消=放回学习计划
        learn_row = tk.Frame(body, bg=BG)
        learn_row.pack(fill="x", pady=(0, self.px(2)))
        if s.kind in PLAN_KINDS:
            learn_var = tk.BooleanVar(value=s.done_learn)
            tk.Checkbutton(learn_row, variable=learn_var, bg=BG, activebackground=BG,
                           cursor="hand2",
                           command=lambda v=learn_var: self._toggle_plan_learn(s, v, rows, top)
                           ).pack(side="left")
        learn_lb = tk.Label(learn_row, text=self._learn_label(s),
                            bg=BG, fg=TEXT, font=self.f_body, anchor="w")
        learn_lb.pack(side="left", padx=(self.px(2), 0))
        tk.Button(learn_row, text="改期", relief="flat", fg=ACCENT, bg=BG,
                  activebackground=BG, activeforeground=ACCENT, font=self.f_sub,
                  cursor="hand2",
                  command=lambda: self._reschedule_learn(s, learn_lb, rows, top)
                  ).pack(side="right")

        # 复习方式: 需复习(plan) / 不复习(simple_plan);
        # 复习日程也可一键改成"不复习的学习计划"(会先确认再删除复习计划)
        if s.kind == "review":
            options = (("review", "复习日程"), ("simple_plan", "不复习的学习计划"))
        else:
            options = (("plan", "需复习"), ("simple_plan", "不复习"))
        mode_row = tk.Frame(body, bg=BG)
        mode_row.pack(fill="x", pady=(self.px(4), self.px(2)))
        tk.Label(mode_row, text="复习方式：", bg=BG, fg=SUBTLE,
                 font=self.f_sub).pack(side="left")
        mode_var = tk.StringVar(value=s.kind)
        for val, text in options:
            tk.Radiobutton(mode_row, text=text, value=val, variable=mode_var, bg=BG,
                           activebackground=BG, fg=TEXT, font=self.f_sub,
                           cursor="hand2", selectcolor=CARD_BG,
                           command=lambda v=mode_var: self._set_review_mode(s, v, top)
                           ).pack(side="left")

        rows = tk.Frame(body, bg=BG)
        if s.kind == "simple_plan":
            # 不复习的学习计划: 没有任何复习行, 也不显示复习说明文字
            pass
        elif s.kind == "plan" and not s.done_learn:
            # 绿卡(待学习): 尚无复习行
            tk.Label(body, text="勾选上方「应完成学习」方框 = 完成学习，自动生成"
                                " +1/+4/+14/+28 天的 4 次复习；"
                                "「完成」清单里取消勾选可放回学习计划",
                     bg=BG, fg=SUBTLE, font=self.f_sub, anchor="w",
                     wraplength=self.px(360), justify="left").pack(anchor="w",
                                                                   pady=(0, self.px(10)))
            rows.pack(fill="x")
        else:
            tk.Label(body, text="勾选方框标记完成，取消勾选可还原为未完成",
                     bg=BG, fg=SUBTLE, font=self.f_sub).pack(anchor="w", pady=(0, self.px(10)))
            rows.pack(fill="x")
            self._render_detail_rows(rows, s, top)

        btns = tk.Frame(top, bg=BG)
        btns.pack(fill="x", padx=self.px(18), pady=(0, self.px(16)))
        tk.Button(btns, text="删除任务", command=lambda: self._delete_series(s, top),
                  fg=RED, relief="flat", font=self.f_body,
                  cursor="hand2").pack(side="left")
        tk.Button(btns, text="修改名称",
                  command=lambda: self._rename_series(s, top, head_lb),
                  fg=ACCENT, relief="flat", font=self.f_body,
                  cursor="hand2").pack(side="left", padx=(self.px(12), 0))
        tk.Button(btns, text="关闭", command=top.destroy, bg=ACCENT, fg="white",
                  activebackground=ACCENT, activeforeground="white", relief="flat",
                  font=self.f_body, cursor="hand2").pack(side="right", ipadx=self.px(12))

        top.grab_set()

    def _learn_label(self, s):
        """详情顶部行的文字: 复习日程=首次学习; 绿卡=应完成学习。"""
        if s.kind in PLAN_KINDS:
            t = f"应完成学习：{fmt_date(s.learn_date)}"
            if s.done_learn:
                t += f"（{fmt_date(s.done_learn_date)} 完成）"
            return t
        return f"首次学习：{fmt_date(s.learn_date)}"

    def _render_detail_rows(self, rows, s, top):
        self._clear(rows)
        for r in s.reviews:
            row = tk.Frame(rows, bg=BG)
            row.pack(fill="x", pady=self.px(3))
            var = tk.BooleanVar(value=r.done)
            tk.Checkbutton(row, variable=var, bg=BG, activebackground=BG, cursor="hand2",
                           command=lambda rr=r, v=var: self._detail_toggle(s, rr, v, rows, top)
                           ).pack(side="left")
            if r.done:
                fnt, fg = self.f_strike, GRAY
                extra = f"    （{fmt_date(r.done_date)} 完成）" if r.done_date else "    （已完成）"
            else:
                fnt = self.f_body
                due = r.date <= self.today
                fg = RED if due else TEXT
                extra = "    · 今日到期" if due else ""
            tk.Label(row, text=f"第{r.n}次复习    {fmt_date(r.date)}{extra}",
                     bg=BG, fg=fg, font=fnt, anchor="w").pack(side="left", padx=(self.px(4), 0))
            tk.Button(row, text="改期", relief="flat", fg=ACCENT, bg=BG,
                      activebackground=BG, activeforeground=ACCENT, font=self.f_sub,
                      cursor="hand2",
                      command=lambda rr=r: self._reschedule(s, rr, rows, top)
                      ).pack(side="right")

    def _detail_toggle(self, s, r, var, rows, top):
        if var.get():
            s.complete_review(r.n, self.today)
        else:
            s.restore_review(r.n, self.today)
        self.store.save()
        self._render_detail_rows(rows, s, top)
        self.refresh_lists()

    def _reschedule(self, s, r, rows, top):
        picker = DatePicker(top, initial=r.date, title=f"修改第{r.n}次复习日期")
        if picker.result is None:
            return
        s.edit_review_date(r.n, picker.result)
        self.store.save()
        self._render_detail_rows(rows, s, top)
        self.refresh_lists()

    def _reschedule_learn(self, s, learn_lb, rows, top):
        """修改任务"学习日期"(整条任务日期):
        - 绿卡(学习计划): 只改"应完成日期"(警示用, 程序不后移);
          复习在完成学习后按完成日生成, 因此不在此重排。
        - 复习日程: 按新规则同步调整未完成复习 (已完成不动):
            改到 <= 今天 -> 按今日复习计划处理: 最早的未完成复习落到今天,
              其后按 +3/+10/+14 的间隔顺延 (保持复习间隔);
            改到 > 今天  -> 作为日程计划: 未完成复习按 新日期+1/+4/+14/+28 排期。
        """
        title = ("修改应完成学习日期" if s.kind in PLAN_KINDS
                 else "修改首次学习日期")
        picker = DatePicker(top, initial=s.learn_date, title=title)
        if picker.result is None:
            return
        if picker.result == s.learn_date:
            return
        if s.kind in PLAN_KINDS:
            # 绿卡(含不复习类型): 只改"应完成日期"(警示用, 程序不后移)
            s.learn_date = picker.result
        else:
            s.change_learn_date(picker.result, self.today)
        self.store.save()
        self.refresh_lists()
        learn_lb.config(text=self._learn_label(s))
        if s.kind not in PLAN_KINDS or (s.kind == "plan" and s.done_learn):
            self._render_detail_rows(rows, s, top)

    def _set_review_mode(self, s, var, top):
        """详情里切换"复习方式": 需复习 <-> 不复习。

        - 复习日程(review) -> 不复习的学习计划: 删除其 4 次复习计划(先确认);
        - 需复习(plan) <-> 不复习(simple_plan): 未完成学习时直接切换;
          已完成学习时会重建 / 删除复习计划(先确认)。
        """
        target = var.get()
        if target == s.kind:
            return
        if s.kind not in PLAN_KINDS:
            # 复习日程 -> 不复习的学习计划
            if not messagebox.askyesno(
                    "修改复习方式",
                    f"把「{s.name}」改成「不复习的学习计划」？\n"
                    f"将删除它的 4 次复习计划（含已完成的复习记录）。",
                    parent=top):
                var.set(s.kind)
                return
            s.to_simple_plan()
        else:
            want_review = (target == "plan")
            if s.done_learn:
                if want_review:
                    msg = (f"「{s.name}」已完成学习，改成「需复习」将按完成学习日"
                           f"重新生成 4 次复习。")
                else:
                    msg = (f"「{s.name}」已完成学习，改成「不复习」将删除它已生成的"
                           f"复习计划（含已完成的复习记录）。")
                if not messagebox.askyesno("修改复习方式", msg, parent=top):
                    var.set(s.kind)
                    return
            s.set_review_mode(want_review, self.today)
        self.store.save()
        self.refresh_lists()
        top.destroy()
        self.open_detail(s)

    def _toggle_plan_learn(self, s, var, rows, top):
        """详情里"应完成学习"行勾选框: 勾选=完成学习(生成4次复习);
        取消=还原学习, 放回学习计划 (与"完成"清单里取消卡片同一逻辑)。"""
        if var.get() and not s.done_learn:
            s.complete_learning(self.today)
            self.store.save()
            self.refresh_lists()
            top.destroy()
            self.open_detail(s)
        elif (not var.get()) and s.done_learn:
            if s.reviews and any(r.done for r in s.reviews):
                if not messagebox.askyesno(
                        "还原学习",
                        f"确定撤销「{s.name}」的完成学习？将同时删除已生成的复习计划。",
                        parent=top):
                    var.set(True)
                    return
            s.undo_learning()
            self.store.save()
            self.refresh_lists()
            top.destroy()
            self.open_detail(s)

    def _rename_series(self, s, top, head_lb):
        """修改任务名称: 弹小窗输入新名称, 同步刷新弹窗标题 / 头部与背后列表。"""
        dlg = tk.Toplevel(top)
        dlg.title("修改任务名称")
        dlg.transient(top)
        dlg.configure(bg=BG)
        dlg.resizable(False, False)

        wrap = tk.Frame(dlg, bg=BG, padx=self.px(18), pady=self.px(16))
        wrap.pack(fill="both", expand=True)
        tk.Label(wrap, text="任务名称", bg=BG, fg=TEXT, font=self.f_body).pack(anchor="w")
        entry = tk.Entry(wrap, font=self.f_body, relief="solid", bd=1)
        entry.insert(0, s.name)
        entry.pack(fill="x", pady=(self.px(4), self.px(2)), ipady=self.px(5))
        entry.focus_set()
        entry.select_range(0, "end")
        err = tk.Label(wrap, text="", bg=BG, fg=RED, font=self.f_sub, anchor="w")
        err.pack(fill="x", pady=(self.px(2), 0))

        btns = tk.Frame(wrap, bg=BG)
        btns.pack(fill="x", pady=(self.px(10), 0))

        def ok(event=None):
            name = entry.get().strip()
            if not name:
                err.config(text="名称不能为空")
                return
            s.name = name
            self.store.save()
            top.title(s.name)
            head_lb.config(text=s.name)
            self.refresh_lists()
            dlg.destroy()

        entry.bind("<Return>", ok)
        tk.Button(btns, text="确定", width=8, command=ok, bg=ACCENT, fg="white",
                  activebackground=ACCENT, activeforeground="white", relief="flat",
                  cursor="hand2").pack(side="right")
        tk.Button(btns, text="取消", width=8, command=dlg.destroy,
                  relief="flat", cursor="hand2").pack(side="right", padx=(6, 0))

        dlg.grab_set()
        dlg.wait_window()

    def _delete_series(self, s, top):
        if messagebox.askyesno("删除任务",
                               f"确定删除「{s.name}」及其全部复习记录？", parent=top):
            self.store.remove(s.id)
            top.destroy()
            self.refresh_lists()

    # ---------------- 创建 ----------------
    def on_create(self, event=None):
        """录入一条学习内容。支持回车触发: 录入后清空输入框并保持焦点, 便于连续录入。

        三种类型 + 日期自动纠正 (提示里会写明"已自动改为…"):
        - 今日所学（创建复习计划）+ 未来日期 -> 自动改为学习计划;
        - 学习计划（需复习 / 不复习） + 过去日期 -> 自动改为复习计划;
        - 今天选「学习计划」= 今日到期的绿卡, 计入今日待办与进度。
        """
        name = self.name_entry.get().strip()
        if not name:
            self._flash_create_hint("请输入学习内容", RED)
            self.name_entry.focus_set()
            return "break"
        try:
            d = date(self.cy.get(), self.cm.get(), self.cd.get())
        except ValueError:
            self._flash_create_hint("日期无效，请检查年 / 月 / 日", RED)
            return "break"

        today = date.today()
        want = self.create_kind.get()   # review / plan / simple_plan
        corrected = ""
        if want in ("plan", "simple_plan") and d < today:
            # 过去日期对"学习计划"没有意义 -> 自动改为复习日程
            want = "review"
            corrected = "日期已过去，已自动改为复习计划"
        elif want == "review" and d > today:
            want = "plan"
            corrected = "日期为未来，已自动改为学习计划"

        if want == "simple_plan":
            self.store.add_simple_plan(name, d)
            base = (f"已添加学习计划（不复习）「{name}」（应完成 {fmt_date(d)}），"
                    f"可继续输入下一条")
        elif want == "plan":
            self.store.add_plan(name, d)
            base = (f"已添加学习计划「{name}」（应完成 {fmt_date(d)}），"
                    f"完成学习后自动排复习，可继续输入下一条")
        else:
            self.store.add(name, d)
            base = f"已添加「{name}」，可继续输入下一条"
        msg = f"{corrected}；{base}" if corrected else base

        self.name_entry.delete(0, "end")
        self.name_entry.focus_set()
        self._flash_create_hint(msg, GREEN)
        self.refresh_lists()
        return "break"

    def _flash_create_hint(self, text, color):
        """在创建页底部内联显示一条提示 (不弹模态框)。"""
        self.create_hint.config(text=text, fg=color)

    # ---------------- 搜索窗口 ----------------
    def open_search(self):
        """打开独立搜索窗口 (不替换主页面; 关掉它就退出搜索, 不影响主程序)。"""
        win = self._search_win
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            if self._search_entry is not None:
                self._search_entry.focus_set()
            return

        win = tk.Toplevel(self.root)
        self._search_win = win
        win.title("搜索任务")
        win.transient(self.root)
        win.configure(bg=BG)
        win.geometry(f"{self.px(400)}x{self.px(520)}")
        win.minsize(self.px(320), self.px(360))
        win.protocol("WM_DELETE_WINDOW", self._close_search)

        # 顶部搜索栏: 左输入框 + 右"搜索"按钮
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=self.px(14), pady=(self.px(12), self.px(8)))
        self._search_entry = tk.Entry(bar, font=self.f_body, relief="solid", bd=1)
        self._search_entry.pack(side="left", fill="x", expand=True, ipady=self.px(4))
        self._search_entry.bind("<Return>", self._on_search)
        tk.Button(bar, text="搜索", command=self._on_search, bg=ACCENT, fg="white",
                  activebackground=ACCENT, activeforeground="white", relief="flat",
                  font=self.f_nav, cursor="hand2").pack(side="right",
                                                       padx=(self.px(8), 0),
                                                       ipadx=self.px(8))

        # 结果区: 复用现有 Canvas + 滚动条 + 内部 Frame 的可滚动结构
        self._search_area = tk.Frame(win, bg=BG)
        self._search_area.pack(fill="both", expand=True, padx=self.px(4),
                               pady=(0, self.px(8)))
        self._search_list = self._make_scrollable(self._search_area)

        # 旋转动画: 画在结果区中央, 默认隐藏
        self._search_spinner = tk.Canvas(self._search_area, width=self.px(44),
                                         height=self.px(44), bg=BG,
                                         highlightthickness=0)
        self._spinner_item = self._search_spinner.create_arc(
            self.px(4), self.px(4), self.px(40), self.px(40),
            start=0, extent=280, style="arc", outline=ACCENT, width=self.px(4))
        self._spinner_angle = 0
        self._spinner_started = 0.0

        self._search_entry.focus_set()
        self._empty(self._search_list, "请输入关键词")

    def _close_search(self):
        """关闭搜索窗口: 停掉动画与在途回调, 只销毁这个 Toplevel。"""
        self._search_seq += 1              # 让还没执行的结果回调失效
        self._stop_spinner()
        if self._search_win is not None:
            try:
                self._search_win.destroy()
            except Exception:
                pass
        self._search_win = None
        self._search_entry = None
        self._search_list = None
        self._search_spinner = None

    def _on_search(self, event=None):
        """点击"搜索"或输入框回车: 开始搜索 (旋转动画至少显示 250ms)。"""
        if self._search_win is None or not self._search_win.winfo_exists():
            return "break"
        kw = self._search_entry.get().strip()
        self._search_seq += 1
        seq = self._search_seq
        self._stop_spinner()

        if not kw:                          # 空关键词: 只提示, 不展示任何卡片
            self._clear(self._search_list)
            self._empty(self._search_list, "请输入关键词")
            return "break"

        self._clear(self._search_list)
        self._start_spinner()
        results = self._match_series(kw)
        # 本地搜索几乎瞬时完成, 这里保证动画至少可见 250ms
        delay = max(0, int((0.25 - (time.monotonic() - self._spinner_started)) * 1000))
        self._search_win.after(delay, lambda: self._finish_search(seq, results))
        return "break"

    def _match_series(self, kw):
        """大小写不敏感的子串匹配; 按任务去重 (每个 Series 只返回一次)。"""
        key = kw.casefold()
        return [s for s in self.store.series if key in s.name.casefold()]

    def _finish_search(self, seq, results):
        """动画结束 -> 渲染结果 (窗口已关或又发起了新搜索则丢弃)。"""
        if seq != self._search_seq or self._search_win is None:
            return
        self._stop_spinner()
        self._clear(self._search_list)
        if not results:
            self._empty(self._search_list, "没有找到匹配的任务")
            return
        for s in results:
            self._make_search_card(s)

    def _make_search_card(self, s):
        """搜索结果卡片: 只显示任务名称, 点击进入与学习表一致的详情弹窗。"""
        inner = self._card_frame(self._search_list, BORDER, CARD_BG)
        name = tk.Label(inner, text=s.name, bg=CARD_BG, fg=TEXT,
                        font=self.f_title, anchor="w", justify="left",
                        cursor="hand2")
        name.pack(fill="x", padx=self.px(12), pady=self.px(12))
        name.bind("<Button-1>", lambda e, ss=s: self.open_detail(ss))

    def _start_spinner(self):
        """显示并启动旋转动画 (Canvas 圆弧 + after 定时改起始角度)。"""
        cv = self._search_spinner
        if cv is None:
            return
        self._spinner_started = time.monotonic()
        cv.place(relx=0.5, rely=0.42, anchor="center")
        self._rotate_spinner()

    def _rotate_spinner(self):
        cv = self._search_spinner
        if cv is None or not cv.winfo_exists():
            return
        self._spinner_angle = (self._spinner_angle - 30) % 360
        cv.itemconfig(self._spinner_item, start=self._spinner_angle)
        self._spinner_job = cv.after(80, self._rotate_spinner)

    def _stop_spinner(self):
        cv = self._search_spinner
        if self._spinner_job is not None and cv is not None:
            try:
                cv.after_cancel(self._spinner_job)
            except Exception:
                pass
        self._spinner_job = None
        if cv is not None:
            try:
                cv.place_forget()
            except Exception:
                pass

    # ---------------- 后台运行 / 系统托盘 / 定点通知 ----------------
    def _setup_background(self):
        """关窗隐藏到托盘 + 后台定点提醒。托盘不可用时回退为正常关闭退出。"""
        now = datetime.now()
        # 启动时若正处在整点, 记下来避免立刻再补发一条通知
        if now.hour in TRIGGER_HOURS:
            self._last_notify_key = (now.date(), now.hour)
        if tray is not None:
            try:
                self.tray = tray.TrayIcon(
                    self._tray_tip(self._last_due_count or 0),
                    self.show_window, self.quit_app)
            except Exception:
                self.tray = None
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        if self.tray:
            self.root.after(200, self._pump_tray)
        self.root.after(20000, self._tick)
        # 开机自启(后台)时, 启动后先推送一条"今天任务"通知, 像原来的提醒代理一样
        if self.notify_on_start:
            self.root.after(1500, self._startup_notify)

    def _startup_notify(self):
        """启动即发一条通知: 问候语 + 今天待完成任务 (数目用内存实时值, 与标题栏一致)。"""
        try:
            now = datetime.now()
            notifier.toast(notifier.greeting(now),
                           notifier.task_phrase(self._due_count()))
        except Exception:
            pass

    def _pump_tray(self):
        """由 Tk 主循环驱动托盘消息, 避免另开线程与 Win32 消息循环冲突。"""
        if self.tray:
            try:
                self.tray.pump()
            except Exception:
                pass
            self.root.after(200, self._pump_tray)

    def _tick(self):
        """周期性: 跨天重算后移 + 到点推送通知。"""
        now = datetime.now()
        if now.date() != self.today:
            self.today = now.date()
            self.store.run_daily_postpone(self.today)
            self.refresh_lists()
        self._maybe_notify(now)
        self.root.after(20000, self._tick)

    def _maybe_notify(self, now):
        key = (now.date(), now.hour)
        if now.hour in TRIGGER_HOURS and self._last_notify_key != key:
            self._last_notify_key = key
            try:
                notifier.notify_tasks(now)
            except Exception:
                pass

    def hide_window(self):
        """点 X: 有托盘就藏起来继续后台跑, 否则真正退出。"""
        if self.tray:
            self.root.withdraw()
        else:
            self.quit_app()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit_app(self):
        if self.tray:
            try:
                self.tray.remove()
            except Exception:
                pass
        self.root.destroy()


def _acquire_single_instance():
    """独占运行。若已有实例在跑, 通知它显示窗口, 并返回 (False, None)。
    正常获得锁时返回 (True, mutex_handle), 需持有句柄直到进程退出。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            _signal_existing_instance()
            return False, None
        return True, handle
    except Exception:
        # 互斥不可用时不阻塞启动, 照常运行
        return True, None


def _signal_existing_instance():
    """让已在后台运行的实例把主界面显示出来 (双击自启后再点图标时用)。"""
    if tray is None:
        return
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.PostMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        hwnd = user32.FindWindowW(None, tray.TRAY_WINDOW_TITLE)
        if hwnd:
            user32.PostMessageW(hwnd, tray.WM_TRAY_SHOW, 0, 0)
    except Exception:
        pass


def main():
    minimized = ("--minimized" in sys.argv) or ("--tray" in sys.argv)

    ok, _mutex = _acquire_single_instance()
    if not ok:
        return  # 已有实例在运行, 已请求其显示窗口

    _enable_dpi_awareness()
    root = tk.Tk()
    dpi = root.winfo_fpixels("1i")  # 真实每英寸像素数 (100%=96, 150%=144)
    root.tk.call("tk", "scaling", dpi / 72.0)  # 让点数字体按真实 DPI 渲染
    app = App(root, scale=dpi / 96.0, notify_on_start=minimized)  # 像素常量按同比例放大, 尺寸保持不变
    # 开机自启时用 --minimized: 直接缩进托盘后台运行, 不弹窗打扰
    if minimized and app.tray is not None:
        root.withdraw()
    root.mainloop()


if __name__ == "__main__":
    main()
