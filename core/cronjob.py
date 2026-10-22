# -*- coding: utf-8 -*-
"""计划任务：crontab / cron.d / systemd timer / Windows 计划任务，一处看全。

为什么不用「接口」而是解析文本：cron 没有查询接口，`crontab -l` 就是唯一真相。
所以这里的做法是「读原文 → 在原文上改 → 整份写回」，而不是 sed 就地替换：

  · 读：一次命令把 crontab、/etc/crontab、/etc/cron.d/*、systemd timers 全取回来
  · 改：纯字符串操作（toggle / remove / append），可以离线单测
  · 写：base64 写临时文件 → `crontab <文件>` 或覆盖 /etc/cron.d/<name>
  · 验：写完再读一遍，确认目标行确实在

关掉一条任务不是删掉，是**注释掉**（`#` 前缀）—— 和宝塔、和大多数人的习惯一致，
后悔了还能开回来。真删要单独确认。
"""
from __future__ import annotations

import base64
import csv
import io
import re
import shlex
import zlib
from dataclasses import dataclass, field

FIELD_RE = re.compile(r"^[\d*/,\-]+$")
MACROS = {
    "@reboot": "开机时", "@yearly": "每年 1 月 1 日 00:00",
    "@annually": "每年 1 月 1 日 00:00", "@monthly": "每月 1 日 00:00",
    "@weekly": "每周日 00:00", "@daily": "每天 00:00",
    "@midnight": "每天 00:00", "@hourly": "每小时整点",
}
DOW_CN = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

SOURCE_CN = {"crontab": "用户 crontab", "etccrontab": "/etc/crontab",
             "crond": "/etc/cron.d", "timer": "systemd timer",
             "schtasks": "Windows 计划任务"}


@dataclass
class Job:
    id: str
    source: str
    schedule: str
    command: str
    enabled: bool = True
    user: str = ""
    path: str = ""
    raw: str = ""
    next_run: str = ""
    unit: str = ""

    @property
    def source_cn(self) -> str:
        return SOURCE_CN.get(self.source, self.source)

    def as_row(self) -> dict:
        tone = "ok" if self.enabled else "mute"
        if self.source == "timer" and not self.enabled:
            tone = "mute"
        return {
            "source": self.source_cn,
            "user": self.user or "—",
            "schedule": self.schedule,
            "when": describe(self.schedule) if self.source != "timer" else self.next_run,
            "command": self.command or "—",
            "state": "启用" if self.enabled else "已禁用",
            "next": self.next_run or "—",
            "_tone": tone,
            "_id": self.id,
            "_raw": self.raw,
            "_path": self.path,
        }


# ------------------------------------------------------------------ 身份
def job_id(source: str, path: str, raw: str, user: str = "") -> str:
    key = f"{source}|{path}|{user}|{raw.strip()}".encode()
    return format(zlib.crc32(key) & 0xFFFFFFFF, "08x")


# ------------------------------------------------------------------ 解析
def looks_like_job(body: str) -> bool:
    body = (body or "").strip()
    if not body:
        return False
    head = body.split(None, 1)[0]
    if head in MACROS:
        return len(body.split(None, 1)) == 2
    parts = body.split(None, 5)
    if len(parts) < 6:
        return False
    return all(FIELD_RE.match(p) for p in parts[:5])


def parse_cron_line(line: str, with_user: bool = False):
    """→ (schedule, command, user, enabled) 或 None。

    `with_user=True` 用于 /etc/crontab 与 /etc/cron.d/*，它们比用户 crontab
    多一个「以谁的身份跑」字段。
    """
    s = (line or "").rstrip("\n")
    body = s.strip()
    if not body:
        return None
    enabled = True
    if body.startswith("#"):
        body2 = body.lstrip("#").strip()
        if not looks_like_job(body2):
            return None
        enabled = False
        body = body2

    if body.startswith("@"):
        parts = body.split(None, 1)
        if len(parts) < 2:
            return None
        return parts[0], parts[1], "", enabled

    # 用户 crontab：分 时 日 月 周 + 命令（共 6 段）
    # /etc/crontab 与 cron.d：多一个「以谁的身份跑」，共 7 段
    want = 7 if with_user else 6
    parts = body.split(None, want - 1)
    if len(parts) < want:
        return None
    if not all(FIELD_RE.match(p) for p in parts[:5]):
        return None
    schedule = " ".join(parts[:5])
    if with_user:
        return schedule, parts[6], parts[5], enabled
    return schedule, parts[5], "", enabled


def parse_crontab(text: str, source: str = "crontab", path: str = "",
                  with_user: bool = False, default_user: str = "") -> list[Job]:
    out: list[Job] = []
    for raw in (text or "").splitlines():
        got = parse_cron_line(raw, with_user=with_user)
        if not got:
            continue
        schedule, command, user, enabled = got
        out.append(Job(
            id=job_id(source, path, raw, user or default_user),
            source=source, schedule=schedule, command=command,
            enabled=enabled, user=user or default_user, path=path,
            raw=raw.rstrip("\n")))
    return out


_TIMER_TAIL = re.compile(r"(\S+\.timer)\s*(?:(\S+\.service))?\s*$")
_TIMER_NEXT = re.compile(r"^\S+\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def parse_timers(text: str) -> list[Job]:
    """`systemctl list-timers --all --no-pager --no-legend`。

    列是空格对齐的，按列数切不可靠（还没触发过的 timer，NEXT 那一列是 `-`），
    所以只认两个锚点：行尾的 `xxx.timer [xxx.service]`，行首可选的 NEXT 时间。
    """
    out: list[Job] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lower().startswith("next"):
            continue
        m = _TIMER_TAIL.search(line)
        if not m:
            continue
        unit, activates = m.group(1), m.group(2) or ""
        d = _TIMER_NEXT.match(line)
        out.append(Job(
            id=job_id("timer", unit, unit),
            source="timer", schedule=unit, command=activates,
            enabled=True, next_run=d.group(1) if d else "", unit=unit,
            raw=line, path=""))
    return out


def parse_schtasks(text: str) -> list[Job]:
    """`schtasks /query /fo csv /nh` → TaskName, Next Run Time, Status。

    用 /nh 去掉表头，是因为中文 Windows 的列名是本地化的，
    按位置取比按名字取稳。
    """
    out: list[Job] = []
    reader = csv.reader(io.StringIO(text or ""))
    for row in reader:
        if len(row) < 3:
            continue
        name, nxt, status = row[0].strip(), row[1].strip(), row[2].strip()
        if not name or name.lower() in ("taskname", "任务名"):
            continue
        enabled = status not in ("已禁用", "Disabled", "禁用")
        out.append(Job(
            id=job_id("schtasks", "", name),
            source="schtasks", schedule=name, command="",
            enabled=enabled, next_run=nxt if nxt != "N/A" else "",
            raw=name, unit=name))
    return out


# ------------------------------------------------------------------ 人类可读
def _num(v: str):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def describe(spec: str) -> str:
    """把 cron 表达式翻成人话。翻不出来就原样返回，别硬编。"""
    spec = (spec or "").strip()
    if not spec:
        return ""
    if spec in MACROS:
        return MACROS[spec]
    parts = spec.split()
    if len(parts) != 5:
        return spec
    mi, ho, dom, mon, dow = parts

    def every(v: str, unit: str, base: str) -> str:
        if v.startswith("*/"):
            return f"每 {v[2:]} {unit}"
        return base

    if mi == "*" and ho == "*" and dom == "*" and mon == "*" and dow == "*":
        return "每分钟"
    if ho == "*" and dom == "*" and mon == "*" and dow == "*":
        if mi.startswith("*/"):
            return f"每 {mi[2:]} 分钟"
        return f"每小时第 {mi} 分钟" if _num(mi) is not None else f"每小时 {mi}"
    if dom == "*" and mon == "*" and dow == "*":
        if mi.isdigit() and ho.isdigit():
            return f"每天 {int(ho):02d}:{int(mi):02d}"
        if hi := re.fullmatch(r"\*/(\d+)", ho):
            return f"每 {hi.group(1)} 小时（第 {mi} 分钟）"
        return f"每天 {ho}:{mi}"
    if dom == "*" and mon == "*" and dow != "*":
        d = _num(dow)
        if d is None:          # 1-5 这种区间翻不成「周几」，原样返回更诚实
            return spec
        day = DOW_CN[d % 7]
        if mi.isdigit() and ho.isdigit():
            return f"每{day} {int(ho):02d}:{int(mi):02d}"
        return f"每{day} {ho}:{mi}"
    if mon == "*" and dow == "*" and dom != "*":
        if mi.isdigit() and ho.isdigit():
            return f"每月 {int(dom)} 日 {int(ho):02d}:{int(mi):02d}"
        return f"每月 {dom} 日 {ho}:{mi}"
    return spec


def _check_field(v: str, lo: int, hi: int) -> str | None:
    for piece in v.split(","):
        step = piece
        if "/" in piece:
            step, _, st = piece.partition("/")
            if not st.isdigit() or int(st) == 0:
                return f"步长不合法：{piece}"
        if step == "*":
            continue
        if "-" in step:
            a, _, b = step.partition("-")
            if not (a.isdigit() and b.isdigit()):
                return f"范围不合法：{piece}"
            if not (lo <= int(a) <= hi and lo <= int(b) <= hi):
                return f"超出范围 {lo}-{hi}：{piece}"
            continue
        if not step.isdigit():
            return f"不是数字也不是 *：{piece}"
        if not (lo <= int(step) <= hi):
            return f"超出范围 {lo}-{hi}：{piece}"
    return None


def validate_schedule(spec: str) -> str | None:
    """返回错误说明；None 表示合法。"""
    spec = (spec or "").strip()
    if not spec:
        return "调度表达式不能为空"
    if spec.startswith("@"):
        return None if spec in MACROS else f"不认识的时间宏：{spec}"
    parts = spec.split()
    if len(parts) != 5:
        return "需要 5 段：分 时 日 月 周"
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    labels = ["分", "时", "日", "月", "周"]
    for p, (lo, hi), lb in zip(parts, ranges, labels):
        err = _check_field(p, lo, hi)
        if err:
            return f"{lb}：{err}"
    return None


# ------------------------------------------------------------------ 文本改写
def _find_raw_line(text: str, raw: str) -> tuple[int, str] | None:
    target = (raw or "").strip()
    if not target:
        return None
    for i, line in enumerate((text or "").splitlines()):
        if line.strip() == target:
            return i, line
    return None


def toggle_raw(text: str, raw: str) -> str:
    """注释 / 取消注释某一行（按整行匹配，不误伤相邻任务）。"""
    hit = _find_raw_line(text, raw)
    if hit is None:
        return text
    idx, line = hit
    lines = (text or "").splitlines()
    strip = line.strip()
    if strip.startswith("#"):
        body = strip.lstrip("#").strip()
        indent = line[:len(line) - len(line.lstrip())]
        lines[idx] = indent + body
    else:
        indent = line[:len(line) - len(line.lstrip())]
        lines[idx] = indent + "# " + strip
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def remove_raw(text: str, raw: str) -> str:
    hit = _find_raw_line(text, raw)
    if hit is None:
        return text
    idx, _ = hit
    lines = (text or "").splitlines()
    del lines[idx]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def append_job(text: str, schedule: str, command: str,
               user: str = "", comment: str = "") -> str:
    base = (text or "").rstrip("\n")
    if user:
        sp = schedule.split()
        # 5 段才谈得上插用户（宏没有这个位置：@reboot 在 /etc/crontab 里
        # 写的是 `@reboot user cmd`，但那属于少数派，这里不猜）
        line = (" ".join(sp[:5]) + f" {user} {command}") if len(sp) == 5 \
            else f"{schedule} {command}"
    else:
        line = f"{schedule} {command}"
    extra = f"# {comment}\n" if comment else ""
    return (base + "\n" if base else "") + extra + line + "\n"


# ------------------------------------------------------------------ 写入
def _b64(text: str) -> str:
    return base64.b64encode((text or "").encode()).decode()


def crontab_write_cmd(text: str) -> str:
    """整份写回用户 crontab。走临时文件，避免 stdin 依赖。"""
    return ("f=$(mktemp); printf '%s' " + shlex.quote(_b64(text)) +
            " | base64 -d > \"$f\" && crontab \"$f\"; rc=$?; "
            "rm -f \"$f\"; exit $rc")


def file_write_cmd(path: str, text: str, mode: str = "644") -> str:
    q = shlex.quote(path)
    return ("printf '%s' " + shlex.quote(_b64(text)) +
            f" | base64 -d > {q} && chmod {mode} {q}")


def schtasks_cmd(action: str, name: str, *, command: str = "",
                 schedule: str = "DAILY", start: str = "03:00") -> str:
    """Windows 计划任务的增删改查命令（一条一个动作）。"""
    q = '"' + name.replace('"', "'") + '"'
    if action == "delete":
        return f"schtasks /delete /tn {q} /f"
    if action == "run":
        return f"schtasks /run /tn {q}"
    if action == "enable":
        return f"schtasks /change /tn {q} /enable"
    if action == "disable":
        return f"schtasks /change /tn {q} /disable"
    if action == "create":
        return (f"schtasks /create /tn {q} /tr \"{command}\" "
                f"/sc {schedule} /st {start} /f")
    return ""


# ------------------------------------------------------------------ 读取
POSIX_BUNDLE = (
    "echo '@@crontab'; crontab -l 2>/dev/null; "
    "echo '@@etccrontab'; cat /etc/crontab 2>/dev/null; "
    "for f in /etc/cron.d/*; do echo \"@@file $f\"; cat \"$f\" 2>/dev/null; "
    "done; echo '@@timers'; "
    "systemctl list-timers --all --no-pager --no-legend 2>/dev/null")


def parse_bundle(text: str) -> dict:
    """按 `@@` 标记切段：{crontab, etccrontab, files:{path: text}, timers}。"""
    out = {"crontab": "", "etccrontab": "", "files": {}, "timers": ""}
    cur = ""
    buf: list[str] = []
    cur_file = ""

    def flush():
        body = "\n".join(buf).strip("\n")
        if cur == "files" and cur_file:
            out["files"][cur_file] = body
        elif cur in out:
            out[cur] = body

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.startswith("@@file "):
            flush()
            cur, cur_file, buf = "files", line[7:].strip(), []
            continue
        if line.startswith("@@"):
            flush()
            name = line[2:].strip()
            cur = "etccrontab" if name == "etccrontab" else name
            cur_file = ""
            buf = []
            continue
        if cur:
            buf.append(line)
    flush()
    return out


def jobs_from_bundle(bundle: dict, default_user: str = "") -> list[Job]:
    jobs: list[Job] = []
    jobs += parse_crontab(bundle.get("crontab", ""), "crontab",
                          default_user=default_user)
    jobs += parse_crontab(bundle.get("etccrontab", ""), "etccrontab",
                          path="/etc/crontab", with_user=True)
    for path, text in (bundle.get("files") or {}).items():
        jobs += parse_crontab(text, "crond", path=path, with_user=True)
    jobs += parse_timers(bundle.get("timers", ""))
    return jobs


def source_key(job: Job) -> str:
    """把一条任务映射到「它属于哪份可写文本」。"""
    if job.source == "crontab":
        return "crontab"
    if job.source == "etccrontab":
        return "/etc/crontab"
    if job.source == "crond":
        return job.path
    return ""


def write_cmd_for(key: str, text: str) -> str:
    if key == "crontab":
        return crontab_write_cmd(text)
    return file_write_cmd(key, text, "644")
