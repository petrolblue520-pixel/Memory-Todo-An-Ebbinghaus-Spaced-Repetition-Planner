"""Tkinter 界面冒烟测试: 造几条数据, 构建界面, 截图, 不进入 mainloop。

用带 Pillow 的 .venv Python 运行可自动截图; 否则仅验证无异常。
"""

import os
import tempfile
from datetime import date, timedelta

_d = tempfile.mkdtemp()
os.environ["EBBINGHAUS_DATA_DIR"] = _d

from models import Store  # noqa: E402

today = date.today()
st = Store(os.path.join(_d, "data.json"))
# 3 天前学的 -> 第1次复习已过期 (今日到期, 红框)
st.add("高数-拉格朗日中值定理", today - timedelta(days=3))
# 今天学的 -> 复习都在未来
st.add("英语单词 Unit 5", today)
# 20 天前学的, 前两次已完成 -> 完成 list 有卡片, 复习表还有后两次
c = st.add("数据结构-红黑树", today - timedelta(days=20))
c.complete_review(1, today - timedelta(days=19))
c.complete_review(2, today - timedelta(days=16))
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

# 详情弹窗 (取第一条系列)
a.show_page("home")
root.update()
a.open_detail(st.series[2])  # 红黑树: 有已完成也有未完成
for w in root.winfo_children():
    if isinstance(w, tk.Toplevel):
        grab(w, "smoke_detail.png")
        break

print("SMOKE_OK")
root.destroy()
