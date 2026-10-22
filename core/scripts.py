# -*- coding: utf-8 -*-
"""脚本库 —— 一段命令 + 一个名字，双击就在当前主机上跑。

两层：
  · **内置脚本**来自代码里的 `BUILTIN`，不可删除（删了下次升级又回来，
    留着删除按钮只会骗人）。想改就「复制为自定义」，改副本。
  · **自定义脚本**存 `data/scripts.json`，明文 —— 脚本本身不是机密，
    用户可能想直接开文件改，藏进密文里反而不好维护。

脚本正文**不包含任何凭据**：需要提权的用 `sudo`，口令由会话层负责喂，
不写进脚本文本。这是刻意的 —— 脚本会被复制、导出、贴进聊天窗口。
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import time
from pathlib import Path

KINDS: dict[str, dict] = {
    "shell": {"label": "Shell", "hint": "bash / sh，Linux 与 macOS"},
    "powershell": {"label": "PowerShell", "hint": "Windows"},
    "python": {"label": "Python", "hint": "目标机需有 python3"},
}
KIND_KEYS = tuple(KINDS)
SOURCE_BUILTIN = "内置"
SOURCE_CUSTOM = "自定义"


def kind_label(kind: str) -> str:
    return KINDS.get(kind, KINDS["shell"])["label"]


def new_id(existing: list[str] | None = None) -> str:
    have = set(existing or [])
    for _ in range(64):
        v = "u" + _secrets.token_hex(3)
        if v not in have:
            return v
    raise RuntimeError("脚本 id 生成失败：连续 64 次撞号")


def now_ts() -> int:
    return int(time.time())


def fmt_ts(v) -> str:
    if v in (None, "", 0, "0"):
        return ""
    try:
        ts = float(v)
    except (TypeError, ValueError):
        return str(v)
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y/%m/%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


# ------------------------------------------------------------------ 内置脚本
# 每条都能直接跑。原则：只读、幂等、不依赖额外安装、输出人看得懂。
# 顺序按「日常最常查的」排，不是按字母。
BUILTIN: list[dict] = [
    {
        "id": "b_disk", "kind": "shell", "tags": "磁盘,日常",
        "name": "磁盘与 inode 水位",
        "desc": "各分区容量与 inode 使用率。inode 满了会表现为「有空间却写不进文件」。",
        "body": 'echo "== 容量 =="; df -h 2>/dev/null || df -k\n'
                'echo; echo "== inode =="; df -i 2>/dev/null || true\n'
                'echo; echo "== 挂载点 =="; mount | grep -vE "^(proc|sysfs|cgroup|tmpfs|devpts)" | head -20',
    },
    {
        "id": "b_bigdir", "kind": "shell", "tags": "磁盘,排查",
        "name": "大目录 Top 10",
        "desc": "根下各一级目录占用，找出把盘吃满的那一个。",
        "body": 'du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -12',
    },
    {
        "id": "b_top", "kind": "shell", "tags": "性能,日常",
        "name": "CPU 与内存占用 Top",
        "desc": "按 CPU 排序的前 11 个进程，附内存概览。",
        "body": 'echo "== CPU Top =="; ps aux --sort=-%cpu 2>/dev/null | head -11\n'
                'echo; echo "== 内存 Top =="; ps aux --sort=-%mem 2>/dev/null | head -6\n'
                'echo; free -h 2>/dev/null || vm_stat 2>/dev/null || true',
    },
    {
        "id": "b_load", "kind": "shell", "tags": "性能,日常",
        "name": "负载与运行时长",
        "desc": "1/5/15 分钟负载、运行时长、登录人数、核数。",
        "body": 'uptime; echo; echo "核数: $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null)"\n'
                'echo; echo "== 内存 =="; free -m 2>/dev/null || true',
    },
    {
        "id": "b_ports", "kind": "shell", "tags": "网络,安全",
        "name": "监听端口与进程",
        "desc": "所有 LISTEN 的端口、对应进程。判断「谁在对外服务」的第一条命令。",
        "body": 'ss -lntup 2>/dev/null || netstat -lntup 2>/dev/null || lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null',
    },
    {
        "id": "b_conn", "kind": "shell", "tags": "网络,排查",
        "name": "当前连接与连接数统计",
        "desc": "已建立的连接，以及按对端 IP 归并的连接数（找异常扫描/爬虫）。",
        "body": 'echo "== 已建立 =="; ss -tnp state established 2>/dev/null | head -25 || true\n'
                'echo; echo "== 按对端统计 Top 10 =="; ss -tn state established 2>/dev/null | awk \'NR>1{print $5}\' | cut -d: -f1 | sort | uniq -c | sort -rn | head -10',
    },
    {
        "id": "b_login", "kind": "shell", "tags": "安全,审计",
        "name": "最近登录记录",
        "desc": "成功登录与失败次数、来源 IP。查「有没有人登过」必看。",
        "body": 'echo "== 最近 15 次登录 =="; last -n 15 2>/dev/null || lastlog 2>/dev/null || true\n'
                'echo; echo "== 登录失败次数 Top =="; lastb -n 200 2>/dev/null | awk \'{print $3}\' | sort | uniq -c | sort -rn | head -10 || echo "(需要 root 才能读 lastb)"\n'
                'echo; echo "== 当前在线 =="; who 2>/dev/null || w 2>/dev/null || true',
    },
    {
        "id": "b_authlog", "kind": "shell", "tags": "安全,审计",
        "name": "认证日志里的失败尝试",
        "desc": "从 auth.log / secure 里抓失败与爆破痕迹（最近 40 条）。",
        "body": 'for f in /var/log/auth.log /var/log/secure; do\n'
                '  [ -r "$f" ] && { echo "== $f =="; grep -iE "failed|invalid user|BREAK-IN" "$f" 2>/dev/null | tail -40; }\n'
                'done\n'
                'echo; echo "== 按来源 IP 统计 =="\n'
                'grep -hoE "from [0-9]{1,3}(\\.[0-9]{1,3}){3}" /var/log/auth.log /var/log/secure 2>/dev/null | awk \'{print $2}\' | sort | uniq -c | sort -rn | head -10',
    },
    {
        "id": "b_cron", "kind": "shell", "tags": "计划任务,审计",
        "name": "计划任务全量清单",
        "desc": "用户 crontab、/etc/crontab、cron.d、systemd timer 一次看全。",
        "body": 'echo "== 当前用户 crontab =="; crontab -l 2>/dev/null || echo "(空)"\n'
                'echo; echo "== /etc/crontab =="; cat /etc/crontab 2>/dev/null || echo "(无)"\n'
                'echo; echo "== /etc/cron.d =="; ls -l /etc/cron.d/ 2>/dev/null || echo "(无)"\n'
                'echo; echo "== 周期目录 =="; ls /etc/cron.daily /etc/cron.hourly /etc/cron.weekly 2>/dev/null\n'
                'echo; echo "== systemd timer =="; systemctl list-timers --all --no-pager 2>/dev/null | head -20 || echo "(无 systemd)"',
    },
    {
        "id": "b_svc", "kind": "shell", "tags": "系统,排查",
        "name": "失败的服务单元",
        "desc": "systemd 里处于 failed 的服务，附最近日志尾部。",
        "body": 'systemctl --failed --no-pager 2>/dev/null || true\n'
                'echo; echo "== 最近失败的日志 =="\n'
                'journalctl -p err -n 30 --no-pager 2>/dev/null || echo "(无 journalctl)"',
    },
    {
        "id": "b_sysinfo", "kind": "shell", "tags": "系统,日常",
        "name": "系统概要",
        "desc": "发行版、内核、CPU 型号、内存、时区、虚拟化类型。",
        "body": 'echo "== 系统 =="; cat /etc/os-release 2>/dev/null | head -5\n'
                'echo; echo "== 内核 =="; uname -a\n'
                'echo; echo "== CPU =="; grep -m1 "model name" /proc/cpuinfo 2>/dev/null || sysctl -n machdep.cpu.brand_string 2>/dev/null\n'
                'echo; echo "== 虚拟化 =="; systemd-detect-virt 2>/dev/null || echo "(未知)"\n'
                'echo; echo "== 时间 =="; date; timedatectl 2>/dev/null | head -6 || true',
    },
    {
        "id": "b_docker", "kind": "shell", "tags": "Docker,日常",
        "name": "Docker 容器与资源",
        "desc": "运行中的容器、镜像与磁盘占用、异常的退出记录。",
        "body": 'command -v docker >/dev/null || { echo "未安装 docker"; exit 0; }\n'
                'echo "== 容器 =="; docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Image}}" 2>/dev/null\n'
                'echo; echo "== 最近退出的 5 个 =="; docker ps -a --filter status=exited --format "table {{.Names}}\\t{{.Status}}" 2>/dev/null | head -6\n'
                'echo; echo "== 占用 =="; docker system df 2>/dev/null || true',
    },
    {
        "id": "b_nginx", "kind": "shell", "tags": "Web,配置",
        "name": "Nginx 站点速览",
        "desc": "从 nginx -T 的展开配置里抓 server_name / listen / root / 来源文件。",
        "body": 'command -v nginx >/dev/null || { echo "未安装 nginx"; exit 0; }\n'
                'nginx -T 2>/dev/null | grep -E "^# configuration file|^\\s*(server_name|listen|root|ssl_certificate)\\b" | head -80',
    },
    {
        "id": "b_tls", "kind": "shell", "tags": "Web,证书",
        "name": "证书到期时间",
        "desc": "扫 /etc 下常见证书文件，输出主题与到期日，挑出 30 天内到期的。",
        "body": 'command -v openssl >/dev/null || { echo "未安装 openssl"; exit 0; }\n'
                'find /etc -maxdepth 4 \\( -name "*.crt" -o -name "*.pem" \\) -type f 2>/dev/null | head -40 | while read -r f; do\n'
                '  end=$(openssl x509 -enddate -noout -in "$f" 2>/dev/null | cut -d= -f2)\n'
                '  sub=$(openssl x509 -subject -noout -in "$f" 2>/dev/null | sed "s/^subject=//")\n'
                '  [ -n "$end" ] && echo "$end | $f | $sub"\n'
                'done | sort',
    },
    {
        "id": "b_db", "kind": "shell", "tags": "数据库,日常",
        "name": "数据库实例探测",
        "desc": "看常见数据库端口有没有在听、进程是谁、版本多少。",
        "body": 'echo "== 监听 =="\n'
                'ss -lntp 2>/dev/null | grep -E ":(3306|5432|6379|27017|1433|11211|9200)\\b" || echo "(没有常见数据库端口在监听)"\n'
                'echo; echo "== 进程 =="\n'
                'ps -eo pid,user,comm,args 2>/dev/null | grep -E "mysqld|postgres|redis-server|mongod|sqlservr" | grep -v grep | cut -c1-160 || true\n'
                'echo; echo "== 版本 =="\n'
                'mysql --version 2>/dev/null; psql --version 2>/dev/null; redis-server --version 2>/dev/null; mongod --version 2>/dev/null | head -1',
    },
    {
        "id": "b_pkg", "kind": "shell", "tags": "系统,维护",
        "name": "可升级的软件包",
        "desc": "apt / dnf / yum 里有多少安全更新待装（只看数量，不安装）。",
        "body": 'if command -v apt >/dev/null; then\n'
                '  echo "== apt =="; apt list --upgradable 2>/dev/null | tail -n +2 | wc -l | xargs echo "可升级:"\n'
                '  apt list --upgradable 2>/dev/null | grep -i security | head -10\n'
                'elif command -v dnf >/dev/null; then\n'
                '  echo "== dnf =="; dnf check-update -q 2>/dev/null | tail -n +1 | head -20\n'
                'elif command -v yum >/dev/null; then\n'
                '  echo "== yum =="; yum check-update -q 2>/dev/null | head -20\n'
                'else echo "(没有 apt / dnf / yum)"; fi',
    },
    {
        "id": "b_win_disk", "kind": "powershell", "tags": "磁盘,日常",
        "name": "磁盘水位（Windows）",
        "desc": "各盘可用/总计与使用百分比。",
        "body": 'Get-PSDrive -PSProvider FileSystem | '
                'Select-Object Name, '
                '@{n="已用GB";e={[math]::Round($_.Used/1GB,1)}}, '
                '@{n="可用GB";e={[math]::Round($_.Free/1GB,1)}}, '
                '@{n="使用率";e={if(($_.Used+$_.Free) -gt 0){[math]::Round(100*$_.Used/($_.Used+$_.Free),1).ToString()+"%"}else{"-"}}} | '
                'Format-Table -AutoSize',
    },
    {
        "id": "b_win_proc", "kind": "powershell", "tags": "性能,日常",
        "name": "进程占用 Top（Windows）",
        "desc": "按工作集与 CPU 排序的前 12 个进程。",
        "body": 'Get-Process | Sort-Object -Property WS -Descending | '
                'Select-Object -First 12 Name, Id, '
                '@{n="内存MB";e={[math]::Round($_.WS/1MB,1)}}, CPU | '
                'Format-Table -AutoSize',
    },
    {
        "id": "b_win_port", "kind": "powershell", "tags": "网络,安全",
        "name": "监听端口（Windows）",
        "desc": "LISTEN 状态的端口与所属进程名。",
        "body": 'Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | '
                'Select-Object LocalAddress, LocalPort, '
                '@{n="进程";e={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}}, '
                'OwningProcess | Sort-Object LocalPort | Format-Table -AutoSize',
    },
    {
        "id": "b_win_svc", "kind": "powershell", "tags": "系统,排查",
        "name": "非运行状态的服务（Windows）",
        "desc": "所有不是 Running 的服务，附启动类型。",
        "body": 'Get-Service | Where-Object { $_.Status -ne "Running" } | '
                'Select-Object Status, StartType, Name, DisplayName | '
                'Sort-Object Status, Name | Format-Table -AutoSize',
    },
    {
        "id": "b_win_fw", "kind": "powershell", "tags": "安全,防火墙",
        "name": "防火墙与远程桌面状态（Windows）",
        "desc": "三个配置文件是否启用，以及 RDP 是否开放。",
        "body": 'Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction | Format-Table -AutoSize\n'
                'echo ""; echo "== RDP =="\n'
                '$p = Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server" -ErrorAction SilentlyContinue\n'
                'if ($p.fDenyTSConnections -eq 0) { echo "远程桌面：已允许" } else { echo "远程桌面：已禁用" }',
    },
    {
        "id": "b_win_task", "kind": "powershell", "tags": "计划任务,审计",
        "name": "计划任务清单（Windows）",
        "desc": "非禁用的计划任务，含路径与上次运行结果。",
        "body": 'Get-ScheduledTask | Where-Object { $_.State -ne "Disabled" } | '
                'ForEach-Object {\n'
                '  $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue\n'
                '  [pscustomobject]@{\n'
                '    任务 = $_.TaskName; 路径 = $_.TaskPath; 状态 = $_.State\n'
                '    上次运行 = $i.LastRunTime; 结果 = $i.LastTaskResult\n'
                '  }\n'
                '} | Sort-Object 路径, 任务 | Format-Table -AutoSize',
    },
    {
        "id": "b_win_login", "kind": "powershell", "tags": "安全,审计",
        "name": "最近登录记录（Windows）",
        "desc": "安全日志里的 4624/4625 事件（需要管理员权限）。",
        "body": 'try {\n'
                '  Get-WinEvent -FilterHashtable @{LogName="Security"; Id=4624,4625} -MaxEvents 20 -ErrorAction Stop |\n'
                '    Select-Object TimeCreated, Id, @{n="用户";e={$_.Properties[5].Value}}, @{n="来源";e={$_.Properties[18].Value}} |\n'
                '    Format-Table -AutoSize\n'
                '} catch { echo "读不到安全日志：需要以管理员身份运行。" }',
    },
    {
        "id": "b_win_startup", "kind": "powershell", "tags": "系统,审计",
        "name": "开机启动项（Windows）",
        "desc": "注册表与启动目录里的自启命令。",
        "body": 'Get-CimInstance Win32_StartupCommand | '
                'Select-Object Name, Command, Location, User | Format-Table -AutoSize -Wrap',
    },
]


# 内置脚本统一打标。散落 24 处写 "builtin": True 迟早漏一条，
# 漏掉的那条就会被当成「自定义」—— 能删、不能复制，行为全错。
for _b in BUILTIN:
    _b["builtin"] = True


# ------------------------------------------------------------------ 展示
def preview(body: str, n: int = 70) -> str:
    """把多行脚本压成一行预览：跳过注释与空行，取第一句有内容的。"""
    for line in (body or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("::") or s.startswith("REM "):
            continue
        s = re.sub(r"\s+", " ", s)
        return s if len(s) <= n else s[: n - 1] + "…"
    return "(空脚本)"


def line_count(body: str) -> int:
    return len([1 for ln in (body or "").splitlines() if ln.strip()])


def script_view(s: dict) -> dict:
    s = s or {}
    builtin = bool(s.get("builtin"))
    return {
        "name": str(s.get("name") or "未命名脚本"),
        "kind": kind_label(str(s.get("kind") or "shell")),
        "kind_key": str(s.get("kind") or "shell"),
        "preview": preview(s.get("body")),
        "lines": str(line_count(s.get("body"))),
        "source": SOURCE_BUILTIN if builtin else SOURCE_CUSTOM,
        "builtin": builtin,
        "tags": str(s.get("tags") or ""),
        "desc": str(s.get("desc") or ""),
        "created": fmt_ts(s.get("created")),
        "_id": str(s.get("id") or ""),
        "_raw": s,
    }


def filter_scripts(rows: list[dict], q: str = "", kind: str = "",
                   source: str = "") -> list[dict]:
    out = list(rows or [])
    if kind:
        out = [r for r in out if str(r.get("kind_key") or "") == kind]
    if source:
        want = source == SOURCE_BUILTIN
        out = [r for r in out if bool(r.get("builtin")) == want]
    words = [w for w in re.split(r"[,，;；、\s]+", (q or "").strip()) if w]
    if words:
        def hit(r: dict) -> bool:
            pool = " ".join([
                str(r.get("name") or ""), str(r.get("desc") or ""),
                str(r.get("tags") or ""), str(r.get("preview") or ""),
                str(r.get("source") or ""),
            ]).lower()
            return all(w.lower() in pool for w in words)
        out = [r for r in out if hit(r)]
    return out


def summary(rows: list[dict], total: int | None = None) -> str:
    n = len(rows or [])
    b = len([r for r in rows or [] if r.get("builtin")])
    if total is not None and total != n:
        return f"筛选出 {n} 个 / 共 {total} 个脚本"
    if not n:
        return "还没有脚本"
    return f"共 {n} 个脚本 · 内置 {b} · 自定义 {n - b}"


# ------------------------------------------------------------------ 校验
def validate(s: dict, others: list[dict] | None = None,
             skip_id: str = "") -> list[str]:
    errs: list[str] = []
    s = s or {}
    name = str(s.get("name") or "").strip()
    if not name:
        errs.append("名称不能为空")
    if str(s.get("kind") or "") not in KINDS:
        errs.append("类型不对（只能是 Shell / PowerShell / Python）")
    if not str(s.get("body") or "").strip():
        errs.append("脚本内容不能为空")
    elif len(s.get("body") or "") > 20000:
        errs.append("脚本太长（超过 20000 字符），请拆成多个")
    if name and others:
        for o in others or []:
            if not isinstance(o, dict):
                continue
            if skip_id and str(o.get("id") or "") == skip_id:
                continue
            if bool(o.get("builtin")):
                continue        # 自定义可以跟内置重名，但保存时会提示
            if str(o.get("name") or "").strip().lower() == name.lower():
                errs.append(f"已有一个同名自定义脚本「{name}」")
                break
    return errs


def name_warning(name: str, rows: list[dict]) -> str:
    """和内置脚本重名时的一个软提示（不拦）。"""
    n = (name or "").strip().lower()
    for r in rows or []:
        if r.get("builtin") and str(r.get("name") or "").strip().lower() == n:
            return "与内置脚本同名，清单里会挨在一起（不影响运行）"
    return ""


def shell_cmd(script: dict, os_family: str = "unix") -> str:
    """把脚本正文包成一条能直接丢给 session.run 的命令。

    Shell 用 `bash -c`（远端没 bash 时退 `sh -c`）；PowerShell 用
    `-NoProfile -NonInteractive -Command`，避免加载用户 profile 挂住。
    Python 用 heredoc 写到临时文件再跑，不依赖 python -c 的引号转义。
    """
    body = str((script or {}).get("body") or "")
    kind = str((script or {}).get("kind") or "shell")
    if kind == "powershell":
        # 单引号转义：PowerShell 里单引号串内两个单引号表示一个
        esc = body.replace("'", "''")
        return ("powershell -NoProfile -NonInteractive -Command "
                f"'{esc}'")
    if kind == "python":
        return ("f=$(mktemp /tmp/_tb_XXXXXX.py) && cat > \"$f\" <<'__TB_PY__'\n"
                f"{body}\n__TB_PY__\n"
                "python3 \"$f\"; rc=$?; rm -f \"$f\"; exit $rc")
    # shell：先试 bash，退 sh。用 heredoc 保证多行与引号都原样传过去
    return ("if command -v bash >/dev/null 2>&1; then\n"
            "bash -s <<'__TB_SH__'\n"
            f"{body}\n__TB_SH__\n"
            "else\n"
            "sh -s <<'__TB_SH__'\n"
            f"{body}\n__TB_SH__\n"
            "fi")


def kind_for_os(os_family: str) -> str:
    """按目标系统挑内置脚本类型。"""
    f = (os_family or "").lower()
    return "powershell" if f.startswith("win") else "shell"


def suggested_kind(os_family: str, current: str = "") -> str:
    """新建脚本时的默认类型：跟着当前设备走，但别覆盖用户已选的。"""
    return current or kind_for_os(os_family)


def clone_as_custom(s: dict, existing_ids: list[str]) -> dict:
    """内置 → 自定义副本。名字加「（副本）」，id 换新。"""
    return {
        "id": new_id(existing_ids),
        "name": (str(s.get("name") or "") + "（副本）")[:60],
        "kind": str(s.get("kind") or "shell"),
        "desc": str(s.get("desc") or ""),
        "body": str(s.get("body") or ""),
        "tags": str(s.get("tags") or ""),
        "builtin": False,
        "created": str(now_ts()),
    }


def blank_script(kind: str = "shell") -> dict:
    return {"id": "", "name": "", "kind": kind if kind in KINDS else "shell",
            "desc": "", "body": "", "tags": "", "builtin": False,
            "created": str(now_ts())}


def script_from_form(data: dict, base: dict | None = None) -> dict:
    out = dict(base or blank_script())
    for k in ("name", "desc", "body", "tags"):
        if k in data:
            out[k] = str(data.get(k) or "")
    if "kind" in data and str(data.get("kind") or "") in KINDS:
        out["kind"] = str(data.get("kind"))
    out["name"] = out["name"].strip()
    out["builtin"] = False
    if not out.get("created"):
        out["created"] = str(now_ts())
    return out


# ------------------------------------------------------------------ 存储
class ScriptStore:
    """`data/scripts.json`：只存自定义脚本，内置那份在代码里。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"version": 1, "scripts": []}
        self.load()

    def load(self) -> None:
        self.data = {"version": 1, "scripts": []}
        if not self.path.is_file():
            return
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("scripts"), list):
            return
        self.data = {
            "version": int(obj.get("version") or 1),
            "scripts": [self._clean(s) for s in obj["scripts"]
                        if isinstance(s, dict) and not s.get("builtin")],
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _clean(s: dict) -> dict:
        kind = str(s.get("kind") or "shell")
        if kind not in KINDS:
            kind = "shell"
        return {
            "id": str(s.get("id") or ""),
            "name": str(s.get("name") or ""),
            "kind": kind,
            "desc": str(s.get("desc") or ""),
            "body": str(s.get("body") or ""),
            "tags": str(s.get("tags") or ""),
            "builtin": False,
            "created": str(s.get("created") or ""),
        }

    @property
    def scripts(self) -> list[dict]:
        return self.data["scripts"]

    def ids(self) -> list[str]:
        return [s["id"] for s in self.scripts if s.get("id")]

    def get(self, sid: str) -> dict | None:
        for s in self.scripts:
            if s.get("id") == sid:
                return s
        return None

    def all(self) -> list[dict]:
        """内置在前、自定义在后；各自按名字排。"""
        builtin = sorted((dict(b) for b in BUILTIN),
                         key=lambda x: str(x.get("name") or ""))
        custom = sorted((dict(s) for s in self.scripts),
                        key=lambda x: str(x.get("name") or ""))
        return builtin + custom

    def find_any(self, sid: str) -> dict | None:
        """内置与自定义一起找 —— 双击运行时不知道它来自哪边。"""
        for b in BUILTIN:
            if b.get("id") == sid:
                return dict(b)
        return self.get(sid)

    def upsert(self, rec: dict) -> dict:
        s = self._clean(rec)
        if not s["name"]:
            raise ValueError("脚本名不能为空")
        if not s["id"]:
            s["id"] = new_id(self.ids())
        for i, old in enumerate(self.scripts):
            if old.get("id") == s["id"]:
                self.scripts[i] = s
                break
        else:
            self.scripts.append(s)
        self.scripts.sort(key=lambda x: str(x.get("name") or "").lower())
        self.save()
        return s

    def remove(self, sid: str) -> bool:
        """内置脚本删不掉（返回 False），只能改副本。"""
        for b in BUILTIN:
            if b.get("id") == sid:
                return False
        n0 = len(self.scripts)
        self.data["scripts"] = [s for s in self.scripts if s.get("id") != sid]
        changed = len(self.data["scripts"]) != n0
        if changed:
            self.save()
        return changed

    def is_builtin(self, sid: str) -> bool:
        return any(b.get("id") == sid for b in BUILTIN)
