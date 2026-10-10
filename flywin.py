"""逐像素透明的 Windows 分层窗口 (纯 ctypes, 零第三方依赖)。

一只苍蝇 / 一个卵 / 一把苍蝇拍 = 一个这样的窗口。要点:

- **逐像素透明**: 内容用 UpdateLayeredWindow 整帧提交 (预乘 alpha 的 BGRA DIB),
  所以翅膀半透明、边缘抗锯齿都是真的, 不像色键透明那样只能全透或全不透。
- **不抢焦点、不进 Alt+Tab**: WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE + WM_MOUSEACTIVATE 返回
  MA_NOACTIVATE, 点它也不会把主界面顶到前面。
- **纯装饰 / 可点击 一键切换**: WS_EX_TRANSPARENT 开 = 完全穿透 (鼠标事件全部漏给下层),
  关 = 接收点击 (苍蝇拍模式才需要)。
- **层级可切**: 默认钉在 z 序最底 (HWND_BOTTOM) —— 压在桌面图标上、被所有窗口盖住;
  也可以切到最上层 (HWND_TOPMOST)。
- **截屏隐身**: SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), 旧系统不支持就静默跳过。
- **淡出**: BLENDFUNCTION.SourceConstantAlpha 直接乘整窗, 不重建位图。
- 消息由 Tk 定时 tick 调 pump() 抽取 (和 tray.py 同一套"单线程 + 主循环驱动"的模式)。

坐标系: 全部是屏幕物理像素, 左上角为原点。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Dict, Optional

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# ---- 窗口样式 / 消息 ----
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
HWND_BOTTOM = 1
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
WM_MOUSEACTIVATE = 0x0021
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_ERASEBKGND = 0x0014
WM_PAINT = 0x000F
MA_NOACTIVATE = 3
HTCLIENT = 1
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11

_instances: Dict[int, "LayeredWindow"] = {}
_class_name = "EbbinghausLayerWnd"
_class_ready = False
_wndproc_ref = None
_hinst = None


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROCTYPE),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _setup_signatures() -> None:
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.UINT]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.GetForegroundWindow.restype = wintypes.HWND
    # 注意: 64 位下句柄必须声明 argtypes, 否则 ctypes 会按 32 位 int 转换句柄
    # (典型的 "OverflowError: int too long to convert", 而且时灵时不灵)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]


def _window_proc(hwnd, msg, wparam, lparam):
    inst = _instances.get(int(hwnd))
    if inst is not None:
        if msg == WM_LBUTTONDOWN:
            inst._fire()
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE          # 点它也不激活 (不抢焦点)
        if msg == WM_NCHITTEST and inst.clickable:
            return HTCLIENT
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_DESTROY:
            _instances.pop(int(hwnd), None)
            return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_class() -> None:
    """注册窗口类 (整个进程只注册一次)。"""
    global _class_ready, _wndproc_ref, _hinst
    if _class_ready:
        return
    _setup_signatures()
    _hinst = kernel32.GetModuleHandleW(None)
    _wndproc_ref = WNDPROCTYPE(_window_proc)
    wc = WNDCLASSW()
    wc.style = 0x0003                    # CS_HREDRAW | CS_VREDRAW
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = _hinst
    wc.lpszClassName = _class_name
    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        # 1400 = ERROR_CLASS_ALREADY_EXISTS (正常情况: 前面的窗口已经注册过)
        if err != 1400:
            raise ctypes.WinError(err)
    _class_ready = True


class LayeredWindow:
    """一个逐像素透明的分层窗口。所有坐标都是屏幕物理像素。"""

    def __init__(self, width: int, height: int, x: int = 0, y: int = 0,
                 topmost: bool = False, clickable: bool = False,
                 exclude_capture: bool = True,
                 on_click: Optional[Callable[[], None]] = None):
        _ensure_class()
        self.w = int(width)
        self.h = int(height)
        self.x = int(x)
        self.y = int(y)
        self.topmost = bool(topmost)
        self.clickable = bool(clickable)
        self._alpha = 255
        self._on_click = on_click
        self._alive = True

        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if not clickable:
            ex |= WS_EX_TRANSPARENT
        if self.topmost:
            ex |= WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(
            ex, _class_name, "", WS_POPUP, self.x, self.y, self.w, self.h,
            None, None, _hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd_int = int(self.hwnd)
        _instances[self.hwnd_int] = self
        self._make_dib()
        self._apply_zorder()
        if exclude_capture:
            # 截屏 / 录屏里隐身; 旧系统 (Win10 2004 以前) 会失败, 静默忽略
            try:
                user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
            except Exception:
                pass
        self.show()

    # ---------------- 内部 ----------------
    def _make_dib(self) -> None:
        """建一块 top-down 的 32bpp DIB (BGRA, 预乘 alpha), 之后每帧只 memmove。"""
        self._screen_dc = user32.GetDC(None)
        if not self._screen_dc:
            raise ctypes.WinError(ctypes.get_last_error() or 0)
        self._mem_dc = gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = BITMAPINFO()
        hdr = bmi.bmiHeader
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth = self.w
        hdr.biHeight = -self.h            # 负高度 = top-down, 与 Python 缓冲同序
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = BI_RGB
        self._bits = ctypes.c_void_p()
        self._bmp = gdi32.CreateDIBSection(
            self._mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
            ctypes.byref(self._bits), None, 0)
        if not self._bmp:
            raise ctypes.WinError(ctypes.get_last_error())
        self._old_bmp = gdi32.SelectObject(self._mem_dc, self._bmp)
        self._blend = BLENDFUNCTION(AC_SRC_OVER, 0, self._alpha, AC_SRC_ALPHA)

    def _apply_zorder(self) -> None:
        target = HWND_TOPMOST if self.topmost else HWND_BOTTOM
        user32.SetWindowPos(self.hwnd, target, self.x, self.y, self.w, self.h,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _push(self) -> None:
        """把内存位图 + 当前整窗 alpha 提交给系统。"""
        if not self._alive:
            return
        pt_dst = wintypes.POINT(self.x, self.y)
        size = wintypes.SIZE(self.w, self.h)
        pt_src = wintypes.POINT(0, 0)
        self._blend.SourceConstantAlpha = int(max(0, min(255, self._alpha)))
        user32.UpdateLayeredWindow(
            self.hwnd, self._screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
            self._mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(self._blend),
            ULW_ALPHA)

    def _fire(self) -> None:
        if self._on_click is not None:
            try:
                self._on_click()
            except Exception:
                pass

    # ---------------- 对外 ----------------
    def set_frame(self, data: bytes) -> None:
        """整帧替换 (data 必须是 w*h*4 字节的预乘 BGRA)。"""
        if not self._alive:
            return
        n = self.w * self.h * 4
        if len(data) != n:
            raise ValueError("帧大小不匹配: 期望 %d 字节, 实际 %d" % (n, len(data)))
        ctypes.memmove(self._bits, data, n)
        self._push()

    def set_alpha(self, alpha: float) -> None:
        """整窗不透明度 0..255 (淡入淡出用, 不重建位图)。"""
        self._alpha = int(max(0, min(255, alpha)))
        self._push()

    @property
    def alpha(self) -> int:
        return self._alpha

    def move(self, x: int, y: int) -> None:
        x, y = int(x), int(y)
        if x == self.x and y == self.y:
            return
        self.x, self.y = x, y
        user32.SetWindowPos(self.hwnd, None, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER)

    def set_clickable(self, on: bool) -> None:
        """True = 接收点击 (苍蝇拍模式); False = 完全穿透 (纯装饰)。"""
        on = bool(on)
        if on == self.clickable:
            return
        self.clickable = on
        ex = user32.GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)
        ex = (ex & ~WS_EX_TRANSPARENT) if on else (ex | WS_EX_TRANSPARENT)
        user32.SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, ex)
        user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                            | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def set_capture_excluded(self, on: bool) -> None:
        """运行时开关"截屏/录屏里隐身"。"""
        try:
            user32.SetWindowDisplayAffinity(
                self.hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE)
        except Exception:
            pass

    def set_topmost(self, on: bool) -> None:
        on = bool(on)
        if on == self.topmost:
            return
        self.topmost = on
        self._apply_zorder()

    def send_to_bottom(self) -> None:
        """重新钉到 z 序最底 (Explorer 刷新等偶发情况下的兜底)。"""
        if self.topmost:
            return
        user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def show(self) -> None:
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)

    def hide(self) -> None:
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def is_covered(self) -> bool:
        """窗口中心是否被前台窗口盖住 (苍蝇在底层时, 这决定要不要继续动画)。

        用 GetForegroundWindow + 矩形包含判断: 比 WindowFromPoint 靠谱,
        因为后者会跳过 WS_EX_TRANSPARENT 的窗口 (我们穿透模式下正是它)。
        """
        hwnd = user32.GetForegroundWindow()
        if not hwnd or int(hwnd) == self.hwnd_int:
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        cx = self.x + self.w // 2
        cy = self.y + self.h // 2
        return rect.left <= cx < rect.right and rect.top <= cy < rect.bottom

    def destroy(self) -> None:
        if not self._alive:
            return
        self._alive = False
        _instances.pop(self.hwnd_int, None)
        try:
            user32.DestroyWindow(self.hwnd)
        except Exception:
            pass
        try:
            if self._old_bmp:
                gdi32.SelectObject(self._mem_dc, self._old_bmp)
            if self._bmp:
                gdi32.DeleteObject(self._bmp)
            if self._mem_dc:
                gdi32.DeleteDC(self._mem_dc)
            if self._screen_dc:
                user32.ReleaseDC(None, self._screen_dc)
        except Exception:
            pass

    __del__ = destroy

    # ---------------- 消息 ----------------
    def pump(self) -> None:
        """抽取并派发本窗口的消息 (由 Tk 定时 tick 调用)。"""
        if not self._alive:
            return
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 0x0001):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def pump_all() -> None:
    """给所有活着的分层窗口抽一次消息 (一个 tick 一次)。"""
    for inst in list(_instances.values()):
        try:
            inst.pump()
        except Exception:
            pass


def count() -> int:
    return len(_instances)


def destroy_all() -> None:
    for inst in list(_instances.values()):
        try:
            inst.destroy()
        except Exception:
            pass
