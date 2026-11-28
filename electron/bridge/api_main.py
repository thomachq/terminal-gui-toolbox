# -*- coding: utf-8 -*-
"""Electron 外壳的 Python 桥：包装**已有** `api/server.py`，不重写任何协议逻辑。

启动后把 `{ok, port, token}` 作为一行 JSON 打到 stdout，Electron 主进程读这一行
就知道该连哪个端口、带什么令牌；令牌**只走进程间管道**，不落盘、不进日志。

为什么不另写一套 API：仓库里 `api/server.py` 已有会话池、Bearer token、
/v1/sessions 全套端点，重写等于把已验证过的东西再验一遍。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # bridge/api_main.py → 上两级是仓库根
sys.path.insert(0, str(ROOT))

from api.server import make_default  # noqa: E402


def main() -> int:
    data = ROOT / "data"
    cfg, _pool, srv = make_default(data)
    ok, msg = srv.start()
    if not ok:
        print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False),
              flush=True)
        return 1
    print(json.dumps({"ok": True, "port": srv.port, "token": cfg.token(),
                      "message": msg}, ensure_ascii=False), flush=True)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
