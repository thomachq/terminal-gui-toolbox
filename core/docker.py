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

import shlex

from core import parsers

STATUS_OK = "ok"
STATUS_ABSENT = "absent"
STATUS_DAEMON = "daemon-down"
STATUS_PERM = "no-permission"
STATUS_UNKNOWN = "unknown"

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

    # ------------------------------------------------------------ 探测
    def detect(self, timeout: int = 25) -> dict:
        s = self.session
        if s is None:
            self.status, self.detail = STATUS_ABSENT, "尚未连接设备"
            return self.info()

        rc, out, err = s.run("docker --version", timeout=timeout)
        blob = (out or "") + (err or "")
        if rc != 0 or not blob.strip():
            # Windows 上 docker 可能只在 PATH 的特定位置，再试一次 cmd 形式
            rc2, out2, err2 = s.run("docker version --format '{{.Client.Version}}'",
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
        rc, out, err = s.run("docker info --format '{{.ServerVersion}}'",
                                    timeout=timeout)
        blob = (out or "") + (err or "")
        if rc == 0 and out.strip():
            self.status = STATUS_OK
            self.detail = f"服务端 {out.strip()}"
            return self.info()

        low = blob.lower()
        if any(h in low for h in _PERM_HINTS):
            # 试试 sudo（有密码才试）
            ok, why = s.can_admin()
            if ok and getattr(s, "sudo_pw", None):
                rc2, out2, err2 = s.run_admin(
                    "docker info --format '{{.ServerVersion}}'", timeout=timeout)
                if rc2 == 0 and out2.strip():
                    self.needs_sudo = True
                    self.status = STATUS_OK
                    self.detail = f"服务端 {out2.strip()}（通过 sudo）"
                    return self.info()
            self.status = STATUS_PERM
            self.detail = "权限不足：把用户加入 docker 组，或填写 sudo 密码"
            return self.info()

        if any(h in low for h in _DAEMON_HINTS):
            self.status = STATUS_DAEMON
            self.detail = "Docker 已安装，但守护进程没有运行"
            return self.info()

        # 命令在、info 失败但没有可识别的原因：按守护进程问题处理，把原文带上
        self.status = STATUS_DAEMON
        self.detail = (blob.strip().splitlines() or ["docker info 失败"])[0][:140]
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
    def _run(self, args: str, timeout: int = 60):
        cmd = "docker " + args
        if self.needs_sudo:
            return self.session.run_admin(cmd, timeout=timeout)
        return self.session.run_traced(cmd, timeout=timeout)

    def _require(self):
        if not self.ready:
            title, hint = self.message()
            raise DockerError(f"{title}：{hint}")

    # ------------------------------------------------------------ 列表
    def ps(self, all_: bool = True, timeout: int = 60) -> list[dict]:
        self._require()
        args = "ps " + ("-a " if all_ else "") + "--format '{{json .}}'"
        rc, out, err = self._run(args, timeout)
        if rc != 0:
            raise DockerError(_err_line(err, out))
        return parsers.docker_ps(out)

    def images(self, timeout: int = 60) -> list[dict]:
        self._require()
        rc, out, err = self._run("images --format '{{json .}}'", timeout)
        if rc != 0:
            raise DockerError(_err_line(err, out))
        return parsers.docker_images(out)

    def stats(self, timeout: int = 40) -> list[dict]:
        self._require()
        rc, out, err = self._run("stats --no-stream --format '{{json .}}'", timeout)
        if rc != 0:
            raise DockerError(_err_line(err, out))
        return parsers.docker_stats(out)

    def df(self, timeout: int = 40) -> list[dict]:
        self._require()
        rc, out, err = self._run("system df --format '{{json .}}'", timeout)
        if rc != 0:
            return []
        return parsers.docker_df(out)

    def networks(self, timeout: int = 40) -> list[dict]:
        self._require()
        rc, out, err = self._run("network ls --format '{{json .}}'", timeout)
        if rc != 0:
            return []
        rows = []
        for o in parsers._json_lines(out):          # noqa: SLF001
            rows.append({"id": (o.get("ID") or "")[:12], "name": o.get("Name", ""),
                         "driver": o.get("Driver", ""), "scope": o.get("Scope", "")})
        return rows

    def volumes(self, timeout: int = 40) -> list[dict]:
        self._require()
        rc, out, err = self._run("volume ls --format '{{json .}}'", timeout)
        if rc != 0:
            return []
        rows = []
        for o in parsers._json_lines(out):          # noqa: SLF001
            rows.append({"name": o.get("Name", ""), "driver": o.get("Driver", ""),
                         "mount": o.get("Mountpoint", "")})
        return rows

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
