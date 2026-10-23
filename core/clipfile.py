# -*- coding: utf-8 -*-
"""剪贴板里的「文件」和「图片」—— 不依赖 PIL。

为什么自己写：

  `toolbox.spec` 把 PIL 放进了 excludes（只有截图核对脚本用它，主程序不背
  这个包袱），所以打包后的 exe 里根本没有 ImageGrab。Windows 剪贴板是几套
  老 API，ctypes 直调反而更稳、更可控：

    CF_HDROP —— 在资源管理器里 Ctrl+C 文件，剪贴板里是一串宽字符路径
    CF_DIB   —— 截图工具/复制图片，剪贴板里是一张设备无关位图

  位图转 PNG 也就三十行（zlib + 手工 IHDR/IDAT/CRC），不值得为它引依赖。

对外只有一个函数：`grab(dest_dir)` 返回 (路径列表, 一句人话)。图片会先落盘
成 png 再返回路径 —— 上层拿到的永远是「文件」，不用区分它是从哪来的。
"""

from __future__ import annotations

import os
import struct
import sys
import time
import zlib
from pathlib import Path

# 剪贴板格式编号（Windows）
CF_DIB = 8
CF_HDROP = 15
CF_DIBV5 = 17

BI_RGB = 0
BI_BITFIELDS = 3


# ------------------------------------------------------------------ PNG
def _chunk(tag: bytes, payload: bytes) -> bytes:
    """PNG 的一个块：长度 + 类型 + 数据 + CRC32（算的是「类型+数据」）。"""
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def png_bytes(width: int, height: int, rows, *, alpha: bool = True) -> bytes:
    """把若干行像素打成 PNG。

    rows 里每条必须是 bytes，长度严格等于 width × (4 或 3)。每行前面补一个
    0x00（filter=None）—— 截图这种内容用不上差分过滤，省下的几 KB 不值得
    多写一套预测器。
    """
    ch = 4 if alpha else 3
    need = width * ch
    for r in rows:
        if len(r) != need:
            raise ValueError(f"行长度不对：{len(r)} != {need}")
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


# ------------------------------------------------------------------ DIB
def parse_dib(data: bytes) -> tuple[int, int, list[bytes]] | None:
    """BITMAPINFOHEADER 开头的位图 → (宽, 高, RGBA 行)。

    只认 24/32 位、不压缩或位域两种。8 位调色板图直接放弃 —— 截图工具不会
    产出那种格式，为它写一套调色板展开属于自找麻烦。
    """
    if len(data) < 40:
        return None
    bi_size = int.from_bytes(data[0:4], "little")
    width = int.from_bytes(data[4:8], "little", signed=True)
    height = int.from_bytes(data[8:12], "little", signed=True)
    bpp = int.from_bytes(data[14:16], "little")
    comp = int.from_bytes(data[16:20], "little")
    clr_used = int.from_bytes(data[32:36], "little")
    if width <= 0 or height == 0 or bpp not in (24, 32):
        return None
    if comp not in (BI_RGB, BI_BITFIELDS):
        return None
    bottom_up = height > 0
    h = abs(height)
    stride = ((width * bpp + 31) // 32) * 4
    off = bi_size
    if clr_used:
        off += clr_used * 4
    if comp == BI_BITFIELDS and bi_size == 40:
        off += 12                     # 老式头：三个掩码跟在头后面
    if off + stride * h > len(data):
        # 有些程序给的 buffer 比声明的短，能读几行算几行 —— 总比整张放弃好
        h = max(1, (len(data) - off) // stride)
    step = bpp // 8
    rows: list[bytes] = []
    for y in range(h):
        src = off + (h - 1 - y if bottom_up else y) * stride
        line = data[src:src + stride]
        out = bytearray(width * 4)
        for x in range(width):
            p = x * step
            out[x * 4] = line[p + 2]
            out[x * 4 + 1] = line[p + 1]
            out[x * 4 + 2] = line[p]
            out[x * 4 + 3] = line[p + 3] if step == 4 else 255
        rows.append(bytes(out))
    # 大量截图工具写 32bpp 但 alpha 全是 0（它们不用 alpha 通道）。
    # 原样当 PNG 存会得到一张「全透明」的图 —— 打开是空白，最坑人。
    if step == 4 and not any(any(r[3::4]) for r in rows):
        rows = [_opaque(r) for r in rows]
    return width, len(rows), rows


def _opaque(row: bytes) -> bytes:
    """把一行 RGBA 的 alpha 全改成 255。"""
    out = bytearray(row)
    out[3::4] = b"\xff" * (len(out) // 4)
    return bytes(out)


# ------------------------------------------------------------------ Win32
def _grab_windows(dest: Path, max_files: int = 8) -> tuple[list[str], str]:
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    shell = ctypes.windll.shell32
    k32 = ctypes.windll.kernel32
    k32.GlobalLock.restype = wintypes.LPVOID
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    u.GetClipboardData.restype = wintypes.HANDLE
    u.GetClipboardData.argtypes = [wintypes.UINT]

    if not u.OpenClipboard(None):
        return [], "剪贴板被别的程序占着，等一秒再试"
    try:
        # 1) 复制的文件：优先，因为它就是用户「选中的东西」
        if u.IsClipboardFormatAvailable(CF_HDROP):
            h = u.GetClipboardData(CF_HDROP)
            if h:
                n = shell.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
                out: list[str] = []
                for i in range(min(n, max_files)):
                    ln = shell.DragQueryFileW(h, i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 2)
                    shell.DragQueryFileW(h, i, buf, ln + 2)
                    p = buf.value
                    if p and os.path.exists(p):
                        out.append(p)
                if out:
                    more = "" if n <= max_files else f"（还有 {n - max_files} 个没加）"
                    return out, f"从剪贴板加了 {len(out)} 个文件{more}"

        # 2) 图片：截图/「复制图片」都是这个
        fmt = (CF_DIBV5 if u.IsClipboardFormatAvailable(CF_DIBV5)
               else CF_DIB)
        if u.IsClipboardFormatAvailable(fmt):
            h = u.GetClipboardData(fmt)
            if not h:
                return [], "剪贴板里有图，但读不出来"
            k32.GlobalLock.restype = ctypes.c_void_p
            ptr = k32.GlobalLock(h)
            size = k32.GlobalSize(h)
            if not ptr or not size:
                return [], "剪贴板里有图，但读不出来"
            try:
                data = ctypes.string_at(ptr, size)
            finally:
                k32.GlobalUnlock(h)
            parsed = parse_dib(data)
            if parsed is None:
                return [], "这张图的格式不认识（只支持 24/32 位位图）"
            w, hh, rows = parsed
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"clip-{time.strftime('%Y%m%d-%H%M%S')}-{w}x{hh}.png"
            path.write_bytes(png_bytes(w, hh, rows))
            return [str(path)], f"从剪贴板存了一张图（{w}×{hh}）"
        return [], "剪贴板里没有文件或图片"
    finally:
        u.CloseClipboard()


def grab(dest, max_files: int = 8) -> tuple[list[str], str]:
    """读剪贴板里的文件/图片。

    dest —— 图片落盘的目录（通常 data/attachments）。文本粘贴不走这里：
            Tk 自己就能处理文本，硬抢只会把输入法搞乱。
    """
    dest = Path(dest)
    if not sys.platform.startswith("win"):
        return [], "这个系统读不了剪贴板图片（Windows 才有 CF_DIB）"
    try:
        return _grab_windows(dest, max_files)
    except Exception as e:  # noqa: BLE001
        # 剪贴板是全局资源，任何一步失败都不该把助手搞崩
        return [], f"读剪贴板失败：{type(e).__name__}: {e}"


# ------------------------------------------------------------------ 检测
def looks_text(data: bytes) -> bool:
    """字节像不像文本：有 NUL 就直接判死，再试 utf-8 解码。"""
    if b"\x00" in data[:4096]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
