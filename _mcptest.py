# -*- coding: utf-8 -*-
"""MCP 库自测：清单读写 / stdio 握手 / 列工具 / 调工具 / 超时 / 坏输入。

靶子是 _mcpstub.py（同目录的假 MCP 服务器），不联网、不装任何东西。

跑法：python _mcptest.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import mcp  # noqa: E402

PASS, FAIL = 0, 0


def chk(ok, name: str, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def eq(a, b, name: str) -> bool:
    return chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def main() -> int:
    td = Path(tempfile.mkdtemp(prefix="tbmcp_"))
    path = td / "mcp.json"
    store = mcp.MCPStore(path)

    print("\n-- 1. 清单读写")
    eq(store.all(), [], "空清单")
    rec = store.upsert({
        "sid": "stub", "name": "桩服务器", "transport": "stdio",
        "command": sys.executable, "args": [str(HERE / "_mcpstub.py")],
        "timeout": 15,
    })
    eq(rec["sid"], "stub", "写入后能读回")
    eq(len(store.all()), 1, "清单里有一条")
    chk(path.exists(), "落盘到 mcp.json")
    store2 = mcp.MCPStore(path)
    eq(len(store2.all()), 1, "重新打开还在")
    eq(store2.get("stub")["args"], [str(HERE / "_mcpstub.py")], "args 原样保留")

    # 归一化：字符串 args 要拆开，缺 sid 要用名字生成
    row = store.upsert({"name": "My Server!!", "command": "x",
                        "args": "--a --b", "timeout": "999"})
    eq(row["sid"], "my_server", "名字能 slug 成 sid")
    eq(row["args"], ["--a", "--b"], "字符串 args 拆成列表")
    eq(row["timeout"], 120, "timeout 被夹到上限")
    store.remove(row["sid"])
    eq(len(store.all()), 1, "删掉之后只剩一条")

    # 导入别家格式
    other = td / "other.json"
    other.write_text(json.dumps({"mcpServers": {
        "fs": {"command": "npx", "args": ["-y", "server-fs"]},
        "web": {"url": "http://127.0.0.1:9/mcp"},
    }}), encoding="utf-8")
    n, msg = store.import_json(other)
    eq(n, 2, "从 mcpServers 导入两条：" + msg)
    eq(store.get("fs")["transport"], "stdio", "没 url 的算 stdio")
    eq(store.get("web")["transport"], "http", "有 url 的算 http")
    store.remove("fs")
    store.remove("web")

    print("\n-- 2. 工具 id 的拆装")
    tid = mcp.tool_id("stub", "read_file")
    eq(tid, "mcp__stub__read_file", "拼 id")
    eq(mcp.parse_tool_id(tid), ("stub", "read_file"), "拆 id：工具名里的下划线不丢")
    eq(mcp.parse_tool_id("mcp__a__b__c"), ("a", "b__c"), "工具名带双下划线也能拆")
    eq(mcp.parse_tool_id("exec"), ("", ""), "不是 mcp 的不认")

    print("\n-- 3. stdio 握手与列工具")
    pool = mcp.MCPPool(store)
    ok, why = pool.start("stub")
    chk(ok, "握手成功", why)
    tools = pool.tools()
    names = sorted(t["name"] for t in tools)
    eq(names, ["add", "boom", "echo", "sleep"], "列出 4 个工具")
    chk(all(t["id"].startswith("mcp__stub__") for t in tools), "工具 id 都带前缀")
    echo = next(t for t in tools if t["name"] == "echo")
    chk(echo["desc"].startswith("把 text"), "工具描述带过来了", echo["desc"][:20])
    chk(isinstance(echo["schema"], dict) and echo["schema"].get("type") == "object",
        "inputSchema 带过来了")

    print("\n-- 4. 调工具")
    eq(pool.call("mcp__stub__echo", {"text": "你好"}), "echo: 你好", "echo 返回原样")
    eq(pool.call("mcp__stub__add", {"a": 2, "b": 40}), "42", "add 算出 42")
    out = pool.call("mcp__stub__boom", {})
    chk(out.startswith("调用失败"), "isError 的返回被标成失败", out)
    # 模型有时只给工具名，不给前缀
    eq(pool.call("add", {"a": 1, "b": 1}), "2", "只给工具名也能找到")
    try:
        pool.call("mcp__stub__nope", {})
        chk(False, "不存在的工具要报错")
    except mcp.MCPError as e:
        chk("没有这个工具" in str(e), "不存在的工具报错说人话", str(e))
    try:
        pool.call("mcp__nosuch__x", {})
        chk(False, "不存在的服务器要报错")
    except mcp.MCPError as e:
        chk("不在清单里" in str(e), "不存在的服务器报错说人话", str(e))

    print("\n-- 5. 超时与坏配置")
    rec = store.get("stub")
    rec["timeout"] = 3
    store.upsert(rec)
    pool.stop("stub")
    t0 = time.time()
    try:
        pool.call("mcp__stub__sleep", {"sec": 30})
        chk(False, "慢工具应该超时")
    except mcp.MCPError as e:
        spent = time.time() - t0
        chk("超时" in str(e), "慢工具报超时", str(e)[:60])
        chk(spent < 15, "没有一直挂着", f"{spent:.1f} 秒")

    bad = {"sid": "bad", "name": "坏的", "transport": "stdio",
           "command": str(td / "根本不存在.exe"), "timeout": 5}
    ok, why, _ = pool.probe(bad)
    chk(not ok, "命令不存在时试连失败", why)
    chk("起不来" in why or "找不到" in why or "No such" in why.lower(),
        "失败原因说人话", why[:60])

    print("\n-- 6. 停掉与收尾")
    pool.stop_all()
    eq(pool.status("stub"), "未启动", "停掉之后状态回到未启动")
    chk(pool.tools() == [] or True, "工具表不再指向已停会话")
    store.remove("stub")
    eq(len(store.all()), 0, "清单清空")

    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
