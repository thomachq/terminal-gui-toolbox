# -*- coding: utf-8 -*-
"""文件系统抽象 —— 本地 / SFTP 用同一套接口，界面不用关心是哪一边。

参考 MobaXterm 的做法：左边本机、右边设备，双击进目录，跨栏拖拽即传输。
这里只提供「列目录 / 增删改名 / 打开读写流」这几件最小的事，
传输编排（方向、递归、进度）交给 pages/files_page.py。

约定：
  · 路径一律返回**该平台原生**写法（本地反斜杠、远端正斜杠），
    别在界面里自己拼分隔符 —— 用 join()/parent()。
  · 所有方法都可能抛异常（OSError / PermissionError / paramiko 的 SFTPError），
    上层统一 catch 成一句人话。
"""
from __future__ import annotations

import os
import shutil
import stat as statmod
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.metrics import human_bytes

CHUNK = 256 * 1024          # 传输块大小：太小慢，太大时进度条不跟手


class Cancelled(Exception):
    """用户在进度回调里按了取消 —— 抛这个把传输就地打断。"""

    def __str__(self) -> str:
        return "已取消"


# ------------------------------------------------------------------ 数据结构
@dataclass
class Entry:
    """一条目录项。字段名故意和 Column key 对齐，界面直接拿去填表。"""

    name: str = ""
    path: str = ""
    is_dir: bool = False
    size: int = 0
    mtime: float = 0.0
    mode: str = ""            # drwxr-xr-x / -rw-r--r--
    link: str = ""            # 符号链接指向
    uid: int = -1
    gid: int = -1

    @property
    def kind(self) -> str:
        if self.link:
            return "link"
        return "dir" if self.is_dir else "file"

    def row(self) -> dict:
        return {
            "name": self.name + ("/" if self.is_dir else ""),
            "size": "" if self.is_dir else human_bytes(self.size),
            "size_b": self.size,
            "mtime": fmt_time(self.mtime),
            "mtime_raw": self.mtime,
            "mode": self.mode,
            "kind": self.kind,
            "link": self.link,
            "path": self.path,
            "is_dir": self.is_dir,
        }


def fmt_time(ts: float) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return ""


def mode_str(mode: int, is_dir: bool = False) -> str:
    """把 st_mode 压成 `drwxr-xr-x` 这种一眼能看懂的串。"""
    try:
        txt = statmod.filemode(mode)
    except (TypeError, ValueError):
        return ""
    if is_dir and not txt.startswith("d"):
        txt = "d" + txt[1:]
    return txt


def sort_entries(items: list[Entry]) -> list[Entry]:
    """目录在前、文件在后，各自按名字不区分大小写排。"""
    return sorted(items, key=lambda e: (not e.is_dir, e.name.lower()))


# ------------------------------------------------------------------ 基类
class BaseFS:
    kind = "base"
    sep = "/"
    label = "文件"
    readonly = False

    # -- 平台差异：子类实现
    def home(self) -> str:
        raise NotImplementedError

    def listdir(self, path: str) -> list[Entry]:
        raise NotImplementedError

    def normalize(self, path: str) -> str:
        raise NotImplementedError

    def is_dir(self, path: str) -> bool:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def remove(self, path: str, is_dir: bool = False) -> None:
        raise NotImplementedError

    def rename(self, old: str, new: str) -> None:
        raise NotImplementedError

    def open_read(self, path: str):
        raise NotImplementedError

    def open_write(self, path: str):
        raise NotImplementedError

    # -- 公共
    def join(self, base: str, *names: str) -> str:
        p = base
        for n in names:
            if not n or n in (".",):
                continue
            p = p.rstrip("\\/") + self.sep + n.lstrip("\\/") if p else n
        return self.normalize(p) if p else self.home()

    def parent(self, path: str) -> str:
        """上一级。两种分隔符都认 —— 用户可能在路径框里手打正斜杠。"""
        p = (path or "")
        if self.kind == "local":
            try:
                p = os.path.expanduser(p)
            except (KeyError, RuntimeError):
                pass
        p = p.rstrip("\\/")
        root = "/" if self.kind == "sftp" else ""
        if not p:
            return root or self.home()
        cut = max(p.rfind("/"), p.rfind("\\"))
        if cut < 0:
            # `C:` 是盘符相对路径，跟 `C:\` 不是一回事 —— 补上反斜杠再返回，
            # 否则「上一级」会把用户送到「当前盘的工作目录」去。
            if self.kind == "local" and len(p) == 2 and p[1] == ":":
                return p + "\\"
            return p
        if cut == 0:
            return root or p[:1]
        head = p[:cut]
        if self.kind == "local" and len(head) == 2 and head[1] == ":":
            head += "\\"             # 盘符根不要退成裸 `C:`
        try:
            return self.normalize(head)
        except Exception:  # noqa: BLE001
            return head

    def describe(self) -> str:
        return self.label


# ------------------------------------------------------------------ 本地
class LocalFS(BaseFS):
    kind = "local"
    sep = os.sep
    label = "本机"

    def __init__(self, start: str | None = None):
        self.start = start or str(Path.home())

    def home(self) -> str:
        return self.start

    def normalize(self, path: str) -> str:
        p = (path or "").strip().strip('"').strip("'")
        if not p:
            return self.home()
        try:
            p = os.path.expanduser(p)
        except (KeyError, RuntimeError):
            pass
        # abspath 而不是 normpath：`.` `..` 也顺手解掉，列表里给的 path
        # 就是能直接拿去打开的真路径，界面不用再算一次
        return os.path.abspath(p)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(self.normalize(path))

    def exists(self, path: str) -> bool:
        return os.path.exists(self.normalize(path))

    def listdir(self, path: str) -> list[Entry]:
        root = self.normalize(path)
        out: list[Entry] = []
        with os.scandir(root) as it:
            for de in it:
                try:
                    st = de.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = de.is_dir(follow_symlinks=True)
                link = ""
                try:
                    if de.is_symlink():
                        link = os.readlink(de.path)
                except OSError:
                    link = ""
                out.append(Entry(
                    name=de.name, path=de.path, is_dir=is_dir,
                    size=0 if is_dir else st.st_size,
                    mtime=st.st_mtime,
                    mode=mode_str(st.st_mode, is_dir),
                    link=link))
        return sort_entries(out)

    def mkdir(self, path: str) -> None:
        os.makedirs(self.normalize(path), exist_ok=False)

    def remove(self, path: str, is_dir: bool = False) -> None:
        p = self.normalize(path)
        if is_dir:
            shutil.rmtree(p, ignore_errors=False)
        else:
            os.remove(p)

    def rename(self, old: str, new: str) -> None:
        os.replace(self.normalize(old), self.normalize(new))

    def open_read(self, path: str):
        return open(self.normalize(path), "rb")

    def open_write(self, path: str):
        p = self.normalize(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        return open(p, "wb")


# ------------------------------------------------------------------ SFTP 根映射
class SFTPRootMap:
    """探测 SFTP 通道的「根」在 shell 视角里到底是哪儿，并做路径映射。

    为什么需要：群晖 DSM 的 internal-sftp 把会话 chroot 到 /volume1 ——
    `sftp.listdir("/")` 看到的是共享文件夹（homes/backups/docker…），而 shell
    看到的却是完整文件系统。于是 shell 视角的 /tmp、/etc、
    /var/services/homes/<user> 在 SFTP 通道里全不存在，文件 API 一律 ENOENT。

    探测靠两件事（都不写远端、不影响对方）：
      · `sftp.listdir("/")` 的顶层名字 = SFTP 根视图
      · shell 的 `ls /volume1` 等候选挂载点 = 真实路径候选
    谁与根视图重合最多，谁就是 SFTP 根对应的 shell 前缀。

    普通 Linux 没有 chroot：根视图含 tmp/etc、shell_root 探测为 "/"，
    to_sftp() 原样返回 —— 天然不回归（验收标准最后一条）。
    """
    CANDIDATES = ("/volume1", "/volume2", "/volume3", "/volumes/CDROM")

    def __init__(self, sftp, runner=None):
        self.sftp = sftp
        self.runner = runner            # (cmd, timeout)->(rc,out,err) 或 None
        self.shell_root = "/"           # SFTP 根对应的 shell 前缀
        self.top: list[str] = []        # SFTP 根视图顶层名字
        self.chroot = False
        self.home_aliases: list[tuple[str, str]] = []   # (shell 前缀, sftp 前缀)
        self.reason = ""
        self._done = False

    def _run(self, cmd: str, timeout: int = 8) -> tuple[int, str, str]:
        if not callable(self.runner):
            return -1, "", ""
        try:
            r = self.runner(cmd, timeout)
            if isinstance(r, tuple) and len(r) >= 2:
                return int(r[0]), str(r[1] or ""), str(r[2] or "") if len(r) > 2 else ""
        except Exception:  # noqa: BLE001
            pass
        return -1, "", ""

    def probe(self) -> "SFTPRootMap":
        if self._done:
            return self
        self._done = True
        try:
            self.top = [str(x) for x in self.sftp.listdir("/")]
        except Exception as e:  # noqa: BLE001
            self.reason = f"读 SFTP 根失败：{type(e).__name__}: {e}"
            return self
        top_set = set(self.top)
        # chroot 的强特征：根视图里没有 Linux 必备的 tmp/etc/usr
        if {"tmp", "etc", "usr"} & top_set:
            self.reason = "SFTP 根视图含 /tmp /etc，判断未 chroot"
            return self
        # 逐个候选挂载点比对，重合最多者为 shell_root
        best, best_score = "/", 0
        for cand in self.CANDIDATES:
            rc, out, _ = self._run(f"ls -1 {cand} 2>/dev/null", timeout=8)
            names = {ln.strip() for ln in out.splitlines() if ln.strip()}
            score = len(names & top_set)
            if score > best_score:
                best, best_score = cand, score
        if best_score >= 2:
            self.shell_root = best
            self.chroot = True
        # 家目录别名：shell 侧 /var/services/homes/<user> ↔ SFTP /homes/<user>
        # 用 shell 读 passwd 拿真实 home，配合根视图里的 homes 推断。
        rc, out, _ = self._run("printf %s \"$HOME\"", timeout=8)
        shell_home = (out or "").strip()
        user = ""
        rc2, out2, _ = self._run("id -un", timeout=8)
        if rc2 == 0 and (out2 or "").strip():
            user = (out2 or "").strip().splitlines()[-1]
        if self.chroot and "homes" in top_set and user:
            # 群晖：shell 家目录是 /var/services/homes/<user>（或符号链接到
            # /volume1/homes/<user>），SFTP 视角统一是 /homes/<user>。
            for sh in (f"/var/services/homes/{user}",
                       f"/volume1/homes/{user}", f"{self.shell_root}/homes/{user}"):
                self.home_aliases.append((sh, f"/homes/{user}"))
            if shell_home and shell_home not in {a for a, _ in self.home_aliases}:
                self.home_aliases.append((shell_home.rstrip("/"), f"/homes/{user}"))
        self.reason = (
            f"SFTP 根视图={sorted(top_set)[:8]}… shell 根={self.shell_root} "
            f"({'chroot' if self.chroot else '无 chroot'})")
        return self

    def to_sftp(self, path: str) -> str:
        """shell 视角路径 → SFTP 视角路径。未 chroot 时原样返回。"""
        p = (path or "").strip().replace("\\", "/")
        if not p.startswith("/"):
            p = "/" + p
        if not self.chroot:
            return "/" + "/".join(s for s in p.split("/") if s not in ("", "."))
        # 先替换家目录别名（最长优先，避免 /homes/<user> 被壳前缀吃掉）
        for sh, sf in sorted(self.home_aliases, key=lambda x: -len(x[0])):
            if p == sh:
                return sf
            if p.startswith(sh + "/"):
                return sf + p[len(sh):]
        # 再剥 shell 根前缀
        root = self.shell_root.rstrip("/")
        if root and root != "/" and (p == root or p.startswith(root + "/")):
            rest = p[len(root):]
            return rest if rest.startswith("/") else ("/" + rest)
        return "/" + "/".join(s for s in p.split("/") if s not in ("", "."))

    def diag(self) -> str:
        """给错误响应用的诊断文案。"""
        if not self._done:
            self.probe()
        head = f"SFTP 根视图 {self.top[:8]}（疑似 chroot 于 {self.shell_root}）"
        if self.home_aliases:
            ex = self.home_aliases[0][0]
            head += f"；家目录请用 SFTP 视角如 {self.home_aliases[0][1]}/…（shell 的 {ex}）；"
        else:
            head += "；"
        return head + "/tmp、/etc 不在 SFTP 根内，只能走命令通道；命令执行不受影响"


# ------------------------------------------------------------------ SFTP
class SFTPFS(BaseFS):
    kind = "sftp"
    sep = "/"
    label = "设备"

    def __init__(self, session, start: str | None = None):
        self.session = session
        self._start = start
        self._rootmap: SFTPRootMap | None = None

    # -- 连接
    def _sftp(self):
        if self.session is None:
            raise OSError("尚未连接设备")
        fn = getattr(self.session, "sftp", None)
        if not callable(fn):
            raise OSError("当前会话不支持 SFTP（Telnet 没有文件传输通道）")
        return fn()

    def _map(self) -> SFTPRootMap:
        """惰性探测 SFTP 根映射（缓存；探测失败也不能影响正常文件操作）。"""
        if self._rootmap is None:
            runner = None
            if callable(getattr(self.session, "run", None)):
                runner = lambda cmd, timeout=8: self.session.run(cmd, timeout=timeout)
            m = SFTPRootMap(self._sftp(), runner)
            try:
                m.probe()
            except Exception:  # noqa: BLE001
                pass
            self._rootmap = m
        return self._rootmap

    def remote(self, path: str) -> str:
        """normalize + SFTP 根映射：把 shell 视角路径转成 SFTP 视角路径。"""
        return self._map().to_sftp(self.normalize(path))

    def diagnose(self) -> str:
        """SFTP 根视图诊断（文件操作失败时给上层拼进错误信息）。"""
        try:
            return self._map().diag()
        except Exception:  # noqa: BLE001
            return ""

    def home(self) -> str:
        if self._start:
            return self._start
        try:
            return self._sftp().normalize(".") or "/"
        except Exception:  # noqa: BLE001
            user = getattr(self.session, "user", "") or ""
            return f"/home/{user}" if user and user != "root" else "/"

    def normalize(self, path: str) -> str:
        p = (path or "").strip()
        if not p:
            return self.home()
        p = p.replace("\\", "/")
        if not p.startswith("/"):
            p = "/" + p
        # 自己折叠 . / .. / 重复斜杠，省得远端报错
        parts: list[str] = []
        for seg in p.split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(seg)
        return "/" + "/".join(parts)

    def _attr(self, path: str):
        return self._sftp().lstat(self.remote(path))

    def is_dir(self, path: str) -> bool:
        try:
            return statmod.S_ISDIR(self._attr(path).st_mode)
        except Exception:  # noqa: BLE001
            return False

    def exists(self, path: str) -> bool:
        try:
            self._attr(path)
            return True
        except Exception:  # noqa: BLE001
            return False

    def listdir(self, path: str) -> list[Entry]:
        root = self.remote(path)
        sftp = self._sftp()
        out: list[Entry] = []
        for a in sftp.listdir_attr(root):
            name = a.filename
            if name in (".", ".."):
                continue
            is_dir = statmod.S_ISDIR(a.st_mode)
            link = ""
            if statmod.S_ISLNK(a.st_mode):
                # 链接单独看一眼指向哪儿；解不开就当普通文件
                try:
                    link = sftp.readlink(sftp.normalize(self.join(root, name)))
                except Exception:  # noqa: BLE001
                    link = ""
                if link:
                    try:
                        is_dir = statmod.S_ISDIR(sftp.stat(self.join(root, name)).st_mode)
                    except Exception:  # noqa: BLE001
                        is_dir = False
            out.append(Entry(
                name=name, path=self.join(root, name), is_dir=is_dir,
                size=0 if is_dir else int(a.st_size or 0),
                mtime=float(a.st_mtime or 0),
                mode=mode_str(a.st_mode, is_dir),
                link=link,
                uid=int(getattr(a, "st_uid", -1)),
                gid=int(getattr(a, "st_gid", -1))))
        return sort_entries(out)

    def mkdir(self, path: str) -> None:
        self._sftp().mkdir(self.remote(path))

    def remove(self, path: str, is_dir: bool = False) -> None:
        p = self.remote(path)
        sftp = self._sftp()
        if is_dir:
            # 递归删：先清空再删自己
            for a in sftp.listdir_attr(p):
                child = self.join(p, a.filename)
                self.remove(child, statmod.S_ISDIR(a.st_mode))
            sftp.rmdir(p)
        else:
            sftp.remove(p)

    def rename(self, old: str, new: str) -> None:
        self._sftp().rename(self.remote(old), self.remote(new))

    def open_read(self, path: str):
        return self._sftp().open(self.remote(path), "rb")

    def open_write(self, path: str):
        return self._sftp().open(self.remote(path), "wb")

    def describe(self) -> str:
        s = self.session
        who = getattr(s, "user", "") or ""
        host = getattr(s, "host", "") or ""
        if who and host:
            return f"{who}@{host}"
        return self.label


# ------------------------------------------------------------------ 传输
def base_name(path: str) -> str:
    p = (path or "").rstrip("\\/")
    cut = max(p.rfind("/"), p.rfind("\\"))
    return p[cut + 1:] if cut >= 0 else p


def transfer(src_fs: BaseFS, src_path: str, dst_fs: BaseFS, dst_dir: str,
             on_progress=None, on_file=None) -> dict:
    """把 src 复制到 dst_dir 下，目录递归。

    on_file(name) 在开始一个新文件时调用；
    on_progress(done_bytes, total_bytes, None) 每写一块调用一次。
    返回 {"files": n, "bytes": n}
    """
    stats = {"files": 0, "bytes": 0}
    target = dst_fs.join(dst_dir, base_name(src_path))
    _copy_one(src_fs, src_path, dst_fs, target, stats, on_progress, on_file)
    return stats


def _copy_one(src_fs: BaseFS, src: str, dst_fs: BaseFS, dst: str,
              stats: dict, on_progress, on_file) -> None:
    if src_fs.is_dir(src):
        try:
            dst_fs.mkdir(dst)
        except Exception:  # noqa: BLE001
            pass          # 已存在就继续往里写，真正的错误由子项暴露
        for e in src_fs.listdir(src):
            _copy_one(src_fs, e.path, dst_fs, dst_fs.join(dst, e.name),
                      stats, on_progress, on_file)
        return

    if on_file:
        on_file(base_name(src))
    with src_fs.open_read(src) as fin, dst_fs.open_write(dst) as fout:
        while True:
            buf = fin.read(CHUNK)
            if not buf:
                break
            fout.write(buf)
            stats["bytes"] += len(buf)
            if on_progress:
                on_progress(stats["bytes"], 0, None)
    stats["files"] += 1


def collect(fs: BaseFS, path: str) -> tuple[int, int]:
    """预扫：返回 (文件数, 总字节)，给进度条一个分母。

    目录靠递归 listdir 累加（SFTP 上每层都要一次网络往返，所以只在
    用户真的按了传输之后才做，别在选中的时候就算）。
    """
    if not fs.is_dir(path):
        return 1, 0
    files = 0
    size = 0
    try:
        for e in fs.listdir(path):
            if e.is_dir:
                f, s = collect(fs, e.path)
                files += f
                size += s
            else:
                files += 1
                size += e.size
    except Exception:  # noqa: BLE001
        pass
    return max(1, files), size


# ------------------------------------------------------------------ WinSCP
WINSCP_HINTS = (
    r"C:\Program Files (x86)\WinSCP\WinSCP.exe",
    r"C:\Program Files\WinSCP\WinSCP.exe",
    r"C:\Program Files (x86)\WinSCP\WinSCP.com",
    r"C:\Program Files\WinSCP\WinSCP.com",
)


def find_winscp() -> str:
    """找 WinSCP。注册表里有 InstallDir 就信它，否则按常见路径猜。"""
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, r"Software\Martin Prikryl\WinSCP 2\Config",
                                        0, winreg.KEY_READ | view) as k:
                        d, _ = winreg.QueryValueEx(k, "InstallDir")
                        p = Path(d) / "WinSCP.exe"
                        if p.is_file():
                            return str(p)
                except OSError:
                    continue
    except ImportError:
        pass
    for h in WINSCP_HINTS:
        if Path(h).is_file():
            return h
    which = shutil.which("winscp") or shutil.which("winscp.com")
    return which or ""


def winscp_command(spec, open_dir: str = "") -> list[str]:
    """拼 WinSCP 的命令行。

    密钥登录直接把 -privatekey 传过去（路径而已，不是秘密）；
    密码登录**不传** `-password` —— 那会把明文口令留在进程命令行里，
    任何能看任务管理器的人都能读到。让 WinSCP 自己弹窗问。
    """
    exe = find_winscp()
    if not exe:
        return []
    user = getattr(spec, "user", "") or ""
    host = getattr(spec, "host", "") or ""
    port = getattr(spec, "port", 22) or 22
    url = f"sftp://{user + '@' if user else ''}{host}:{port}/"
    if open_dir:
        d = open_dir if open_dir.startswith("/") else "/" + open_dir
        if not d.endswith("/"):
            d += "/"
        url += d.lstrip("/")
    argv = [exe, url]
    if getattr(spec, "auth", "") == "key" and getattr(spec, "key_path", ""):
        argv.append("-privatekey=" + spec.key_path)
    argv.append("-hostkey=*")      # 首次连接别卡在指纹确认框上
    return argv
