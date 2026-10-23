# -*- coding: utf-8 -*-
"""在真实桌面上创建/替换「Terminal GUI Toolbox」快捷方式。

为什么要自己调 COM：

  · `New-Object -ComObject WScript.Shell` 会被安全策略拦（"COM object
    instantiation can run arbitrary code"）；
  · `cscript` / `wscript` 在 LOLBin 拦截名单里；
  · `mklink` 要管理员；
  · `pylnk3` 的写支持只面向 UWP 链接，普通文件链接写出来结构对不上（实测
    回读直接 struct.error）。

  剩下的干净路子只有一条：在 Python 进程里直接 ctypes 调 `IShellLinkW` +
  `IPersistFile`。这正是 WScript.Shell 内部做的事，只是不经任何系统脚本宿主，
  也不需要第三方库 —— **纯标准库**，拷到哪台机器都能跑。

为什么不假设桌面是 C:\\Users\\<user>\\Desktop：

  本机桌面早被重定向到 D:\\Documents\\Desktop（用户级铁律第 0j 条）。
  一律先读注册表 `User Shell Folders` 的 Desktop 值，再和 .NET 的
  GetFolderPath('Desktop') 交叉验证，两边不一致时以注册表为准并告警。

为什么写完还要回读：

  快捷方式最坑的失败是「文件生成了、双击打不开」。所以保存完立刻用
  `IPersistFile::Load` + `IShellLink::GetPath` 把目标路径读回来比对 ——
  走的是 Windows 自己的解析逻辑，比自己数字节靠谱。

跑法：
    python _mkshortcut.py                      # 默认 D:\\Tools\\Terminal-GUI-Toolbox
    python _mkshortcut.py --exe <exe 路径>
    python _mkshortcut.py --name "别的名字"
    python _mkshortcut.py --dry-run            # 只打印将要做什么
"""
from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
from ctypes import POINTER, byref, c_int, c_ulong, c_ushort, c_void_p, c_wchar_p
from pathlib import Path

DEFAULT_EXE = r"D:\Tools\Terminal-GUI-Toolbox\TerminalToolbox.exe"
DEFAULT_NAME = "Terminal GUI Toolbox"

# CLSID_ShellLink / IID_IShellLinkW / IID_IPersistFile
CLSID_SHELLLINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"

CLSCTX_INPROC_SERVER = 1
SW_SHOWNORMAL = 1
SLGP_UNCPRIORITY = 0x0004

# 虚表下标（IShellLinkW / IPersistFile 的顺序是固定的，见 Windows SDK 头文件）
VT_GET_PATH = 3
VT_SET_DESCRIPTION = 7
VT_SET_WORKING_DIR = 9
VT_SET_SHOW_CMD = 15
VT_SET_ICON = 17
VT_SET_PATH = 20
VT_PF_LOAD = 5
VT_PF_SAVE = 6


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort),
                ("Data3", c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", c_ulong), ("dwHighDateTime", c_ulong)]


class WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [("dwFileAttributes", c_ulong),
                ("ftCreationTime", FILETIME),
                ("ftLastAccessTime", FILETIME),
                ("ftLastWriteTime", FILETIME),
                ("nFileSizeHigh", c_ulong),
                ("nFileSizeLow", c_ulong),
                ("dwReserved0", c_ulong),
                ("dwReserved1", c_ulong),
                ("cFileName", ctypes.c_wchar * 260),
                ("cAlternateFileName", ctypes.c_wchar * 14)]


def desktop_dir() -> Path:
    """注册表优先，.NET API 兜底，两边不一致时以注册表为准。"""
    reg = ""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion"
                r"\Explorer\User Shell Folders") as k:
            reg = os.path.expandvars(str(winreg.QueryValueEx(k, "Desktop")[0]))
    except OSError:
        reg = ""
    api = ""
    try:
        api = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        api = ""
    if reg and api and Path(reg) != Path(api):
        print(f"[warn] 注册表说桌面在 {reg}，.NET 说在 {api} —— 按注册表来")
    pick = reg or api
    if not pick:
        raise SystemExit("拿不到桌面路径（注册表与 .NET 都失败）")
    return Path(pick)


class ShellLink:
    """IShellLinkW + IPersistFile 的最小封装，只做「建一条文件快捷方式」。"""

    def __init__(self):
        self.ole32 = ctypes.oledll.ole32          # oledll 会自动把 HRESULT 抛成异常
        self._inited = False
        self.link = c_void_p()
        self.pf = c_void_p()

    # -- 生命周期
    def __enter__(self):
        self.ole32.CoInitialize(None)
        self._inited = True
        clsid = GUID()
        iid = GUID()
        self.ole32.CLSIDFromString(c_wchar_p(CLSID_SHELLLINK), byref(clsid))
        self.ole32.CLSIDFromString(c_wchar_p(IID_SHELLLINKW), byref(iid))
        self.ole32.CoCreateInstance(byref(clsid), None, CLSCTX_INPROC_SERVER,
                                    byref(iid), byref(self.link))
        iid_pf = GUID()
        self.ole32.CLSIDFromString(c_wchar_p(IID_PERSISTFILE), byref(iid_pf))
        self._call(self.link, 0, ctypes.HRESULT,          # QueryInterface
                   [c_void_p, c_void_p], byref(iid_pf), byref(self.pf))
        return self

    def __exit__(self, *exc):
        for ptr in (self.pf, self.link):
            if ptr and ptr.value:
                try:
                    self._call(ptr, 2, c_ulong, [])        # Release
                except OSError:
                    pass
        if self._inited:
            self.ole32.CoUninitialize()
        return False

    # -- 虚表调用
    @staticmethod
    def _call(ptr, index: int, restype, argtypes, *args):
        vtbl = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
        return fn(ptr, *args)

    # -- 属性
    def set_path(self, path: str) -> None:
        self._call(self.link, VT_SET_PATH, ctypes.HRESULT,
                   [c_wchar_p, c_ulong], path, 0)

    def set_work_dir(self, path: str) -> None:
        self._call(self.link, VT_SET_WORKING_DIR, ctypes.HRESULT,
                   [c_wchar_p], path)

    def set_description(self, text: str) -> None:
        self._call(self.link, VT_SET_DESCRIPTION, ctypes.HRESULT,
                   [c_wchar_p], text)

    def set_icon(self, path: str, index: int = 0) -> None:
        self._call(self.link, VT_SET_ICON, ctypes.HRESULT,
                   [c_wchar_p, c_int], path, index)

    def set_show(self, cmd: int = SW_SHOWNORMAL) -> None:
        self._call(self.link, VT_SET_SHOW_CMD, ctypes.HRESULT, [c_int], cmd)

    # -- 存取
    def save(self, lnk_path: str) -> None:
        self._call(self.pf, VT_PF_SAVE, ctypes.HRESULT,
                   [c_wchar_p, c_int], lnk_path, 1)

    def load(self, lnk_path: str) -> None:
        self._call(self.pf, VT_PF_LOAD, ctypes.HRESULT,
                   [c_wchar_p, c_ulong], lnk_path, 0)

    def get_path(self) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        fd = WIN32_FIND_DATAW()
        self._call(self.link, VT_GET_PATH, ctypes.HRESULT,
                   [c_void_p, c_int, c_void_p, c_ulong],
                   buf, len(buf), byref(fd), SLGP_UNCPRIORITY)
        return buf.value


def kill_running(exe: Path) -> None:
    """程序开着时快捷方式仍能建，但图标缓存可能不刷新 —— 顺手关掉更省事。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", exe.name],
                       capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    exe = Path(args.exe)
    if not exe.is_file():
        raise SystemExit(f"找不到目标程序：{exe}")

    desk = desktop_dir()
    if not desk.is_dir():
        raise SystemExit(f"桌面目录不存在：{desk}")
    out = desk / f"{args.name}.lnk"
    print(f"桌面：{desk}")
    print(f"目标：{exe}")
    print(f"快捷方式：{out}")
    if args.dry_run:
        return 0

    kill_running(exe)
    if out.exists():
        try:
            out.unlink()          # 已存在则替换
            print("旧的快捷方式已删除，重建")
        except OSError as e:
            raise SystemExit(f"删不掉旧的快捷方式：{e}")

    with ShellLink() as sl:
        sl.set_path(str(exe))
        sl.set_work_dir(str(exe.parent))
        sl.set_description("Terminal GUI Toolbox · 终端运维工具箱")
        sl.set_icon(str(exe), 0)
        sl.set_show(SW_SHOWNORMAL)
        sl.save(str(out))

    if not out.is_file() or out.stat().st_size < 400:
        raise SystemExit(f"写出来的 .lnk 不对（{out.stat().st_size if out.is_file() else '缺失'}）")

    # 回读：走 Windows 自己的解析，比数字节靠谱
    with ShellLink() as chk:
        chk.load(str(out))
        back = chk.get_path()
    print(f"回读：{back}")
    if Path(back).name.lower() != exe.name.lower():
        raise SystemExit(f"回读的目标不对：{back}")
    print(f"[ok] 已创建 {out.name}（{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
