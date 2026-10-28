# -*- coding: utf-8 -*-
"""审计日志「设备」字段必须是**真正跑命令的那台**，不是界面上当前选中的那台。

背景（2026-10-28）：
  审计的设备字段以前由 `app._trace_cmd` 取 `self.session.label`，而
  `self.session` 是「界面当前选中的会话」。API 池里的会话是另一套连接：
  界面停在 192.168.3.48 时，智能体在 192.168.3.12 上跑的每条命令都会被记成
  fnosadmin@192.168.3.48。翻日志时根本分不清哪条是哪台，追问题会追到错误的机器上。

修法：tracer 多带一个 `device`，谁跑的谁传（会话自己知道 label）；
老 tracer 只认 5 个参数，用 `call_tracer()` 兜一次签名。

另外钉住：`_reconnect()` 也要重新挂 tracer —— 以前只在 open() 挂，
断线重连过的 API 会话，之后跑的命令既不进日志也不进审计，等于全程没留痕。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import api.pool as P                                  # noqa: E402
import core.session as S                              # noqa: E402

FAIL = []
PASS = [0]


def chk(ok, title, detail=""):
    if ok:
        PASS[0] += 1
        print(f"  ok   {title}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL.append(title)
        print(f"  FAIL {title}" + (f"  [{detail}]" if detail else ""))


print("1. call_tracer：新老 tracer 都要能调")
seen = {}


def t6(display, rc, out, err, ms, device=""):
    seen["args"] = (display, rc, out, err, ms, device)
    return "ret6"


def t5(display, rc, out, err, ms):
    seen["args5"] = (display, rc, out, err, ms)
    return "ret5"


r = S.call_tracer(t6, "ls", 0, "out", "", 12, device="u@h")
chk(r == "ret6" and seen["args"][5] == "u@h",
    "6 参数 tracer 收到 device", str(seen["args"]))
r = S.call_tracer(t5, "ls", 0, "out", "", 12, device="u@h")
chk(r == "ret5" and seen["args5"] == ("ls", 0, "out", "", 12),
    "5 参数 tracer 仍然能用（回落）", str(seen["args5"]))

calls = []


def t5cnt(display, rc, out, err, ms):
    calls.append(display)
    return 1


S.call_tracer(t5cnt, "ls", 0, "", "", 1, device="x")
chk(len(calls) == 1, "回落时只调一次（不会重复执行）", f"调用 {len(calls)} 次")


def t_boom(display, rc, out, err, ms, device=""):
    raise TypeError("int 和 NoneType 不能相加")


try:
    S.call_tracer(t_boom, "ls", 0, "", "", 1, device="x")
    chk(False, "tracer 自己的 TypeError 不该被当成签名不匹配吞掉")
except TypeError as e:
    chk("device" not in str(e), "别的 TypeError 照旧抛出", str(e)[:40])

print("\n2. BaseSession._trace 把自己的 label 当 device 传出去")
got = {}


def grab(display, rc, out, err, ms, device=""):
    got["d"] = device


class FakeSess(S.BaseSession):
    def __init__(self, label):
        super().__init__()
        self.label = label


s = FakeSess("fnosadmin@192.168.3.48")
s.tracer = grab
s._trace("uname -a", 0, "Linux", "", 5)
chk(got.get("d") == "fnosadmin@192.168.3.48", "device 取自会话 label", got.get("d"))
s2 = FakeSess("")
s2.tracer = grab
got.clear()
s2._trace("cat /etc/os-release", 0, "", "", 3)
chk(got.get("d") == "", "空 label 传空串（上层再决定回落到哪台）", repr(got.get("d")))

print("\n3. api.pool._tagged：API 会话用自己的 label，加 [API] 前缀")
cap = {}


def cap6(display, rc, out, err, ms, device=""):
    cap["d"] = (display, device)


w = P._tagged(cap6, "cloudadmin@192.168.3.12")
w("hostname", 0, "nas", "", 4)
chk(cap["d"][0].startswith(P.API_CMD_TAG), "命令加了 [API] 前缀", cap["d"][0])
chk(cap["d"][1] == "cloudadmin@192.168.3.12",
    "device 用池子自己的 label（不是界面那台）", cap["d"][1])
w("hostname", 0, "nas", "", 4, device="explicit@1.1.1.1")
chk(cap["d"][1] == "explicit@1.1.1.1", "显式 device 优先于池 label", cap["d"][1])

w5 = P._tagged(t5cnt, "u@h")
w5("id", 0, "", "", 1)
chk(calls[-1].startswith(P.API_CMD_TAG), "5 参数 tracer 也能被包（前缀照样加）",
    calls[-1])

print("\n4. app._audit_command：别人给了 device 就用它")
import app as A                                        # noqa: E402


class StubApp:
    def __init__(self):
        self.events = []
        self.session = types.SimpleNamespace(label="fnosadmin@192.168.3.48")

    def _scrub(self, text):
        return text or ""

    def _audit(self, event, status="ok", detail="", device="", actor="用户"):
        self.events.append({"event": event, "status": status,
                            "device": device, "actor": actor})


st = StubApp()
A.ToolboxApp._audit_command(st, "hostname", 0, "nas", "", 30, by_api=True,
                           device="cloudadmin@192.168.3.12")
ev = st.events[-1]
chk(ev["device"] == "cloudadmin@192.168.3.12", "显式 device 覆盖会话 label",
    ev["device"])
chk(ev["event"] == "ai.command" and ev["actor"] == "智能体",
    "by_api 仍归到 AI")

st2 = StubApp()
A.ToolboxApp._audit_command(st2, "ls", 0, "", "", 10)
chk(st2.events[-1]["device"] == "", "没给 device 就传空串（由 _audit 回落）",
    repr(st2.events[-1]["device"]))

print("\n5. 源码钉住：两处挂 tracer、trace 带 device 参数")
pool_src = (ROOT / "api" / "pool.py").read_text(encoding="utf-8")
app_src = (ROOT / "app.py").read_text(encoding="utf-8")
chk(pool_src.count("def _hook(") == 1 and pool_src.count("self._hook(") == 2,
    "_hook 定义一处、open 与 _reconnect 各调一处",
    f"def {pool_src.count('def _hook(')} 次 / 调用 {pool_src.count('self._hook(')} 次")
reconn = pool_src[pool_src.index("def _reconnect"):]
chk("self._hook(" in reconn, "重连后也重新挂 tracer（否则命令不留痕）")
chk("def _trace_cmd(self, display, rc, out, err, ms, device=\"\")" in app_src,
    "_trace_cmd 收了 device")
chk("by_api=by_api,\n                                device=device)" in app_src,
    "_trace_cmd 把 device 传给 _audit_command")

print("\n6. 改完的文件还能编译")
import py_compile  # noqa: E402
for rel in ("core/session.py", "api/pool.py", "app.py"):
    try:
        py_compile.compile(str(ROOT / rel), doraise=True)
        chk(True, f"{rel} 编译通过")
    except py_compile.PyCompileError as e:
        chk(False, f"{rel} 编译失败", str(e)[:200])

print(f"\n结果：{PASS[0]} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  · " + f)
    sys.exit(1)
