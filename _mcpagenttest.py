# -*- coding: utf-8 -*-
"""MCP 工具接进助手这条链路自测（不调模型，只走「清单 → 解析 → 落地」）。

要证明三件事：
  · 提示词里的工具清单能看见 MCP 工具（否则模型永远不知道有它）；
  · 模型给出的 mcp__ 调用能被接受，参数整包透传（不是按内置工具的 arg_keys 卡）；
  · 落地时真的去调了那条服务器，结果拍平成文本回给模型。
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.argv = [sys.argv[0], "--no-probe"]
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
TD = tempfile.mkdtemp(prefix="tbmcpa_")

import app as appmod  # noqa: E402
from core import agent as A  # noqa: E402

appmod.APP_DIR = Path(TD)
appmod.DATA_DIR = Path(TD) / "data"
appmod.DATA_DIR.mkdir(parents=True, exist_ok=True)


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {extra}" if extra else ""))
    return bool(ok)


def eq(a, b, name):
    return chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def main() -> int:
    win = appmod.ToolboxApp()
    win.geometry("900x600+20+20")
    win.deiconify()
    win.update()

    win.mcp_store.upsert({"sid": "stub", "name": "桩服务器", "transport": "stdio",
                          "command": sys.executable,
                          "args": [str(HERE / "_mcpstub.py")], "timeout": 10})
    rows = win.mcp_pool.ensure_tools()
    chk(len(rows) >= 3, "池子列出工具", f"{len(rows)} 个")

    A.register_dynamic(rows)
    print("\n-- 1. 提示词里的工具清单")
    cat = A.tool_catalog(win.agent_cfg.tools)
    chk("mcp__stub__echo" in cat, "清单里有 mcp__stub__echo")
    chk("【MCP】" in cat, "MCP 单独一段")
    chk("text" in cat, "参数名从 inputSchema 里读出来了")
    prompt = win.agent.system_prompt("本机")
    chk("mcp__stub__echo" in prompt, "系统提示词里也有（占位符被替换了）")

    print("\n-- 2. 模型给回来的调用能不能被认")
    calls, bad = A.normalize_calls([
        {"tool": "mcp__stub__echo", "args": {"text": "你好"}, "why": "试一下"},
        {"tool": "mcp__stub__add", "arguments": {"a": 1, "b": 2}},
        {"tool": "mcp__stub__不存在的", "args": {}},
    ], allowed=win.agent_cfg.tools)
    eq(len(calls), 2, "两条合法的被接受")
    eq(bad and len(bad), 1, "一条不在清单里的被记进 bad：" + str(bad))
    eq(calls[0]["args"], {"text": "你好"}, "args 原样透传")
    eq(calls[1]["args"], {"a": 1, "b": 2}, "arguments 也认")
    eq(A.default_risk(calls[0]), "write", "MCP 调用一律按改动类对待")

    print("\n-- 3. 落地：真的调到子进程")
    ok, text = win._ai_tool_dispatch("mcp__stub__echo", {"text": "hi"})
    chk(ok, "dispatch 成功", text)
    eq(text, "echo: hi", "回的是桩服务器算出来的内容")
    ok2, text2 = win._ai_tool_dispatch("mcp__stub__add", {"a": 20, "b": 22})
    eq((ok2, text2), (True, "42"), "换一个工具也对")
    ok3, text3 = win._ai_tool_dispatch("mcp__stub__boom", {})
    chk(ok3, "服务端 isError 不算传输失败", text3)
    chk(text3.startswith("调用失败"), "但输出里写清了失败", text3)

    print("\n-- 4. 不认识的工具要有话说")
    ok4, text4 = win._ai_tool_dispatch("mcp__nosuch__x", {})
    chk(not ok4, "未知服务器：返回失败")
    chk("不在清单里" in text4 or "不认识" in text4, "并且说清原因", text4)

    print("\n-- 5. 没有 MCP 时助手照常工作")
    A.register_dynamic([])
    cat2 = A.tool_catalog(win.agent_cfg.tools)
    chk("【MCP】" not in cat2, "清单里没有 MCP 段")
    chk("exec" in cat2, "内置工具还在")

    win.mcp_pool.stop_all()
    win.destroy()
    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
