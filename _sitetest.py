# -*- coding: utf-8 -*-
"""站点与域名绑定的离线自测。

两件事最容易出事，所以重点打这两处：
  · 解析：`server { }` 里套 `location { }`，花括号配对不能错；
    注释里的括号、引号里的 `#` 都不能带偏
  · 写入：只改目标块的 server_name，其它字节不动；配置检查失败要能回滚
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import webstack as W   # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(ok)


NGINX = """# configuration file /etc/nginx/nginx.conf:
user www-data;
events { worker_connections 768; }
http {
    include /etc/nginx/sites-enabled/*;
}
# configuration file /etc/nginx/sites-enabled/default:
# 下面这个 server 是默认站点
server {
    listen 80 default_server;
    server_name _;
    root /var/www/html;
    location / {
        try_files $uri $uri/ =404;
    }
    location ~ \\.php$ {
        # 注释里的 } 不能算数
        fastcgi_pass unix:/run/php.sock;
    }
}
server {
    listen 443 ssl;
    server_name a.example.com www.a.example.com;
    root /var/www/a;
    ssl_certificate /etc/ssl/a.pem;
    ssl_certificate_key /etc/ssl/a.key;
    add_header X-Note "a#b";
}
"""

APACHE = """# configuration file /etc/apache2/sites-enabled/000-default.conf:
<VirtualHost *:80>
    ServerName old.example.com
    ServerAlias www.old.example.com other.example.com
    DocumentRoot /var/www/html
</VirtualHost>
"""


def test_split() -> None:
    print("== 1. 配置分段 ==")
    files = W.split_config_dump(NGINX)
    chk(len(files) == 2, "两个文件段", str(list(files)))
    chk("/etc/nginx/sites-enabled/default" in files, "站点文件路径完整")
    chk("server {" in files["/etc/nginx/sites-enabled/default"], "正文里有内容")
    chk(W.split_config_dump("没有标记的文本") == {}, "没有标记就返回空")


def test_blocks() -> None:
    print("== 2. server 块配对 ==")
    text = W.split_config_dump(NGINX)["/etc/nginx/sites-enabled/default"]
    blocks = W.nginx_server_blocks(text)
    chk(len(blocks) == 2, "两个 server 块（location 不算）", str(len(blocks)))
    first = text.splitlines()[blocks[0][0]:blocks[0][1] + 1]
    chk(first[0].strip() == "server {", "起点是 server {", first[0].strip())
    chk(first[-1].strip() == "}", "终点是配对的 }", first[-1].strip())
    chk(any("fastcgi_pass" in x for x in first), "location 被完整包住")
    # 注释里的 } 不能提前收尾
    trick = "server {\n  # }\n  listen 80;\n}\n"
    chk(len(W.nginx_server_blocks(trick)) == 1, "注释里的 } 不计入")
    quoted = 'server {\n  add_header X "a}b";\n  listen 80;\n}\n'
    chk(len(W.nginx_server_blocks(quoted)) == 1, "引号里的 } 不计入")


def test_nginx_parse() -> None:
    print("== 3. nginx 站点解析 ==")
    files = W.split_config_dump(NGINX)
    sites = W.parse_nginx_sites(files)
    chk(len(sites) == 2, "两个站点", str(len(sites)))
    d = sites[0]
    chk(d.names == [], "server_name _ 视为未绑定", str(d.names))
    chk(d.listen == ["80 default_server"], "listen 原样保留", str(d.listen))
    chk(d.root == "/var/www/html", "站点根", d.root)
    chk(d.line == 2, "行号对得上（文件头那行注释是第 1 行）", str(d.line))
    a = sites[1]
    chk(a.names == ["a.example.com", "www.a.example.com"], "域名与别名", str(a.names))
    chk(a.ssl == "/etc/ssl/a.pem", "证书路径", a.ssl)
    chk(a.as_row()["ssl"] == "有", "表格里标出有证书")
    chk(W.summarize_sites(sites) == "2 个站点（1 个已绑定域名）",
        "摘要文案", W.summarize_sites(sites))


def test_nginx_write() -> None:
    print("== 4. nginx 域名改写 ==")
    text = W.split_config_dump(NGINX)["/etc/nginx/sites-enabled/default"]
    new = W.set_nginx_names(text, 1, ["b.example.com", "www.b.example.com"])
    chk("server_name b.example.com www.b.example.com;" in new, "写入了新域名")
    chk("a.example.com" not in new, "旧域名被替换")
    chk("add_header X-Note" in new, "同一块的其它指令没丢")
    chk(len(W.nginx_server_blocks(new)) == 2, "块数量不变")
    chk(W.nginx_server_blocks(text) == W.nginx_server_blocks(new),
        "块的起止行号不变（只改了内容）")
    chk(W.parse_nginx_sites({"f": new})[1].names
        == ["b.example.com", "www.b.example.com"], "改完能重新解析")
    # 默认站点（server_name _）也能改
    new0 = W.set_nginx_names(text, 0, ["d.example.com"])
    chk("server_name d.example.com;" in new0, "未绑定的块也能绑定")
    # 没有 server_name 行时补一行
    bare = "server {\n    listen 8080;\n}\n"
    got = W.set_nginx_names(bare, 0, ["x.example.com"])
    chk("server_name x.example.com;" in got and "listen 8080" in got,
        "缺 server_name 时补上", got.replace("\n", " / "))
    # 越界
    try:
        W.set_nginx_names(text, 9, ["x"])
        chk(False, "越界要报错")
    except ValueError as e:
        chk("第 10 个" in str(e), "越界报错信息清楚", str(e))
    # 空域名 → 写成 _
    chk("server_name _;" in W.set_nginx_names(text, 1, []), "清空域名写成 _")


def test_apache() -> None:
    print("== 5. Apache VirtualHost ==")
    files = W.split_config_dump(APACHE)
    sites = W.parse_apache_sites(files)
    chk(len(sites) == 1, "一个 vhost", str(len(sites)))
    s = sites[0]
    chk(s.names == ["old.example.com", "www.old.example.com", "other.example.com"],
        "ServerName + ServerAlias", str(s.names))
    chk(s.root == "/var/www/html", "DocumentRoot", s.root)
    chk(s.listen == ["*:80"], "监听", str(s.listen))
    chk(s.line == 1, "行号", str(s.line))

    text = list(files.values())[0]
    new = W.set_apache_names(text, 0, ["new.example.com", "www.new.example.com"])
    chk("ServerName new.example.com" in new, "写 ServerName")
    chk("ServerAlias www.new.example.com" in new, "写 ServerAlias")
    chk("other.example.com" not in new, "旧别名被替换")
    chk(W.parse_apache_sites({"f": new})[0].names
        == ["new.example.com", "www.new.example.com"], "改完能重新解析")

    one = W.set_apache_names(text, 0, ["only.example.com"])
    chk("# ServerAlias" in one or "ServerAlias" not in
        [x.strip() for x in one.splitlines() if x.strip().startswith("ServerAlias")],
        "只剩一个域名时把别名注释掉", one.replace("\n", " / "))

    bare = "<VirtualHost *:80>\n    DocumentRoot /x\n</VirtualHost>\n"
    got = W.set_apache_names(bare, 0, ["y.example.com", "a.y.example.com"])
    chk("ServerName y.example.com" in got and "ServerAlias a.y.example.com" in got,
        "缺行时补 ServerName 与 ServerAlias", got.replace("\n", " / "))
    try:
        W.set_apache_names(bare, 5, ["z"])
        chk(False, "越界要报错")
    except ValueError:
        chk(True, "越界报错")
    try:
        W.set_apache_names(bare, 0, [])
        chk(False, "空域名要报错")
    except ValueError as e:
        chk("不能为空" in str(e), "空域名报错", str(e))


def test_iis() -> None:
    print("== 6. IIS 只读 ==")
    sites = W.parse_iis_sites(
        'SITE "Default Web Site" (id:1,bindings:http/*:80:,state:Started)\n'
        'SITE "app" (id:2,bindings:http/*:8080:,state:Stopped)\n')
    chk(len(sites) == 2, "两个站点", str(len(sites)))
    chk(sites[0].names == ["Default Web Site"], "站点名", str(sites[0].names))
    chk(sites[0].listen == ["http/*:80:"], "绑定信息", str(sites[0].listen))
    chk("IIS 管理器" in sites[0].note, "说明改绑定的正确入口")
    chk(W.parse_sites("iis", "") == [], "空输出返回空")


def test_binder_ok() -> None:
    print("== 7. 写入流程（全部成功） ==")
    site = W.parse_nginx_sites(W.split_config_dump(NGINX))[1]
    calls: list[str] = []

    def runner(cmd: str, admin: bool):
        calls.append(cmd)
        if cmd.startswith("cat "):
            return 0, NGINX.split("default:")[1], ""
        return 0, "", ""

    res = W.SiteBinder(None, site, runner=runner).update(["c.example.com"])
    chk(res["ok"], "整体成功", res.get("detail"))
    steps = [s["name"] for s in res["steps"]]
    chk(steps[:2] == ["读取", "备份"], "先读后备份", str(steps))
    chk("配置检查" in steps and "重载" in steps, "检查后再重载", str(steps))
    chk(any(c.startswith("cp -a") for c in calls), "备份命令是 cp -a")
    chk(any("base64 -d" in c for c in calls), "写入走 base64 管道")
    chk(res.get("backup", "").endswith(tuple("0123456789")), "返回备份文件名",
        res.get("backup", ""))


def test_binder_fail() -> None:
    print("== 8. 写入流程（配置检查失败 → 回滚） ==")
    site = W.parse_nginx_sites(W.split_config_dump(NGINX))[1]
    calls: list[str] = []

    def runner(cmd: str, admin: bool):
        calls.append(cmd)
        if cmd.startswith("cat "):
            return 0, NGINX.split("default:")[1], ""
        if "nginx -t" in cmd:
            return 1, "", "nginx: [emerg] invalid server_name"
        return 0, "", ""

    res = W.SiteBinder(None, site, runner=runner).update(["bad example"])
    chk(not res["ok"], "失败要如实返回")
    chk("配置检查未通过" in res["detail"], "说明失败原因", res["detail"][:60])
    steps = [s["name"] for s in res["steps"]]
    chk("还原" in steps, "回滚步骤出现了", str(steps))
    chk(any(c.startswith("cp -a") and ".bak-" in c for c in calls),
        "用 .bak 盖回去")

    def bad_write(cmd: str, admin: bool):
        if cmd.startswith("cat "):
            return 0, NGINX.split("default:")[1], ""
        if "base64 -d" in cmd:
            return 1, "", "磁盘只读"
        return 0, "", ""

    res2 = W.SiteBinder(None, site, runner=bad_write).update(["x.example.com"])
    chk(not res2["ok"] and "写入失败" in res2["detail"], "写失败要还原",
        res2["detail"][:40])

    # 域名没变：不写、不备份
    same = "server {\n    listen 80;\n    server_name same.com;\n}\n"
    s2 = W.parse_nginx_sites({"f": same})[0]
    calls.clear()
    res3 = W.SiteBinder(None, s2, runner=lambda c, a: (0, same, "")).update(["same.com"])
    chk(res3["ok"] and "没有变化" in res3["detail"], "没变化时不写盘",
        res3["detail"])
    chk(not any("base64 -d" in c for c in calls), "确实没有写入命令")


def test_binder_blocked() -> None:
    print("== 9. 不该动的情况 ==")
    site = W.Site(kind="iis", file="", index=0, names=["A"])
    res = W.SiteBinder(None, site, runner=lambda c, a: (0, "", "")).update(["b"])
    chk(not res["ok"] and "IIS" in res["detail"], "IIS 直接拒绝", res["detail"][:40])
    site2 = W.parse_nginx_sites({"f": "server {\n listen 80;\n}\n"})[0]
    res2 = W.SiteBinder(None, site2,
                        runner=lambda c, a: (1, "", "无权限")).update(["x"])
    chk(not res2["ok"] and "读不到" in res2["detail"], "读不到文件就中止",
        res2["detail"][:40])
    chk(W.site_dump_cmd("tomcat") == "", "不支持的 kind 返回空命令")
    chk(W.site_dump_cmd("nginx").startswith("nginx -T"), "nginx 用 -T")


def main() -> int:
    test_split()
    test_blocks()
    test_nginx_parse()
    test_nginx_write()
    test_apache()
    test_iis()
    test_binder_ok()
    test_binder_fail()
    test_binder_blocked()
    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n合计 {len(RESULTS)} 项：通过 {npass}，失败 {len(RESULTS) - npass}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL · {name}  {detail}")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
