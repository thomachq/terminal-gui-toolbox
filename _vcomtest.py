# -*- coding: utf-8 -*-
"""COM console 的「虚拟设备」整链路测试（不需要真交换机）。

为什么要有它：`_serialtest.py` 验的是解析与协议细节，用的是"按脚本回话"的
假串口；`_comrealcheck.py` 验的是真句柄（本机 COM1 已通过），但**设备应答**
那一半一直空着（R-010）。这里补上：一个会登录、会分页、会进 enable 的虚拟
交换机 + 一条注入的串口链路，**跑的是产品代码**（`ComSession` → `ComClient`
→ `_drain` 的自动翻页），不是另写一份。

它证明什么、不证明什么：
  · 证明：登录提示识别、命令回显与安静判定、`--More--` 自动翻页、`enable`
    提示符变 `#`、密码错时不崩 —— 这几条在真设备上就是这样跑的。
  · 不证明：真串口的时序/流控/线序（那由 `_comrealcheck.py --probe-only` 验），
    也不代表某个厂商的私有提示符一定认得出。

    python _vcomtest.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import console_session as CS        # noqa: E402
from core import serialport                   # noqa: E402

OK = 0
BAD = 0


def chk(cond, msg, extra=None):
    global OK, BAD
    if cond:
        OK += 1
        print(f"  [ok] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))
    else:
        BAD += 1
        print(f"  [NG] {msg}" + (f"  —— {extra}" if extra not in (None, "") else ""))


def sec(t):
    print(f"\n-- {t}")


class VirtualSwitch:
    """一台只会三件事的交换机：登录、给命令输出、分页。

    回显也照真设备来（console 上敲什么它就显什么），否则「命令回显」这类
    断言在虚拟环境里永远是假的绿。
    """

    def __init__(self, user="admin", password="pw", enable_pw="en"):
        self.user = user
        self.password = password
        self.enable_pw = enable_pw
        self.state = "user"          # user / pass / cli / enpass
        self.priv = ">"
        self.line = ""
        self.pending: list[str] = []
        self.page_left: list[str] = []
        self.written = 0
        self.push("\r\nCisco IOS Software, Virtual Switch\r\n")
        self.push("User Access Verification\r\n\r\nUsername: ")

    # -- 设备侧输出
    def push(self, s: str) -> None:
        if s:
            self.pending.append(s)

    def read(self, timeout: float = 0.35) -> bytes:
        if self.pending:
            data = "".join(self.pending)
            self.pending.clear()
            return data.encode("utf-8")
        time.sleep(min(max(float(timeout), 0.01), 0.05))
        return b""

    # -- 设备侧收数据
    def write(self, data: bytes) -> int:
        text = data.decode("utf-8", "replace")
        self.written += len(data)
        prev_cr = False
        for ch in text:
            if ch == "\r":
                line, self.line = self.line, ""
                self.push("\r\n")
                self.on_line(line)
                prev_cr = True
                continue
            if ch == "\n":
                # `\r\n` 是**一个**换行。早先这里把两个字符各当一次回车，
                # 于是「用户名」后面紧跟一个空行，被当成第二次输入 ——
                # 设备一路回 Login invalid，测试里看着像「密码没发出去」。
                if prev_cr:
                    prev_cr = False
                    continue
                line, self.line = self.line, ""
                self.push("\r\n")
                self.on_line(line)
                continue
            prev_cr = False
            if ch == " " and self.page_left:
                self.next_page()          # 分页状态下的空格 = 下一页
            else:
                self.line += ch
                self.push(ch)             # 回显
        return len(data)

    # -- 行为
    def on_line(self, line: str) -> None:
        if self.state == "user":
            if line.strip() == self.user:
                self.state = "pass"
                self.push("Password: ")
            else:
                self.push("Login invalid\r\n\r\nUsername: ")
            return
        if self.state == "pass":
            if line == self.password:
                self.state = "cli"
                self.push(f"SW1{self.priv} ")
            else:
                self.state = "user"
                self.push("Login invalid\r\n\r\nUsername: ")
            return
        if self.state == "enpass":
            if line == self.enable_pw:
                self.priv = "#"
                self.state = "cli"
                self.push("SW1# ")
            else:
                self.state = "cli"
                self.push("% Bad secrets\r\nSW1> ")
            return
        cmd = line.strip()
        if not cmd:
            self.push(f"SW1{self.priv} ")
        elif cmd == "show version":
            self.push("Cisco IOS XE Software, Version 17.06.03\r\n"
                      "System image file is \"bootflash:packages.conf\"\r\n"
                      "Switch uptime is 3 days, 4 hours\r\n")
            self.push(f"SW1{self.priv} ")
        elif cmd == "show running-config":
            self.page_left = [f"interface GigabitEthernet1/0/{i}\r\n"
                              f" description port-{i}\r\n" for i in range(1, 37)]
            self.page()
        elif cmd == "enable":
            self.state = "enpass"
            self.push("Password: ")
        else:
            self.push(f"% Invalid input detected: {cmd}\r\nSW1{self.priv} ")

    def page(self, size: int = 12) -> None:
        chunk, self.page_left = self.page_left[:size], self.page_left[size:]
        for s in chunk:
            self.push(s)
        if self.page_left:
            self.push("--More--")
        else:
            self.push(f"SW1{self.priv} ")

    def next_page(self) -> None:
        self.push("\r" + " " * 12 + "\r")
        self.page()


class FakeLink:
    """注入给 ComClient 的串口链路。接口与 `serialport.SerialLink` 一致。"""

    def __init__(self, dev: VirtualSwitch):
        self.dev = dev
        self.port = "COM-VIRT"
        self.params = serialport.norm_params(115200)
        self.alive = True
        self.closed = False

    def write(self, data: bytes) -> int:
        if not self.alive:
            raise serialport.SerialError("串口已关闭")
        return self.dev.write(data)

    def read(self, timeout: float = 0.35) -> bytes:
        if not self.alive:
            return b""
        return self.dev.read(timeout)

    def close(self) -> None:
        self.alive = False
        self.closed = True


def make(login_pw="pw", enable_pw="en", user="admin"):
    dev = VirtualSwitch(user=user, password=login_pw, enable_pw=enable_pw)
    link = FakeLink(dev)
    sess = CS.ComSession()
    sess.connect("COM-VIRT", user=user, password=login_pw, link=link)
    return dev, link, sess


# ------------------------------------------------------------------ 用例
sec("1. 登录：提示符识别")
dev, link, sess = make()
chk(sess.alive, "虚拟链路连上了")
chk("COM-VIRT" in (sess.label or ""), "会话标签带端口名", sess.label)
prompt = getattr(sess._impl, "last_prompt", "")
chk(prompt.endswith(">"), "登录后认出用户模式提示符", prompt)

sec("2. 命令：回显 + 输出齐全")
rc, out, err = sess.run("show version")
chk(rc == 0, "rc=0")
chk("show version" in out, "设备回显了命令本身")
chk("17.06.03" in out, "拿到了版本号")
chk("uptime" in out, "拿到了运行时长")
chk(err == "", "stderr 为空")

sec("3. 分页：--More-- 被自动翻完")
rc, out, err = sess.run("show running-config", timeout=30)
# 注意：`--More--` 这几个字会留在输出里（客户端收到就 append，真设备也一样），
# 所以不能断言「输出里没有 More」——那是把正常现象当失败。真正要验的是
# ①确实遇到过分页（否则这条用例没验到东西）②内容一直翻到底、一条不少。
chk(out.count("--More--") >= 1, "确实遇到过分页提示",
    f"{out.count('--More--')} 次")
chk("port-36" in out, "最后一页也收到了（真的翻到底）")
chk(out.count("interface GigabitEthernet1/0/") == 36, "36 个接口一条不少",
    out.count("interface GigabitEthernet1/0/"))
# 36 个接口 / 每页 12 个 = 3 页 → 页与页之间 2 次分页。
chk(out.count("--More--") == 2, "3 页之间有 2 次分页 —— 每页都是靠空格翻过去的",
    f"{out.count('--More--')} 次")

sec("4. enable：提示符从 > 变 #")
ok = sess._impl.enable("en")
chk(ok is True, "enable 成功")
chk("#" in (sess._impl.last_prompt or ""), "提示符变成特权模式",
    sess._impl.last_prompt)

sec("5. enable 密码错：不崩、给出原因、还能继续用")
dev2 = VirtualSwitch(password="pw", enable_pw="right")
sess2 = CS.ComSession()
sess2.connect("COM-VIRT", user="admin", password="pw", link=FakeLink(dev2))
ok2 = sess2._impl.enable("wrong")
chk(ok2 is False, "错密码时 enable 返回 False")
chk(bool(sess2._impl.last_error), "给了人话原因", sess2._impl.last_error)
_rc, out2, _e = sess2.run("show version")
chk("17.06.03" in out2, "失败之后还能继续执行命令（没把会话玩坏）")

sec("6. 登录密码错：不抛异常，把人留在登录提示")
dev3 = VirtualSwitch(password="right")
sess3 = CS.ComSession()
try:
    sess3.connect("COM-VIRT", user="admin", password="wrong", link=FakeLink(dev3))
    raised = ""
except Exception as e:  # noqa: BLE001
    raised = f"{type(e).__name__}: {e}"
chk(not raised, "登录密码错不抛异常（真设备上也只会回到 Username）", raised)
banner3 = getattr(sess3._impl, "banner", "") if sess3._impl else ""
chk("Login invalid" in banner3 or "Username" in banner3,
    "回到登录提示（不是卡死等超时）", (banner3 or "")[-40:])

sec("7. 关闭：链路释放")
link.close()
chk(link.closed and not link.alive, "close 之后 alive=False")
chk(sess.alive is False, "会话的 alive 跟着变 false")

print(f"\n合计 {OK + BAD} 项：通过 {OK}，失败 {BAD}")
sys.exit(1 if BAD else 0)
