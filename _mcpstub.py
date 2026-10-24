# -*- coding: utf-8 -*-
"""假 MCP 服务器（stdio，按行 JSON-RPC）—— 只给 _mcptest.py 用。

它不是产品的一部分：真跑起来只会挂在测试里当靶子。
故意保留了三个行为：正常回 text、故意报错、故意慢（测超时）。
"""
import json
import sys
import time

TOOLS = [
    {"name": "echo", "description": "把 text 原样回显（前面加个 echo:）",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add", "description": "两数相加", "inputSchema": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}},
    {"name": "boom", "description": "总是失败", "inputSchema": {}},
    {"name": "sleep", "description": "睡几秒再回（测超时用）", "inputSchema": {
        "type": "object", "properties": {"sec": {"type": "number"}}}},
]


def reply(mid, result):
    print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result},
                     ensure_ascii=False), flush=True)


def fail(mid, code, msg):
    print(json.dumps({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": code, "message": msg}},
                     ensure_ascii=False), flush=True)


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:
            continue                      # 通知（initialized）不用回
        if method == "initialize":
            reply(mid, {"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "stub", "version": "0"}})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            if name == "echo":
                reply(mid, {"content": [{"type": "text",
                                         "text": "echo: " + str(args.get("text", ""))}],
                            "isError": False})
            elif name == "add":
                total = int(args.get("a") or 0) + int(args.get("b") or 0)
                reply(mid, {"content": [{"type": "text", "text": str(total)}],
                            "isError": False})
            elif name == "boom":
                reply(mid, {"content": [{"type": "text", "text": "故意炸的"}],
                            "isError": True})
            elif name == "sleep":
                time.sleep(float(args.get("sec") or 3))
                reply(mid, {"content": [{"type": "text", "text": "睡醒了"}],
                            "isError": False})
            else:
                fail(mid, -32601, f"没有这个工具：{name}")
        else:
            fail(mid, -32601, f"没有这个方法：{method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
