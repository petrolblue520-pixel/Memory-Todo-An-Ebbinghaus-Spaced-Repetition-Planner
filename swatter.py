"""跟随光标的苍蝇拍 (自己画的, 完全不碰系统鼠标指针)。

为什么不用 SetSystemCursor 换系统指针:
  - 那是**全局**改动, 程序被强杀后指针会一直留在苍蝇拍上, 恢复不了;
  - 这里改成"一个置顶 + 完全穿透的小窗口跟着鼠标跑", 关掉就没了, 零风险。

关于系统箭头: Windows 的鼠标指针是系统画在所有窗口**之上**的, 我们的窗口盖不住它。
所以拍面中心就放在光标位置上 —— 箭头正好落在拍面里当准星, 拍柄往右下伸, 看起来像
"捏着拍子"。这是不需要动系统指针时最自然的画法。

拍子窗口是 WS_EX_TRANSPARENT 的: 鼠标事件全部漏下去, 点击会落到下面的苍蝇窗口上,
由苍蝇群处理"拍中了没有"。
"""

from __future__ import annotations

import time
from typing import Optional

import flywin

SWING_TIME = 0.18                 # 挥拍动画时长
SWING_FRAMES = 3                  # 挥拍帧数 (0 = 抬起)


class Swatter:
    """跟手的苍蝇拍。enable/disable 由设置窗或托盘菜单控制。"""

    def __init__(self, art, prefs):
        self.art = art
        self.prefs = prefs
        self.window: Optional[flywin.LayeredWindow] = None
        self.enabled = False
        self.visible = False
        self._swing_t = -1.0
        self._frame = -1
        self._hwnd_ok = True

    # ---------------- 开关 ----------------
    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        if self.enabled:
            self._ensure_window()
        else:
            self._hide()

    def _ensure_window(self) -> None:
        if self.window is not None or not self._hwnd_ok:
            return
        try:
            size = self.art.swatter_size
            self.window = flywin.LayeredWindow(
                size, size, clickable=False, topmost=True,
                exclude_capture=self.prefs.get("hide_from_capture"))
            self._frame = -1
            self._apply_frame(0)
            self.window.hide()
            self.visible = False
        except Exception:
            self.window = None
            self._hwnd_ok = False

    def _hide(self) -> None:
        if self.window is not None and self.visible:
            self.window.hide()
        self.visible = False

    def destroy(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
        self.visible = False

    # ---------------- 挥拍 ----------------
    def swing(self) -> None:
        if self.enabled:
            self._swing_t = 0.0

    def _apply_frame(self, frame: int) -> None:
        if self.window is None or frame == self._frame:
            return
        self._frame = frame
        try:
            self.window.set_frame(self.art.swatter(frame))
        except Exception:
            self.window = None

    # ---------------- 每帧 ----------------
    def tick(self, mx, my, now: float, dt: float) -> None:
        if not self.enabled:
            return
        if mx is None:
            self._hide()
            return
        if self.prefs.get("swatter_desktop_only") and not self._cursor_on_desktop(mx, my):
            self._hide()
            return
        if self.window is None:
            self._ensure_window()
        if self.window is None:
            return

        # 挥拍动画
        if self._swing_t >= 0.0:
            self._swing_t += dt
            if self._swing_t >= SWING_TIME:
                self._swing_t = -1.0
        if self._swing_t >= 0.0 and self.prefs.get("swatter_swing"):
            frame = int(self._swing_t / SWING_TIME * SWING_FRAMES)
            frame = max(0, min(SWING_FRAMES - 1, frame))
        else:
            frame = SWING_FRAMES - 1 if not self.prefs.get("swatter_swing") else 0
        self._apply_frame(frame)

        size = self.window.w
        self.window.move(int(mx - size / 2.0), int(my - size / 2.0))
        if not self.visible:
            self.window.show()
            self.window.set_topmost(True)
            self.visible = True

    # ---------------- 只在桌面可见时才露面 ----------------
    @staticmethod
    def _cursor_on_desktop(mx: int, my: int) -> bool:
        """光标下面是不是"桌面"(而不是某个应用窗口)。

        用前台窗口矩形判断: 光标落在前台窗口里 -> 用户在工作, 拍子收起来;
        光标在桌面空白处 -> 才把拍子亮出来。
        """
        rect, cls = _foreground_rect_and_class()
        if rect is None:
            return True
        if cls in ("Progman", "WorkerW", "Shell_TrayWnd", "SysListView32"):
            return True
        left, top, right, bottom = rect
        return not (left <= mx < right and top <= my < bottom)


def _foreground_rect_and_class():
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetForegroundWindow.restype = ctypes.c_void_p
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None, ""
        r = ctypes.wintypes.RECT()
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
        if not u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
            return None, ""
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return (r.left, r.top, r.right, r.bottom), buf.value
    except Exception:
        return None, ""
