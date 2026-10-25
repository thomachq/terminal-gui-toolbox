# -*- coding: utf-8 -*-
"""单实例：同一时间只允许一个「终端工具箱」在跑（参考微信）。

双击第二次不该再起一套窗口 —— 两份程序各写一套 data、托盘里两个图标、
关掉一个还剩一个在后台，用户根本分不清刚才关的是哪个。做法是 Windows 命名
互斥量：第二个进程发现名字已存在，就把已有那个窗口叫到前台，然后自己退出。

为什么不用「发一条自定义 Win32 消息」叫醒：Tk 不暴露窗口过程，收不到
WM_* 消息。改成命名事件 + 已有实例在 `after` 里轮询 —— 简单、可靠、
不依赖窗口句柄是否还有效（托盘隐藏时窗口仍在，事件照样收得到）。
"""
from __future__ import annotations

import ctypes
import sys

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002
SW_RESTORE = 9

_IS_WIN = sys.platform == "win32"
_held: list = []          # 句柄保活：进程退出时由系统回收
_ev: dict = {}            # key → 唤醒事件句柄（只对它 Wait，别去 Wait 互斥量）


def _mutex_name(key: str) -> str:
    # Local\ 是当前登录会话，够用且不需要额外权限（Global\ 要 SeCreateGlobal）
    return rf"Local\{key}-single-instance"


def _event_name(key: str) -> str:
    return rf"Local\{key}-wake"


DEFAULT_KEY = "TerminalToolbox"


def key_from_argv(argv, default: str = DEFAULT_KEY) -> str:
    """从命令行取实例命名空间。

    为什么要这个东西：`--page` / `--no-probe` 这些测试参数早先是**直接跳过
    单实例检查**的（_texe 要连着起好几个窗口，不跳就互相挡死）。代价是测试
    实例和用户开着的那个可以并存 —— 托盘里就多出两三个图标，用户根本分不清
    哪个是自己开的（2026-10-25 用户反馈「任务栏都是终端工具箱的托盘」）。

    现在改成：默认还是同一个命名空间（用户双击永远只有一个），测试脚本用
    `--instance-key exe` 换一个命名空间 —— 既不挡用户，自己之间也不互相挡。
    """
    try:
        i = list(argv).index("--instance-key")
    except ValueError:
        return default
    nxt = list(argv)[i + 1] if i + 1 < len(argv) else ""
    suffix = str(nxt).strip()
    # 加前缀而不是直接用 suffix：随便传个空串/怪字符也不会撞上用户那个
    return f"{default}-{suffix}" if suffix and not suffix.startswith("-") \
        else default


if _IS_WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                  wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                  wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenEventW.restype = wintypes.HANDLE
    _k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.SetEvent.argtypes = [wintypes.HANDLE]
    _k32.ResetEvent.argtypes = [wintypes.HANDLE]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _u32.FindWindowW.restype = wintypes.HWND
    _u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]


def acquire(key: str = "TerminalToolbox") -> bool:
    """抢单实例锁。返回 False = 已经有一个在跑。

    非 Windows 或 API 失败时一律返回 True —— 宁可多开一次，也不能因为
    环境奇怪就把程序拦在门外开不起来。
    """
    global _ev
    if not _IS_WIN:
        return True
    # 顺手建一个唤醒事件，供别的进程叫醒我们（有信号表示「有人叫过我」）
    ev = _k32.CreateEventW(None, False, False, _event_name(key))
    if ev:
        _ev[key] = ev
        _held.append(ev)
    h = _k32.CreateMutexW(None, False, _mutex_name(key))
    if not h:
        return True
    _held.append(h)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    return True


def wake(key: str = "TerminalToolbox") -> bool:
    """叫醒已有那个实例（第二个进程退出前调用）。"""
    if not _IS_WIN:
        return False
    ev = _k32.OpenEventW(EVENT_MODIFY_STATE, False, _event_name(key))
    if not ev:
        return False
    try:
        return bool(_k32.SetEvent(ev))
    finally:
        _k32.CloseHandle(ev)


def consume(key: str = "TerminalToolbox") -> bool:
    """已有实例侧：这一轮有没有人叫我？有就消费掉这次信号。

    事件是自动重置的（CreateEventW 的 bManualReset=False），Wait 一到就复位，
    不用再手动 ResetEvent。只 Wait 事件句柄 —— 去 Wait 互斥量会把它「抢过来」，
    白白搅乱所有权语义。
    """
    if not _IS_WIN:
        return False
    h = _ev.get(key)
    if not h:
        return False
    return _k32.WaitForSingleObject(h, 0) == WAIT_OBJECT_0


def bring_to_front(title: str) -> bool:
    """兜底：直接按标题把已有窗口 restore 到前台。

    事件是给「本版本之后」的实例用的；如果对面是更老的版本（没有轮询），
    它收不到，那就退化成直接动它的窗口 —— 老用户升级那一刻用得上。
    """
    if not _IS_WIN or not title:
        return False
    hwnd = _u32.FindWindowW(None, title)
    if not hwnd:
        return False
    _u32.ShowWindow(hwnd, SW_RESTORE)
    _u32.SetForegroundWindow(hwnd)
    return True
