# -*- coding: utf-8 -*-
"""Docker 管理。

关键点：**设备上可能根本没装 Docker**，也可能装了但没权限 / 守护进程没起来。
这三种情况必须在界面上有不同的话说，不能一律报「执行失败」。

所以先 `detect()` 给出状态：

  ok            可用
  absent        没装（命令不存在）
  daemon-down   装了但守护进程没跑
  no-permission 当前用户不在 docker 组，且没有 sudo 密码

列表统一用 `--format '{{json .}}'`，逐行 JSON，解析比表格文本稳得多。
"""
from __future__ import annotations

import os
import shlex

from core import parsers

STATUS_OK = "ok"
STATUS_ABSENT = "absent"
STATUS_DAEMON = "daemon-down"
STATUS_PERM = "no-permission"
STATUS_UNKNOWN = "unknown"

# docker 不在 PATH 里的地方。**群晖最典型**：Container Manager 把二进制装在
# /var/packages/ContainerManager/target/usr/bin/，只在 /usr/local/bin 留一个
# 符号链接；而 SSH 会话的 PATH 往往是 /usr/bin:/bin:/usr/sbin:/sbin，
# 于是 `command -v docker` 什么都找不到 —— 界面就一路报「未安装」。
# 用户明确反馈过：这台设备明明有 Docker（Container Manager 里跑着好几个容器）。
_BIN_CANDIDATES = (
    "/usr/local/bin/docker",                                   # 群晖 DSM 7 符号链接
    "/var/packages/ContainerManager/target/usr/bin/docker",    # 群晖新版真身
    "/var/packages/Docker/target/usr/bin/docker",              # 群晖 DSM 6 套件
    "/opt/bin/docker",                                         # 群晖旧版
    "/usr/bin/docker",
    "/usr/local/bin/docker",
    "/snap/bin/docker",
    "/opt/homebrew/bin/docker",                                # Apple Silicon
)

_ABSENT_HINTS = ("command not found", "未找到", "不是内部或外部命令",
                 "no such file", "无法将", "not recognized",
                 "docker: not found", "找不到")
_PERM_HINTS = ("permission denied", "权限不够", "必须是 root",
               "are you root", "got permission denied")
_DAEMON_HINTS = ("cannot connect to the docker daemon", "is the docker daemon running",
                 "无法连接到 docker", "error during connect",
                 "open //./pipe/docker_engine")


class DockerError(Exception):
    pass


class DockerEngine:
    def __init__(self, session, device=None):
        self.session = session
        self.device = device
        self.status = STATUS_UNKNOWN
        self.version = ""
        self.detail = ""
        self.needs_sudo = False
        self.family = getattr(device, "os_family", "") or ""
        self.bin = "docker"        # 找到可执行文件后换成绝对路径
        # "" / "nopasswd"（sudo -n，群晖这类给管理员免密） / "admin"（要密码）
        self.sudo_mode = ""

    # ------------------------------------------------------------ 找命令
    def _is_windows(self) -> bool:
        if self.family == "windows":
            return True
        s = self.session
        return (getattr(s, "kind", "") == "local" and os.name == "nt")

    def _locate(self, timeout: int = 20) -> str:
        """找出 docker 到底在哪。返回可直接执行的命令名，找不到返回 ""。

        PATH 里没有就逐个试已知安装位置 —— 不这么做的话，群晖、以及任何
        「装在 /usr/local/bin 但 SSH 的 PATH 没有它」的机器都会被判成未安装。
        """
        s = self.session
        if self._is_windows():
            rc, out, err = s.run("where docker", timeout=timeout)
            for ln in (out or "").splitlines():
                ln = ln.strip()
                if ln.lower().endswith(".exe"):
                    return ln
            return "" if rc != 0 else (out or "").strip().splitlines()[:1] and \
                (out or "").strip().splitlines()[0].strip()
        # POSIX：一条命令里挨个试，找到就停（省得开好几条 exec 通道）
        probe = ('for p in docker ' + " ".join(_BIN_CANDIDATES) + '; do '
                 'if command -v "$p" >/dev/null 2>&1; then echo "$p"; break; fi; '
                 'done')
        rc, out, err = s.run(probe, timeout=timeout)
        for ln in (out or "").splitlines():
            ln = ln.strip()
            if ln:
                return ln
        return ""

    def _server_version(self, timeout: int):
        """问服务端版本。返回 (ok, 版本, 失败回显)。

        顺序很重要：直连 → `sudo -n` → 带密码的 sudo。
        `sudo -n` 这一步是关键：群晖把 administrators 组配成免密 sudo，
        用户根本没填 sudo 密码，但 `sudo -n docker info` 就是能过。
        以前只试「直连」和「有密码才试 sudo」，于是群晖一律落进「无权限」。
        """
        s = self.session
        probe = self.bin + " info --format '{{.ServerVersion}}'"
        rc, out, err = s.run(probe, timeout=timeout)
        if rc == 0 and (out or "").strip():
            return True, out.strip().splitlines()[0].strip(), ""
        blob = (out or "") + (err or "")
        if self.family == "network":
            return False, "", blob
        rc2, out2, err2 = s.run("sudo -n " + probe, timeout=timeout)
        if rc2 == 0 and (out2 or "").strip():
            self.sudo_mode = "nopasswd"
            return True, out2.strip().splitlines()[0].strip(), ""
        blob2 = blob + (out2 or "") + (err2 or "")
        try:
            res = s.can_admin()
            # 真会话返回 (bool, 原因)，但别的地方也见过直接返回 bool 的；
            # 写成只认元组的话，返回 bool 会 TypeError，被下面吞掉 ——
            # 表现就是「有密码但不试 sudo」，和当年群晖那个 bug 同一个症状。
            ok = bool(res[0]) if isinstance(res, tuple) else bool(res)
        except Exception:  # noqa: BLE001
            ok = False
        if ok and getattr(s, "sudo_pw", None):
            try:
                rc3, out3, err3 = s.run_admin(probe, timeout=timeout)
            except Exception as e:  # noqa: BLE001
                rc3, out3, err3 = 1, "", str(e)
            if rc3 == 0 and (out3 or "").strip():
                self.sudo_mode = "admin"
                return True, out3.strip().splitlines()[0].strip(), ""
            blob2 += (out3 or "") + (err3 or "")
        return False, "", blob2 or blob

    # ------------------------------------------------------------ 探测
    def detect(self, timeout: int = 25) -> dict:
        s = self.session
        if s is None:
            self.status, self.detail = STATUS_ABSENT, "尚未连接设备"
            return self.info()

        found = ""
        try:
            found = self._locate(timeout)
        except Exception:  # noqa: BLE001
            found = ""
        if found:
            self.bin = found

        rc, out, err = s.run(f"{self.bin} --version", timeout=timeout)
        blob = (out or "") + (err or "")
        if rc != 0 or not blob.strip():
            rc2, out2, err2 = s.run(
                f"{self.bin} version --format '{{{{.Client.Version}}}}'",
                timeout=timeout)
            blob2 = (out2 or "") + (err2 or "")
            if rc2 == 0 and blob2.strip():
                blob = blob2
            else:
                low = (blob + blob2).lower()
                if any(h in low for h in _ABSENT_HINTS):
                    self.status = STATUS_ABSENT
                    self.detail = "未检测到 docker 命令"
                elif any(h in low for h in _PERM_HINTS):
                    self.status = STATUS_PERM
                    self.detail = "当前用户无权访问 Docker"
                else:
                    self.status = STATUS_ABSENT
                    self.detail = (blob.strip().splitlines() or
                                   ["未检测到 docker 命令"])[0][:120]
                return self.info()

        self.version = _version_of(blob)
        ok, ver, blob = self._server_version(timeout)
        if ok:
            self.status = STATUS_OK
            self.needs_sudo = bool(self.sudo_mode)
            suffix = "（通过 sudo）" if self.sudo_mode else ""
            self.detail = f"服务端 {ver}{suffix}"
            return self.info()

        low = (blob or "").lower()
        if any(h in low for h in _PERM_HINTS):
            self.status = STATUS_PERM
            self.detail = ("权限不足：把用户加入 docker 组，或填写 sudo 密码"
                           if not self.sudo_mode else "sudo 也没能拿到访问权限")
            return self.info()
        if any(h in low for h in _DAEMON_HINTS):
            self.status = STATUS_DAEMON
            self.detail = "Docker 已安装，但守护进程没有运行"
            return self.info()

        # 命令在、info 失败但没有可识别的原因：按守护进程问题处理，把原文带上
        self.status = STATUS_DAEMON
        self.detail = ((blob or "").strip().splitlines() or
                       ["docker info 失败"])[0][:140]
        return self.info()

    def info(self) -> dict:
        return {"status": self.status, "version": self.version,
                "detail": self.detail, "sudo": self.needs_sudo}

    @property
    def ready(self) -> bool:
        return self.status == STATUS_OK

    def message(self) -> tuple[str, str]:
        """给界面用的一句话：(标题, 说明)"""
        if self.status == STATUS_OK:
            return "Docker 可用", self.detail
        if self.status == STATUS_ABSENT:
            return ("当前设备未安装 Docker",
                    "没找到 docker 命令。可以在设备上装 Docker Engine / "
                    "Docker Desktop，或确认 docker 在 PATH 里。")
        if self.status == STATUS_DAEMON:
            return ("Docker 已安装，但服务未运行",
                    f"{self.detail}。Linux 上试 `systemctl start docker`；"
                    "群晖在「套件中心」启动 Container Manager；"
                    "Windows / macOS 打开 Docker Desktop。")
        if self.status == STATUS_PERM:
            return ("无权访问 Docker",
                    f"{self.detail}。把当前用户加入 docker 组"
                    "（`sudo usermod -aG docker $USER` 后重新登录），"
                    "或在连接设置里填写 sudo 密码。")
        return "Docker 状态未知", self.detail or "还没有探测"

    # ------------------------------------------------------------ 执行
    def _cmd(self, args: str) -> str:
        """拼出真正要执行的命令行（含必要的 sudo 前缀与绝对路径）。"""
        base = f"{self.bin} {args}"
        if self.sudo_mode == "nopasswd":
            return "sudo -n " + base
        return base

    def _run(self, args: str, timeout: int = 60):
        cmd = self._cmd(args)
        if self.sudo_mode == "admin":
            return self.session.run_admin(cmd, timeout=timeout)
        return self.session.run_traced(cmd, timeout=timeout)

    def _require(self):
        if not self.ready:
            title, hint = self.message()
            raise DockerError(f"{title}：{hint}")

    # ------------------------------------------------------------ 列表
    def _list(self, sub: str, fields, parse, extra: str = "",
              timeout: int = 60) -> list[dict]:
        """取一份列表。

        先走**精简字段**（0.1 秒级），拿不到再退回逐行 JSON（慢，但兼容老版本
        docker 不支持自定义分隔符的情况）。群晖上 `{{json .}}` 实测 65 秒，
        用字段模板只要 0.1 秒 —— 同一个 daemon、同一批容器，差别全在字段上。
        """
        fmt = parsers.fmt_fields(fields)
        rc, out, err = self._run(f"{sub} {extra}--format {shlex.quote(fmt)}",
                                 timeout)
        if rc == 0 and (out or "").strip():
            rows = parse(out)
            if rows:
                return rows
        rc2, out2, err2 = self._run(
            f"{sub} {extra}--format '{{{{json .}}}}'", max(timeout, 120))
        if rc2 != 0:
            raise DockerError(_err_line(err2, out2) or _err_line(err, out))
        return parse(out2)

    def ps(self, all_: bool = True, timeout: int = 60) -> list[dict]:
        self._require()
        return self._list("ps", parsers.PS_FIELDS, parsers.docker_ps,
                          "-a " if all_ else "", timeout)

    def images(self, timeout: int = 60) -> list[dict]:
        self._require()
        return self._list("images", parsers.IMG_FIELDS, parsers.docker_images,
                          "", timeout)

    def stats(self, timeout: int = 60) -> list[dict]:
        self._require()
        return self._list("stats", parsers.STAT_FIELDS, parsers.docker_stats,
                          "--no-stream ", timeout)

    def df(self, timeout: int = 90) -> list[dict]:
        self._require()
        try:
            return self._list("system df", parsers.DF_FIELDS, parsers.docker_df,
                              "", timeout)
        except DockerError:
            return []

    def networks(self, timeout: int = 40) -> list[dict]:
        self._require()
        fields = ("ID", "Name", "Driver", "Scope")
        rows = []

        def parse(text):
            for o in parsers._records(text, fields):      # noqa: SLF001
                rows.append({"id": (o.get("ID") or "")[:12],
                             "name": o.get("Name", ""),
                             "driver": o.get("Driver", ""),
                             "scope": o.get("Scope", "")})
            return rows

        try:
            return self._list("network ls", fields, parse, "", timeout)
        except DockerError:
            return []

    def volumes(self, timeout: int = 40) -> list[dict]:
        self._require()
        fields = ("Name", "Driver", "Mountpoint")
        rows = []

        def parse(text):
            for o in parsers._records(text, fields):      # noqa: SLF001
                rows.append({"name": o.get("Name", ""),
                             "driver": o.get("Driver", ""),
                             "mount": o.get("Mountpoint", "")})
            return rows

        try:
            return self._list("volume ls", fields, parse, "", timeout)
        except DockerError:
            return []

    # ------------------------------------------------------------ 单个容器
    def inspect(self, cid: str, timeout: int = 40) -> str:
        self._require()
        rc, out, err = self._run(f"inspect {shlex.quote(cid)}", timeout)
        if rc != 0:
            raise DockerError(_err_line(err, out))
        return out

    def logs(self, cid: str, tail: int = 300, timeout: int = 45) -> str:
        self._require()
        rc, out, err = self._run(
            f"logs --tail {int(tail)} {shlex.quote(cid)} 2>&1", timeout)
        return out or err

    def action(self, verb: str, cid: str, timeout: int = 60) -> tuple[int, str, str]:
        """verb: start / stop / restart / pause / unpause / kill"""
        self._require()
        if verb not in ("start", "stop", "restart", "pause", "unpause", "kill"):
            raise DockerError(f"不支持的操作 {verb}")
        return self._run(f"{verb} {shlex.quote(cid)}", timeout)

    def remove(self, cid: str, force: bool = False, timeout: int = 60
               ) -> tuple[int, str, str]:
        self._require()
        return self._run(f"rm {'-f ' if force else ''}{shlex.quote(cid)}", timeout)

    def remove_image(self, image: str, force: bool = False, timeout: int = 90
                     ) -> tuple[int, str, str]:
        self._require()
        return self._run(f"rmi {'-f ' if force else ''}{shlex.quote(image)}", timeout)

    def pull(self, image: str, timeout: int = 600) -> tuple[int, str, str]:
        self._require()
        return self._run(f"pull {shlex.quote(image)}", timeout)

    def run_container(self, image: str, name: str = "", ports: str = "",
                      envs: str = "", volumes: str = "", restart: str = "",
                      extra: str = "", detach: bool = True,
                      timeout: int = 180) -> tuple[int, str, str]:
        """从镜像起一个新容器。参数都做白名单式转义，避免拼进奇怪的东西。"""
        self._require()
        if not image.strip():
            raise DockerError("镜像不能为空")
        parts = ["run"]
        if detach:
            parts.append("-d")
        if name.strip():
            parts += ["--name", shlex.quote(name.strip())]
        if restart.strip():
            parts += ["--restart", shlex.quote(restart.strip())]
        for kv in _split_items(ports):
            parts += ["-p", shlex.quote(kv)]
        for kv in _split_items(envs):
            parts += ["-e", shlex.quote(kv)]
        for kv in _split_items(volumes):
            parts += ["-v", shlex.quote(kv)]
        if extra.strip():
            parts.append(extra.strip())
        parts.append(shlex.quote(image.strip()))
        return self._run(" ".join(parts), timeout)


def _split_items(text: str) -> list[str]:
    out = []
    for part in (text or "").replace("；", ";").replace(",", ";").split(";"):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _version_of(blob: str) -> str:
    import re
    m = re.search(r"Docker version ([\w.\-]+)", blob)
    if m:
        return m.group(1)
    m = re.search(r"(\d+\.\d+\.\d+)", blob)
    return m.group(1) if m else ""


def _err_line(err: str, out: str = "") -> str:
    for src in (err, out):
        for ln in (src or "").splitlines():
            s = ln.strip()
            if s:
                return s[:220]
    return "docker 命令失败"


def quick_state(session, device=None) -> dict:
    """轻量探测，给界面初始化用（不拉列表）。"""
    eng = DockerEngine(session, device)
    try:
        return eng.detect()
    except Exception as e:  # noqa: BLE001
        return {"status": STATUS_UNKNOWN, "version": "", "detail": str(e),
                "sudo": False}
