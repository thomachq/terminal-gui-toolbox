# -*- coding: utf-8 -*-
"""能力接口（/v1/tools/*）自测：工具箱的具体功能能不能被智能体直接用。

用户要求：「技能要能使用终端工具箱里的具体功能，不止是作为 terminal」。
所以这里不测「能不能跑命令」（那是 /v1/sessions/<id>/exec 的事），测的是
「问容器 / 问磁盘 / 列目录 / 读文件 / 问服务」这些**结构化能力**。

用本机 local 会话跑，不需要 ssh、不需要密码。
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
FAILS: list[str] = []


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


from api.pool import SessionPool  # noqa: E402
from api.tools import ToolKit  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="wbt_tool_"))
cmds: list[tuple] = []


def tracer(display, rc, out, err, ms):
    cmds.append((display, rc))


pool = SessionPool(on_event=lambda k, t: None, tracer=tracer)
kit = ToolKit(pool, tmp)

conn = pool.open({"protocol": "local", "name": "自测-能力"})
sid = conn.id


def call(method, parts, qs=None, body=None):
    return kit.route(method, parts, qs or {}, body or {})


# ================================================================== 1. 清单
sec("1. 能力清单是自描述的")

st, pl = call("GET", [])
chk(st == 200, "GET /v1/tools 返回 200", st)
names = [t["path"] for t in (pl.get("tools") or [])]
for want in ("/v1/tools/sysinfo", "/v1/tools/docker", "/v1/tools/files",
             "/v1/tools/services", "/v1/hosts"):
    chk(want in names, f"清单里有 {want}")
chk("session" in (pl.get("note") or ""),
    "顶层 note 说明了 session 参数（智能体不用猜）")


# ================================================================== 2. 系统
sec("2. sysinfo：设备 / CPU / 内存 / 磁盘")

st, pl = call("GET", ["sysinfo"])
chk(st == 200, "sysinfo 返回 200", st)
chk(pl.get("session") == sid, "回应里带 session id")
dev = pl.get("device") or {}
chk(bool(dev.get("os_family") or dev.get("os_name")), "认出了系统类型",
    str(dev)[:60])
cpu = pl.get("cpu") or {}
chk("count" in cpu and "pct" in cpu, "CPU 有核数与占用率", str(cpu))
mem = pl.get("memory") or {}
chk(mem.get("total", 0) > 0, "内存总量拿到了", str(mem)[:60])
chk(bool(mem.get("total_h")), "内存有可读形式（不会让模型自己换算）")
chk(isinstance(pl.get("disk"), list), "磁盘是列表")
chk("uptime_raw" in pl, "带上了 uptime 原文（解析失败时还能自己看）")


# ================================================================== 3. 服务
sec("3. services：监听端口 + 服务识别")

st, pl = call("GET", ["services"])
chk(st == 200, "services 返回 200", st)
chk(isinstance(pl.get("listeners"), list), "listeners 是列表")
for row in (pl.get("listeners") or [])[:60]:
    chk(isinstance(row.get("port"), int) and row["port"] > 0,
        f"端口 {row.get('port')} 是整数")
    break
chk(all("kind" in r for r in (pl.get("listeners") or [])),
    "每条都带 kind（模型不用自己认进程名）")


# ================================================================== 4. 文件
sec("4. files：列 / 读 / 写 / 建 / 改名 / 删")

root = str(tmp)
st, pl = call("GET", ["files"], {"path": root})
chk(st == 200, "列目录 200", st)
chk(pl.get("path"), "返回了规范化后的路径", str(pl)[:60])

target = os.path.join(root, "能力测试.txt")
st, pl = call("POST", ["files", "write"],
              body={"path": target, "text": "第一行\n第二行\n"})
chk(st == 200 and pl.get("ok"), "写文件成功", str(pl)[:80])
chk(os.path.isfile(target), "文件真的落在磁盘上")

st, pl = call("GET", ["files", "read"], {"path": target})
chk(st == 200, "读文件 200", st)
chk("第二行" in (pl.get("text") or ""), "读回来的内容对得上", str(pl)[:80])
chk(pl.get("encoding") == "utf-8", "文本按 utf-8 返回")
chk(pl.get("bytes") == len("第一行\n第二行\n".encode()), "字节数准确",
    str(pl.get("bytes")))

st, pl = call("GET", ["files"], {"path": root})
names_in = [e.get("name") for e in (pl.get("entries") or [])]
chk("能力测试.txt" in names_in, "新文件出现在列表里", str(names_in)[:80])

st, pl = call("POST", ["files", "mkdir"],
              body={"path": os.path.join(root, "子目录")})
chk(st == 200 and pl.get("ok"), "建目录成功")
chk(os.path.isdir(os.path.join(root, "子目录")), "目录真的建出来了")

new = os.path.join(root, "改过名.txt")
st, pl = call("POST", ["files", "rename"], body={"path": target, "to": new})
chk(st == 200 and os.path.isfile(new), "改名成功（旧名没了、新名在）")
chk(not os.path.exists(target), "旧文件确实不在了")

st, pl = call("POST", ["files", "remove"], body={"path": new})
chk(st == 200 and not os.path.exists(new), "删文件成功")

st, pl = call("POST", ["files", "remove"],
              body={"path": os.path.join(root, "子目录"), "dir": True})
chk(st == 200 and not os.path.exists(os.path.join(root, "子目录")), "删目录成功")


# ================================================================== 5. 审计
sec("5. 经能力层跑的命令照样进审计（不能被绕过）")

pool.exec(sid, "hostname")           # 先制造一条基准
before = len([c for c in cmds if c[0].startswith("[API]")])
call("GET", ["sysinfo"])
call("GET", ["services"])
after = len([c for c in cmds if c[0].startswith("[API]")])
chk(after > before, "sysinfo / services 打的命令带 [API] 前缀进了 tracer",
    f"{before} -> {after}")
chk(all(c[0].startswith("[API]") for c in cmds), "所有 tracer 记录都有 [API] 前缀")
chk(any(c[0].startswith("[API] sftp ") for c in cmds),
    "SFTP 操作（不是命令）也留了痕")


# ================================================================== 6. Docker
sec("6. docker：探测结果结构正确（本机没装也要好好说话）")

st, pl = call("GET", ["docker"], {"what": "containers"})
chk(st == 200, "docker 端点 200（探测失败也不该 500）", st)
chk("docker" in pl and "ready" in pl, "返回里带 docker 状态与 ready")
chk(isinstance(pl.get("docker", {}).get("detail"), str),
    "有可读的一句话状态")
if not pl.get("ready"):
    chk(bool(pl.get("title")) and bool(pl.get("why")),
        "不可用时也给标题 + 原因（模型能据此决定下一步）", str(pl)[:80])

st, pl = call("GET", ["docker"], {"what": "bogus"})
chk(st == 200, "未知 what 不炸")


# ================================================================== 7. 错误
sec("7. 出错要说清楚，不能假装成功")

st, pl = call("GET", ["nosuch"])
chk(st == 404 and "error" in pl, "未知工具名 → 404 + error", st)

st, pl = call("GET", ["files", "read"], {"path": "/绝对不存在的路径/x.txt"})
chk(st == 500 and "error" in pl, "读不存在的文件 → 带 error", st)

st, pl = call("GET", ["files", "write"], body={})
chk(st in (404, 405), "GET 调写入类操作被拒", st)

pool.close_all()
st, pl = call("GET", ["sysinfo"])
chk(st == 400 and "hint" in pl, "没有连接时 400 + hint（不瞎猜）", str(pl)[:80])


# ================================================================== 8. 档案
sec("8. /v1/hosts：档案清单不含任何口令")

st, pl = kit.hosts(), None
hosts = st.get("hosts") if isinstance(st, dict) else []
chk(isinstance(hosts, list), "hosts 是列表")
raw = str(st)
for leak in ("password", "passwd", "secret", "key_data", "sudo_pw"):
    chk(leak not in raw, f"档案输出里没有 {leak} 字段")
chk(isinstance(st.get("count"), int), "带 count")


# ================================================================== 汇总
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
