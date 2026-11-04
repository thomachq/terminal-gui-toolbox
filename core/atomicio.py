# -*- coding: utf-8 -*-
"""原子写（临时文件 + 替换）的统一实现。

**为什么不能只 `os.replace` 一下**

Windows 上目标文件被**任何**进程以普通方式打开着（杀软扫一下、另一个线程在
并发读、资源管理器在生成缩略图），`os.replace` 就报 WinError 5「拒绝访问」。
v1.0.29 实测：22:06 存成功、22:11 就失败，纯靠运气。失败时新数据还躺在
`.tmp` 里，但用户看到的是「保存失败」，刚改的东西等于白改。

**三层兜底**（原 `profiles.save_blob` 的写法，现在全项目共用一份）

1. `os.replace` 重试 5 次，间隔 0.15/0.30/0.45/0.60 秒 —— 覆盖杀软扫几秒；
2. 还不行就**直接覆写目标**（放弃原子性：写坏一半的概率远小于保存不进去，
   而且 JSON 写坏了下次读会被 `except (OSError, ValueError)` 兜住）；
3. 两条路都不通才把最后一个错误抛出去，由调用方决定是弹窗还是静默。

**换行**：`save_text` 走的是文本模式（`open(..., "w")`），与原来的
`Path.write_text` 行为一致 —— 在 Windows 上会把 `\\n` 落成 `\\r\\n`。
不要改成先 `encode` 再写 bytes，那会悄悄把整个项目的数据文件行尾从
CRLF 改成 LF，diff 一片红。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

RETRIES = 5


def _tmp_for(dst: Path) -> Path:
    return dst.with_name(dst.name + ".tmp")


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def _commit(tmp: Path, dst: Path, fallback: Callable[[], None]) -> None:
    """把已经写好的 tmp 换成 dst，replace 失败就退到 fallback。"""
    last: OSError | None = None
    for i in range(RETRIES):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError as e:      # WinError 5：目标被别的进程开着
            last = e
            time.sleep(0.15 * (i + 1))
        except OSError as e:              # 跨卷之类的，重试也没用，直接退到覆写
            last = e
            break
    try:
        fallback()
    except OSError:
        _unlink_quiet(tmp)
        raise last or OSError(f"写不进 {dst}")
    _unlink_quiet(tmp)


def save_bytes(dst: Path, blob: bytes) -> None:
    """把字节写进 dst（临时文件 + 三层兜底）。目录不存在会先建。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    tmp.write_bytes(blob)

    def _fallback() -> None:
        with open(dst, "wb") as f:
            f.write(blob)

    _commit(tmp, dst, _fallback)


def save_text(dst: Path, text: str, encoding: str = "utf-8") -> None:
    """把文本写进 dst。与 `Path.write_text` 等价（含 Windows 的 CRLF 转换）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(dst)
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)

    def _fallback() -> None:
        with open(dst, "w", encoding=encoding) as f:
            f.write(text)

    _commit(tmp, dst, _fallback)
