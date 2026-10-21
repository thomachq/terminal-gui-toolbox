# -*- coding: utf-8 -*-
"""系统托盘（Windows，纯 ctypes，不引第三方库）。

为什么不用 pystray：多一个依赖就多一份打包风险（它要 Pillow + 后台线程模型
与 Tk 不太合拍）。这里直接调 Shell_NotifyIconW，几百行搞定，打包零负担。

线程模型：
  · 托盘窗口与消息循环跑在**独立线程**（必须，否则阻塞 Tk 主循环）
  · 菜单、显示窗口这些动作**投递回 Tk 线程**执行（tkinter 非线程安全）
  · 通过构造时传入的 `post`（就是 app.post）投递

非 Windows 平台 start() 直接返回 False，调用方当没托盘处理。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading

WM_USER = 0x0400                   # wintypes 里没有这个常量，自己定
WM_TRAY = WM_USER + 20             # 自定义回调消息
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_CLOSE, WM_DESTROY, WM_COMMAND = 0x0010, 0x0002, 0x0111


class NOTIFYICONDATAW(ctypes.Structure):
    """Windows 的 NOTIFYICONDATAW（Vista+ 尺寸）。

    字段顺序与对齐必须与 windows.h 一致，否则 Shell_NotifyIcon 静默失败。
    """
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class Tray:
    """一个托盘图标 + 右键菜单 + 双击显示主窗。"""

    def __init__(self, root, title: str, ico_path: str, menu_factory,
                 on_show=None, post=None, on_quit=None):
        """
        menu_factory() 应返回一个 tk.Menu（每次右键重新生成，状态才最新）
        on_show()      双击图标时调用
        post(fn, *a)   线程安全投递到 Tk 线程
        on_quit()      菜单「退出」时调用
        """
        self.root = root
        self.title = title or "Terminal GUI Toolbox"
        self.ico_path = ico_path
        self.menu_factory = menu_factory
        self.on_show = on_show
        self.on_quit = on_quit
        self._post = post or (lambda fn, *a: fn(*a))
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._icon = None
        self._running = False
        self._wndproc_ref = None
        self.last_error = ""

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if sys.platform != "win32":
            self.last_error = "仅 Windows 支持托盘"
            return False
        if self._running:
            return True
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,),
                                        daemon=True, name="tray")
        self._thread.start()
        ready.wait(3.0)
        return self._running

    def stop(self) -> None:
        if not self._running or not self._hwnd:
            return
        try:
            u = ctypes.windll.user32
            u.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        self._running = False

    def notify(self, title: str, text: str) -> None:
        """气泡提示。失败不影响主流程。"""
        if not self._running or not self._hwnd:
            return
        try:
            nid = self._make_nid(NIF_INFO)
            nid.szInfo = text[:255]
            nid.szInfoTitle = title[:63]
            nid.dwInfoFlags = 0x1
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 内部
    def _make_nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _run(self, ready: threading.Event) -> None:
        try:
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            shell = ctypes.windll.shell32
            hinst = k.GetModuleHandleW(None)

            # ---- 明确声明参数/返回类型。
            # 不设 argtypes 时 ctypes 按 c_int 传参，64 位下指针和高位 LRESULT
            # 会被截断（DefWindowProcW 直接抛 OverflowError，消息循环跟着废掉）。
            u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            u.DefWindowProcW.restype = ctypes.c_longlong
            u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND,
                                      wt.UINT, wt.UINT]
            u.GetMessageW.restype = ctypes.c_int
            u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
            u.DispatchMessageW.restype = ctypes.c_longlong
            u.CreateWindowExW.restype = wt.HWND
            u.CreateWindowExW.argtypes = [
                wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
            u.DestroyWindow.argtypes = [wt.HWND]
            u.PostQuitMessage.argtypes = [ctypes.c_int]
            u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            u.SetForegroundWindow.argtypes = [wt.HWND]
            shell.Shell_NotifyIconW.argtypes = [wt.DWORD,
                                                ctypes.POINTER(NOTIFYICONDATAW)]
            shell.Shell_NotifyIconW.restype = wt.BOOL

            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, wt.UINT,
                                         wt.WPARAM, wt.LPARAM)

            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_TRAY:
                    low = int(lparam) & 0xFFFF
                    if low == WM_RBUTTONUP:
                        self._post(self._show_menu)
                    elif low == WM_LBUTTONDBLCLK:
                        if self.on_show:
                            self._post(self.on_show)
                    return 0
                if msg == WM_CLOSE:
                    u.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    u.PostQuitMessage(0)
                    return 0
                return u.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wndproc_ref = WNDPROC(wndproc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
                ]

            cls_name = "TerminalToolboxTrayWnd"
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = cls_name
            try:
                u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
                u.RegisterClassW.restype = ctypes.c_ushort
            except Exception:  # noqa: BLE001
                pass
            u.RegisterClassW(ctypes.byref(wc))

            hwnd = u.CreateWindowExW(0, cls_name, cls_name, 0, 0, 0, 0, 0,
                                     None, None, hinst, None)
            if not hwnd:
                self.last_error = f"CreateWindowEx 失败 (err={k.GetLastError()})"
                ready.set()
                return
            self._hwnd = hwnd

            # 图标：优先用程序自带的 .ico，退回落系统默认应用图标
            hicon = 0
            try:
                u.LoadImageW.restype = wt.HANDLE
                u.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                                         ctypes.c_int, ctypes.c_int, wt.UINT]
                hicon = u.LoadImageW(None, self.ico_path, IMAGE_ICON, 0, 0,
                                     LR_LOADFROMFILE | LR_DEFAULTSIZE) or 0
            except Exception:  # noqa: BLE001
                hicon = 0
            if not hicon:
                try:
                    u.LoadIconW.restype = wt.HANDLE
                    u.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
                    hicon = u.LoadIconW(None, ctypes.c_void_p(32512)) or 0
                except Exception:  # noqa: BLE001
                    hicon = 0
            self._icon = hicon

            nid = self._make_nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = hicon
            nid.szTip = self.title[:127]
            ok = shell.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            if not ok:
                self.last_error = "Shell_NotifyIcon 失败"
                u.DestroyWindow(hwnd)
                ready.set()
                return

            self._running = True
            ready.set()

            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self._running = False
            try:
                ready.set()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                if self._hwnd:
                    nid = self._make_nid(0)
                    ctypes.windll.shell32.Shell_NotifyIconW(
                        NIM_DELETE, ctypes.byref(nid))
            except Exception:  # noqa: BLE001
                pass
            self._running = False
            self._hwnd = None

    def _show_menu(self) -> None:
        """在 Tk 线程里弹菜单。"""
        try:
            pt = wt.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            menu = self.menu_factory()
            menu.tk_popup(pt.x, pt.y)
        except Exception:  # noqa: BLE001
            pass
