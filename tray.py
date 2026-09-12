"""Windows 系统托盘图标 (纯 ctypes 调 Win32 Shell_NotifyIcon, 无第三方依赖)。

设计成单线程: 创建一个隐藏窗口接收托盘消息, 由外部 (Tkinter 的 root.after)
定期调用 pump() 抽取并派发本窗口的消息。这样回调都在主线程执行, 可安全操作 Tk。

功能:
- 托盘图标 + 悬停 tooltip (set_tip 可随时更新)。
- 左键双击 -> on_open。
- 右键 -> 弹出菜单「打开主界面 / 退出」。
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

# 隐藏窗口的固定标题, 供第二个实例用 FindWindow 定位并唤起既有实例
TRAY_WINDOW_TITLE = "EbbinghausScheduleTrayWindow"

# 消息与常量
WM_APP_TRAY = 0x0400 + 20   # 托盘回调消息
WM_TRAY_UPDATE = 0x0400 + 21
WM_TRAY_SHOW = 0x0400 + 22  # 外部通知: 请显示主界面 (单实例唤起)
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_NULL = 0x0000
PM_REMOVE = 0x0001

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IDI_APPLICATION = 32512
MF_STRING = 0x0000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

ID_OPEN = 1
ID_QUIT = 2

WNDPROCTYPE = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROCTYPE),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


# 关键函数的签名 (64 位下句柄必须声明为指针, 否则会被截断)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    _seq = 0

    def __init__(self, tip, on_open, on_quit):
        self.on_open = on_open
        self.on_quit = on_quit
        self._removed = False

        TrayIcon._seq += 1
        cls_name = f"EbbinghausTrayWnd{TrayIcon._seq}"
        hinst = kernel32.GetModuleHandleW(None)

        # 保存回调防止被 GC 回收 (否则窗口收到消息时会崩溃)
        self._wndproc = WNDPROCTYPE(self._on_message)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError()
        self._wc = wc  # 保持引用

        self.hwnd = user32.CreateWindowExW(
            0, cls_name, TRAY_WINDOW_TITLE, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError()

        self._hicon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        self._nid = NOTIFYICONDATAW()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self._nid.hWnd = self.hwnd
        self._nid.uID = 1
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = WM_APP_TRAY
        self._nid.hIcon = self._hicon
        self._nid.szTip = tip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid)):
            raise ctypes.WinError()

    # ---- 消息处理 (在调用 pump 的线程内同步执行) ----
    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            event = lparam & 0xFFFF
            if event == WM_LBUTTONDBLCLK:
                self._safe(self.on_open)
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == WM_TRAY_SHOW:      # 第二个实例请求唤起主界面
            self._safe(self.on_open)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "打开主界面")
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出")
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        if cmd == ID_OPEN:
            self._safe(self.on_open)
        elif cmd == ID_QUIT:
            self._safe(self.on_quit)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass

    # ---- 对外接口 ----
    def pump(self):
        """抽取并派发本托盘窗口收到的消息。由 Tk 定时调用。"""
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def set_tip(self, text):
        if self._removed:
            return
        self._nid.szTip = (text or "")[:127]
        self._nid.uFlags = NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def remove(self):
        if self._removed:
            return
        self._removed = True
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass
        try:
            user32.DestroyWindow(self.hwnd)
        except Exception:
            pass
