"""Tkinter 界面冒烟测试: 造几条数据, 构建界面, 截图, 不进入 mainloop。

用带 Pillow 的 Python 运行可自动截图; 否则仅验证无异常。
"""

import os
import tempfile
from datetime import date, timedelta

_d = tempfile.mkdtemp()
os.environ["EBBINGHAUS_DATA_DIR"] = _d

from models import Store  # noqa: E402

# 一条超长标题 (用于验证卡片单行 + "…" 省略 / 详情窗横向滚动)
LONG_NAME = ("数据结构与算法总复习：红黑树、B 树、线段树与并查集在真实工程场景下的"
             "复杂度分析与手写实现要点（含旋转、染色、路径压缩与均摊分析）")
LONG_DETAIL = ("知识点：\n"
               "1. 红黑树五条性质与插入删除后的旋转/染色修正\n"
               "2. B 树阶数与磁盘 IO 次数的关系\n"
               "3. 线段树的懒标记下传顺序\n"
               "4. 并查集的路径压缩 + 按秩合并的均摊复杂度\n\n"
               "特殊方法：先在纸上画小规模样例，再手写一遍，最后用对数器对拍。\n"
               "备注：第 4 次复习时把错题重做一遍。")

today = date.today()
st = Store(os.path.join(_d, "data.json"))
# 3 天前学的 -> 第1次复习已过期 (今日到期, 红框)
st.add("高数-拉格朗日中值定理", today - timedelta(days=3),
       "中值定理的三种形式与构造函数法。\n重点：辅助函数怎么想。")
# 今天学的 -> 复习都在未来
st.add("英语单词 Unit 5", today)
# 20 天前学的, 前两次已完成 -> 完成 list 有卡片, 复习表还有后两次
c = st.add(LONG_NAME, today - timedelta(days=20), LONG_DETAIL)
c.complete_review(1, today - timedelta(days=19))
c.complete_review(2, today - timedelta(days=16))
# 一条学习计划(不复习), 空详情 -> 详情窗里应显示"无"
st.add_simple_plan("整理错题本（不复习）", today)
st.save()

import tkinter as tk  # noqa: E402
import app as appmod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def grab(win, name):
    try:
        from PIL import ImageGrab
    except Exception as e:  # pragma: no cover
        print("no PIL, screenshot skipped:", e)
        return
    win.update_idletasks()
    win.update()
    win.lift()
    win.attributes("-topmost", True)
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    path = os.path.join(HERE, name)
    img.save(path)
    win.attributes("-topmost", False)
    print("SCREENSHOT:", path)


def find_button(widget, text):
    """在控件树里找一个按钮 (按显示文字)。"""
    for c in widget.winfo_children():
        if isinstance(c, tk.Button) and c.cget("text") == text:
            return c
        found = find_button(c, text)
        if found is not None:
            return found
    return None


def top_levels():
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]


root = tk.Tk()
appmod._enable_dpi_awareness()
dpi = root.winfo_fpixels("1i")
root.tk.call("tk", "scaling", dpi / 72.0)
a = appmod.App(root, scale=dpi / 96.0)
print("DPI:", dpi, "scale:", round(dpi / 96.0, 3))
root.update_idletasks()
root.update()

# 复习表
a.show_page("home")
grab(root, "smoke_home.png")

# 完成 list
a.show_page("completed")
grab(root, "smoke_completed.png")

# 今日所学
a.show_page("create")
grab(root, "smoke_create.png")

# 详情弹窗 (红黑树: 标题超长 + 有多段详情, 也有已完成/未完成的复习)
a.show_page("home")
root.update()
a.open_detail(c)
root.update()
for w in top_levels():
    grab(w, "smoke_detail.png")
    edit_btn = find_button(w, "编辑详情")
    if edit_btn is not None:
        edit_btn.invoke()          # 进入编辑状态再截一张
        grab(w, "smoke_detail_edit.png")
    w.destroy()
    break

# 空详情的弹窗 (应显示"无")
a.open_detail(st.series[3])
root.update()
for w in top_levels():
    grab(w, "smoke_detail_empty.png")
    w.destroy()
    break

# 创建流程: 第一次 Enter 弹出的「详情编辑窗口」
a.show_page("create")
a.name_entry.insert(0, "新任务：线性代数特征值")


def capture_create_dialog():
    for w in top_levels():
        grab(w, "smoke_create_detail.png")
        w._submit()                # 等同按 Enter / 点「确认」
        break


root.after(400, capture_create_dialog)
a.on_create()
a.show_page("home")
grab(root, "smoke_home_after_create.png")
print("TASK COUNT:", len(a.store.series))

print("SMOKE_OK")
root.destroy()
