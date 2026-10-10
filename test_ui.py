"""界面行为测试 (需要 Windows 桌面, 会真的建窗口, 但不进入 mainloop)。

覆盖「标题单行省略 / 详情窗横竖滚动 / 详情查看与编辑 / 创建任务两段式流程」这些
交互规则, 全部用 Tk 内省断言 (不依赖截图)。直接 `python test_ui.py` 运行,
结尾打印 ALL UI CHECKS PASSED 表示全部通过。数据写在临时目录, 不碰真实数据。
"""

import calendar as _calendar
import os
import tempfile
import time
from datetime import date, timedelta

os.environ["EBBINGHAUS_DATA_DIR"] = tempfile.mkdtemp()

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import app as appmod  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   - " if cond else "  FAIL - ") + msg)
    if not cond:
        FAILS.append(msg)


def walk(w, cls=None):
    out = []
    for c in w.winfo_children():
        if cls is None or isinstance(c, cls):
            out.append(c)
        out += walk(c, cls)
    return out


def find_button(w, text):
    for b in walk(w, tk.Button):
        if b.cget("text") == text:
            return b
    return None


LONG = "数据结构与算法总复习：" + "红黑树与线段树并查集" * 5
SHORT = "高数"
DETAIL = "\n".join(f"第 {i} 行：知识点与备注" for i in range(1, 15))

root = tk.Tk()
a = appmod.App(root, scale=1.0)
long_s = a.store.add(LONG, a.today, DETAIL)
short_s = a.store.add(SHORT, a.today)
empty_s = a.store.add("空详情任务", a.today)
root.update()
a.show_page("home")
root.update()
# 新建任务的首个复习都在未来, 默认收在「后续任务」里; 展开后再验证卡片渲染
a._toggle_home_future()
root.update()

# ---------------- A. 卡片标题单行 + 省略号 + 动态重算 ----------------
print("A. 卡片标题")
labels = walk(a.home_list, appmod.ElidedLabel)
by_full = {lb._full_text: lb for lb in labels}
lb_long = by_full.get(LONG)
lb_short = by_full.get(SHORT)
check(lb_long is not None and lb_short is not None, "卡片标题用 ElidedLabel 渲染")
fnt = a.f_title
shown = lb_long.cget("text")
check(shown.endswith("…"), f"长标题被截断为省略号: {shown!r}")
check(fnt.measure(shown) <= lb_long.winfo_width(), "省略后文本宽度不超过标签宽度")
check(lb_short.cget("text") == SHORT, "短标题原样显示")

# 单行: 卡片高度不因长标题变高
card_h = {}
for lb in (lb_long, lb_short):
    w = lb
    while w.master is not a.home_list:
        w = w.master
    card_h[lb._full_text] = w.winfo_height()
check(card_h[LONG] == card_h[SHORT],
      f"长标题不撑高卡片: 长={card_h[LONG]} 短={card_h[SHORT]}")

# 窗口拉宽 -> 多显示; 变窄 -> 重新省略
n1 = len(lb_long.cget("text"))
root.geometry("760x724")
root.update()
root.update_idletasks()
root.update()
n2 = len(lb_long.cget("text"))
root.geometry("400x724")
root.update()
root.update_idletasks()
root.update()
n3 = len(lb_long.cget("text"))
check(n2 > n1 > n3, f"标题随宽度动态重算: 窄{n3} < 原{n1} < 宽{n2}")
check(fnt.measure(lb_long.cget("text")) <= lb_long.winfo_width(),
      "变窄后仍不溢出")
root.geometry("448x724")
root.update()

# 卡片列表不展示详情
allcard_text = " ".join(lb._full_text for lb in walk(a.home_list, appmod.ElidedLabel))
check("知识点与备注" not in allcard_text, "卡片上不展示详情内容")

# ---------------- B. 详情窗口 ----------------
print("B. 详情窗口")
a.open_detail(long_s)
root.update()
top = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
ts = walk(top, appmod.ScrollingTitle)[0]
det = walk(top, tk.Text)[0]
check(ts.canvas.itemcget(ts._item, "text") == LONG, "顶部标题区显示完整标题")
check(ts._shown and ts.hsb.winfo_ismapped(), "标题溢出 -> 出现横向滚动条")

x0 = ts.canvas.xview()
ts.canvas.event_generate("<MouseWheel>", delta=-120)
root.update()
x1 = ts.canvas.xview()
check(x1[0] > x0[0], f"标题区滚轮向右滚动: {x0[0]:.2f} -> {x1[0]:.2f}")
y0 = det.yview()
det.event_generate("<MouseWheel>", delta=-120)
root.update()
check(det.yview()[0] > y0[0], f"详情区滚轮向下滚动: {y0[0]:.3f} -> {det.yview()[0]:.3f}")
check(ts.canvas.xview()[0] == x1[0], "标题滚动位置不被详情滚动影响 (各滚各的)")

det_vsb = walk(det.master, ttk.Scrollbar)[0]
check(det.cget("state") == "disabled", "详情默认只读")
check(det.get("1.0", "end-1c") == DETAIL, "详情内容完整展示")
check(det.yview()[1] < 1.0, "长详情内容溢出")
check(det_vsb.winfo_ismapped(), "长详情 -> 出现竖向滚动条")

# 编辑详情
edit_btn = find_button(top, "编辑详情")
edit_btn.invoke()
root.update()
check(det.cget("state") == "normal", "点「编辑详情」后可编辑")
det.insert("end", "\n补充一行")
det.event_generate("<Control-s>")
root.update()
check(det.cget("state") == "normal", "Ctrl+S 保存但不结束编辑")
check(long_s.detail.endswith("补充一行"), "Ctrl+S 已写回数据")

n_before = int(det.index("end-1c").split(".")[0])
det.event_generate("<Shift-Return>")
root.update()
n_after = int(det.index("end-1c").split(".")[0])
check(n_after == n_before + 1, "Shift+Enter 换行")
check(det.cget("state") == "normal", "Shift+Enter 不提交")

det.event_generate("<Return>")
root.update()
check(det.cget("state") == "disabled", "Enter 保存并结束编辑")
check(long_s.detail.endswith("补充一行"), "Enter 写回数据")
check(find_button(top, "编辑详情") is not None, "结束后按钮回到「编辑详情」")

# 清空详情 -> 存空, 显示"无"
edit_btn.invoke()
root.update()
det.delete("1.0", "end")
det.event_generate("<Return>")
root.update()
check(long_s.detail == "", "清空详情后存空字符串 (不存'无')")
check(det.get("1.0", "end-1c") == "无", "空详情显示为「无」")
check(not det_vsb.winfo_ismapped(), "装得下时竖向滚动条消失")

# 空详情的窗口
top.destroy()
a.open_detail(empty_s)
root.update()
top2 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
det2 = walk(top2, tk.Text)[0]
check(det2.get("1.0", "end-1c") == "无", "旧/空任务详情显示「无」")
find_button(top2, "编辑详情").invoke()
root.update()
check(det2.get("1.0", "end-1c") == "", "从「无」进入编辑时输入框为空")
top2.destroy()

# 标题装得下时不显示横向滚动条
a.open_detail(short_s)
root.update()
top3 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
ts3 = walk(top3, appmod.ScrollingTitle)[0]
check(not ts3._shown and not ts3.hsb.winfo_ismapped(),
      "标题装得下 -> 不显示横向滚动条")
top3.destroy()

# ---------------- C. 创建流程 ----------------
print("C. 创建流程")
answers = {"yes": True}
appmod.messagebox.askyesno = lambda *ar, **kw: answers["yes"]


def open_create_entry():
    a.show_page("create")
    root.update()
    a.name_entry.delete(0, "end")
    a.name_entry.insert(0, "线性代数特征值")


n0 = len(a.store.series)
open_create_entry()
a.name_entry.delete(0, "end")
check(a.on_create() == "break" and len(a.store.series) == n0, "空标题禁止创建")

# 第一次 Enter -> 详情窗 (不直接创建)
open_create_entry()


def fill_and_enter():
    dlg = [w for w in root.winfo_children() if isinstance(w, appmod.DetailEditor)][-1]
    dlg.text.insert("1.0", "先复习定义\n再刷两道题")
    dlg.text.event_generate("<Return>")


root.after(300, fill_and_enter)
a.on_create()
root.update()
check(len(a.store.series) == n0 + 1, "第二次 Enter 完成创建")
new = a.store.series[-1]
check(new.detail == "先复习定义\n再刷两道题", f"详情已随创建保存: {new.detail!r}")

# 取消 -> 询问保留 -> 选"是": 创建任务, 详情为空
n1 = len(a.store.series)
open_create_entry()
answers["yes"] = True


def cancel_dialog():
    dlg = [w for w in root.winfo_children() if isinstance(w, appmod.DetailEditor)][-1]
    dlg.text.insert("1.0", "这段详情应被丢弃")
    dlg._cancel()


root.after(300, cancel_dialog)
a.on_create()
root.update()
check(len(a.store.series) == n1 + 1, "取消后选「是」仍然创建任务")
check(a.store.series[-1].detail == "", "选「是」时详情为「无」(空)")

# 取消 -> 询问保留 -> 选"否": 不创建
n2 = len(a.store.series)
open_create_entry()
answers["yes"] = False
root.after(300, cancel_dialog)
a.on_create()
root.update()
check(len(a.store.series) == n2, "取消后选「否」不创建任务")

# Esc 等同取消
n3 = len(a.store.series)
open_create_entry()
answers["yes"] = False


def esc_dialog():
    dlg = [w for w in root.winfo_children() if isinstance(w, appmod.DetailEditor)][-1]
    dlg.text.event_generate("<Escape>")


root.after(300, esc_dialog)
a.on_create()
root.update()
check(len(a.store.series) == n3, "详情窗按 Esc 触发取消询问 (选否不创建)")
answers["yes"] = True

# 详情窗里的 Ctrl+S 不结束编辑
open_create_entry()
state = {}


def ctrl_s_check():
    dlg = [w for w in root.winfo_children() if isinstance(w, appmod.DetailEditor)][-1]
    dlg.text.insert("1.0", "暂存内容")
    dlg.text.event_generate("<Control-s>")
    root.update()
    state["alive"] = bool(dlg.winfo_exists())
    state["hint"] = dlg.hint.cget("text")
    dlg._submit()


root.after(300, ctrl_s_check)
a.on_create()
root.update()
check(state.get("alive") and "暂存" in state.get("hint", ""),
      "创建详情窗 Ctrl+S 暂存且不关闭")

# ---------------- D. 改名同步 ----------------
print("D. 改名")
a.open_detail(short_s)
root.update()
top4 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
ts4 = walk(top4, appmod.ScrollingTitle)[0]


def rename_flow():
    # 找到「修改名称」弹窗里的输入框
    dlg = [w for w in top4.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    entry = walk(dlg, tk.Entry)[0]
    entry.delete(0, "end")
    entry.insert(0, LONG)
    entry.event_generate("<Return>")


root.after(200, rename_flow)
find_button(top4, "修改名称").invoke()
root.update()
check(short_s.name == LONG, "改名写回数据")
check(ts4.canvas.itemcget(ts4._item, "text") == LONG, "改名后上方完整标题同步更新")
check(ts4._shown, "改名变长后自动出现横向滚动条")
top4.destroy()

# ---------------- E. 输入法 / 兜底 ----------------
print("E. 其它")
check(appmod.ime_composing(root) is False, "无组合输入时 ime_composing 返回 False")
check(appmod.elide_text("abcdef", a.f_title, 1) == "", "宽度不足时省略号也放不下 -> 空串")
check(appmod.elide_text("短", a.f_title, 200) == "短", "装得下就不截断")

# ---------------- F. 详情窗布局不被裁掉 ----------------
print("F. 布局")


def bottom_ok(top, child):
    return (child.winfo_ismapped()
            and child.winfo_rooty() + child.winfo_height()
            <= top.winfo_rooty() + top.winfo_height() + 1)


def open_and_check(series, label, detail_lines=1):
    a.open_detail(series)
    root.update()
    t = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    t.update_idletasks()
    body = walk(t, tk.Text)[0].master.master
    btns = find_button(t, "关闭").master
    rows = walk(body, tk.Frame)
    ok_btns = bottom_ok(t, btns)
    ok_body = body.winfo_reqheight() <= body.winfo_height() + 1
    check(ok_btns and ok_body,
          f"{label}: 内容不被裁掉 (body 需要{body.winfo_reqheight()}"
          f"/实得{body.winfo_height()}, 窗口高{t.winfo_height()}, 按钮可见={ok_btns})")
    t.destroy()


plan_s = a.store.add_plan("学习计划任务", a.today)
simple_s = a.store.add_simple_plan("不复习计划任务", a.today)
open_and_check(long_s, "复习日程 (4 次复习行)")
open_and_check(plan_s, "学习计划(需复习) 未完成学习")
open_and_check(simple_s, "学习计划(不复习)")

# 创建详情窗的按钮也要在窗口内
open_create_entry()
state2 = {}


def layout_create():
    dlg = [w for w in root.winfo_children() if isinstance(w, appmod.DetailEditor)][-1]
    dlg.update_idletasks()
    state2["ok"] = bottom_ok(dlg, find_button(dlg, "确认").master)
    state2["w"] = dlg.winfo_width()
    state2["h"] = dlg.winfo_height()
    dlg._submit()


root.after(300, layout_create)
a.on_create()
root.update()
check(state2.get("ok"), "创建详情窗: 确认/取消按钮可见 "
                        f"({state2.get('w')}x{state2.get('h')})")

# ---------------- G. 复制按钮 + "已复制" 提示 ----------------
print("G. 复制与提示")
a.show_page("home")
root.update()

copy_btns = walk(a.home_list, appmod.CopyIconButton)
check(bool(copy_btns), f"学习表卡片上有复制按钮 ({len(copy_btns)} 个)")


def card_title_of(btn):
    """复制按钮所在卡片的标题 (拿来和剪贴板比对)。"""
    for lb in walk(btn.master, appmod.ElidedLabel):
        return lb._full_text
    return None


def copied_series():
    """剪贴板内容对应的任务 (按"完整信息文本"反查), 没有则 None。"""
    text = root.clipboard_get()
    for s in a.store.series:
        if s.full_info_text() == text:
            return s
    return None


def press(btn):
    btn.event_generate("<Button-1>", x=5, y=5)
    root.update()


def copy_btn_for(name):
    """按卡片标题找复制按钮。"""
    for b in walk(a.home_list, appmod.CopyIconButton):
        if card_title_of(b) == name:
            return b
    return None


tops_before = [x for x in root.winfo_children() if isinstance(x, tk.Toplevel)]
first_title = card_title_of(copy_btns[0])
press(copy_btns[0])
clip_lines = root.clipboard_get().splitlines()
check(copied_series() is not None,
      "剪贴板里是某条任务的完整信息 (与 full_info_text 一致)")
check(copied_series() is not None and copied_series().name == first_title,
      f"复制的正是这张卡片的任务: {first_title!r}")
check(clip_lines[0] == f"标题：{first_title}", "第一行是「标题：…」")
check(any(x.startswith("详情：") for x in clip_lines), "带「详情：」一行")
check(any(x.startswith("类型：") for x in clip_lines), "带「类型：」一行")
check(any(x.startswith("复习日期：") for x in clip_lines), "带「复习日期：」一行")
check(len(clip_lines) >= 5, f"纯文本、每条信息一行 ({len(clip_lines)} 行)")

# 详情为空的任务 -> 复制内容里详情一行必须写「无」
empty_name = next((s.name for s in a.store.series if not s.detail), None)
empty_btn = copy_btn_for(empty_name) if empty_name else None
check(empty_btn is not None, f"找到一条空详情任务的卡片: {empty_name!r}")
if empty_btn is not None:
    press(empty_btn)
    check("详情：无" in root.clipboard_get().splitlines(),
          "详情为空时复制内容里写「无」")

# 三种类型都走一遍卡片复制按钮
probes = [a.store.add("探针-复习日程", a.today, ""),
          a.store.add_plan("探针-学习计划", a.today, ""),
          a.store.add_simple_plan("探针-不复习计划", a.today, "")]
a.refresh_lists()
root.update()
for s in probes:
    btn = copy_btn_for(s.name)
    check(btn is not None, f"{s.name}: 卡片上有复制按钮")
    if btn is None:
        continue
    press(btn)
    check(root.clipboard_get() == s.full_info_text(),
          f"{s.name}: 剪贴板 = 该任务完整信息")
check("类型：复习日程" in probes[0].full_info_text(), "复习日程的类型文字正确")
check("类型：学习计划（需复习）" in probes[1].full_info_text(), "需复习计划的类型文字正确")
check("应完成日期：" in probes[1].full_info_text()
      and "类型：学习计划（不复习）" in probes[2].full_info_text(),
      "不复习计划也是应完成日期 + 不复习类型")
for s in probes:
    a.store.remove(s.id)
a.refresh_lists()
root.update()
copy_btns = walk(a.home_list, appmod.CopyIconButton)
press(copy_btns[0])

toast = a._toast
cv = toast.canvas
root.update_idletasks()
check(isinstance(toast, appmod.CopiedToast) and cv.winfo_exists(),
      "复制成功后弹出「已复制」提示框")
check(cv.master is root and len([x for x in root.winfo_children()
                                 if isinstance(x, tk.Toplevel)]) == len(tops_before),
      "提示框画在主窗口内, 没有额外开窗")
check(cv.winfo_ismapped(), "提示框可见")
check(cv.itemcget(toast.label, "text") == "已复制", "方框里只放「已复制」三个字")
check(cv.itemcget(toast.label, "fill") == appmod.TOAST_FG == "#000000", "文字是黑色")

# 位置: 窗口最上层、最上方、水平居中
cx = cv.winfo_rootx() + cv.winfo_width() / 2.0
wx = root.winfo_rootx() + root.winfo_width() / 2.0
check(abs(cx - wx) <= 1.5, f"提示框水平居中 (提示 {cx:.1f} vs 窗口 {wx:.1f})")
check(cv.winfo_rooty() - root.winfo_rooty() == a.px(appmod.TOAST_TOP),
      "提示框贴着窗口最上方")
check(cv.winfo_rooty() + cv.winfo_height()
      <= root.winfo_rooty() + a.header.winfo_height(),
      "提示框落在窗口最上方的标题栏区域")
stack = [str(n) for n in root.tk.call("winfo", "children", root._w)]
check(bool(stack) and stack[-1] == str(cv), "提示框在控件堆叠顺序的最上层")

# 形状与颜色
check(cv.type(toast.box) == "polygon" and len(cv.coords(toast.box)) >= 20,
      "方框是圆角 (多边形逼近圆角, 不是直角矩形)")
peak = cv.itemcget(toast.box, "fill")
check(peak == appmod.mix_color(toast.backdrop, appmod.TOAST_BG,
                               appmod.TOAST_PEAK_ALPHA)
      and peak != appmod.TOAST_BG,
      f"白底是半透明的 (与背后的 {toast.backdrop} 混成 {peak}, 不是纯白)")

# 淡出曲线: 0~1 秒全亮, 1~3 秒平滑变淡到 0
check(appmod.toast_alpha(0.0) == 1.0 and appmod.toast_alpha(0.5) == 1.0
      and appmod.toast_alpha(1.0) == 1.0, "第 0~1 秒完全不透明")
check(0.0 < appmod.toast_alpha(2.0) < 1.0, "第 1 秒之后开始变淡")
check(appmod.toast_alpha(1.5) > appmod.toast_alpha(2.0)
      > appmod.toast_alpha(2.5) > 0, "越往后越淡 (平滑单调)")
check(appmod.toast_alpha(3.0) == 0.0 and appmod.toast_alpha(5.0) == 0.0,
      "到第 3 秒正好完全透明")

# 连续复制: 旧框被替换 (不叠加), 并且重新计时
press(copy_btns[1])
toast2 = a._toast
root.update_idletasks()
check(toast2 is not toast and not cv.winfo_exists(), "连续复制时旧的提示框被替换")
check(len([x for x in root.winfo_children() if isinstance(x, tk.Canvas)]) == 1,
      "同一时刻只有一个提示框 (不会叠成一堆)")
check(copied_series() is not None
      and copied_series().name == card_title_of(copy_btns[1]),
      "第二次复制照样把完整信息写进剪贴板")
check(root.grab_current() is None, "提示框不抓鼠标/不抢焦点, 界面照常能操作")

# 真按秒跑一遍: 0.5 秒全亮 -> 2.4 秒已明显变淡 -> 3 秒后自动消失
cv2 = toast2.canvas
t0 = time.monotonic()
seen = {}
while time.monotonic() - t0 < 3.6:
    root.update()
    elapsed = time.monotonic() - t0
    if "early" not in seen and elapsed >= 0.5:
        seen["early"] = cv2.itemcget(toast2.box, "fill")
    if "late" not in seen and elapsed >= 2.4:
        seen["late"] = cv2.itemcget(toast2.box, "fill")
    time.sleep(0.01)
check(seen.get("early") == peak, "0.5 秒时仍是完全不透明")
check(seen.get("late") not in (None, peak, toast2.backdrop),
      f"2.4 秒时已明显变淡 ({seen.get('late')})")
check(not cv2.winfo_exists() and a._toast is None, "3 秒后自动消失, 没有残留")
check(copied_series() is not None
      and copied_series().name == card_title_of(copy_btns[1]),
      "提示消失后剪贴板内容还在 (复制功能没被影响)")

# 悬停: 图标换成主题色
btn = copy_btns[0]
before = btn.itemcget(btn.find_all()[-1], "outline")
btn.event_generate("<Enter>")
root.update()
hover = btn.itemcget(btn.find_all()[-1], "outline")
check(before != hover and hover == appmod.ACCENT, f"鼠标悬停时图标变主题色 ({hover})")

# ---------------- H. 「修改名称」窗口宽度 ----------------
print("H. 改名窗口宽度")
a.open_detail(long_s)
root.update()
top5 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
state3 = {}


def rename_geometry():
    dlg = [w for w in top5.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    entry = walk(dlg, tk.Entry)[0]
    dlg.update_idletasks()
    state3["dlg_w"] = dlg.winfo_width()
    state3["entry_w"] = entry.winfo_width()
    state3["name"] = entry.get()
    state3["xview"] = entry.xview()[0]
    dlg.destroy()


root.after(200, rename_geometry)
find_button(top5, "修改名称").invoke()
root.update()
check(state3.get("name") == LONG, "改名窗里能看到当前任务名称")
check(state3.get("dlg_w", 0) >= a.px(appmod.RENAME_WIDTH_MIN),
      f"改名窗够宽 (实得 {state3.get('dlg_w')}px, 下限 {a.px(appmod.RENAME_WIDTH_MIN)}px)")
need = min(a.f_body.measure(LONG),
           a.px(appmod.RENAME_WIDTH_MAX) - a.px(appmod.RENAME_WIDTH_PAD))
check(state3.get("entry_w", 0) >= need,
      f"输入框能放下当前名称 ({state3.get('entry_w')}px >= {need}px 文字宽度)")
check(state3.get("xview") == 0.0, "输入框从名称开头显示 (不是停在末尾)")
top5.destroy()
root.update()

# 短名称的任务: 窗口也不小于下限 (常见长度都看得全)
a.open_detail(plan_s)
root.update()
top6 = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][-1]
state4 = {}


def rename_short():
    dlg = [w for w in top6.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    dlg.update_idletasks()
    state4["dlg_w"] = dlg.winfo_width()
    state4["h"] = dlg.winfo_height()
    dlg.destroy()


root.after(200, rename_short)
find_button(top6, "修改名称").invoke()
root.update()
check(state4.get("dlg_w", 0) >= a.px(appmod.RENAME_WIDTH_MIN),
      f"短名称时窗口同样不小于下限 ({state4.get('dlg_w')}px)")
check(0 < state4.get("h", 0) < a.px(200), f"窗口高度仍是内容高度 ({state4.get('h')}px)")
top6.destroy()

# ---------------- I. 搜索: 按名称 / 按日期 ----------------
print("I. 搜索")
today = a.today
d_both = today.replace(day=1)          # 一定落在当前展示的月份里
s_both = a.store.add("搜索测试-创建并完成", d_both)
s_both.created_at = d_both.isoformat() + "T09:00:00"
s_both.complete_review(1, d_both)
s_created = a.store.add("搜索测试-仅创建", d_both)
s_created.created_at = d_both.isoformat() + "T10:30:00"
s_done = a.store.add_plan("搜索测试-仅完成", today)
s_done.created_at = (today - timedelta(days=6)).isoformat() + "T08:00:00"
s_done.complete_learning(d_both)
a.store.save()
a.refresh_lists()
root.update()

a.open_search()
root.update()
check(a._search_win is not None and a._search_win.winfo_exists(), "搜索窗口打开")
check(a._search_mode.get() == "name", "默认按名称搜索")
check(a._search_bar.winfo_ismapped() and not a._search_cal_frame.winfo_ismapped(),
      "名称模式显示输入栏、不显示日历")

# 名称搜索照旧可用
a._search_entry.delete(0, "end")
a._search_entry.insert(0, "搜索测试-仅")
a._on_search()
deadline = time.monotonic() + 1.2
while time.monotonic() < deadline:
    root.update()
    time.sleep(0.01)
names = [lb._full_text for lb in walk(a._search_list, appmod.ElidedLabel)]
check("搜索测试-仅创建" in names and "搜索测试-仅完成" in names
      and "搜索测试-创建并完成" not in names,
      f"名称搜索仍能按子串命中, 实际 {names}")

# 切到日期模式: 内嵌月历
a._search_mode.set("date")
a._on_search_mode()
root.update()
check(a._search_cal_frame.winfo_ismapped() and not a._search_results.winfo_ismapped(),
      "日期模式在当前窗口内嵌月历")
check(not a._search_bar.winfo_ismapped(), "日期模式隐藏名称输入栏")

def cal_cells():
    return [c for c in walk(a._search_cal_frame, tk.Canvas)
            if getattr(c, "_cal_day", None) is not None]
def cal_cell(day):
    return next((c for c in cells if c._cal_day == day), None)
def num_text(c):
    items = c.find_withtag("num")
    return c.itemcget(items[0], "text") if items else None
def num_fill(c):
    items = c.find_withtag("num")
    return c.itemcget(items[0], "fill") if items else None

cells = cal_cells()
c_both = cal_cell(d_both)
check(c_both is not None and num_text(c_both) == str(d_both.day)
      and num_fill(c_both) == appmod.TEXT,
      f"日期数字一律黑色 ({c_both and num_fill(c_both)})")
check(c_both is not None and c_both.cget("bg") == appmod.CAL_CHANGED_BG,
      f"有改动的日期是淡绿色方块 ({c_both and c_both.cget('bg')})")
c_today = cal_cell(today)
check(c_today is not None and num_fill(c_today) == appmod.TEXT,
      "今天的数字也是黑色")
check(c_today is not None
      and c_today.cget("highlightbackground") == appmod.CAL_BORDER_TODAY,
      f"今天的日期框是黑色 ({c_today and c_today.cget('highlightbackground')})")
check(c_today is not None
      and int(c_today.cget("highlightthickness")) == a.px(appmod.CAL_BORDER_W),
      "今天用加粗边框")

marked = a.store.changed_dates()
last = _calendar.monthrange(today.year, today.month)[1]
empty_day = next((date(today.year, today.month, dd)
                  for dd in range(1, last + 1)
                  if date(today.year, today.month, dd) not in marked), None)
check(empty_day is not None, "本月找得到没有改动的日期")
if empty_day is not None:
    c_empty = cal_cell(empty_day)
    check(c_empty is not None and num_fill(c_empty) == appmod.TEXT
          and c_empty.cget("bg") == appmod.CARD_BG,
          f"没有改动的日期是白底黑字方块 ({c_empty and c_empty.cget('bg')})")

# 加粗边框后月历仍要完整放得下 (不被窗口裁掉)
a._search_cal_frame.update_idletasks()
check(a._search_cal_frame.winfo_reqheight() <= a._search_body.winfo_height() + 1,
      f"月历完整放得下 (需要 {a._search_cal_frame.winfo_reqheight()}"
      f" <= 实得 {a._search_body.winfo_height()})")

# 当前查看的日期: 变量记着它, 每次画表套用 -> 深绿椭圆铺满格子
a._search_selected_date = d_both
a._build_search_calendar()
root.update()
a._search_cal_frame.update_idletasks()
cells = cal_cells()
c_sel = cal_cell(d_both)
marks = c_sel.find_withtag("mark") if c_sel is not None else ()
check(bool(marks) and c_sel.type(marks[0]) == "oval",
      "当前查看的日期画了一个椭圆标记")
if marks:
    check(c_sel.itemcget(marks[0], "outline") == appmod.CAL_BORDER_SEL,
          f"椭圆是深绿色 ({c_sel.itemcget(marks[0], 'outline')})")
    x0, y0, x1, y1 = c_sel.coords(marks[0])
    w, h = c_sel.winfo_width(), c_sel.winfo_height()
    check(x0 < w / 2 < x1 and y0 < h / 2 < y1
          and (x1 - x0) >= w - a.px(appmod.CAL_BORDER_W) - 1
          and (y1 - y0) >= h - a.px(appmod.CAL_BORDER_W) - 1,
          f"椭圆按格子长宽铺满 ({x1 - x0:.0f}x{y1 - y0:.0f} / 格子 {w}x{h})")
    check(c_sel.cget("highlightbackground") != appmod.CAL_BORDER_SEL,
          "查看标记用椭圆而不是深绿方框, 不和今天的黑框冲突")
a._search_selected_date = None
a._build_search_calendar()
root.update()

# 翻月
y0, m0 = a._search_cal_year, a._search_cal_month
a._shift_search_month(1)
root.update()
check((a._search_cal_year, a._search_cal_month) != (y0, m0), "月历可以翻到下个月")
a._shift_search_month(-1)
root.update()
check((a._search_cal_year, a._search_cal_month) == (y0, m0), "又能翻回本月")

# 点日期格子 -> 「该日创建」「该日完成」两段
a._on_pick_search_date(d_both)
root.update()
check(a._search_results.winfo_ismapped() and not a._search_cal_frame.winfo_ismapped(),
      "点日期格子后切到结果, 日历收起")
link_labels = [lb.cget("text") for lb in walk(a._search_link_bar, tk.Label)]
check(link_labels[:2] == ["该日创建", "该日完成"],
      f"选择框下面一行是两个跳转链接, 实际 {link_labels}")
check(a._search_link_bar.winfo_ismapped()
      and a._search_link_bar.winfo_rooty() < a._search_area.winfo_rooty(),
      "跳转链接条正好在结果列表上方")
check(a._search_list.winfo_children()[0] is a._search_create_heading,
      "「该日创建」大标题直接排在跳转按钮下面")
check(a._search_create_heading.winfo_rooty() < a._search_done_heading.winfo_rooty(),
      "「该日创建」段排在「该日完成」段之前")

dy = a._search_done_heading.winfo_rooty()
created_cards = [c._full_text for c in walk(a._search_list, appmod.ElidedLabel)
                 if c.winfo_rooty() < dy]
done_cards = [c._full_text for c in walk(a._search_list, appmod.ElidedLabel)
              if c.winfo_rooty() >= dy]
check("搜索测试-创建并完成" in created_cards and "搜索测试-仅创建" in created_cards,
      f"该日创建段列出当天创建的任务, 实际 {created_cards}")
check("搜索测试-仅完成" not in created_cards, "该日创建段不含其它天创建的任务")
check("搜索测试-创建并完成" in done_cards and "搜索测试-仅完成" in done_cards,
      f"该日完成段列出当天完成的任务, 实际 {done_cards}")
check("搜索测试-仅创建" not in done_cards, "该日完成段不含没有完成的任务")

# 没有改动的日期: 仍然两段, 只是说明文字
if empty_day is not None:
    a._on_pick_search_date(empty_day)
    root.update()
    empty_texts = [lb.cget("text") for lb in walk(a._search_list, tk.Label)]
    check("该日没有创建的任务" in empty_texts and "该日没有完成的任务" in empty_texts,
          "没有改动的日期照样分两段, 只写说明文字")
    check(not walk(a._search_list, appmod.ElidedLabel), "空日期没有任何任务卡片")

# 「更改日期」回到月历
back = next((lb for lb in walk(a._search_link_bar, tk.Label)
             if lb.cget("text") == "更改日期"), None)
check(back is not None, "跳转条右侧有「更改日期」回到月历")
if back is not None:
    back.event_generate("<Button-1>", x=2, y=2)
    root.update()
    check(a._search_cal_frame.winfo_ismapped(), "点「更改日期」回到月历")
    a._search_cal_frame.update_idletasks()
    cells = cal_cells()
    c_back = cal_cell(empty_day)
    check(c_back is not None and bool(c_back.find_withtag("mark")),
          "回到月历后, 刚查看的那天照样带深绿椭圆")

# 结果够长时: 点「该日完成」链接真的跳到该段落
for i in range(24):
    fs = a.store.add("搜索填充-%02d" % i, d_both)
    fs.created_at = d_both.isoformat() + "T11:%02d:00" % i
a.store.save()
a._on_pick_search_date(d_both)
root.update()
a._jump_to(a._search_create_heading)
root.update()
top = a._search_canvas.yview()[0]
done_link = next(lb for lb in walk(a._search_link_bar, tk.Label)
                 if lb.cget("text") == "该日完成")
done_link.event_generate("<Button-1>", x=2, y=2)
root.update()
check(a._search_canvas.yview()[0] > top,
      f"点「该日完成」跳到该段落 ({top:.3f} -> {a._search_canvas.yview()[0]:.3f})")

a._close_search()
root.update()
check(a._search_win is None and a._search_canvas is None, "关掉搜索窗口后状态清空")

# ---------------- J. 列表分段折叠 ----------------
print("J. 分段折叠")


def find_label(w, substr):
    """按文字片段找纯 tk.Label (排除 ElidedLabel 这类子类)。"""
    for lb in walk(w, tk.Label):
        if type(lb) is tk.Label and substr in lb.cget("text"):
            return lb
    return None


# 学习表: 今日待完成直接展示, 后续任务(日期>今天)默认折叠
fut = a.store.add("折叠-后续任务", today + timedelta(days=7))
a._home_future_open = False          # 前面的用例展开过, 这里从默认折叠态开始
a.show_page("home")
root.update()


def home_names():
    return [lb._full_text for lb in walk(a.home_list, appmod.ElidedLabel)]


check(fut.name not in home_names(), "后续任务默认折叠: 不渲染任何卡片")
_fut_lbl = find_label(a.home_list, "后续任务")
check(_fut_lbl is not None, "后续任务有一行可点击的折叠标题")
check(_fut_lbl is not None and _fut_lbl.master.winfo_ismapped()
      and _fut_lbl.master.winfo_height() >= a.px(20),
      f"折叠标题行可见且有高度 ({_fut_lbl and _fut_lbl.master.winfo_height()}px)")
check(find_label(a.home_list, "今日待完成") is not None, "学习表有「今日待完成」分段标题")
n_collapsed = len(walk(a.home_list, appmod.ElidedLabel))
fut_hdr = find_label(a.home_list, "后续任务")
fut_hdr.event_generate("<Button-1>", x=2, y=2)
root.update()
check(fut.name in home_names(), "点「后续任务」标题后展开渲染卡片")
check(len(walk(a.home_list, appmod.ElidedLabel)) > n_collapsed,
      f"展开后卡片变多 ({n_collapsed} -> {len(walk(a.home_list, appmod.ElidedLabel))})")
fut_hdr = find_label(a.home_list, "后续任务")
fut_hdr.event_generate("<Button-1>", x=2, y=2)
root.update()
check(fut.name not in home_names(), "再点一次重新折叠")

# 完成 list: 3 天内完成的直接展示, 更早完成的默认折叠
recent_s = a.store.add("折叠-最近完成", today)
recent_s.complete_review(1, today)
old_s = a.store.add("折叠-更早完成", today - timedelta(days=40))
old_s.complete_review(1, today - timedelta(days=10))
a.store.save()
a.show_page("completed")
root.update()


def done_names():
    return [lb._full_text for lb in walk(a.completed_list, appmod.ElidedLabel)]


check("折叠-最近完成" in done_names(), "3 天内完成的卡片直接展示")
check("折叠-更早完成" not in done_names(), "更早完成的卡片默认折叠: 不渲染")
check(find_label(a.completed_list, f"最近 {appmod.COMPLETED_RECENT_DAYS} 天完成")
      is not None, "完成 list 有「最近 N 天完成」分段标题")
old_hdr = find_label(a.completed_list, "更早完成")
check(old_hdr is not None, "更早完成有一行可点击的折叠标题")
check(old_hdr is not None and old_hdr.master.winfo_ismapped()
      and old_hdr.master.winfo_height() >= a.px(20),
      f"更早完成折叠行可见且有高度 ({old_hdr and old_hdr.master.winfo_height()}px)")
if old_hdr is not None:
    old_hdr.event_generate("<Button-1>", x=2, y=2)
    root.update()
    check("折叠-更早完成" in done_names(), "点「更早完成」标题后展开渲染卡片")

print()
root.destroy()
if FAILS:
    print(f"{len(FAILS)} 项未通过:")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print("ALL UI CHECKS PASSED")
