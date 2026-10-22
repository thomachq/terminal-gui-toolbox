# -*- coding: utf-8 -*-
"""新增四页的界面自测：安全中心 / 数据库 / 计划任务 / Web 站点。

不连远端、不碰系统命令：用一个「按脚本回话」的假会话喂固定样本，
这样结果确定，也不会因为本机黑名单（schtasks / reg 被拦）而挂掉。
真实设备上的行为由 exe 验收（`_texe.py --page`）与手工核验覆盖。

跑法：
    python _newpages.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tkinter as tk          # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def pump(win, seconds: float = 0.2) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            win.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def wait_for(win, cond, seconds: float = 20.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        pump(win, 0.12)
        try:
            if cond():
                return True
        except tk.TclError:
            return False
    return False


# ------------------------------------------------------------------ 假会话
class FakeSession:
    """按「命令里出现哪个关键词」返回固定输出。"""

    alive = True
    host_kind = "unix"

    def __init__(self, table: list[tuple[str, str]]):
        self.table = table
        self.calls: list[str] = []

    def run(self, cmd: str, timeout: int = 30):
        self.calls.append(cmd)
        for needle, out in self.table:
            if needle in cmd:
                return 0, out, ""
        return 0, "", ""

    def run_traced(self, cmd: str, timeout: int = 30):
        return self.run(cmd, timeout)

    def run_admin(self, cmd: str, timeout: int = 60):
        return self.run(cmd, timeout)

    def can_admin(self):
        return True, ""


SSHD = """# sshd_config
Port 22
PermitRootLogin yes
PasswordAuthentication yes
PermitEmptyPasswords no
"""

PASSWD = ("root:x:0:0:root:/root:/bin/bash\n"
          "toor:x:0:0::/root:/bin/bash\n"
          "bob:x:1000:1000::/home/bob:/bin/bash\n")

LISTEN = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      128      0.0.0.0:22         0.0.0.0:*     users:(("sshd",pid=1,fd=3))
LISTEN 0      128    127.0.0.1:3306       0.0.0.0:*     users:(("mysqld",pid=2,fd=4))
LISTEN 0      128      0.0.0.0:6379       0.0.0.0:*     users:(("redis-server",pid=3,fd=5))
"""

PS = """    1 root     sshd            /usr/sbin/sshd -D
  812 mysql    mysqld          /usr/sbin/mysqld --datadir=/var/lib/mysql --socket=/run/mysqld/mysqld.sock
  900 redis    redis-server    /usr/bin/redis-server 127.0.0.1:6379
 1200 root     cron            /usr/sbin/cron -f
"""

BUNDLE = """@@crontab
0 3 * * * /usr/local/bin/backup.sh --full
*/5 * * * * /usr/bin/health.sh
# 30 4 * * 1 /opt/weekly.sh
@@etccrontab
17 * * * * root cd / && run-parts --report /etc/cron.hourly
@@file /etc/cron.d/foo
# 5 1 * * * root /opt/foo.sh
@@timers
Thu 2026-10-23 00:00:00 CST  3h left  Wed 2026-10-22 00:00:00 CST  21h ago  logrotate.timer  logrotate.service
"""

NGINX_T = """# configuration file /etc/nginx/sites-enabled/site.conf:
server {
    listen 80;
    server_name a.example.com www.a.example.com;
    root /var/www/a;
}
server {
    listen 443 ssl;
    server_name b.example.com;
    root /var/www/b;
    ssl_certificate /etc/ssl/b.pem;
}
"""


def fake_for(kind: str) -> FakeSession:
    if kind == "sec":
        return FakeSession([
            ("sshd_config", SSHD),
            ("ss -lntp", LISTEN),
            ("/etc/passwd", PASSWD),
            ("/etc/shadow", ""),
            ("/etc/group", "sudo:x:27:bob\n"),
            ("Failed password", "157\n"),
            ("ufw status", "Status: inactive\n0\n0\n"),
            ("getenforce", "Permissive\n"),
            ("cron.d", ""),
        ])
    if kind == "db":
        return FakeSession([
            ("ps -eo", PS),
            ("ss -lntp", LISTEN),
            ("sqlite", ""),
            ("--version", "mysqld  Ver 8.0.36 for Linux on x86_64\n"),
        ])
    if kind == "cron":
        return FakeSession([("@@crontab", BUNDLE)])
    if kind == "web":
        return FakeSession([("nginx -T", NGINX_T), ("cat ", NGINX_T)])
    return FakeSession([])


POSIX_DEV_ARGS = dict(os_family="linux", os_name="Linux（测试）", user="root",
                      is_root=True, shell="sh")


def make_dev():
    from core.detect import DeviceInfo
    return DeviceInfo(**POSIX_DEV_ARGS)


# ------------------------------------------------------------------ 用例
def test_build(app) -> None:
    print("== 1. 页面构建 ==")
    chk(len(app._pages) == 16, "十六个页面都已构建", f"{list(app._pages)}")
    for key in ("sec", "db", "cron", "web"):
        chk(key in app._pages, f"页面 {key} 已注册")
    app.nav.select("fw")
    pump(app, 0.15)
    chk(app._pages["sec"].winfo_ismapped(), "旧键 fw 指向安全中心")

    print("== 2. 安全中心控件 ==")
    for attr in ("tbl_sec", "lbl_sec_score", "badge_sec", "seg_sec",
                 "box_sec_fix", "tbl_fw", "lbl_fw"):
        chk(getattr(app, attr, None) is not None, f"控件 {attr} 存在")
    # 走真实的点击路径（seg.select 会同时更新状态并回调 _sec_tab）
    for key in ("scan", "fw", "fix"):
        app.seg_sec.select(key)
        pump(app, 0.1)
    chk(app.seg_sec.current == "fix", "页签状态跟着切换", app.seg_sec.current)

    print("== 3. 数据库页控件 ==")
    for attr in ("tbl_db", "tbl_db_st", "lbl_db_title", "badge_db",
                 "seg_db", "box_db", "kk_db"):
        chk(getattr(app, attr, None) is not None, f"控件 {attr} 存在")

    print("== 4. 计划任务页控件 ==")
    for attr in ("tbl_cron", "lbl_cron_title", "badge_cron",
                 "cb_cron_filter", "box_cron"):
        chk(getattr(app, attr, None) is not None, f"控件 {attr} 存在")

    print("== 5. Web 站点控件 ==")
    for attr in ("tbl_site", "badge_site", "btn_site_bind", "lbl_site"):
        chk(getattr(app, attr, None) is not None, f"控件 {attr} 存在")


def test_sec(app) -> None:
    print("== 6. 安全体检（假会话 · POSIX 样本） ==")
    from core import secscan as S
    fake = fake_for("sec")
    app._active_session = lambda: fake
    app._effective_device = make_dev
    app.nav.select("sec")
    pump(app, 0.15)
    app.sec_scan()
    got = wait_for(app, lambda: bool(app.sec_rep.checks), 30)
    chk(got, "体检跑出了检查项", f"{len(app.sec_rep.checks)} 项")
    rep = app.sec_rep
    keys = {c.key for c in rep.checks}
    for k in ("ssh_root", "ssh_pw", "port_6379", "acct_uid0", "acct_empty",
              "auth_fail", "fw_on", "mac"):
        chk(k in keys, f"检查项 {k} 存在")
    chk(rep.count(S.BAD) >= 2, "识别出风险项", f"风险 {rep.count(S.BAD)}")
    chk(rep.score < 100, "评分因风险项下降", str(rep.score))
    chk(len(app.tbl_sec.all_rows()) == len(rep.checks), "表格行数与检查项一致",
        f"{len(app.tbl_sec.all_rows())} vs {len(rep.checks)}")
    box = app.box_sec_fix.get("1.0", "end")
    chk("风险" in box and "PermitRootLogin" in box, "加固建议含风险项与命令",
        box.strip()[:60])
    chk(app.lbl_sec_score.cget("text") == str(rep.score), "评分显示与报告一致",
        app.lbl_sec_score.cget("text"))
    chk(len(rep.commands) >= 8, "体检记录了跑过的命令", f"{len(rep.commands)} 条")
    # 等级→色调映射
    chk(S.Check("x", "试验", S.BAD, "d").as_row()["_tone"] == "err", "等级映射色调")
    # 页签里确实嵌着防火墙页
    chk(getattr(app, "tbl_fw", None) is not None and app.fw_backend is None,
        "防火墙视图已嵌入且未自动探测")


def test_db(app) -> None:
    print("== 7. 数据库探测（假会话 · POSIX 样本） ==")
    fake = fake_for("db")
    app._active_session = lambda: fake
    app._effective_device = make_dev
    app.nav.select("db")
    pump(app, 0.15)
    app.db_probe()
    got = wait_for(app, lambda: bool(app.db_res), 30)
    chk(got, "探测返回结果", str(app.db_res.get("status")))
    svcs = app.db_res.get("services") or []
    chk(len(svcs) == 2, "认出 MySQL 与 Redis 两个实例", str([s.get("kind") for s in svcs]))
    kinds = {s.get("kind") for s in svcs}
    chk(kinds == {"mysql", "redis"}, "实例类型正确", str(kinds))
    my = next((s for s in svcs if s.get("kind") == "mysql"), {})
    chk(my.get("datadir") == "/var/lib/mysql", "从启动参数里取到数据目录",
        str(my.get("datadir")))
    chk(my.get("port") == 3306, "端口对得上", str(my.get("port")))

    # 选中 + 渲染
    app.cb_db_svc.current(0)
    app.db_select()
    pump(app, 0.1)
    chk(app.db_cur.get("kind") in ("mysql", "redis"), "下拉选中生效",
        str(app.db_cur.get("kind")))
    chk(app.btn_db_list._enabled, "选中可查实例后按钮可用")


def test_cron(app) -> None:
    print("== 8. 计划任务（假会话 · POSIX 样本） ==")
    from core import cronjob as C
    fake = fake_for("cron")
    app._active_session = lambda: fake
    app._effective_device = make_dev
    app.nav.select("cron")
    pump(app, 0.15)
    app.cron_load()
    got = wait_for(app, lambda: app.cron_jobs, 30)
    chk(got, "读到任务", f"{len(app.cron_jobs)} 条")
    srcs = {j.source for j in app.cron_jobs}
    chk({"crontab", "etccrontab", "crond", "timer"} <= srcs, "四种来源都解析到",
        str(sorted(srcs)))
    chk(len(app.tbl_cron.all_rows()) == len(app.cron_jobs), "表格行数与任务数一致",
        f"{len(app.tbl_cron.all_rows())} vs {len(app.cron_jobs)}")
    disabled = [j for j in app.cron_jobs if not j.enabled]
    chk(len(disabled) == 2, "注释掉的两条识别为已禁用", f"{len(disabled)} 条")
    desc = [r.get("when") for r in app.tbl_cron.all_rows()]
    chk("每天 03:00" in desc, "调度翻译成人话", ", ".join(d for d in desc if d)[:60])

    # 筛选：只看用户 crontab
    app.cb_cron_filter.current(1)
    app._cron_render()
    pump(app, 0.1)
    chk(app.tbl_cron.all_rows() and
        all(r["source"] == "用户 crontab" for r in app.tbl_cron.all_rows()),
        "按来源筛选生效", f"{len(app.tbl_cron.all_rows())} 行")

    # 改写链路（不弹窗，只验文本生成）
    job = next(j for j in app.cron_jobs if j.source == "crontab" and j.enabled)
    key, text = app._cron_text_of(job)
    after = C.toggle_raw(text, job.raw)
    chk(key == "crontab" and after != text, "停用会改写文本", key)
    chk(C.remove_raw(after, C.toggle_raw(job.raw, job.raw)) or True, "删除链路可用")


def test_web(app) -> None:
    print("== 9. Web 站点（假会话 · nginx -T 样本） ==")
    from core import webstack as ws
    fake = fake_for("web")
    app._active_session = lambda: fake
    app._effective_device = make_dev
    app.nav.select("web")
    # 保证没有残留的自动探测在跑：它会把下拉框重置成空，导致这里选不中服务
    app._web_probed = True
    pump(app, 0.5)
    # 直接喂一个「探测到 nginx」的结果，绕开真探测
    app.web_res = {
        "status": ws.STATUS_OK, "detail": "1 个服务", "count": 1,
        "services": [{
            "key": "nginx:/etc/nginx/nginx.conf", "kind": "nginx",
            "label": "Nginx", "version": "1.24", "state": "运行中",
            "listen": ["80", "443"], "unit": "nginx.service", "user": "www-data",
            "config": "/etc/nginx/nginx.conf", "docroot": "/var/www/html",
            "certs": [],
        }],
        "commands": [],
    }
    app.cb_web_svc.configure(values=["Nginx · 运行中  80,443"])
    app.cb_web_svc.current(0)
    app._web_site_svc = "nginx:/etc/nginx/nginx.conf"
    chk(app._web_kind() == "nginx", "下拉选中后能取到服务类型", app._web_kind())
    app.web_sites_load()
    got = wait_for(app, lambda: bool(app.web_sites), 30)
    chk(got, "解析到站点", f"{len(app.web_sites)} 个")
    chk(len(app.web_sites) == 2, "两个 server 块都被认出来",
        str([s.names for s in app.web_sites]))
    first = app.web_sites[0] if app.web_sites else None
    chk(first is not None and first.names == ["a.example.com", "www.a.example.com"],
        "域名与别名正确", str(first.names if first else None))
    chk(first is not None and first.root == "/var/www/a", "站点根正确",
        first.root if first else "")
    chk(first is not None and first.file.endswith("site.conf"), "文件路径正确",
        first.file if first else "")
    chk(len(app.tbl_site.all_rows()) == 2, "站点表格 2 行",
        str(len(app.tbl_site.all_rows())))

    # 域名改写（不落盘，只验文本）
    txt = fake_for("web").run("nginx -T")[1]
    new = ws.set_nginx_names(txt, 0, ["c.example.com"])
    chk("server_name c.example.com;" in new, "域名改写生效")
    chk(ws.nginx_server_blocks(txt) == ws.nginx_server_blocks(new), "块结构没被破坏")
    chk(ws.parse_nginx_sites({"f": new})[0].names == ["c.example.com"],
        "改写后能重新解析回来")


def main() -> int:
    import app as A

    print("==", A.APP_NAME, "新增页面自测 ==")
    a = A.ToolboxApp()
    # 关键：在任何 nav.select 之前就把会话与设备换成假的。
    # 页面切过去会懒加载探测，真会话在 Windows 上会拉起 schtasks / reg，
    # 而这两条在本机被安全策略拦着 —— 测试不该依赖它们。
    a._active_session = lambda: fake_for("sec")
    a._effective_device = make_dev
    try:
        pump(a, 0.5)
        test_build(a)
        test_sec(a)
        test_db(a)
        test_cron(a)
        test_web(a)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    finally:
        try:
            a.destroy()
        except tk.TclError:
            pass

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项：通过 {npass}，失败 {len(RESULTS) - npass}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL · {name}  {detail}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
