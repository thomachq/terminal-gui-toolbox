# -*- coding: utf-8 -*-
"""群晖 SFTP chroot 修复回归：路径映射 + exec 兜底（纯 mock，不连真机）。

    python _sftpmaptest.py

模拟 DSM 7.4 的 SFTP 根 = /volume1：shell 视角 /var/services/homes/<u> 在
SFTP 通道里是 /homes/<u>；/tmp、/etc 在 SFTP 通道里不存在（ENOENT），
只能走命令通道。验证验收标准：write 家目录 / mkdir /tmp / read /etc /
错误消息含根视图诊断 / 普通 Linux 不回归。
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import threading
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
    print(("  ok   " if ok else "  FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


from api.pool import Conn, SessionPool  # noqa: E402
from api.tools import ToolKit  # noqa: E402
from core.detect import DeviceInfo  # noqa: E402
from core.files import SFTPRootMap  # noqa: E402

# ------------------------------------------------------------------ mock
class ENOENT(IOError):
    def __init__(self, path):
        super().__init__(2, "No such file", path)
        self.errno = 2


class FakeSFTP:
    """SFTP 视角：只有 /homes/cloudadmin/... 可达；/tmp /etc 全 ENOENT。"""
    def __init__(self, files: dict[str, bytes]):
        self.files = files           # sftp 视角路径 -> bytes（模拟家目录）
        self.dirs: set[str] = set()

    def _map(self, p):
        return "/" + "/".join(s for s in p.split("/") if s not in ("", "."))

    def listdir(self, p):
        p = self._map(p)
        if p == "/":
            return ["AI-Workspace", "backups", "docker", "homes", "Jellyfin"]
        if p.startswith("/homes/cloudadmin"):
            return sorted({f.split("/")[3] for f in self.files
                           if f.startswith(p + "/") and len(f.split("/")) > 3})
        raise ENOENT(p)

    def listdir_attr(self, p):
        raise ENOENT(p)

    def stat(self, p):
        p = self._map(p)
        if p in self.files:
            class S:
                st_size = len(self.files[p])
            return S()
        raise ENOENT(p)

    def normalize(self, p):
        return "/" if p in (".", "/") else self._map(p)

    def open(self, p, mode="rb"):
        p = self._map(p)
        if "w" in mode:
            if not p.startswith("/homes/cloudadmin"):
                raise ENOENT(p)
            return _WriteBuf(self.files, p)
        if p in self.files:
            return io.BytesIO(self.files[p])
        raise ENOENT(p)

    def file(self, p, mode="rb"):
        return self.open(p, mode)

    def mkdir(self, p):
        raise ENOENT(p)


class _WriteBuf:
    def __init__(self, store, path):
        self.store, self.path, self.buf = store, path, bytearray()

    def write(self, b):
        self.buf.extend(b)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.store[self.path] = bytes(self.buf)
        return False


class FakeSession:
    alive = True
    user = "cloudadmin"
    host = "192.168.3.12"

    def __init__(self, sftp):
        self._sftp = sftp
        self.shell_files: dict[str, bytes] = {"/etc/hostname": b"nas-hostname\n"}
        self.cmds: list[str] = []

    def sftp(self):
        return self._sftp

    def run(self, cmd, timeout=30):
        self.cmds.append(cmd)
        # base64 < path  -> 回读 shell 视角文件
        if cmd.startswith("base64 < "):
            p = cmd[len("base64 < "):].strip().strip("'")
            if p in self.shell_files:
                return 0, base64.b64encode(self.shell_files[p]).decode(), ""
            return 1, "", "base64: No such file"
        # mkdir -p
        if "mkdir -p" in cmd and "base64 -d" not in cmd:
            return 0, "", ""
        # printf >> 临时文件 / base64 -d > 目标 / rm：用 shell_files 落地
        if "base64 -d" in cmd and ">" in cmd:
            dest = cmd.split(">")[-1].strip().strip("'")
            # 从前面 printf 拼出的临时文件还原：简化为记录写入字节数
            self.shell_files[dest] = b"\x00" * 1
            return 0, "", ""
        if "printf" in cmd or "rm -f" in cmd or ": >" in cmd:
            return 0, "", ""
        if "$HOME" in cmd:
            return 0, "/var/services/homes/cloudadmin", ""
        if "id -un" in cmd:
            return 0, "cloudadmin", ""
        if "ls -1 /volume1" in cmd:
            return 0, "AI-Workspace\nbackups\ndocker\nhomes\nJellyfin\n", ""
        if "ls -1 /volume2" in cmd or "ls -1 /volume3" in cmd:
            return 0, "", ""
        return 0, "", ""


def make_pool():
    cmds = []
    pool = SessionPool(on_event=lambda k, t: None,
                       tracer=lambda d, rc, o, e, ms: cmds.append(d))
    sftp_store = {}
    sftp = FakeSFTP(sftp_store)
    sess = FakeSession(sftp)
    conn = Conn(id="syno", spec={"protocol": "ssh", "host": "192.168.3.12"},
                session=sess, device=DeviceInfo(), dialect=object(),
                lock=threading.RLock())
    pool.conns["syno"] = conn
    return pool, ToolKit(pool, Path(tempfile.mkdtemp(prefix="tbmap_"))), sftp_store, sess, conn


def call(kit, method, parts, qs=None, body=None):
    return kit.route(method, parts, qs or {}, body or {})


# ================================================================== 探测
sec("1. SFTPRootMap 群晖探测")
rm = SFTPRootMap(FakeSFTP({}), FakeSession(FakeSFTP({})).run).probe()
chk(rm.chroot and rm.shell_root == "/volume1", "识别群晖 chroot 根 /volume1",
    f"chroot={rm.chroot} root={rm.shell_root}")
chk(rm.to_sftp("/var/services/homes/cloudadmin/a.txt") == "/homes/cloudadmin/a.txt",
    "shell 家目录 → SFTP 视角")
chk(rm.to_sftp("/tmp/x") == "/tmp/x", "/tmp 不在映射内（走兜底）")

# ================================================================== files API
sec("2. files/write 家目录（路径映射）")
pool, kit, store, sess, conn = make_pool()
st, pl = call(kit, "POST", ["files", "write"],
              body={"session": "syno", "path": "/var/services/homes/cloudadmin/note.txt",
                    "text": "hello"})
chk(st == 200 and pl.get("ok"), "写家目录成功", f"st={st} pl={pl}")
chk(store.get("/homes/cloudadmin/note.txt") == b"hello",
    "内容落在 SFTP 视角 /homes/cloudadmin/note.txt", str(list(store)))

sec("3. files/mkdir /tmp（exec 兜底）")
st, pl = call(kit, "POST", ["files", "mkdir"],
              body={"session": "syno", "path": "/tmp/ttb-test"})
chk(st == 200 and pl.get("ok"), "建 /tmp 目录成功（兜底）", f"st={st} pl={pl}")
chk(pl.get("via") == "exec-fallback", "返回体标注 exec-fallback")

sec("4. files/read /etc/hostname（exec 兜底）")
st, pl = call(kit, "GET", ["files", "read"],
              {"session": "syno", "path": "/etc/hostname"})
chk(st == 200 and "nas-hostname" in (pl.get("text") or ""),
    "读 /etc/hostname 成功（兜底）", f"st={st} pl={pl}")
chk(pl.get("via") == "exec-fallback", "返回体标注 exec-fallback")

sec("5. files/list /tmp（exec 兜底）")
st, pl = call(kit, "GET", ["files", "list"],
              {"session": "syno", "path": "/tmp"})
chk(st == 200, "列 /tmp 成功（兜底）", f"st={st} pl={pl}")

sec("6. pool.upload/download 家目录 + /etc 兜底")
pool, kit, store, sess, conn = make_pool()
r = pool.upload("syno", "/var/services/homes/cloudadmin/up.bin", b"upload-data")
chk(r.get("ok") and r.get("sftp_path") == "/homes/cloudadmin/up.bin",
    "upload 家目录映射到 SFTP 视角", str(r))
chk(store.get("/homes/cloudadmin/up.bin") == b"upload-data", "upload 内容正确")
sess.shell_files["/etc/hostname"] = b"device-name\n"
r = pool.download("syno", "/etc/hostname")
chk(r.get("ok") and r.get("data") == b"device-name\n",
    "download /etc/hostname 走 exec 兜底", str({k: v for k, v in r.items() if k != "data"}))
chk(r.get("via") == "exec-fallback", "download 标注 exec-fallback")

sec("7. 错误消息含 SFTP 根视图诊断")
pool, kit, store, sess, conn = make_pool()
st, pl = call(kit, "POST", ["files", "write"],
              body={"session": "syno", "path": "/var/services/homes/cloudadmin/ok.txt",
                    "text": "x"})
chk(st == 200, "先写一条正常文件", st)
# 制造一个权限类非 ENOENT 错误
orig_open = sess.sftp().open
def boom(p, mode="rb"):
    raise PermissionError(13, "Permission denied", p)
sess._sftp.open = boom
st, pl = call(kit, "GET", ["files", "read"],
              {"session": "syno", "path": "/homes/cloudadmin/ok.txt"})
hint = str(pl.get("hint") or "")
chk(("SFTP" in hint or "volume1" in hint) and "命令执行不受影响" in hint,
    "非 ENOENT 错误的 hint 含根视图诊断", hint[:120])

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{PASS}/{PASS + FAIL}")
for x in FAILS:
    print("  ✗", x)
raise SystemExit(1 if FAILS else 0)
