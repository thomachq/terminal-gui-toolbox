# -*- coding: utf-8 -*-
"""数据库巡检：发现实例 → 用你给的口令连一次 → 只看不写。

支持 MySQL / MariaDB、PostgreSQL、Redis、MongoDB、SQL Server，以及散落在
磁盘上的 SQLite 文件。分两层，和 secscan 一样：

  解析层   纯函数：从 ps / ss / 客户端输出里抽出结构，能离线测
  执行层   `discover()` 跑探测命令；`DbClient` 拼带凭据的命令并执行

**凭据怎么处理**（这条最重要）：
  · 口令只存在页面内存里，不写盘、不进日志、不回显
  · 连接时把凭据写成远端一个 600 的临时文件（MySQL 的 .cnf / PG 的 .pgpass），
    命令跑完立刻 `rm -f`，不用 `-p口令` 这种会留在 ps 与 shell 历史里的写法
  · 这一条命令**不走 run_traced**，所以侧边栏日志里不会出现凭据；
    页面自己写一行「已连接 xxx（凭据已隐藏）」
"""
from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

KINDS = {
    "mysql": {
        "label": "MySQL / MariaDB", "ports": [3306],
        "procs": ["mysqld", "mariadbd"], "client": "mysql",
        "version": "mysqld --version 2>/dev/null || mariadbd --version 2>/dev/null "
                   "|| mysql --version 2>/dev/null",
    },
    "postgres": {
        "label": "PostgreSQL", "ports": [5432],
        "procs": ["postgres", "postmaster"], "client": "psql",
        "version": "psql --version 2>/dev/null || postgres -V 2>/dev/null",
    },
    "redis": {
        "label": "Redis", "ports": [6379],
        "procs": ["redis-server"], "client": "redis-cli",
        "version": "redis-server -v 2>/dev/null || redis-cli -v 2>/dev/null",
    },
    "mongo": {
        "label": "MongoDB", "ports": [27017],
        "procs": ["mongod", "mongosh"], "client": "mongosh",
        "version": "mongod --version 2>/dev/null | head -2",
    },
    "mssql": {
        "label": "SQL Server", "ports": [1433],
        "procs": ["sqlservr"], "client": "sqlcmd",
        "version": "sqlcmd -? 2>&1 | head -2",
    },
    "sqlite": {
        "label": "SQLite 文件", "ports": [], "procs": [], "client": "sqlite3",
        "version": "sqlite3 --version",
    },
}

# 能连、能查的（SQLite 只列文件，不做连接）
QUERYABLE = ("mysql", "postgres", "redis", "mongo", "mssql")

DEFAULT_PORT = {k: (v["ports"][0] if v["ports"] else 0) for k, v in KINDS.items()}


@dataclass
class Cred:
    """一次性的连接凭据。只在内存里待着，页面切走就丢。"""

    user: str = ""
    password: str = ""
    host: str = ""
    port: int = 0
    socket: str = ""
    db: str = ""

    def describe(self) -> str:
        who = self.user or "（当前系统用户）"
        where = self.socket or f"{self.host or '127.0.0.1'}:{self.port or '?'}"
        return f"{who}@{where}"


# ------------------------------------------------------------------ 探测
def parse_ps(text: str) -> list[dict]:
    """`ps -eo pid=,user=,comm=,args=` → 只留跟数据库有关的进程。"""
    out = []
    for raw in (text or "").splitlines():
        parts = raw.split(None, 3)
        if len(parts) < 3:
            continue
        pid, user, comm = parts[0], parts[1], parts[2]
        args = parts[3] if len(parts) > 3 else ""
        kind = kind_of(comm, args)
        if not kind:
            continue
        out.append({"pid": pid, "user": user, "comm": comm,
                    "args": args, "kind": kind})
    return out


def kind_of(comm: str, args: str = "") -> str:
    c = (comm or "").lower()
    a = (args or "").lower()
    for kind, meta in KINDS.items():
        for p in meta["procs"]:
            if c == p or c.startswith(p) or f"/{p}" in a:
                return kind
    return ""


_SS_RE = re.compile(r"^(?:LISTEN|UNCONN)\s+\d+\s+\d+\s+(\S+?):(\d+)\s+\S+\s*(.*)$")
_NETSTAT_RE = re.compile(r"^(TCP|UDP)\s+(\S+?):(\d+)\s+\S+\s+\S+.*$", re.I)


def parse_listeners(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("netid", "proto", "active")):
            continue
        m = _SS_RE.match(line)
        if m:
            pid = ""
            pm = re.search(r"pid=(\d+)", m.group(3))
            if pm:
                pid = pm.group(1)
            pm2 = re.search(r'"([^"]+)"', m.group(3))
            out.append({"proto": "tcp", "addr": m.group(1),
                        "port": int(m.group(2)),
                        "proc": pm2.group(1) if pm2 else "", "pid": pid})
            continue
        m = _NETSTAT_RE.match(line)
        if m:
            out.append({"proto": m.group(1).lower(), "addr": m.group(2),
                        "port": int(m.group(3)), "proc": "", "pid": ""})
    return out


def parse_launch_args(kind: str, args: str) -> dict:
    """从启动参数里抠出数据目录 / 配置文件 / socket / 端口。

    `ps` 已经把命令行给我们了，没必要为了几个路径再跑一条命令 ——
    少一次往返，也少一次「远端没有那个工具」的失败。
    """
    a = args or ""
    out: dict[str, str] = {}

    def grab(patterns):
        for pat in patterns:
            m = re.search(pat, a)
            if m:
                return m.group(1)
        return ""

    if kind in ("mysql", "mariadb"):
        out["datadir"] = grab([r"--datadir=(\S+)"])
        out["config"] = grab([r"--defaults-file=(\S+)",
                              r"--defaults-extra-file=(\S+)"])
        out["socket"] = grab([r"--socket=(\S+)"])
    elif kind == "postgres":
        out["datadir"] = grab([r"(?:^|\s)-D\s+(\S+)", r"--pgdata=(\S+)"])
        out["config"] = grab([r"-c\s+config_file=(\S+)"])
    elif kind == "redis":
        # redis-server 127.0.0.1:6379 或 redis-server /etc/redis/redis.conf
        m = re.search(r"redis-server\s+(\S+)", a)
        if m:
            target = m.group(1)
            if ":" in target and not target.endswith(".conf"):
                out["bind"] = target
            else:
                out["config"] = target
        out["datadir"] = grab([r"--dir\s+(\S+)"])
    elif kind == "mongo":
        out["datadir"] = grab([r"--dbpath[= ](\S+)"])
        out["config"] = grab([r"(?:^|\s)-f\s+(\S+)", r"--config\s+(\S+)"])
    return {k: v for k, v in out.items() if v}


def merge_services(procs: list[dict], listeners: list[dict]) -> list[dict]:
    """把「进程」和「监听」并成一台台实例。

    有的容器里只有监听没有可见进程（ps 被裁过），有的进程没监听（--skip-networking），
    所以两边都认，双向补齐。
    """
    services: list[dict] = []
    used_pids: set[str] = set()

    for p in procs:
        kind = p["kind"]
        meta = KINDS.get(kind, {})
        ports = meta.get("ports") or []
        own = [x for x in listeners if x.get("pid") == p.get("pid")] if p.get("pid") else []
        if not own:
            own = [x for x in listeners
                   if x.get("port") in ports and x.get("pid") not in used_pids]
        port = own[0]["port"] if own else (ports[0] if ports else 0)
        addr = own[0]["addr"] if own else ""
        extra = parse_launch_args(kind, p.get("args", ""))
        services.append({
            "kind": kind,
            "label": meta.get("label", kind),
            "pid": p.get("pid", ""),
            "user": p.get("user", ""),
            "port": port,
            "addr": addr,
            "state": "运行中",
            "args": p.get("args", ""),
            "version": "",
            **extra,
        })
        if p.get("pid"):
            used_pids.add(p["pid"])

    # 只有监听、没有进程的（Docker 里常见）
    known_ports = {s["port"] for s in services if s.get("port")}
    for x in listeners:
        if x.get("pid") and x["pid"] in used_pids:
            continue
        kind = ""
        for k, meta in KINDS.items():
            if x["port"] in meta["ports"]:
                kind = k
                break
        if not kind or x["port"] in known_ports:
            continue
        meta = KINDS[kind]
        services.append({
            "kind": kind, "label": meta["label"], "pid": x.get("pid", ""),
            "user": "", "port": x["port"], "addr": x.get("addr", ""),
            "state": "运行中（无可见进程）", "args": "", "version": "",
        })
        known_ports.add(x["port"])
    return services


def sort_services(services: list[dict]) -> list[dict]:
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(services, key=lambda s: (order.get(s.get("kind"), 9),
                                           s.get("port") or 0))


def parse_version(text: str) -> str:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"\s+", " ", line)
        return line[:80]
    return ""


def parse_sqlite_files(text: str) -> list[dict]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        path, _, size = line.partition("\t")
        try:
            kb = int(size or 0) // 1024
        except ValueError:
            kb = 0
        out.append({
            "kind": "sqlite", "label": "SQLite 文件", "pid": "", "user": "",
            "port": 0, "addr": "", "state": "文件", "args": "",
            "version": "", "config": path,
            "size": f"{kb} KB" if kb < 1024 else f"{kb / 1024:.1f} MB",
        })
    return out


def summarize(services: list[dict]) -> str:
    if not services:
        return "没有发现数据库"
    names: dict[str, int] = {}
    for s in services:
        names[s.get("label") or s.get("kind")] = \
            names.get(s.get("label") or s.get("kind"), 0) + 1
    return " · ".join(f"{k}×{v}" if v > 1 else k for k, v in names.items())


# ------------------------------------------------------------------ 查询
def _b64(text: str) -> str:
    return base64.b64encode((text or "").encode()).decode()


def _tmpfile_cmd(b64: str, path_expr: str = '$(mktemp /tmp/.tt-db.XXXXXX)') -> str:
    """把一段文本以 600 权限落到远端临时文件，路径写进 shell 变量 `f`。"""
    return (f"f={path_expr}; umask 077; printf '%s' {shlex.quote(b64)} | "
            f"base64 -d > \"$f\" && chmod 600 \"$f\"")


def mysql_cmd(cred: Cred, sql: str, db: str = "", binary: str = "mysql") -> str:
    lines = ["[client]"]
    if cred.user:
        lines.append(f"user={cred.user}")
    if cred.password:
        lines.append(f"password={cred.password}")
    lines.append(f"host={cred.host or '127.0.0.1'}")
    lines.append(f"port={cred.port or 3306}")
    if cred.socket:
        lines.append(f"socket={cred.socket}")
    cnf = "\n".join(lines) + "\n"
    tail = f" {shlex.quote(db)}" if db else ""
    return (
        _tmpfile_cmd(_b64(cnf)) + "; "
        f"{binary} --defaults-extra-file=\"$f\" --connect-timeout=8 "
        f"-N -B{tail} -e {shlex.quote(sql)}; "
        "rc=$?; rm -f \"$f\"; exit $rc")


def pg_cmd(cred: Cred, sql: str, db: str = "postgres") -> str:
    # .pgpass 格式：host:port:db:user:password
    pgpass = (f"{cred.host or '127.0.0.1'}:{cred.port or 5432}:*:"
              f"{cred.user or ''}:{cred.password or ''}\n")
    return (
        _tmpfile_cmd(_b64(pgpass), "$(mktemp /tmp/.tt-pg.XXXXXX)") + "; "
        f"PGPASSFILE=\"$f\" psql -h {shlex.quote(cred.host or '127.0.0.1')} "
        f"-p {int(cred.port or 5432)} -U {shlex.quote(cred.user or 'postgres')} "
        f"-d {shlex.quote(db or 'postgres')} -At -A -F '|' -v ON_ERROR_STOP=1 "
        f"-c {shlex.quote(sql)}; "
        "rc=$?; rm -f \"$f\"; exit $rc")


def redis_cmd(cred: Cred, args: str, binary: str = "redis-cli") -> str:
    # 口令通过 REDISCLI_AUTH 传，且用 base64 解出来再赋值 ——
    # 命令串里不会出现明文，日志与 ps 都看不到。
    auth = ""
    if cred.password:
        auth = f"REDISCLI_AUTH=$(printf '%s' {shlex.quote(_b64(cred.password))} | base64 -d) "
    return (f"{auth}{binary} -h {shlex.quote(cred.host or '127.0.0.1')} "
            f"-p {int(cred.port or 6379)} --no-auth-warning {args}")


def query_cmd(kind: str, cred: Cred, sql: str) -> str:
    if kind in ("mysql", "mariadb"):
        return mysql_cmd(cred, sql)
    if kind == "postgres":
        return pg_cmd(cred, sql)
    if kind == "redis":
        return redis_cmd(cred, sql)
    if kind == "mongo":
        uri = (f"mongodb://{cred.user}:{cred.password}@"
               f"{cred.host or '127.0.0.1'}:{cred.port or 27017}/?authSource=admin")
        # 用环境变量接 URI，命令串里不出现明文口令
        return (f"MONGO_URI=$(printf '%s' {shlex.quote(_b64(uri))} | base64 -d) "
                f"mongosh --quiet \"$MONGO_URI\" --eval {shlex.quote(sql)}")
    if kind == "mssql":
        return (f"SQLCMDPASSWORD=$(printf '%s' "
                f"{shlex.quote(_b64(cred.password))} | base64 -d) "
                f"sqlcmd -S {cred.host or '127.0.0.1'},{cred.port or 1433} "
                f"-U {shlex.quote(cred.user)} -h -1 -W -s '|' "
                f"-Q {shlex.quote(sql)}")
    return ""


# ------------------------------------------------------------------ SQL
SQL_LIST_DBS = {
    "mysql": ("SELECT table_schema, COUNT(*) AS tables, "
              "IFNULL(ROUND(SUM(data_length+index_length)/1048576, 2), 0) AS mb "
              "FROM information_schema.tables "
              "GROUP BY table_schema ORDER BY 3 DESC"),
    "postgres": ("SELECT datname, 0, "
                 "ROUND(pg_database_size(datname)/1048576.0, 2) "
                 "FROM pg_database ORDER BY 3 DESC"),
    "redis": "INFO keyspace",
    "mongo": ("db.adminCommand({listDatabases:1}).databases"
              ".forEach(d => print(d.name + '|' + (d.sizeOnDisk/1048576).toFixed(1)))"),
    "mssql": ("SELECT name, 0, 0 FROM sys.databases ORDER BY name"),
}
SQL_STATUS = {
    "mysql": ("SHOW GLOBAL STATUS WHERE Variable_name IN "
              "('Threads_connected','Uptime','Slow_queries','Queries',"
              "'Aborted_connects','Max_used_connections')"),
    "postgres": ("SELECT 'connections', count(*)::text FROM pg_stat_activity "
                 "UNION ALL SELECT 'max_connections', setting FROM pg_settings "
                 "WHERE name='max_connections'"),
    "redis": "INFO clients",
    "mongo": "db.serverStatus().connections",
    "mssql": "SELECT 'connections', COUNT(*) FROM sys.dm_exec_sessions",
}


def parse_db_rows(kind: str, text: str) -> list[dict]:
    """把列库结果解析成 {name, tables, size}，Redis 走 keyspace 特殊解析。

    分隔符不固定：MySQL 的 `-B` 输出是制表符，PostgreSQL 我们显式指定了 `|`，
    所以两种都认 —— 只认一种的话，换一个库就整列串味。
    """
    rows: list[dict] = []
    if kind == "redis":
        # db0:keys=12,expires=0,avg_ttl=0
        for raw in (text or "").splitlines():
            m = re.match(r"^(db\d+):keys=(\d+).*?expires=(\d+)", raw.strip())
            if m:
                rows.append({"name": m.group(1), "tables": int(m.group(2)),
                             "size": f"过期 {m.group(3)} 个"})
        return rows
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        sep = "\t" if "\t" in line else ("|" if "|" in line else None)
        parts = line.split(sep) if sep else [line]
        if len(parts) < 3:
            rows.append({"name": parts[0], "tables": "—", "size": "—"})
            continue
        name, tables, size = parts[0], parts[1], parts[2]
        try:
            tables_s = str(int(float(tables)))
        except ValueError:
            tables_s = tables or "—"
        size_s = f"{float(size):.1f} MB" if _is_num(size) else (size or "—")
        rows.append({"name": name, "tables": tables_s, "size": size_s})
    return rows


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def parse_status(kind: str, text: str) -> list[tuple[str, str]]:
    """状态行统一成 [(名字, 值)]，页面按两列渲染。"""
    out: list[tuple[str, str]] = []
    if kind == "redis":
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            out.append((k, v.strip()))
        return out
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|") if "|" in line else line.split(None, 1)
        if len(parts) >= 2:
            out.append((parts[0].strip(), parts[1].strip()))
        elif len(parts) == 1:
            out.append((parts[0].strip(), ""))
    return out


def status_summary(kind: str, pairs: list[tuple[str, str]]) -> str:
    d = {k.lower(): v for k, v in pairs}
    if kind in ("mysql", "mariadb"):
        conn = d.get("threads_connected", "?")
        up = d.get("uptime", "0")
        try:
            days = int(float(up)) // 86400
            up_s = f"{days} 天"
        except (TypeError, ValueError):
            up_s = up
        return f"连接 {conn} · 慢查询 {d.get('slow_queries', '?')} · 已运行 {up_s}"
    if kind == "postgres":
        return f"连接 {d.get('connections', '?')} / 上限 {d.get('max_connections', '?')}"
    if kind == "redis":
        return f"客户端 {d.get('connected_clients', '?')} · 阻塞 {d.get('blocked_clients', '?')}"
    return " · ".join(f"{k}={v}" for k, v in pairs[:4])


# ------------------------------------------------------------------ 备份
def backup_cmd(kind: str, cred: Cred, db: str, target: str) -> str:
    """只用官方自带的 dump 工具，不引入任何第三方组件。"""
    q = shlex.quote
    if kind in ("mysql", "mariadb"):
        lines = ["[client]", f"user={cred.user}"]
        if cred.password:
            lines.append(f"password={cred.password}")
        lines += [f"host={cred.host or '127.0.0.1'}", f"port={cred.port or 3306}"]
        cnf = "\n".join(lines) + "\n"
        return (_tmpfile_cmd(_b64(cnf)) + "; "
                f"mysqldump --defaults-extra-file=\"$f\" --single-transaction "
                f"--quick --routines --events {q(db)} | gzip > {q(target)}; "
                "rc=$?; rm -f \"$f\"; ls -l " + q(target) + "; exit $rc")
    if kind == "postgres":
        pgpass = (f"{cred.host or '127.0.0.1'}:{cred.port or 5432}:*:"
                  f"{cred.user}:{cred.password}\n")
        return (_tmpfile_cmd(_b64(pgpass), "$(mktemp /tmp/.tt-pg.XXXXXX)") + "; "
                f"PGPASSFILE=\"$f\" pg_dump -h {q(cred.host or '127.0.0.1')} "
                f"-p {int(cred.port or 5432)} -U {q(cred.user)} -Fc "
                f"-f {q(target)} {q(db)}; "
                "rc=$?; rm -f \"$f\"; ls -l " + q(target) + "; exit $rc")
    return ""


def default_target(kind: str, db: str, home: str = "/tmp") -> str:
    import time
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", db or "all")
    ext = ".dump" if kind == "postgres" else ".sql.gz"
    return f"{home.rstrip('/') or '/tmp'}/{name}-{ts}{ext}"


# ------------------------------------------------------------------ 执行
POSIX_PS = "ps -eo pid=,user=,comm=,args= 2>/dev/null | head -800"
POSIX_LISTEN = "ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null"
POSIX_SQLITE = ("find /var/www /opt /srv /home /root /var/lib -maxdepth 3 "
                "\\( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \\) "
                "-size +100k -printf '%p\\t%s\\n' 2>/dev/null | head -40")


class DbScanner:
    def __init__(self, session, device=None):
        self.session = session
        self.device = device
        self.commands: list[str] = []

    def _family(self) -> str:
        fam = (getattr(self.device, "os_family", "") or "").lower()
        if fam:
            return fam
        import sys
        return {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux")

    def _run(self, cmd: str, timeout: int = 20) -> str:
        self.commands.append(cmd)
        if self.session is None:
            return ""
        runner = getattr(self.session, "run_traced", None) or self.session.run
        try:
            _rc, out, _err = runner(cmd, timeout=timeout)
        except Exception:  # noqa: BLE001
            return ""
        return out or ""

    def discover(self) -> dict:
        fam = self._family()
        procs: list[dict] = []
        listeners: list[dict] = []
        services: list[dict] = []
        if fam == "windows":
            text = self._run("tasklist /fo csv /nh")
            procs = parse_tasklist(text)
            listeners = parse_listeners(self._run("netstat -ano"))
            services = merge_services(procs, listeners)
            # Windows 的 SQL Server 用 1433，靠服务名兜一层
            if not services:
                svc = self._run("sc query MSSQLSERVER")
                if "RUNNING" in svc.upper():
                    services = [{"kind": "mssql", "label": "SQL Server",
                                 "pid": "", "user": "", "port": 1433,
                                 "addr": "", "state": "运行中", "args": "",
                                 "version": ""}]
        else:
            procs = parse_ps(self._run(POSIX_PS))
            listeners = parse_listeners(self._run(POSIX_LISTEN))
            services = merge_services(procs, listeners)
            services += parse_sqlite_files(self._run(POSIX_SQLITE))
        services = sort_services(services)
        return {"status": "ok" if services else "empty",
                "services": services,
                "detail": summarize(services),
                "commands": list(self.commands)}

    def version_of(self, service: dict) -> str:
        kind = service.get("kind", "")
        meta = KINDS.get(kind, {})
        cmd = meta.get("version")
        if not cmd:
            return ""
        return parse_version(self._run(cmd, timeout=15))


def parse_tasklist(text: str) -> list[dict]:
    """Windows `tasklist /fo csv /nh` → 挑出数据库进程。"""
    out = []
    for raw in (text or "").splitlines():
        parts = [p.strip('"') for p in raw.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].strip('"')
        pid = parts[1]
        kind = kind_of(name.split(".")[0])
        if kind:
            out.append({"pid": pid, "user": "", "comm": name, "args": name,
                        "kind": kind})
    return out


class DbClient:
    """在某个实例上跑一条查询。凭据只活在实例里。"""

    def __init__(self, session, service: dict, cred: Cred):
        self.session = session
        self.service = service
        self.cred = cred
        self.commands: list[str] = []

    def run(self, sql: str, db: str = "", timeout: int = 30) -> tuple[int, str, str]:
        kind = self.service.get("kind", "")
        cred = self.cred
        if not cred.port:
            cred.port = int(self.service.get("port") or DEFAULT_PORT.get(kind, 0))
        if not cred.host:
            cred.host = "127.0.0.1"
        cmd = (pg_cmd(cred, sql, db=db or cred.db or "postgres")
               if kind == "postgres" else query_cmd(kind, cred, sql))
        self.commands.append(cmd)
        if self.session is None:
            return 1, "", "尚未连接设备"
        # 刻意用 run 而不是 run_traced：命令里带着临时凭据文件的内容，
        # 记进侧边栏日志没有好处（见模块开头的说明）。
        try:
            return self.session.run(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return 1, "", f"{type(e).__name__}: {e}"

    def ping(self) -> tuple[bool, str]:
        kind = self.service.get("kind", "")
        probe = {"mysql": "SELECT VERSION()", "postgres": "SELECT version()",
                 "redis": "PING", "mongo": "db.version()",
                 "mssql": "SELECT @@VERSION"}.get(kind, "SELECT 1")
        rc, out, err = self.run(probe, db=self.cred.db or "")
        if rc == 0 and (out or "").strip():
            return True, parse_version(out)
        return False, ((err or out).strip() or f"rc={rc}")[:300]

    def databases(self) -> list[dict]:
        kind = self.service.get("kind", "")
        sql = SQL_LIST_DBS.get(kind)
        if not sql:
            return []
        rc, out, err = self.run(sql, db=self.cred.db or "")
        if rc != 0:
            raise DbError(((err or out).strip() or f"rc={rc}")[:300])
        return parse_db_rows(kind, out)

    def status(self) -> list[tuple[str, str]]:
        kind = self.service.get("kind", "")
        sql = SQL_STATUS.get(kind)
        if not sql:
            return []
        rc, out, err = self.run(sql, db=self.cred.db or "")
        if rc != 0:
            raise DbError(((err or out).strip() or f"rc={rc}")[:300])
        return parse_status(kind, out)

    def backup(self, db: str, target: str) -> dict:
        kind = self.service.get("kind", "")
        cred = self.cred
        if not cred.port:
            cred.port = int(self.service.get("port") or DEFAULT_PORT.get(kind, 0))
        if not cred.host:
            cred.host = "127.0.0.1"
        cmd = backup_cmd(kind, cred, db, target)
        if not cmd:
            return {"ok": False, "detail": f"{KINDS.get(kind, {}).get('label', kind)} "
                                          f"没有内置备份方式"}
        if self.session is None:
            return {"ok": False, "detail": "尚未连接设备"}
        try:
            rc, out, err = self.session.run(cmd, timeout=1800)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
        tail = ((out or "").strip() or (err or "").strip())[-300:]
        return {"ok": rc == 0, "detail": tail or f"rc={rc}", "path": target}


class DbError(Exception):
    pass
