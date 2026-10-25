# -*- coding: utf-8 -*-
"""Docker 探测必须能认出「装在 PATH 之外 + 靠免密 sudo 才能用」的机器。

用户原话：
  「这台设备是有docker的，你怎么说没有呢」（群晖 192.168.3.12，
   Container Manager 里跑着 15 个容器，界面却报「未安装」）

三个真因，这里逐条钉住：
  · docker 只在 `/usr/local/bin/docker` 这个符号链接上，而 SSH 会话的 PATH
    是 `/usr/bin:/bin:/usr/sbin:/sbin` → `command -v docker` 什么也找不到；
  · 群晖给 administrators 组配了**免密 sudo**，用户没填 sudo 密码，
    旧代码只在「有密码」时才试 sudo，于是永远走不到能过的那条路；
  · `--format '{{json .}}'` 在群晖上 65 秒（撞 60 秒超时被当成失败），
    换成 `{{.A}}<0x1f>{{.B}}` 字段模板后 0.1 秒。

分段：
  1. 候选路径表与探测命令
  2. _locate：PATH 有 / 没有 / 只有绝对路径
  3. _server_version：直连 → sudo -n → 带密码 sudo 的三级顺序
  4. detect：五种状态分类（ok / absent / daemon-down / no-permission / 原文兜底）
  5. _cmd / _run：sudo 前缀与实际通道
  6. _list：精简字段快路径与 {{json .}} 兜底
  7. parsers：fmt_fields / _records / docker_ps 两种格式都吃

不需要真设备，不需要窗口。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []

from core import docker as D  # noqa: E402
from core import parsers  # noqa: E402


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


# ---------------------------------------------------------------- 假对象
class FakeSess:
    """按「命令里含哪个子串」回放预置结果，并把命令记下来供断言。"""

    kind = "ssh"

    def __init__(self, replies, sudo_pw="", can_admin=True, family="",
                 admin_reply=None):
        self.replies = list(replies)
        self.calls: list[str] = []
        self.admin_calls: list[str] = []
        self.sudo_pw = sudo_pw
        self.can_admin_ok = can_admin
        self.family = family
        self.admin_reply = admin_reply

    def _play(self, cmd):
        self.calls.append(cmd)
        for sub, res in self.replies:
            if sub in cmd:
                return res
        return (0, "", "")

    def run(self, cmd, timeout=60):
        return self._play(cmd)

    def run_traced(self, cmd, timeout=60):
        return self._play(cmd)

    def run_admin(self, cmd, timeout=60):
        self.admin_calls.append(cmd)
        if self.admin_reply is not None:
            return self.admin_reply
        return self._play(cmd)

    def can_admin(self):
        # 真会话返回 (bool, 原因)；这里按真形状给，免得把「元组解包失败
        # 被 except 吞掉」伪装成「没密码所以不试 sudo」。
        return self.can_admin_ok, ""


def dev(family=""):
    class Dev:
        os_family = family
    return Dev()


PERM_ERR = ("permission denied while trying to connect to the Docker daemon "
            "socket at unix:///var/run/docker.sock")
DAEMON_ERR = ("Cannot connect to the Docker daemon at "
              "unix:///var/run/docker.sock. Is the docker daemon running?")


def synology(sudo_n_ok=True):
    """仿群晖：docker 只在 /usr/local/bin，直连无权，sudo -n 能过。"""
    return FakeSess([
        ("for p in docker", (0, "/usr/local/bin/docker\n", "")),
        ("--version", (0, "Docker version 24.0.2, build 610b8d0\n", "")),
        ("sudo -n ", (0, "24.0.2\n", "") if sudo_n_ok else (1, "", PERM_ERR)),
        ("info --format", (1, "", PERM_ERR)),
    ])


# ================================================================ 1
sec("1. 候选路径表与探测命令")
chk(any("ContainerManager" in p for p in D._BIN_CANDIDATES),
    "候选里含群晖 Container Manager 真身路径")
chk("/usr/local/bin/docker" in D._BIN_CANDIDATES,
    "候选里含群晖的 /usr/local/bin 符号链接")
s = synology()
D.DockerEngine(s).detect()
probe = next((c for c in s.calls if "for p in docker" in c), "")
chk("command -v" in probe, "探测用 command -v 而不是 which", probe[:60])
chk(all(p in probe for p in D._BIN_CANDIDATES),
    "每个候选都进了一条命令（不挨个开 exec 通道）",
    str([p for p in D._BIN_CANDIDATES if p not in probe]))
chk("break" in probe, "找到就停，不做无用功")

# ================================================================ 2
sec("2. _locate")
eng = D.DockerEngine(synology())
chk(eng._locate() == "/usr/local/bin/docker", "_locate 返回绝对路径")
eng2 = D.DockerEngine(FakeSess([("for p in docker", (0, "", "")),
                                ("--version", (1, "", "sh: docker: not found")),
                                ("version --format", (1, "", "not found"))]))
chk(eng2._locate() == "", "什么都没有时 _locate 返回空")
eng3 = D.DockerEngine(FakeSess([("for p in docker", (0, "docker\n", ""))]))
chk(eng3._locate() == "docker", "PATH 里有就用 docker 这个名字")
chk(D.DockerEngine(FakeSess([("where docker", (0, r"C:\x\docker.exe", ""))]),
                   dev("windows"))._locate().lower().endswith(".exe"),
    "Windows 走 where docker")

# ================================================================ 3
sec("3. _server_version 的三级顺序")
s = synology()
eng = D.DockerEngine(s)
eng.bin = "/usr/local/bin/docker"
ok, ver, _ = eng._server_version(25)
chk(ok and ver == "24.0.2", "直连失败后 sudo -n 拿到版本", f"{ok} {ver}")
chk(eng.sudo_mode == "nopasswd", "记下 sudo_mode=nopasswd")

s = FakeSess([("sudo -n ", (1, "", "sudo: a password is required")),
              ("info --format", (0, "26.1.0\n", ""))])
eng = D.DockerEngine(s)
ok, ver, _ = eng._server_version(25)
chk(ok and ver == "26.1.0", "直连成功就不必试 sudo")
chk(eng.sudo_mode == "", "直连成功时 sudo_mode 保持空")

s = FakeSess([("sudo -n ", (1, "", "sudo: a password is required")),
              ("info --format", (1, "", PERM_ERR))], sudo_pw="pw",
             admin_reply=(0, "24.0.2\n", ""))
eng = D.DockerEngine(s)
ok, ver, _ = eng._server_version(25)
chk(ok and ver == "24.0.2", "前两级失败后走带密码的 sudo")
chk(eng.sudo_mode == "admin", "记下 sudo_mode=admin")
chk(s.admin_calls and "info --format" in s.admin_calls[0],
    "admin 通道真的被调用", str(s.admin_calls[:1]))

s = FakeSess([("sudo -n ", (1, "", "sudo: a password is required")),
              ("info --format", (1, "", PERM_ERR))], sudo_pw="")
eng = D.DockerEngine(s)
ok, _, blob = eng._server_version(25)
chk(not ok, "没密码就不硬试 sudo")
chk("password is required" in blob or "permission denied" in blob,
    "失败回显留给上层分类", blob[:40])


class _BoolSess(FakeSess):
    """把 can_admin 写成返回裸 bool —— 老代码 unpack 会 TypeError。"""

    def can_admin(self):
        return True


s = _BoolSess([("sudo -n ", (1, "", "sudo: a password is required")),
               ("info --format", (1, "", PERM_ERR))], sudo_pw="pw",
              admin_reply=(0, "24.0.2\n", ""))
eng = D.DockerEngine(s)
ok, ver, _ = eng._server_version(25)
chk(ok and eng.sudo_mode == "admin",
    "can_admin 返回裸 bool 也照样能走 admin 通道", f"{ok} {eng.sudo_mode}")

# ================================================================ 4
sec("4. detect 的状态分类")
eng = D.DockerEngine(synology(), dev("linux"))
info = eng.detect()
chk(info["status"] == D.STATUS_OK, "群晖这种机器判为可用", info["detail"])
chk(info["version"] == "24.0.2", "版本取 Docker version 那行", info["version"])
chk(info["sudo"] is True, "needs_sudo 为真（后面每条命令都要带 sudo -n）")
chk("通过 sudo" in info["detail"], "说明里讲清是靠 sudo 拿到权限", info["detail"])
chk(eng.bin == "/usr/local/bin/docker", "bin 换成了绝对路径", eng.bin)

eng = D.DockerEngine(FakeSess([
    ("for p in docker", (0, "", "")),
    ("--version", (1, "", "sh: docker: not found")),
    ("version --format", (1, "", "sh: docker: not found")),
]))
chk(eng.detect()["status"] == D.STATUS_ABSENT, "命令都没有 → 未安装")

eng = D.DockerEngine(FakeSess([
    ("for p in docker", (0, "/usr/bin/docker\n", "")),
    ("--version", (0, "Docker version 20.10.7, build f0df350\n", "")),
    ("sudo -n ", (1, "", "sudo: command not found")),
    ("info --format", (1, "", DAEMON_ERR)),
]))
chk(eng.detect()["status"] == D.STATUS_DAEMON, "守护没起 → daemon-down")

eng = D.DockerEngine(FakeSess([
    ("for p in docker", (0, "/usr/bin/docker\n", "")),
    ("--version", (0, "Docker version 20.10.7, build f0df350\n", "")),
    ("sudo -n ", (1, "", "sudo: a password is required")),
    ("info --format", (1, "", PERM_ERR)),
]))
info = eng.detect()
chk(info["status"] == D.STATUS_PERM, "无权限 → no-permission")
title, hint = eng.message()
chk("docker 组" in hint or "sudo 密码" in hint, "提示里给出两条出路", hint[:40])

eng = D.DockerEngine(FakeSess([
    ("for p in docker", (0, "/usr/bin/docker\n", "")),
    ("--version", (0, "Docker version 20.10.7, build f0df350\n", "")),
    ("sudo -n ", (1, "", "")),
    ("info --format", (1, "", "上下文已取消")),
]))
info = eng.detect()
chk(info["status"] == D.STATUS_DAEMON and "上下文已取消" in info["detail"],
    "认不出原因也把原文带上，不吞成「执行失败」", info["detail"])

eng = D.DockerEngine(None)
chk(eng.detect()["status"] == D.STATUS_ABSENT, "没连接时不说「可用」")

# ================================================================ 5
sec("5. _cmd / _run")
eng = D.DockerEngine(synology())
eng.bin = "/usr/local/bin/docker"
eng.sudo_mode = "nopasswd"
chk(eng._cmd("ps -a").startswith("sudo -n /usr/local/bin/docker"),
    "nopasswd 时拼上 sudo -n", eng._cmd("ps -a"))
eng.sudo_mode = "admin"
chk(not eng._cmd("ps -a").startswith("sudo"),
    "admin 模式不加前缀（交给 run_admin）", eng._cmd("ps -a"))
s = synology()
eng = D.DockerEngine(s, dev("linux"))
eng.detect()
eng.sudo_mode = "admin"
eng._run("ps -a")
chk(s.admin_calls and s.admin_calls[-1].startswith("/usr/local/bin/docker"),
    "admin 模式真的走 run_admin", str(s.admin_calls[-1:]))
eng.sudo_mode = ""
before = len(s.calls)
eng._run("ps -a")
chk(len(s.calls) > before, "普通模式走 run_traced/run")

# ================================================================ 6
sec("6. _list 快路径与兜底")
rows_sep = "\n".join([
    parsers.SEP.join(["aaaaaaaaaaaa", "web", "nginx:1", "running",
                      "Up 3 days", "80/tcp", "2026-10-01", "3 days"]),
    parsers.SEP.join(["bbbbbbbbbbbb", "db", "mysql:8", "exited",
                      "Exited (0) 2 days ago", "3306/tcp", "2026-09-01", "9 days"]),
])
s = FakeSess([("--format", (0, rows_sep + "\n", ""))])
eng = D.DockerEngine(s)
eng.status = D.STATUS_OK
rows = eng._list("ps", parsers.PS_FIELDS, parsers.docker_ps, "-a ")
chk(len(rows) == 2 and rows[0]["name"] == "web", "精简字段能解析出 2 行",
    str(rows)[:60])
chk(not any("{{json .}}" in c for c in s.calls),
    "快路径成功时不再调 {{json .}}", str(s.calls)[:80])
chk("--format" in s.calls[0] and "{{.ID}}" in s.calls[0],
    "用字段模板而不是整份 JSON", s.calls[0][:70])

s = FakeSess([("{{json .}}", (0, '{"ID":"cccc","Names":"x","Image":"i",'
                                 '"State":"running","Status":"Up","Ports":"",'
                                 '"CreatedAt":"","RunningFor":""}\n', "")),
              ("--format", (0, "", ""))])
eng = D.DockerEngine(s)
eng.status = D.STATUS_OK
rows = eng._list("ps", parsers.PS_FIELDS, parsers.docker_ps, "-a ")
chk(len(rows) == 1, "快路径空时退到 {{json .}}", str(rows)[:60])
chk(any("{{json .}}" in c for c in s.calls), "兜底命令确实发了")

s = FakeSess([("--format", (1, "", "docker: unknown flag: --format"))])
eng = D.DockerEngine(s)
eng.status = D.STATUS_OK
try:
    eng._list("ps", parsers.PS_FIELDS, parsers.docker_ps, "-a ")
    chk(False, "两条路都失败时应该抛错")
except D.DockerError as e:
    chk("unknown flag" in str(e), "抛错时带上原文", str(e)[:40])
eng = D.DockerEngine(synology())
chk(eng.df.__doc__ is None, "df 有兜底不往上抛")

# ================================================================ 7
sec("7. parsers 两种格式都吃")
fmt = parsers.fmt_fields(("ID", "Names"))
chk(fmt == '{{.ID}}{{"\\x1f"}}{{.Names}}', "fmt_fields 拼对分隔符", fmt)
chk(parsers.SEP == "\x1f", "分隔符是 0x1f")

line = parsers.SEP.join(["i1", "n1", "img", "running", "Up 1h", "80",
                         "2026-10-01", "1h"])
rec = parsers._records(line, parsers.PS_FIELDS)[0]     # noqa: SLF001
chk(rec["Names"] == "n1" and rec["State"] == "running", "定长行按字段名对上了")
js = ('{"ID":"i2","Names":"n2","Image":"img","State":"exited",'
      '"Status":"Exited","Ports":"","CreatedAt":"","RunningFor":""}')
rec2 = parsers._records(js, parsers.PS_FIELDS)[0]      # noqa: SLF001
chk(rec2["ID"] == "i2", "逐行 JSON 也认")
chk(len(parsers._records("乱码一行", parsers.PS_FIELDS)) == 0,
    "字段数不对的行直接丢掉，不塞进表格")
rows = parsers.docker_ps(line + "\n" + js)
chk(len(rows) == 2, "docker_ps 混合两种格式共 2 行")
chk(rows[0]["_tone"] == "ok" and rows[1]["_tone"] == "err",
    "状态色调按 running/exited 分")
chk(rows[0]["id"] == "i1", "id 只留 12 位")

print(f"\n合计 {PASS + FAIL} 项：通过 {PASS}，失败 {FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAIL else 0)
