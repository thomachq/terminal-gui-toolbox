# -*- coding: utf-8 -*-
"""持久 CLI 通道的公共行为：发一条命令、把输出收回来、翻过分页、进特权。

为什么单独抽出来：设备认的是**设备类型**，不是**协议**。
交换机 / 路由器既可能挂 Telnet 也可能开 SSH，Linux 服务器同样
（有些老设备只留 telnet，有些交换机只开了 SSH）。所以「进配置视图」
这套逻辑必须与传输层解耦，否则就会出现「SSH 连交换机只能只读」
这种把协议当设备类型用的错误结论。

子类只管搬运字节：
  · _send(text)      发一段文本
  · _recv_once(wait) 收一包，没数据返回 ""
  · alive()          连接是否还活着
  · close()
本类负责「安静判定 / 自动翻页 / 记忆提示符 / enable / sudo」。
"""
from __future__ import annotations

import re
import shlex
import time

# 分页提示：Cisco "<--- More --->"，H3C/华为 "--More--"，部分老设备 "---- More ----"
_MORE = re.compile(r"(?:<---\s*More\s*--->|--\s*More\s*--|----\s*More\s*----"
                   r"|--More--|more\s*--\s*|\(q\)uit)", re.I)

_PASSWORD = re.compile(
    # 口令提示的几种写法：`Password:`、`[sudo] password for ops:`、
    # `密码：`。**中间那个「for 用户名」必须吃掉** —— sudo 默认就是这么打印的
    # （`[sudo] password for cloudadmin: `），早先的正则只认 password 紧跟冒号，
    # 于是密码永远发不出去：sudo 干等你、我们干等回显，最后把 `id -u`
    # 当密码喂了进去，表现为「勾了自动提权、终端还是普通用户」。
    r"(?:(?:password|passwd)\s*(?:for\s+[^\s:：]+)?|密码)\s*[:：]", re.I)

_PROMPT_TAIL = re.compile(r"[#>\]$]\s*$")

# sudo 拒绝的几种回显：密码错、不在 sudoers、需要终端
_SUDO_BAD = re.compile(
    r"(sorry,\s*(try again|a password is required)|incorrect password|"
    r"not in the sudoers|is not allowed to run sudo|no password was provided|"
    r"a terminal is required)", re.I)

DEFAULT_ENCODINGS = ("utf-8", "gbk", "latin-1")


def _tail(text: str, limit: int = 180) -> str:
    """把回显压成一句能塞进日志的话（只留最后两行非空内容）。

    失败原因必须**原文**报给用户：`not in the sudoers file` 和
    `Sorry, try again` 是两件完全不同的事，笼统说「提权失败」等于没说。
    """
    lines = [x.strip() for x in re.split(r"[\r\n]+", text or "") if x.strip()]
    s = " ／ ".join(lines[-2:]) if lines else ""
    return s[-limit:]


def _last_int(text: str) -> str:
    """回显里最后一个「整行只有数字」的内容（`id -u` 的结果）。"""
    got = re.findall(r"(?m)^\s*(\d+)\s*$", text or "")
    return got[-1] if got else ""


class CLIError(Exception):
    pass


class CLIClient:
    """一条持久 CLI。command() 是串行的：同一时刻只跑一条命令。"""

    quiet_default = 0.6

    def __init__(self) -> None:
        self.encoding = "utf-8"
        self.buf = ""
        self.last_prompt = ""
        # 上一次提权失败的原因（原文）。空字符串表示没失败过 / 最后一次成功。
        # 上层连接流程会把它写进日志与状态栏 —— 只返回 True/False 的话，
        # 用户永远只能看到「提权未生效」，猜不到是密码错还是没权限。
        self.last_error = ""

    # ------------------------------------------------------------ 子类实现
    def _send(self, text: str) -> None:
        raise NotImplementedError

    def _recv_once(self, wait: float = 0.35) -> str:
        raise NotImplementedError

    def alive(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------ 解码
    def _decode(self, raw: bytes) -> str:
        """先按当前编码解，出现替换字符就换一种试（设备常见 UTF-8 / GBK 混用）。"""
        for enc in (self.encoding,) + DEFAULT_ENCODINGS:
            try:
                text = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            if "\ufffd" not in text:
                self.encoding = enc
                return text
        return raw.decode(self.encoding, "replace")

    # ------------------------------------------------------------ 收包
    def _drain(self, deadline: float, quiet: float = 0.6) -> str:
        """收到「安静一段时间」为止，同时自动翻过所有分页提示。"""
        parts: list[str] = []
        last = time.time()
        while time.time() < deadline:
            chunk = self._recv_once(0.35)
            if chunk:
                parts.append(chunk)
                last = time.time()
                if _MORE.search(chunk):
                    self._send(" ")          # 翻页
                    continue
            elif parts and time.time() - last >= quiet:
                break
            elif not parts and time.time() - last >= max(quiet, 2.0):
                break
        return "".join(parts)

    def _remember_prompt(self, text: str) -> None:
        """把最后一行的提示符记下来（"SW1#"、"R1>" 这种）。"""
        for line in reversed((text or "").splitlines()):
            s = line.strip()
            if s and len(s) <= 96 and _PROMPT_TAIL.search(s):
                self.last_prompt = s
                return

    # ------------------------------------------------------------ 命令
    def command(self, cmd: str, timeout: int = 30, quiet: float = 0.6) -> str:
        """发一条命令并收回输出。

        用「安静判定」而不是硬等提示符：网络设备的提示符五花八门
        （带主机名、带模块号、带 `(config)`），靠正则匹配一定会漏。
        安静 0.6 秒且已有内容，就认为这条命令跑完了。
        """
        if not self.alive():
            raise CLIError("尚未连接")
        self._send(cmd + "\r\n")
        text = self._drain(time.time() + max(timeout, 3), quiet=quiet)
        self._remember_prompt(text)
        return text

    def send_line(self, text: str) -> None:
        """只发不收 —— 给交互式终端用（比如 conf t 之后一条条敲）。"""
        self._send(text + "\r\n")

    def interactive(self, cmd: str, answers: list[str], timeout: int = 60) -> str:
        """按提示依次回答。给那些会连问几遍的交互命令用
        （`passwd`、`copy running-config startup-config` 之类）。"""
        out = self.command(cmd, timeout=max(8, min(timeout, 20)))
        for ans in answers:
            if not _PASSWORD.search(out) and "?" not in out:
                break
            self._send(ans + "\r\n")
            out += self._drain(time.time() + 12, quiet=0.6)
        self._remember_prompt(out)
        return out

    # ------------------------------------------------------------ 特权模式
    def enable(self, password: str = "", timeout: int = 15) -> bool:
        """进 enable 模式（Cisco `enable`，H3C/华为 `super`、`system-view` 另说）。

        判定标准是最后的提示符里出现 `#` —— Cisco / H3C / 华为的特权模式
        全都是 `#`，用户模式是 `>`。这比解析回显文字可靠。
        失败原因同样落到 `self.last_error`。
        """
        self.last_error = ""
        if not self.alive():
            self.last_error = "连接已断开"
            return False
        self._send("enable\r\n")
        out = self._drain(time.time() + 6, quiet=0.5)
        if _PASSWORD.search(out) and password:
            self._send(password + "\r\n")
            out += self._drain(time.time() + 8, quiet=0.6)
        self._remember_prompt(out)
        if "#" in (self.last_prompt or ""):
            return True
        self.last_error = _tail(out) or "提示符没有变成 #（密码不对或权限不足）"
        return False

    def disable_paging(self, vendor: str = "") -> None:
        """关掉分页。不关的话 `display current-configuration` 会一直翻页。"""
        cmds = {
            "cisco": ["terminal length 0"],
            "h3c": ["screen-length disable"],
            "huawei": ["screen-length 0 temporary"],
        }.get(vendor, ["terminal length 0", "screen-length disable",
                       "screen-length 0 temporary"])
        for c in cmds:
            try:
                out = self.command(c, timeout=8)
            except CLIError:
                return
            # 认不出的命令不用再试剩下的，避免日志里一串报错
            if re.search(r"(%\s*invalid|unrecognized|unavailable|too many)",
                         out or "", re.I):
                return

    # ------------------------------------------------------------ sudo
    def run_sudo(self, cmd: str, password: str,
                 timeout: int = 60) -> tuple[bool, str]:
        """在有 sudo 的机器上跑一条需要 root 的命令。

        密码走标准输入（`sudo -S`），不进命令行、不进进程列表。提示语是
        我们自己指定的 `Password:`，这样既能判断「要不要给密码」，
        又不会因为没配 NOPASSWD 而把密码当命令敲进 shell。
        """
        if not password:
            raise CLIError("需要 sudo 密码")
        full = (f"sudo -S -p 'Password: ' sh -c {shlex.quote(cmd)}")
        self._send(full + "\r\n")
        head = self._drain(time.time() + 3.5, quiet=0.35)
        if _PASSWORD.search(head):
            self._send(password + "\r\n")
        out = head + self._drain(time.time() + max(timeout, 5),
                                 quiet=self.quiet_default)
        self._remember_prompt(out)
        return (not _SUDO_BAD.search(out)), out

    def sudo_check(self, password: str) -> bool:
        """验密码 + 顺手刷新 sudo 票据（默认 15 分钟）。"""
        ok, _ = self.run_sudo("true", password, timeout=20)
        return ok

    def sudo_i(self, password: str, timeout: int = 25) -> bool:
        """真的执行 `sudo -i`，进 root shell；之后所有命令都在这个 shell 里跑。

        与 `run_sudo` 的分工：那个是「一条命令临时 sudo」，每条都要重新过一遍
        密码提示；这个是**一次性进入 root**，和在终端里手敲 `sudo -i` 同义 ——
        提示符变成 `#`，之后不再重复提权。

        判定不靠提示符：有的发行版 root 也用 `$`（群晖的 `sudo -i` 之后就是
        `root@xx:~#`，但别家不一定），看走眼就把普通用户当 root。
        进完跑一次 `id -u`，输出是 0 才算数。

        失败原因写进 `self.last_error`，供上层原样报给用户。
        """
        self.last_error = ""
        if not self.alive():
            self.last_error = "连接已断开，开不了 root shell"
            return False
        if not password:
            self.last_error = "没有可用的 sudo 密码"
            return False
        self._send("sudo -i\r\n")
        head = self._drain(time.time() + 5, quiet=0.45)
        if _PASSWORD.search(head):
            self._send(password + "\r\n")
            head += self._drain(time.time() + max(timeout - 5, 6), quiet=0.6)
        # 密码错 / 不在 sudoers / 要 tty：这三种都不能假装成功
        if _SUDO_BAD.search(head):
            self.last_error = _tail(head) or "sudo 拒绝了这次提权"
            return False
        # 密码对了也可能仍停在原 shell（sudoers 只放行了部分命令），落一锤
        self._send("id -u\r\n")
        out = self._drain(time.time() + 8, quiet=0.5)
        self._remember_prompt(out)
        uid = _last_int(out)
        if uid == "0":
            return True
        # 到这里说明 sudo -i 的回显没问题，但人没变成 root。
        # 常见两种情况：sudoers 只放行了部分命令、或者 ^C 打断了登录 shell。
        self.last_error = (f"sudo -i 之后 id -u = {uid or '读不到'}"
                           f"（没真的变成 root）")
        return False
