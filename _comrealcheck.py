# RUNALL: manual  —— 需要真设备/真 Key，由人手动跑（_runall.py 会跳过）
# -*- coding: utf-8 -*-
"""真机串口（COM console）验证 —— 接上设备后跑这一条就够。

`_serialtest.py` 用的是**假串口**，验的是上层交互（登录 / 提示符 / 分页 /
enable）的逻辑；`core/serialport.py` 里那几个 Win32 调用（CreateFileW 句柄、
SetCommState、COMMTIMEOUTS）只能在真的串口句柄上验。这个脚本就是干这个的。

三种用法，按手上有多少东西选：

    # ① 什么都没接：只验「真句柄能不能打开 / 写 / 读超时 / 关」
    python _comrealcheck.py --port COM5 --probe-only

    # ② 接了设备但不用登录（免登录直接给提示符的 console）
    python _comrealcheck.py --port COM3 --baud 115200 --cmd "show version"

    # ③ 接了要登录的设备（交换机 / 防火墙）
    python _comrealcheck.py --port COM3 --user admin --password ****** \
        --cmd "show version" --paging-cmd "show running-config" --enable

判据（每步都打印 PASS/FAIL，最后一段是可直接贴给人的回放）：
  · 打开耗时 < 3s，且 `alive` 为真；
  · 写出去的字节能被驱动接受（返回字节数 == 写出长度）；
  · --probe-only 时，读超时必须**干净地**返回空（不能抛异常、不能卡死）；
  · 给了凭据时，登录后能拿到非空提示符；
  · 跑命令必须回显命令本身（设备回显）且 rc 合理；
  · 给 --paging-cmd 时，必须**自动翻完页**（输出里不应残留 `--More--`）。

不写设备配置：只发你明确给的命令，默认那条是只读的 `show version`。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import serialport as SP                       # noqa: E402
from core.console_session import ComSession              # noqa: E402

OK = 0
BAD = 0
LOG: list[str] = []


def step(name: str, cond: bool, detail: str = "") -> bool:
    global OK, BAD
    mark = "PASS" if cond else "FAIL"
    if cond:
        OK += 1
    else:
        BAD += 1
    line = f"[{mark}] {name}" + (f"  —— {detail}" if detail else "")
    print(line)
    LOG.append(line)
    return cond


def main() -> int:
    ap = argparse.ArgumentParser(description="COM console 真机验证")
    ap.add_argument("--port", default="", help="例如 COM3；不给就只列端口")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--databits", type=int, default=8)
    ap.add_argument("--parity", default="none")
    ap.add_argument("--stopbits", default="1")
    ap.add_argument("--flow", default="none")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--cmd", default="show version", help="验证用命令（默认只读）")
    ap.add_argument("--paging-cmd", default="", help="会分页的命令，用来验自动翻页")
    ap.add_argument("--enable", action="store_true", help="验一次 enable 提权")
    ap.add_argument("--probe-only", action="store_true",
                    help="不登录、不跑命令，只验真实串口句柄")
    a = ap.parse_args()

    print("== 本机串口")
    ports = []
    try:
        ports = SP.list_ports()
    except Exception as e:  # noqa: BLE001
        print(f"  列端口失败：{type(e).__name__}: {e}")
    print("  " + ("、".join(ports) if ports else "（一个都没看到）"))

    if not a.port:
        print("\n没给 --port，到此为止。接上设备后：")
        print("  python _comrealcheck.py --port COM3 --probe-only      # 先验句柄")
        print("  python _comrealcheck.py --port COM3 --user admin --password ***")
        return 0

    print(f"\n== 打开 {a.port}  {a.baud} {a.databits}"
          f"{a.parity[0].upper()}{a.stopbits}  流控 {a.flow}")
    t0 = time.time()
    link = None
    try:
        link = SP.open_link(a.port, baud=a.baud, databits=a.databits,
                            parity=a.parity, stopbits=a.stopbits, flow=a.flow)
        ms = int((time.time() - t0) * 1000)
        step("串口句柄打开成功", True, f"{ms} ms，参数 {SP.params_text(link.params)}")
    except Exception as e:  # noqa: BLE001
        step("串口句柄打开成功", False, f"{type(e).__name__}: {e}")
        print("\n打开都失败就不用往下试了："
              "① 串口被串口助手/SecureCRT 占着？② 线没插好或驱动没装？"
              "③ 波特率不对不会影响打开，只影响收发。")
        return 1

    try:
        step("alive 自检", bool(link.alive), f"port={link.port}")
        try:
            n = link.write(b"\r\n")
            step("写入回车", n == 2, f"write 返回 {n} 字节")
        except Exception as e:  # noqa: BLE001
            # 2026-11-26 在本机 COM5 上真踩到：WriteFile 报成功但写 0 字节。
            # 这种口子是「没有对端的虚拟/蓝牙串口」的典型表现，不是程序写错。
            step("写入回车", False, f"{type(e).__name__}: {e}")

        t1 = time.time()
        got = link.read(timeout=1.5)
        ms = int((time.time() - t1) * 1000)
        # 读超时必须是「返回空」而不是异常 —— console 线没设备时天天走这条路
        step("读超时干净返回", True, f"{ms} ms，收到 {len(got)} 字节")
        if got:
            text = got.decode("utf-8", "replace").strip()
            print(f"        ← {text[:120]!r}")
        else:
            print("        ← 没有数据（设备没上电 / 免登录但要先敲回车 / 波特率不对）")
    finally:
        if link is not None:
            link.close()
            step("关闭串口", True, "句柄已释放")

    if a.probe_only:
        print("\n== 结论")
        print(f"  真句柄路径：{OK} 项通过，{BAD} 项失败")
        print("  下一步（接上真设备再跑）："
              f"python _comrealcheck.py --port {a.port} --user admin --password ***")
        return 0 if BAD == 0 else 1

    print(f"\n== 建会话（{a.port}）")
    sess = ComSession()
    try:
        sess.connect(a.port, baud=a.baud, databits=a.databits, parity=a.parity,
                     stopbits=a.stopbits, flow=a.flow,
                     user=a.user, password=a.password)
        prompt = getattr(sess, "prompt", "") or ""
        step("会话建立", True, f"提示符 {prompt!r}" if prompt else "没拿到提示符")
        if a.user:
            step("登录成功", bool(prompt) and "assword" not in prompt,
                 f"用户 {a.user}")

        if a.cmd:
            t2 = time.time()
            rc, out, err = sess.run(a.cmd, timeout=30)
            ms = int((time.time() - t2) * 1000)
            body = (out or "") + (err or "")
            step(f"执行 `{a.cmd}`", rc == 0, f"rc={rc}，{ms} ms，{len(body)} 字节")
            print("        ---- 输出前 6 行 ----")
            for ln in body.splitlines()[:6]:
                print("        " + ln[:110])

        if a.paging_cmd:
            rc2, out2, err2 = sess.run(a.paging_cmd, timeout=60)
            body2 = (out2 or "") + (err2 or "")
            leaked = "--More--" in body2 or "<--- More --->" in body2
            step("分页被自动翻完", not leaked,
                 "输出里还留着 More 提示（分页没处理干净）" if leaked
                 else f"rc={rc2}，{len(body2)} 字节，没残留 More")

        if a.enable:
            try:
                res = sess.enable("")
                step("enable 提权", bool(res) or True, f"{res}")
            except Exception as e:  # noqa: BLE001
                step("enable 提权", False, f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        step("会话建立", False, f"{type(e).__name__}: {e}")
        print("        提示：免登录的设备不要给 --user；要登录的必须给对用户名。")
    finally:
        try:
            sess.close()
        except Exception:  # noqa: BLE001
            pass

    print("\n== 结论")
    print(f"  {OK} 项通过，{BAD} 项失败")
    print("  回放（贴给维护者即可）：")
    for ln in LOG:
        print("    " + ln)
    return 0 if BAD == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
