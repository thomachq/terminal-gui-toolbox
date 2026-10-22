# -*- coding: utf-8 -*-
"""数据库模块的离线自测：解析 + 命令拼装，不连库。

重点验两件事：
  · 探测：进程 / 端口 / 启动参数能不能对成一台台实例
  · 凭据：拼出来的命令里**不能出现明文口令**，临时文件必须用完即删
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import dbscan as D   # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


PS = """    1 root     systemd         /sbin/init
  812 mysql    mysqld          /usr/sbin/mysqld --datadir=/var/lib/mysql --socket=/run/mysqld/mysqld.sock
  900 redis    redis-server    /usr/bin/redis-server 127.0.0.1:6379
  950 postgres postgres        /usr/lib/postgresql/16/bin/postgres -D /var/lib/postgresql/16/main
 1000 root     mongod          /usr/bin/mongod --dbpath /var/lib/mongodb -f /etc/mongod.conf
"""

LISTEN = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0      128      0.0.0.0:3306     0.0.0.0:*   users:(("mysqld",pid=812,fd=20))
LISTEN 0      128    127.0.0.1:6379     0.0.0.0:*   users:(("redis-server",pid=900,fd=6))
LISTEN 0      128    127.0.0.1:5432     0.0.0.0:*   users:(("postgres",pid=950,fd=7))
"""


def test_discovery() -> None:
    print("== 1. 进程 / 端口 / 启动参数 ==")
    procs = D.parse_ps(PS)
    chk(len(procs) == 4, "四个数据库进程（systemd 被跳过）", str(len(procs)))
    kinds = [p["kind"] for p in procs]
    chk(kinds == ["mysql", "redis", "postgres", "mongo"], "类型识别正确", str(kinds))

    my = next(p for p in procs if p["kind"] == "mysql")
    extra = D.parse_launch_args("mysql", my["args"])
    chk(extra.get("datadir") == "/var/lib/mysql", "MySQL 数据目录", str(extra.get("datadir")))
    chk(extra.get("socket") == "/run/mysqld/mysqld.sock", "MySQL socket")

    pg = next(p for p in procs if p["kind"] == "postgres")
    chk(D.parse_launch_args("postgres", pg["args"]).get("datadir")
        == "/var/lib/postgresql/16/main", "-D 后面的数据目录")

    rd = next(p for p in procs if p["kind"] == "redis")
    chk(D.parse_launch_args("redis", rd["args"]).get("bind") == "127.0.0.1:6379",
        "redis-server 的监听地址")
    chk(D.parse_launch_args("redis", "/usr/bin/redis-server /etc/redis/redis.conf")
        .get("config") == "/etc/redis/redis.conf", "redis 配置文件形态")

    mg = next(p for p in procs if p["kind"] == "mongo")
    mgargs = D.parse_launch_args("mongo", mg["args"])
    chk(mgargs.get("datadir") == "/var/lib/mongodb" and
        mgargs.get("config") == "/etc/mongod.conf", "mongo 的 dbpath 与 -f", str(mgargs))


def test_merge() -> None:
    print("== 2. 实例合并 ==")
    procs = D.parse_ps(PS)
    listeners = D.parse_listeners(LISTEN)
    svcs = D.sort_services(D.merge_services(procs, listeners))
    chk(len(svcs) == 4, "四台实例", str([(s["kind"], s["port"]) for s in svcs]))
    my = next(s for s in svcs if s["kind"] == "mysql")
    chk(my["port"] == 3306 and my["pid"] == "812", "MySQL 端口与 PID",
        f"{my['port']}/{my['pid']}")
    chk(my["addr"] == "0.0.0.0", "监听地址取到", my["addr"])

    # 只有监听、看不到进程（容器里常见）
    only = D.merge_services([], D.parse_listeners(
        "LISTEN 0 128 0.0.0.0:27017 0.0.0.0:* users:((\"docker-proxy\",pid=1,fd=1))\n"))
    chk(len(only) == 1 and only[0]["kind"] == "mongo", "只有监听也能认出来",
        str([s["kind"] for s in only]))
    chk("无可见进程" in only[0]["state"], "如实说明没有进程信息", only[0]["state"])

    # 非默认端口：靠 PID 归位
    odd = D.merge_services(D.parse_ps(PS), D.parse_listeners(
        "LISTEN 0 128 0.0.0.0:13306 0.0.0.0:* users:((\"mysqld\",pid=812,fd=9))\n"))
    myodd = next(s for s in odd if s["kind"] == "mysql")
    chk(myodd["port"] == 13306, "改了端口的 MySQL 仍然归到同一台", str(myodd["port"]))

    chk("没有发现数据库" in D.summarize([]), "空列表有说明")
    chk("MySQL" in D.summarize(svcs), "摘要里带类型名", D.summarize(svcs))


def test_results() -> None:
    print("== 3. 查询结果解析 ==")
    rows = D.parse_db_rows("mysql",
                           "app\t12\t3.5\nmysql\t31\t0.0\nreport\t0\t0\n")
    chk(len(rows) == 3, "三行库", str(len(rows)))
    chk(rows[0]["name"] == "app" and rows[0]["size"] == "3.5 MB",
        "库名与大小", f"{rows[0]['name']} {rows[0]['size']}")
    chk(rows[0]["tables"] == "12", "表数量", rows[0]["tables"])

    redis = D.parse_db_rows("redis", "# Keyspace\ndb0:keys=12,expires=3,avg_ttl=0\n")
    chk(redis and redis[0]["name"] == "db0" and redis[0]["tables"] == 12,
        "Redis keyspace 行", str(redis))

    pairs = D.parse_status("mysql", "Threads_connected\t4\nUptime\t90061\n"
                                   "Slow_queries\t2\n")
    chk(len(pairs) == 3 and pairs[0] == ("Threads_connected", "4"), "状态键值",
        str(pairs))
    summ = D.status_summary("mysql", pairs)
    chk("连接 4" in summ and "慢查询 2" in summ and "天" in summ,
        "状态摘要", summ)
    rp = D.parse_status("redis", "# Clients\nconnected_clients:2\nblocked_clients:0\n")
    chk(dict(rp).get("connected_clients") == "2", "Redis INFO 解析", str(dict(rp)))

    chk(D.parse_version("mysqld  Ver 8.0.36 for Linux\n") ==
        "mysqld Ver 8.0.36 for Linux", "版本行压缩空白",
        D.parse_version("mysqld  Ver 8.0.36 for Linux\n"))
    files = D.parse_sqlite_files("/var/www/a.db\t204800\n/opt/b.sqlite\t1024\n")
    chk(len(files) == 2 and files[0]["size"] == "200 KB", "SQLite 文件列表",
        str([f["size"] for f in files]))
    win = D.parse_tasklist('"sqlservr.exe","1234","Services","0","12,345 K"\n'
                           '"notepad.exe","99","Console","1","1 K"\n')
    chk(len(win) == 1 and win[0]["kind"] == "mssql", "Windows 进程列表",
        str([w["kind"] for w in win]))


def test_commands() -> None:
    print("== 4. 命令拼装与凭据保护 ==")
    cred = D.Cred(user="root", password="s3cr3t'\"pw", host="127.0.0.1", port=3306)
    cmd = D.mysql_cmd(cred, "SELECT 1")
    chk("--defaults-extra-file" in cmd, "MySQL 走 defaults-extra-file")
    chk("s3cr3t" not in cmd, "命令里没有明文口令")
    chk("mktemp" in cmd and "rm -f" in cmd, "临时文件用完即删")
    chk("chmod 600" in cmd, "临时文件权限 600")
    blob = re.search(r"printf '%s' ([A-Za-z0-9+/=]+)", cmd)
    if blob:
        cnf = base64.b64decode(blob.group(1)).decode()
        chk("[client]" in cnf and "password=s3cr3t" in cnf,
            "base64 解出来确实是客户端配置", cnf.replace("\n", " | ")[:60])
        chk('pw' in cnf, "含引号的口令也能原样保留")
    else:
        chk(False, "找不到 base64 段")

    pg = D.pg_cmd(cred, "SELECT 1")
    chk("PGPASSFILE" in pg and "s3cr3t" not in pg, "PG 用 PGPASSFILE 且不含明文")
    rd = D.redis_cmd(cred, "INFO")
    chk("REDISCLI_AUTH" in rd and "s3cr3t" not in rd,
        "Redis 用 REDISCLI_AUTH 且不含明文")
    mongo = D.query_cmd("mongo", cred, "db.version()")
    chk("MONGO_URI" in mongo and "s3cr3t" not in mongo, "Mongo 用环境变量接 URI")
    mssql = D.query_cmd("mssql", cred, "SELECT 1")
    chk("SQLCMDPASSWORD" in mssql and "s3cr3t" not in mssql,
        "sqlcmd 用 SQLCMDPASSWORD")
    chk(D.DbClient is not None and hasattr(D.DbClient, "ping"), "客户端类有 ping")

    bak = D.backup_cmd("mysql", cred, "app", "/tmp/app.sql.gz")
    chk("mysqldump" in bak and "--single-transaction" in bak and "gzip" in bak,
        "MySQL 备份用 mysqldump + gzip")
    chk("s3cr3t" not in bak, "备份命令也不含明文口令")
    pgbak = D.backup_cmd("postgres", cred, "app", "/tmp/app.dump")
    chk("pg_dump" in pgbak and "-Fc" in pgbak, "PG 备份用 pg_dump -Fc")
    chk(D.backup_cmd("redis", cred, "0", "/tmp/x") == "", "Redis 不硬造备份方式")

    t = D.default_target("mysql", "my app", "/root")
    chk(t.startswith("/root/my_app-") and t.endswith(".sql.gz"),
        "默认路径带库名与时间戳", t)
    chk(D.default_target("postgres", "app", "/tmp").endswith(".dump"),
        "PG 用 .dump 后缀")


def test_cred() -> None:
    print("== 5. 凭据对象 ==")
    c = D.Cred(user="root", host="10.0.0.5", port=3306)
    chk(c.describe() == "root@10.0.0.5:3306", "describe 不暴露口令", c.describe())
    s = D.Cred(user="root", socket="/run/mysqld/mysqld.sock")
    chk(s.describe().endswith("mysqld.sock"), "走 socket 时显示 socket",
        s.describe())
    chk(D.KINDS["mysql"]["ports"] == [3306] and D.DEFAULT_PORT["redis"] == 6379,
        "默认端口表")


def main() -> int:
    test_discovery()
    test_merge()
    test_results()
    test_commands()
    test_cred()
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项：通过 {npass}，失败 {len(RESULTS) - npass}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL · {name}  {detail}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
