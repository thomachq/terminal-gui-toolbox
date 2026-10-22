# -*- coding: utf-8 -*-
"""计划任务的离线自测：解析 / 人话描述 / 校验 / 文本改写 / 写回命令。

这里最贵的错误是「改错行」和「把整份 crontab 写坏」，所以逐条验：
  · 注释掉的任务能重新解析成「已禁用」
  · toggle 只动目标那一行，旁边同前缀的行不能误伤
  · 校验要拦住 61 分钟、8 号星期这种明显错的表达式
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import cronjob as C   # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


TAB = """# m h  dom mon dow   command
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/bin:/bin
0 3 * * * /usr/local/bin/backup.sh --full
*/5 * * * * curl -fsS http://x/health | bash
# 30 4 * * 1 /opt/weekly.sh
@reboot /opt/start.sh
@daily /opt/daily.sh
"""

ETC = """SHELL=/bin/sh
# m h dom mon dow user  command
17 *    * * *   root    cd / && run-parts --report /etc/cron.hourly
25 6    * * *   root    test -x /usr/sbin/anacron || run-parts --report /etc/cron.daily
"""

CROND = """# 每天清一次
0 1 * * * root /opt/clean.sh
# 5 1 * * * root /opt/disabled.sh
"""

TIMERS = """NEXT                         LEFT      LAST                         PASSED    UNIT             ACTIVATES
Thu 2026-10-23 00:00:00 CST  3h left   Wed 2026-10-22 00:00:00 CST  21h ago   logrotate.timer  logrotate.service
Thu 2026-10-23 01:00:00 CST  4h left   Wed 2026-10-22 01:00:00 CST  20h ago   fstrim.timer
-                            -         Wed 2026-10-22 01:00:00 CST  20h ago   man-db.timer     man-db.service
"""


def test_parse() -> None:
    print("== 1. crontab 解析 ==")
    jobs = C.parse_crontab(TAB, default_user="bob")
    chk(len(jobs) == 5, "五行任务（env 与纯注释跳过）", str(len(jobs)))
    by_raw = {j.schedule: j for j in jobs}
    chk(by_raw["0 3 * * *"].command == "/usr/local/bin/backup.sh --full",
        "命令完整保留", by_raw["0 3 * * *"].command)
    chk(by_raw["0 3 * * *"].user == "bob", "用户取自参数", by_raw["0 3 * * *"].user)
    chk(by_raw["30 4 * * 1"].enabled is False, "注释掉的任务标记为禁用")
    chk(by_raw["@reboot"].command == "/opt/start.sh", "时间宏能解析")
    chk(len({j.id for j in jobs}) == len(jobs), "id 唯一")

    etc = C.parse_crontab(ETC, "etccrontab", "/etc/crontab", with_user=True)
    chk(len(etc) == 2, "/etc/crontab 两行", str(len(etc)))
    chk(etc[0].user == "root", "系统级 cron 取到运行用户", etc[0].user)
    chk(etc[0].command.startswith("cd /"), "命令里带 & 也没被截断", etc[0].command)

    crond = C.parse_crontab(CROND, "crond", "/etc/cron.d/x", with_user=True)
    chk(len(crond) == 2 and crond[1].enabled is False, "cron.d 的禁用行",
        str([j.enabled for j in crond]))
    chk(crond[0].user == "root" and crond[0].path == "/etc/cron.d/x",
        "cron.d 的路径与用户")


def test_timers_and_win() -> None:
    print("== 2. systemd timer / schtasks ==")
    timers = C.parse_timers(TIMERS)
    chk(len(timers) == 3, "三条 timer（含未触发的）", str(len(timers)))
    chk(timers[0].unit == "logrotate.timer" and
        timers[0].command == "logrotate.service", "单元与激活目标")
    chk(timers[0].next_run == "2026-10-23 00:00:00", "下次执行时间",
        timers[0].next_run)
    chk(timers[1].command == "", "没有 service 列时不瞎填")
    chk(timers[2].next_run == "", "未触发的 timer 下次时间为空")

    rows = C.parse_schtasks('"\\Microsoft\\MyTask","2026-10-23 3:00:00","Ready"\n'
                            '"\\Other","N/A","Disabled"\n')
    chk(len(rows) == 2, "Windows 计划任务两行", str(len(rows)))
    chk(rows[0].enabled is True and rows[1].enabled is False, "状态映射",
        str([r.enabled for r in rows]))
    chk(rows[1].next_run == "", "N/A 当作没有下次执行")


def test_describe() -> None:
    print("== 3. 人话描述 ==")
    cases = {
        "0 3 * * *": "每天 03:00",
        "30 4 * * 1": "每周一 04:30",
        "*/5 * * * *": "每 5 分钟",
        "0 * * * *": "每小时第 0 分钟",
        "0 0 1 * *": "每月 1 日 00:00",
        "@reboot": "开机时",
        "@daily": "每天 00:00",
        "@hourly": "每小时整点",
    }
    for spec, want in cases.items():
        got = C.describe(spec)
        chk(got == want, f"{spec} → {want}", got)
    chk(C.describe("0 3 * * 1-5") == "0 3 * * 1-5", "翻不出来就原样返回",
        C.describe("0 3 * * 1-5"))
    chk(C.describe("") == "", "空表达式返回空")


def test_validate() -> None:
    print("== 4. 校验 ==")
    for good in ("0 3 * * *", "*/10 * * * *", "0 0 1 1 0", "@reboot", "@weekly",
                 "59 23 31 12 7"):
        chk(C.validate_schedule(good) is None, f"{good} 合法")
    bads = {
        "61 * * * *": "分",
        "* 24 * * *": "时",
        "* * 0 * *": "日",
        "* * * 13 *": "月",
        "* * * * 8": "周",
        "* * *": "5 段",
        "": "不能为空",
        "@nope": "不认识",
        "*/0 * * * *": "步长",
    }
    for spec, needle in bads.items():
        err = C.validate_schedule(spec)
        chk(err is not None and needle in err, f"{spec!r} 被拦下", str(err))


def test_rewrite() -> None:
    print("== 5. 文本改写 ==")
    raw = "0 3 * * * /usr/local/bin/backup.sh --full"
    off = C.toggle_raw(TAB, raw)
    chk("# 0 3 * * * /usr/local/bin/backup.sh --full" in off, "停用＝加注释")
    chk("*/5 * * * *" in off, "相邻任务没被误改")
    chk(len(C.parse_crontab(off)) == len(C.parse_crontab(TAB)),
        "改写后任务数量不变")
    back = C.toggle_raw(off, "# " + raw)
    chk(back == TAB, "再点一次回到原样")

    removed = C.remove_raw(TAB, raw)
    chk(raw not in removed and "*/5" in removed, "删除只去掉那一行")
    chk(C.remove_raw(TAB, "不存在的一行") == TAB, "删不到就原样返回")

    added = C.append_job("# 头\n0 1 * * * /old\n", "*/15 * * * *", "/new.sh",
                         comment="工具新增")
    lines = added.strip().splitlines()
    chk(lines[-1] == "*/15 * * * * /new.sh", "追加在末尾", lines[-1])
    chk(any("工具新增" in x for x in lines), "带注释行")
    sysj = C.append_job("", "5 2 * * *", "/root.sh", user="root")
    chk(sysj.strip() == "5 2 * * * root /root.sh", "系统级追加带用户", sysj.strip())


def test_bundle() -> None:
    print("== 6. 一次读全 ==")
    text = ("@@crontab\n0 2 * * * /bin/true\n"
            "@@etccrontab\n17 * * * * root /hourly\n"
            "@@file /etc/cron.d/foo\n# 5 1 * * * root /x\n"
            "@@timers\n" + TIMERS.splitlines()[1] + "\n")
    bundle = C.parse_bundle(text)
    chk(bundle["crontab"] == "0 2 * * * /bin/true", "用户 crontab 段",
        bundle["crontab"])
    chk(bundle["etccrontab"].startswith("17 *"), "/etc/crontab 段")
    chk(list(bundle["files"]) == ["/etc/cron.d/foo"], "cron.d 文件名",
        str(list(bundle["files"])))
    jobs = C.jobs_from_bundle(bundle)
    chk(len(jobs) == 4, "四段共四条任务", str(len(jobs)))
    keys = {C.source_key(j) for j in jobs}
    chk(keys == {"crontab", "/etc/crontab", "/etc/cron.d/foo", ""},
        "每条任务都能映射到可写文本", str(keys))
    timer = next(j for j in jobs if j.source == "timer")
    chk(C.source_key(timer) == "", "timer 不可直接改")


def test_write() -> None:
    print("== 7. 写回命令 ==")
    text = "0 2 * * * /bin/true\n"
    cmd = C.crontab_write_cmd(text)
    chk("crontab" in cmd and "mktemp" in cmd and "rm -f" in cmd,
        "crontab 走临时文件并清理")
    blob = re.search(r"printf '%s' ([A-Za-z0-9+/=]+)", cmd)
    chk(blob is not None, "命令里带 base64")
    if blob:
        chk(base64.b64decode(blob.group(1)).decode() == text,
            "解出来就是原文")
    fcmd = C.file_write_cmd("/etc/cron.d/x", text)
    chk("/etc/cron.d/x" in fcmd and "chmod 644" in fcmd, "cron.d 写文件并给 644")
    chk(C.write_cmd_for("crontab", text).startswith("f=$(mktemp)"),
        "write_cmd_for 分派到 crontab")
    chk(C.write_cmd_for("/etc/cron.d/x", text).startswith("printf"),
        "write_cmd_for 分派到文件")

    chk("schtasks /create" in C.schtasks_cmd("create", "T", command="x.exe",
                                             schedule="DAILY", start="03:00"),
        "创建命令")
    chk(C.schtasks_cmd("delete", "T").endswith("/f"), "删除带 /f 不弹确认")
    chk("/enable" in C.schtasks_cmd("enable", "T"), "启用")
    chk("/disable" in C.schtasks_cmd("disable", "T"), "停用")
    chk(C.schtasks_cmd("run", "T").endswith("/run /tn \"T\""), "立即执行")
    chk(C.schtasks_cmd("unknown", "T") == "", "未知动作返回空")


def main() -> int:
    test_parse()
    test_timers_and_win()
    test_describe()
    test_validate()
    test_rewrite()
    test_bundle()
    test_write()
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项：通过 {npass}，失败 {len(RESULTS) - npass}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL · {name}  {detail}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
