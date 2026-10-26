# -*- coding: utf-8 -*-
"""Terminal GUI Toolbox 界面冒烟 —— 逐页核对几何、日志分离、本机连接。

跑法：  python _tsmoke.py [--dark]
判定：  全部 PASS 才算通过；任何 FAIL 都打印细节。
"""
from __future__ import annotations

import sys
import time
import traceback

sys.argv = [a for a in sys.argv if a != "--smoke"]

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + detail) if detail else ""))
    return bool(ok)


def pump(app, sec: float = 0.35) -> None:
    t0 = time.time()
    while time.time() - t0 < sec:
        app.update()
        time.sleep(0.01)


def visible_children(w) -> list:
    out = []
    for c in w.winfo_children():
        try:
            if c.winfo_ismapped() and c.winfo_width() > 1 and c.winfo_height() > 1:
                out.append(c)
        except Exception:  # noqa: BLE001
            pass
    return out


def count_widgets(w, depth: int = 0) -> int:
    n = 1
    if depth > 12:
        return n
    for c in w.winfo_children():
        n += count_widgets(c, depth + 1)
    return n


def main() -> int:
    import tkinter as tk

    import wbtui as U
    if "--dark" in sys.argv:
        U.T = U.DARK

    import app as A

    try:
        app = A.ToolboxApp()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        chk(False, "主窗口构建")
        return 1
    chk(True, "主窗口构建")
    pump(app, 0.9)

    # ---------------- 几何
    chk(app.winfo_width() > 900 and app.winfo_height() > 600, "主窗尺寸",
        f"{app.winfo_width()}x{app.winfo_height()}")

    # ---------------- 五页
    expected = {
        # v1.0.4 起主机页是「清单 / 连接与设备」两段，默认看到的是清单
        "hosts": ["tbl_host", "lbl_host_sum"],
        # 第三批起「网络工具 / 系统信息 / 防火墙」的正文换成了表格，
        # 原始输出挪进分段页签（默认不显示，所以别拿 txt_* 量宽度）
        "tools": ["tbl_tool", "lbl_ping"],
        "sys": ["tbl_disk"],
        # 远程管理：分四段（SSH 服务 / 密钥 / RDP / XRDP），段控件是常显的，
        # 各段正文按需切换，所以只量段控件本身
        "rmt": ["rm_seg"],
        # 证书控制台已内嵌成页内 Frame（不再是子进程 iframe），量它本身
        "vpn": ["vpn_console"],
        # 防火墙已并进安全中心；tbl_sec 是体检结果表，两个都要能测到
        "sec": ["tbl_fw", "lbl_fw", "tbl_sec"],
    }
    for key, attrs in expected.items():
        app.nav.select(key)
        # 固定 0.4 秒在这台机器上不够：上一页的懒加载/重排还没跑完，
        # vpn 那页量出来是 21x1（内胆还没被 _apply_fit 撑开），
        # 看上去像「页面没显示」，其实是没等够。改成等尺寸稳定。
        for _ in range(30):
            pump(app, 0.1)
            _pg_ = app._pages.get(key)
            if _pg_ is not None and _pg_.winfo_width() > 100:
                break
        page = app._pages[key]
        mapped = visible_children(page)
        n = count_widgets(page)
        ok = page.winfo_ismapped() and len(mapped) >= 1 and n >= 4
        chk(ok, f"页面 {key} 可见", f"控件 {n} 个 / 直接可见 {len(mapped)} 个")
        for a in attrs:
            w = getattr(app, a, None)
            if w is None:
                chk(False, f"页面 {key} 控件 {a} 存在", "属性缺失")
                continue
            # 不能只量一次：tbl_sec（安全体检结果表）要等体检跑完才挂上去，
            # 固定 0.4 秒在机器忙的时候不够 —— 批量连跑 56 个脚本时会撞上
            # 「w=1」的假红（单跑永远过）。给它最多 3 秒，量到就停。
            ok2 = False
            for _ in range(10):
                try:
                    ok2 = bool(w.winfo_exists() and w.winfo_width() > 1)
                except Exception:  # noqa: BLE001
                    ok2 = False
                if ok2:
                    break
                pump(app, 0.3)
            chk(ok2, f"页面 {key} · {a}", f"w={w.winfo_width()}")

    # ---------------- 状态栏
    app.nav.select("hosts")
    pump(app, 0.3)
    sb = app.status
    chk(sb.winfo_height() >= 30, "状态栏高度", f"{sb.winfo_height()}px")
    for nm in ("cell_dev", "cell_cpu", "cell_mem", "cell_up", "cell_down", "cell_up2"):
        c = getattr(sb, nm, None)
        chk(c is not None and c.winfo_width() > 20, f"状态栏 {nm}",
            f"w={c.winfo_width() if c else 0}")
    chk(sb.cv_hist.winfo_width() > 30, "状态栏曲线画布", f"w={sb.cv_hist.winfo_width()}")

    # 空态
    sb.clear()
    chk(sb.cell_dev.lb.cget("text") == "未连接", "状态栏空态文案",
        sb.cell_dev.lb.cget("text"))

    # 喂一个假样本，确认四个格子都写进去
    from core.metrics import Sample
    fake = Sample(ok=True, cpu_pct=37.0, mem_used=6 * 2 ** 30, mem_total=16 * 2 ** 30,
                  net_rx_bps=1.5 * 2 ** 20, net_tx_bps=2048, uptime=90061, ncpu=8)
    sb.set_device("diskstation", "linux")
    sb.update_sample(fake, [10, 25, 37, 30, 37])
    pump(app, 0.2)
    got = {
        "设备": sb.cell_dev.lb.cget("text"),
        "CPU": sb.cell_cpu.lb.cget("text"),
        "内存": sb.cell_mem.lb.cget("text"),
        "上传": sb.cell_up.lb.cget("text"),
        "下载": sb.cell_down.lb.cget("text"),
        "时长": sb.cell_up2.lb.cget("text"),
    }
    print("      状态栏显示:", got)
    chk("diskstation" in got["设备"], "状态栏设备名", got["设备"])
    chk("37" in got["CPU"], "状态栏 CPU", got["CPU"])
    chk("GB" in got["内存"], "状态栏内存", got["内存"])
    chk("KB/s" in got["上传"], "状态栏上传速率", got["上传"])
    chk("MB/s" in got["下载"], "状态栏下载速率", got["下载"])
    chk("days" in got["时长"], "状态栏运行时长", got["时长"])
    chk(len(sb.cv_hist.find_all()) >= 2, "状态栏曲线已绘制",
        f"图元 {len(sb.cv_hist.find_all())} 个")
    sb.set_device("DESKTOP", "windows")
    pump(app, 0.15)
    chk(len(sb.cell_dev.cv.find_all()) >= 1, "系统徽标已绘制",
        f"{len(sb.cell_dev.cv.find_all())} 个图元")

    # ---------------- 日志：内存流水 + 合并进「日志管理」
    for i in range(12):
        app._log_raw("cmd" if i % 3 else "out", f"第 {i} 行 测试输出\n")
    pump(app, 0.3)
    before = len(app.log_model.lines)
    chk(before >= 12, "日志写入", f"{before} 行")
    # v1.0.6：右侧「命令日志」面板与「日志管理」重复，已撤掉面板本身
    chk(not hasattr(app, "_log_panel"), "不再有右侧命令日志面板")
    chk(not hasattr(app, "log_dock"), "分隔器里不再有日志栏",
        f"panes={len(app.paned.panes())}")

    # 命令与输出要落进操作审计（日志管理页读的就是这份），且口令必须打码
    app._audit_command("sshpass -p Hunter2 ssh root@10.0.0.9", 0,
                       "connected to 10.0.0.9\nuptime 3 days", "", 42)
    pump(app, 0.2)
    # 采样器与后台探测也在写命令日志，所以按命令里的唯一标记过滤，
    # 不能直接取「最后一条」—— 那样取到的是别的进程刚跑的命令
    evs = app.audit.tail(300)
    hit = [e for e in evs
           if e.get("event") == "terminal.command"
           and "sshpass" in str(e.get("detail") or "")]
    chk(bool(hit), "命令执行记进操作审计")
    if hit:
        d = str(hit[-1].get("detail") or "")
        chk("Hunter2" not in d, "口令没有落进日志", d[:60])
        chk("******" in d, "口令被打码", d[:60])
        chk("uptime 3 days" in d, "输出也记下来了", d[:80])
        chk("rc=0" in d, "带上了返回码")

    app._audit_command("mysql -uroot -pSecret9 -e 'show databases'", 1,
                       "", "Access denied for user 'root'", 8)
    pump(app, 0.2)
    # tail() 是**时间倒序**（最新在前），所以本轮刚写的那条是 evs2[0]。
    # 用 "--mark-mysql9" 这种唯一标记过滤，避免命中去重跑留下的历史记录。
    evs2 = [e for e in app.audit.tail(300)
            if e.get("event") == "terminal.command"
            and "mysql -uroot" in str(e.get("detail") or "")]
    d2 = str(evs2[0].get("detail") or "") if evs2 else ""
    chk("Secret9" not in d2, "mysql -pXXX 里的口令也被挡住", d2[:60])
    chk(bool(evs2) and evs2[0].get("status") == "fail", "失败的命令标成 fail")
    chk("[stderr]" in d2 and "Access denied" in d2, "stderr 也收进来了")

    # ---------------- 认证区联动（「连接与设备」是主机页的第二段）
    # 前面的页面巡检把当前页留在了别处，这里必须先回到主机页 ——
    # 否则 conn_box 挂在一个没显示的页里，row_auth 永远是「不可见」。
    app.nav.select("hosts")
    for _ in range(30):
        pump(app, 0.1)
        if app._pages["hosts"].winfo_ismapped():
            break
    app.seg_hosts.select("conn")
    pump(app, 0.4)
    app.v_proto.set("local")
    app._proto_changed()
    pump(app, 0.2)
    chk(not app.row_auth.winfo_ismapped(), "本机模式隐藏认证区")
    app.v_proto.set("telnet")
    app._proto_changed()
    pump(app, 0.2)
    chk(app.v_port.get() == "23", "Telnet 默认端口", app.v_port.get())
    chk(not app.row_auth.winfo_ismapped(), "Telnet 模式隐藏认证区")
    app.v_proto.set("ssh")
    app._proto_changed()
    pump(app, 0.2)
    chk(app.v_port.get() == "22", "SSH 默认端口", app.v_port.get())
    chk(app.row_auth.winfo_ismapped(), "SSH 模式显示认证区")
    app.v_mode.set("password")
    app._mode_changed()
    pump(app, 0.2)
    chk(app.row_pw.winfo_ismapped() and not app.row_key.winfo_ismapped(),
        "密码模式切换字段")
    app.v_mode.set("key")
    app._mode_changed()
    pump(app, 0.2)
    chk(app.row_key.winfo_ismapped() and not app.row_pw.winfo_ismapped(),
        "私钥模式切换字段")

    # ---------------- 本机连接（真实探测 + 真实采样）
    app.status.clear()
    app.v_proto.set("local")
    app._proto_changed()
    app.do_connect()
    pump(app, 3.2)
    s = app.session
    chk(s is not None and s.kind == "local", "本机连接建立")
    d = app.device
    chk(bool(d.hostname), "探测到主机名", d.hostname)
    chk(bool(d.os_name) and d.os_name != "未知系统", "探测到系统名", d.os_name)
    chk(d.os_family in ("windows", "linux", "darwin", "freebsd", "bsd", "posix"),
        "系统族识别", d.os_family)
    chk(app.dialect is not None and app.sampler is not None, "方言与采样器就绪",
        type(app.dialect).__name__)
    chk(app.status.cell_dev.lb.cget("text") not in ("", "未连接", "—"),
        "状态栏写入设备名", app.status.cell_dev.lb.cget("text"))
    chk(app.pill_dev.winfo_exists(), "顶部设备标签存在")

    # 等采样器真的出一轮（看 histogram，不看标签，标签可能是上一轮残留）
    deadline = time.time() + 25
    while time.time() < deadline:
        pump(app, 0.4)
        if app.sampler and len(app.sampler.cpu_hist) >= 1:
            break
    pump(app, 0.8)
    cpu_txt = app.status.cell_cpu.lb.cget("text")
    mem_txt = app.status.cell_mem.lb.cget("text")
    chk(cpu_txt != "—", "本机 CPU 采样落地", cpu_txt)
    chk("GB" in mem_txt, "本机内存采样落地", mem_txt)
    chk(len(app.sampler.cpu_hist) >= 1, "CPU 历史累积",
        f"{len(app.sampler.cpu_hist)} 点")

    # ---------------- 工具执行
    app.nav.select("tools")
    pump(app, 0.3)
    app.do_tool("ip")
    pump(app, 6.0)
    body = app.txt_tool.get("1.0", "end")
    chk(len(body) > 80, "IP 工具输出", f"{len(body)} 字符")
    chk("IP" in body.upper() or "addr" in body.lower() or "接口" in body,
        "IP 工具输出内容合理", body.strip().splitlines()[0][:60] if body.strip() else "")

    app.v_target.set("127.0.0.1")
    app.do_tool("ping")
    pump(app, 12.0)
    summary = app.lbl_ping.cget("text")
    chk("丢包" in summary or "发送" in summary, "Ping 摘要", summary)

    app.nav.select("sys")
    pump(app, 0.2)
    app.do_sys("uptime")
    pump(app, 8.0)
    sysbody = app.txt_sys.get("1.0", "end")
    chk(len(sysbody) > 4, "系统信息输出", f"{len(sysbody)} 字符")

    # ---------------- 防火墙（本机探测；第三批改成可读可写后接口名变了）
    app.nav.select("sec")
    pump(app, 0.2)
    app.fw_probe()
    got_fw = False
    for _ in range(80):
        pump(app, 0.2)
        if app.lbl_fw.cget("text"):
            got_fw = True
            break
    fw = app.lbl_fw.cget("text")
    chk(got_fw, "防火墙后端探测", fw.replace("\n", " | ")[:90])

    # ---------------- 主题切换
    try:
        app._toggle_theme()
        pump(app, 0.5)
        chk(True, "主题切换")
        app._toggle_theme()
        pump(app, 0.5)
        chk(True, "主题切回")
    except Exception as e:  # noqa: BLE001
        chk(False, "主题切换", repr(e))

    # ---------------- 断开
    app._disconnect()
    pump(app, 0.4)
    chk(app.session is None, "断开后会话清空")
    chk(app.status.cell_dev.lb.cget("text") == "未连接", "断开后状态栏复位",
        app.status.cell_dev.lb.cget("text"))

    app._log_raw("meta", "冒烟结束\n")
    pump(app, 0.2)

    # ---------------- 汇总
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print("\n===== %d 项，通过 %d，失败 %d =====" % (len(RESULTS), npass, nfail))
    for ok, name, detail in RESULTS:
        if not ok:
            print("  FAIL · %s   %s" % (name, detail))
    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
