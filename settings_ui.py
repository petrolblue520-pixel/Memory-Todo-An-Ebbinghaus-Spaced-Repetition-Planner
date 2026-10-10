"""苍蝇设置窗 (齿轮图标点开的那个)。纯 Tkinter, 不引入任何第三方依赖。

设计原则:
- **改哪一项就立刻生效**, 没有"确定/取消"; 关掉窗口不会回滚;
- 所有控件都只读写同一个 prefs.Prefs 对象, 所以设置窗和托盘菜单不可能不一致;
- 孵化时间用 时/分 两个输入框, 分钟按你要求的 **0-59** 校验: 输错了红框提示, 不写数据,
  失焦后自动退回上一个合法值;
- 静止时间按你要求做成 **输入框**(输入多少是多少, 单位秒), 不是滑块;
- 底部实时显示"苍蝇 / 卵 / fps / 帧缓存", 调参时能立刻看到效果。

配色/字体全部从 App 实例上取 (px 缩放、字体、主题色), 这样和主界面完全一致。
"""

from __future__ import annotations

import tkinter as tk

from prefs import (DEFAULTS, HATCH_HOUR_MAX, hours_ok, join_hatch,
                   minutes_ok, split_hatch)

# 兜底主题 (正常情况下由 App 传入真实配色)
THEME = {
    "accent": "#00897b", "bg": "#f4f6f8", "card": "#ffffff",
    "border": "#e0e0e0", "text": "#212121", "subtle": "#6b6b6b",
    "green": "#2e7d32", "red": "#d81f1f", "nav": "#ffffff",
}
ERR_BG = "#ffe3e3"


class SettingsWindow(tk.Toplevel):
    """齿轮设置窗。同一个 App 最多一个实例 (由 App.open_settings 管理)。"""

    def __init__(self, app, on_close=None):
        super().__init__(app.root)
        self.app = app
        self.px = app.px
        self.prefs = app.prefs
        self.swarm = getattr(app, "flies", None)
        self.on_close = on_close
        self.t = dict(THEME)
        self.t.update(getattr(app, "theme", None) or {})
        self.f_h1 = app.f_h1
        self.f_body = app.f_body
        self.f_sub = app.f_sub
        self.f_nav = app.f_nav

        self.title("设置")
        self.configure(bg=self.t["bg"])
        self.transient(app.root)
        self.geometry("%dx%d" % (self.px(430), self.px(620)))
        self.minsize(self.px(390), self.px(420))
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._vars = {}
        self._stat_job = None
        self._build()
        self.prefs.subscribe(self._on_pref_change)
        self._tick_stats()

    # ================================================================ 构建
    def _build(self):
        self._build_header()
        self._build_footer()
        body = self._make_scroll()
        self._section(body, "苍蝇")
        self._check(body, "enabled", "启用桌面苍蝇")
        self._check(body, "show_eggs", "显示卵（卵仍在倒计时）")
        self._spin(body, "max_entities", "数量上限（苍蝇+卵）", 1, 30, "个")
        self._hint(body, "整个桌面上的苍蝇与卵总数不超过这个数；超出的立即消失（不会变成卵）。")
        self._slider(body, "fly_scale", "苍蝇大小", 0.6, 1.8, 0.05,
                     lambda v: "%d%%" % round(v * 100))
        self._section(body, "苍蝇操作")
        self._actions(body)
        self._section(body, "运动参数")
        self._slider(body, "speed_crawl", "爬行速度", 10, 200, 1,
                     lambda v: "%d px/s" % round(v))
        self._slider(body, "speed_fly", "飞行速度", 100, 1500, 10,
                     lambda v: "%d px/s" % round(v))
        self._slider(body, "flee_factor", "逃跑速度（倍率）", 0.5, 3.0, 0.05,
                     lambda v: "%.2fx" % v)
        self._entry(body, "rest_seconds", "静止时间", "秒")
        self._slider(body, "flee_radius", "逃跑触发半径", 40, 250, 5,
                     lambda v: "%d px" % round(v))
        self._slider(body, "wing_hz", "振翅频率", 8, 40, 1,
                     lambda v: "%d Hz" % round(v))
        self._hint(body, "静止时间直接输入秒数（输入多少是多少）；0 表示不停下来。")
        self._section(body, "苍蝇拍")
        self._radio(body, "swatter", [(False, "关闭"), (True, "开启（跟随光标）")],
                    "苍蝇拍模式")
        self._slider(body, "swatter_hit_radius", "拍打命中半径", 20, 90, 1,
                     lambda v: "%d px" % round(v))
        self._check(body, "swatter_swing", "挥拍动画")
        self._slider(body, "swatter_scale", "拍子大小", 0.6, 1.6, 0.05,
                     lambda v: "%d%%" % round(v * 100))
        self._check(body, "swatter_desktop_only", "只在桌面可见时才显示拍子")
        self._section(body, "层级 / 性能 / 隐私")
        self._radio(body, "layer", [("desktop", "只在桌面级（被窗口盖住）"),
                                    ("topmost", "最上层（浮在所有窗口上）")],
                    "显示层级")
        self._hint(body, "桌面级：压在桌面图标上、被浏览器等窗口盖住；"
                         "最上层：一直浮在最前面，会挡住视线。")
        self._check(body, "pause_when_covered", "被窗口盖住时暂停动画（省电主力）")
        self._check(body, "pause_when_idle", "锁屏 / 长时间无操作时暂停")
        self._check(body, "battery_low_fps", "电池供电时降到 20fps")
        self._check(body, "hide_from_capture", "截屏 / 录屏里隐身")
        self._radio(body, "spawn_mode", [("taskbar", "任务栏随机位置"),
                                         ("tray", "托盘区"),
                                         ("mixed", "随机爬出 / 飞出")],
                    "出场方式")
        self._section(body, "孵化时间")
        self._hatch(body)
        self._section(body, "状态")
        self.stat_label = tk.Label(body, text="", bg=self.t["bg"], fg=self.t["subtle"],
                                   font=self.f_sub, anchor="w", justify="left")
        self.stat_label.grid(row=self._next_row(body), column=0, columnspan=3,
                             sticky="w", padx=self.px(10), pady=(self.px(4), self.px(2)))
        self._hint(body, "改完即时生效，没有“确定”按钮。")

    def _build_header(self):
        head = tk.Frame(self, bg=self.t["accent"], height=self.px(46))
        head.pack(fill="x", side="top")
        head.pack_propagate(False)
        tk.Label(head, text="设置", bg=self.t["accent"], fg="white",
                 font=self.f_h1).pack(side="left", padx=(self.px(16), 0))
        close = tk.Label(head, text="✕", bg=self.t["accent"], fg="white",
                         font=self.f_h1, cursor="hand2")
        close.pack(side="right", padx=self.px(14))
        close.bind("<Button-1>", lambda e: self.close())

    def _build_footer(self):
        foot = tk.Frame(self, bg=self.t["nav"], height=self.px(48),
                        highlightbackground=self.t["border"], highlightthickness=1)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        tk.Button(foot, text="恢复默认设置", command=self._reset,
                  relief="flat", bd=0, bg=self.t["nav"], fg=self.t["subtle"],
                  activebackground=self.t["bg"], activeforeground=self.t["red"],
                  font=self.f_nav, cursor="hand2").pack(side="left",
                                                        padx=self.px(12))

    def _make_scroll(self):
        wrap = tk.Frame(self, bg=self.t["bg"])
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=self.t["bg"], highlightthickness=0)
        bar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.t["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        inner.bind("<MouseWheel>",
                   lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self._body = inner
        self._body_rows = 0
        for col, minsize in ((0, 132), (1, 120), (2, 90)):
            inner.columnconfigure(col, minsize=self.px(minsize))
        return inner

    # ---- 行的通用工具 ----
    def _next_row(self, parent):
        r = self._body_rows
        self._body_rows += 1
        return r

    def _section(self, parent, title):
        r = self._next_row(parent)
        bar = tk.Frame(parent, bg=self.t["bg"])
        bar.grid(row=r, column=0, columnspan=3, sticky="ew",
                 padx=self.px(10), pady=(self.px(14), self.px(4)))
        tk.Label(bar, text=title, bg=self.t["bg"], fg=self.t["accent"],
                 font=self.f_nav).pack(side="left")
        tk.Frame(bar, bg=self.t["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(self.px(8), 0))

    def _hint(self, parent, text):
        r = self._next_row(parent)
        tk.Label(parent, text=text, bg=self.t["bg"], fg=self.t["subtle"],
                 font=self.f_sub, anchor="w", justify="left",
                 wraplength=self.px(390)).grid(row=r, column=0, columnspan=3,
                                               sticky="w", padx=self.px(12),
                                               pady=(0, self.px(2)))

    def _label(self, parent, text):
        r = self._next_row(parent)
        tk.Label(parent, text=text, bg=self.t["bg"], fg=self.t["text"],
                 font=self.f_body, anchor="w").grid(row=r, column=0, sticky="w",
                                                    padx=(self.px(12), 0),
                                                    pady=self.px(3))
        return r

    # ---- 三种控件 ----
    def _check(self, parent, key, text):
        var = tk.BooleanVar(value=bool(self.prefs.get(key)))
        self._vars[key] = var

        def on():
            self.prefs.set(key, bool(var.get()))
        tk.Checkbutton(parent, text=text, variable=var, command=on,
                       bg=self.t["bg"], fg=self.t["text"], font=self.f_body,
                       activebackground=self.t["bg"], selectcolor=self.t["card"],
                       bd=0, highlightthickness=0, anchor="w",
                       cursor="hand2").grid(row=self._next_row(parent), column=0,
                                            columnspan=3, sticky="w", padx=self.px(12))

    def _radio(self, parent, key, options, text):
        row = self._label(parent, text)
        var = tk.StringVar(value=str(self.prefs.get(key)))
        self._vars[key] = var
        box = tk.Frame(parent, bg=self.t["bg"])
        box.grid(row=row, column=1, columnspan=2, sticky="w", pady=self.px(2))

        def on():
            self.prefs.set(key, var.get())
        for value, label in options:
            tk.Radiobutton(box, text=label, value=str(value), variable=var,
                           command=on, bg=self.t["bg"], fg=self.t["text"],
                           font=self.f_body, activebackground=self.t["bg"],
                           selectcolor=self.t["card"], bd=0, highlightthickness=0,
                           anchor="w", cursor="hand2").pack(side="top", anchor="w")

    def _spin(self, parent, key, text, lo, hi, hint=""):
        row = self._label(parent, text)
        var = tk.StringVar(value=str(self.prefs.get(key)))
        self._vars[key] = var
        spin = tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=5,
                          font=self.f_body, justify="center", bd=1,
                          relief="solid", highlightthickness=0)
        spin.grid(row=row, column=1, sticky="w")
        if hint:
            tk.Label(parent, text=hint, bg=self.t["bg"], fg=self.t["subtle"],
                     font=self.f_sub).grid(row=row, column=2, sticky="w",
                                           padx=(self.px(6), 0))
            self._body.columnconfigure(2, minsize=self.px(96))

        def commit(_e=None):
            self.prefs.set(key, var.get())
        spin.bind("<Return>", commit)
        spin.bind("<FocusOut>", commit)

    def _slider(self, parent, key, text, lo, hi, step, fmt):
        row = self._label(parent, text)
        value = tk.DoubleVar(value=float(self.prefs.get(key)))
        val_label = tk.Label(parent, text=fmt(value.get()), bg=self.t["bg"],
                             fg=self.t["subtle"], font=self.f_sub, width=9,
                             anchor="w")
        val_label.grid(row=row, column=2, sticky="w", padx=(self.px(6), 0))

        def on(v):
            got = float(v)
            val_label.config(text=fmt(got))
            self.prefs.set(key, got)
        scale = tk.Scale(parent, from_=lo, to=hi, resolution=step, orient="horizontal",
                         variable=value, showvalue=0, command=on,
                         bg=self.t["card"], troughcolor="#cfd8dc",
                         activebackground=self.t["accent"], highlightthickness=0,
                         bd=0, sliderrelief="raised", length=self.px(150))
        scale.grid(row=row, column=1, sticky="w")
        self._vars[key] = value

    def _entry(self, parent, key, text, unit):
        """自由输入的数字框 (静止时间用: 输入多少是多少)。"""
        row = self._label(parent, text)
        var = tk.StringVar(value=_num_text(self.prefs.get(key)))
        self._vars[key] = var
        ent = tk.Entry(parent, textvariable=var, width=7, font=self.f_body,
                       justify="center", bd=1, relief="solid",
                       highlightthickness=0)
        ent.grid(row=row, column=1, sticky="w")
        tk.Label(parent, text=unit, bg=self.t["bg"], fg=self.t["subtle"],
                 font=self.f_sub).grid(row=row, column=2, sticky="w",
                                       padx=(self.px(6), 0))

        def commit(_e=None):
            txt = var.get().strip()
            try:
                float(txt)
            except ValueError:
                ent.config(bg=ERR_BG)
                return
            ent.config(bg=self.t["card"])
            self.prefs.set(key, txt)
        ent.bind("<Return>", commit)
        ent.bind("<FocusOut>", commit)

    # ---- 孵化时间 (时/分, 分必须 < 60) ----
    def _hatch(self, parent):
        row = self._label(parent, "孵化时间")
        h0, m0 = split_hatch(self.prefs.get("hatch_seconds"))
        self.hatch_h_var = tk.StringVar(value=str(h0))
        self.hatch_m_var = tk.StringVar(value=str(m0))
        self._vars["hatch_seconds"] = self.hatch_h_var
        box = tk.Frame(parent, bg=self.t["bg"])
        box.grid(row=row, column=1, columnspan=2, sticky="w")
        self.hatch_h = tk.Spinbox(box, from_=0, to=HATCH_HOUR_MAX,
                                  textvariable=self.hatch_h_var, width=4,
                                  font=self.f_body, justify="center", bd=1,
                                  relief="solid", highlightthickness=0)
        self.hatch_h.pack(side="left")
        tk.Label(box, text="时", bg=self.t["bg"], fg=self.t["subtle"],
                 font=self.f_sub).pack(side="left", padx=(self.px(3), self.px(8)))
        self.hatch_m = tk.Spinbox(box, from_=0, to=59,
                                  textvariable=self.hatch_m_var, width=4,
                                  font=self.f_body, justify="center", bd=1,
                                  relief="solid", highlightthickness=0)
        self.hatch_m.pack(side="left")
        tk.Label(box, text="分", bg=self.t["bg"], fg=self.t["subtle"],
                 font=self.f_sub).pack(side="left", padx=(self.px(3), 0))

        r2 = self._next_row(parent)
        self.hatch_err = tk.Label(parent, text="", bg=self.t["bg"], fg=self.t["red"],
                                  font=self.f_sub, anchor="w")
        self.hatch_err.grid(row=r2, column=0, columnspan=3, sticky="w",
                            padx=self.px(12))
        self._hint(parent, "分钟必须是 0-59 的整数；改短只影响**新**产下的卵。")

        for w in (self.hatch_h, self.hatch_m):
            w.bind("<Return>", self._commit_hatch)
            w.bind("<FocusOut>", self._commit_hatch)
        self.hatch_h_var.trace_add("write", lambda *a: self._validate_hatch())
        self.hatch_m_var.trace_add("write", lambda *a: self._validate_hatch())

    def _validate_hatch(self):
        """只做红框/提示, 不写数据 —— 输错了不会悄悄改成别的值。"""
        ok_h = hours_ok(self.hatch_h_var.get())
        ok_m = minutes_ok(self.hatch_m_var.get())
        self.hatch_h.config(bg=(self.t["card"] if ok_h else ERR_BG))
        self.hatch_m.config(bg=(self.t["card"] if ok_m else ERR_BG))
        msgs = []
        if not ok_h:
            msgs.append("小时需为 0-%d 的整数" % HATCH_HOUR_MAX)
        if not ok_m:
            msgs.append("分钟需小于 60")
        self.hatch_err.config(text="；".join(msgs))

    def _commit_hatch(self, _e=None):
        ok_h = hours_ok(self.hatch_h_var.get())
        ok_m = minutes_ok(self.hatch_m_var.get())
        if ok_h and ok_m:
            self.prefs.set("hatch_seconds",
                           join_hatch(self.hatch_h_var.get(), self.hatch_m_var.get()))
            self.hatch_err.config(text="")
        else:
            # 失焦时退回上一个合法值, 避免界面和数据对不上
            h, m = split_hatch(self.prefs.get("hatch_seconds"))
            self.hatch_h_var.set(str(h))
            self.hatch_m_var.set(str(m))

    # ---- 四个操作按钮 ----
    def _actions(self, parent):
        row = self._next_row(parent)
        box = tk.Frame(parent, bg=self.t["bg"])
        box.grid(row=row, column=0, columnspan=3, sticky="w",
                 padx=self.px(12), pady=self.px(4))
        specs = (("杀死全部苍蝇", self._kill_all),
                 ("全部复活", self._revive_all),
                 ("重新出场", self._respawn),
                 ("清空所有卵", self._clear_eggs))
        for text, cmd in specs:
            tk.Button(box, text=text, command=cmd, relief="flat", bd=0,
                      bg=self.t["card"], fg=self.t["text"], font=self.f_sub,
                      activebackground=self.t["accent"], activeforeground="white",
                      padx=self.px(8), pady=self.px(3),
                      cursor="hand2").pack(side="left", padx=(0, self.px(6)))

    def _swarm_call(self, name):
        if self.swarm is None:
            return 0
        try:
            return getattr(self.swarm, name)()
        except Exception:
            return 0

    def _kill_all(self):
        self._swarm_call("kill_all_to_eggs")

    def _revive_all(self):
        self._swarm_call("revive_all")

    def _respawn(self):
        self._swarm_call("respawn_all")

    def _clear_eggs(self):
        self._swarm_call("clear_eggs")

    def _reset(self):
        self.prefs.reset()
        self._reload_controls()

    # ================================================================ 同步
    def _on_pref_change(self, key):
        """别的地方 (托盘菜单 / 代码) 改了设置 -> 控件跟着动。"""
        if key == "*":
            self._reload_controls()
            return
        var = self._vars.get(key)
        if var is None:
            return
        value = self.prefs.get(key)
        try:
            if isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            elif isinstance(var, tk.DoubleVar):
                var.set(float(value))
            elif key == "hatch_seconds":
                h, m = split_hatch(value)
                if (self.hatch_h_var.get(), self.hatch_m_var.get()) != (str(h), str(m)):
                    self.hatch_h_var.set(str(h))
                    self.hatch_m_var.set(str(m))
            elif isinstance(var, tk.StringVar):
                if var.get() != str(value):
                    var.set(str(value))
        except tk.TclError:
            pass

    def _reload_controls(self):
        for key, var in self._vars.items():
            value = self.prefs.get(key)
            try:
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(value))
                elif isinstance(var, tk.DoubleVar):
                    var.set(float(value))
                elif key == "hatch_seconds":
                    h, m = split_hatch(value)
                    self.hatch_h_var.set(str(h))
                    self.hatch_m_var.set(str(m))
                elif isinstance(var, tk.StringVar):
                    var.set(str(value))
            except tk.TclError:
                pass

    def _tick_stats(self):
        """底部状态行: 调参时能立刻看到苍蝇/卵/fps 的变化。"""
        if not self.winfo_exists():
            return
        sw = self.swarm
        if sw is None:
            text = "桌面苍蝇未启用"
        else:
            s = sw.stats()
            text = ("苍蝇 %d · 卵 %d · %.1f fps · 帧缓存 %d%%%s%s"
                    % (s["flies"], s["eggs"], s["fps"], s["warm"],
                       " · 已暂停" if s["paused"] else "",
                       " · 电池省电" if s["battery"] else ""))
        try:
            self.stat_label.config(text=text)
        except tk.TclError:
            return
        self._stat_job = self.after(500, self._tick_stats)

    def close(self):
        if self._stat_job is not None:
            try:
                self.after_cancel(self._stat_job)
            except Exception:
                pass
            self._stat_job = None
        self.prefs.flush()
        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass


def _num_text(value):
    """数字 -> 输入框文本 (整数不带小数点)。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(f)) if abs(f - round(f)) < 1e-9 else ("%g" % f)


def gear_points(cx, cy, r_out, r_in, teeth=8):
    """齿轮 → 一圈多边形顶点 (给主界面的 Canvas 手绘图标用)。

    每个齿给 4 个点 (齿根->齿顶->齿顶->齿根), 拼成一圈; 中间再挖一个洞由调用方画。
    """
    import math
    pts = []
    for i in range(teeth):
        a0 = (i * 360.0 / teeth) - 360.0 / (teeth * 2)
        for a, r in ((a0 - 9.0, r_in), (a0 - 5.0, r_out),
                     (a0 + 5.0, r_out), (a0 + 9.0, r_in),
                     (a0 + 360.0 / (teeth * 2) - 9.0, r_in)):
            rad = math.radians(a)
            pts.extend((cx + r * math.sin(rad), cy - r * math.cos(rad)))
    return pts
