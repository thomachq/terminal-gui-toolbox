# -*- coding: utf-8 -*-
"""Electron 外壳的 Python 桥：包装**已有** `api/server.py`，不重写任何协议逻辑。

启动后把 `{ok, port, token}` 作为一行 JSON 打到 stdout，Electron 主进程读这一行
就知道该连哪个端口、带什么令牌；令牌**只走进程间管道**，不落盘、不进日志。

数据目录：与经典版 app.py 同款 `TB_DATA_DIR` 覆盖机制 —— Electron 主进程把
用户实际在用的部署目录（D:\\Tools\\...\\data）传进来，保证主机档案/凭证/脚本
是同一份，而不是仓库里那份开发副本。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # bridge/api_main.py → 上两级是仓库根
sys.path.insert(0, str(ROOT))

from api.server import make_default  # noqa: E402


def data_dir() -> Path:
    env = str(os.environ.get("TB_DATA_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # 没显式指定时优先用**部署目录**（用户真正在用的数据），
    # 其次才退回仓库 data/（开发副本）。
    deployed = Path("D:/Tools/Terminal-GUI-Toolbox/data")
    if (deployed / "profiles.dat").is_file():
        return deployed
    return ROOT / "data"


def main() -> int:
    import crypto_store
    d = data_dir()
    crypto_store.set_key_dir(d)
    cfg, _pool, srv = make_default(d)
    # 部署目录的 api.json 可能是另一份程序用**它自己的密钥**加密的；
    # 这里解不开就必须主动轮换，否则 check_token 永远拿旧密文比对 →
    # 界面上表现为「令牌无效」，而 stdout 里打的是新令牌，两边对不上。
    try:
        if not crypto_store.unprotect(str(cfg.data.get("token_enc") or "")):
            cfg.rotate()
    except Exception:  # noqa: BLE001
        cfg.rotate()
    ok, msg = srv.start()
    if not ok:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False),
              flush=True)
        return 1
    print(json.dumps({"ok": True, "port": srv.port, "token": cfg.token(),
                      "data_dir": str(data_dir()), "message": msg},
                     ensure_ascii=False), flush=True)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
