# -*- coding: utf-8 -*-
"""临时关掉锁屏，进程退出时原样还原 —— 测试与截图专用。

为什么要这个：`_conntest` / `_vpnsmoke` / `_websmoke` / `_shot_web` 都要先
「把锁屏关掉」才能自动跑到界面，以前是直接往真实的 `data/settings.json` 里
写 `lock_enabled=False`、`lock_hash=""`、`lock_pass_enc=""` —— 不备份也不
还原。跑一遍回归就把用户设的锁屏密码抹掉了，而且用户完全不知道。

现在统一走这里：先备份，退出时 move 回去。哪怕脚本中途崩了，
`atexit` 也会兜住。
"""
import atexit
import json
import os
import shutil
from pathlib import Path

_OFF = {"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
        "lock_auto_login": False, "lock_pass_enc": ""}


def _restore(p: Path, bak: Path) -> None:
    try:
        if bak.is_file():
            shutil.move(str(bak), str(p))
    except OSError:
        pass


def unlock(path) -> Path:
    """把 settings.json 里的锁屏配置置空，返回该路径。

    原文件先复制成 `settings.json.testbak`，进程退出时还原。
    如果设了 `TB_DATA_DIR`（跑测时把数据目录挪到临时目录），自动跟着挪 ——
    别在临时目录跑测试却去动真实的 settings.json。
    """
    p = Path(path)
    env = os.environ.get("TB_DATA_DIR", "").strip()
    if env:
        p = Path(env) / p.name
    if p.is_file():
        bak = p.with_name(p.name + ".testbak")
        try:
            shutil.copy2(p, bak)
            atexit.register(_restore, p, bak)
        except OSError:
            pass
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        d = {}
    d.update(_OFF)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
