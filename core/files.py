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


# ------------------------------------------------------------------ SFTP
class SFTPFS(BaseFS):
    kind = "sftp"
    sep = "/"
    label = "设备"

    def __init__(self, session, start: str | None = None):
        self.session = session
        self._start = start

    # -- 连接
    def _sftp(self):
        if self.session is None:
            raise OSError("尚未连接设备")
        fn = getattr(self.session, "sftp", None)
        if not callable(fn):
            raise OSError("当前会话不支持 SFTP（Telnet 没有文件传输通道）")
        return fn()

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
        return self._sftp().lstat(self.normalize(path))

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
        root = self.normalize(path)
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
        self._sftp().mkdir(self.normalize(path))

    def remove(self, path: str, is_dir: bool = False) -> None:
        p = self.normalize(path)
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
        self._sftp().rename(self.normalize(old), self.normalize(new))

    def open_read(self, path: str):
        return self._sftp().open(self.normalize(path), "rb")

    def open_write(self, path: str):
        return self._sftp().open(self.normalize(path), "wb")

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
