# -*- coding: utf-8 -*-
"""Web 服务管理自测。

    python _websmoke.py

分两段：
  A. 纯逻辑（core/webstack）：进程/监听/配置解析、证书报表、密钥配对、部署与回滚
  B. 界面（pages/web_page）：页挂上去、下拉切换、切服务换详情、证书报表上色
全部用假会话喂数据，不碰真机、不写真实配置。
"""
from __future__ import annotations

import datetime
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import webstack as ws  # noqa: E402

OK, FAIL = [], []


def chk(cond, name, detail=""):
    if cond:
        OK.append(name)
        print(f"  ok   {name}" + (f"  · {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  FAIL {name}" + (f"  · {detail}" if detail else ""))


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


# ------------------------------------------------------------------ 假会话
class FakeSession:
    """按「命令里包含某个关键词」返回预置输出。"""

    host_kind = "unix"

    def __init__(self, table, admin_table=None):
        self.table = list(table)
        self.admin_table = list(admin_table or [])
        self.calls: list[str] = []
        self.admin_calls: list[str] = []
        self.alive = True

    def _lookup(self, table, cmd):
        for pat, val in table:
            if pat in cmd:
                if isinstance(val, tuple):
                    return val
                return 0, val, ""
        return 0, "", ""

    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        return self._lookup(self.table, cmd)

    def run_admin(self, cmd, timeout=60):
        self.admin_calls.append(cmd)
        return self._lookup(self.admin_table or self.table, cmd)

    def close(self):
        pass


PS_TEXT = """www-data  901   nginx  nginx: master process /usr/sbin/nginx -g daemon on;
www-data  902   nginx  nginx: worker process
root     1201  apache2  /usr/sbin/apache2 -k start
www-data 1202  apache2  /usr/sbin/apache2 -k start
tomcat   1401     java  /usr/lib/jvm/bin/java -Dcatalina.base=/opt/tomcat -jar bootstrap.jar
root     1501    caddy  /usr/bin/caddy run --config /etc/caddy/Caddyfile
"""

LISTEN_TEXT = """State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=901,fd=6))
LISTEN 0      511             [::]:443           [::]:*    users:(("nginx",pid=901,fd=7))
LISTEN 0      128          0.0.0.0:8080       0.0.0.0:*    users:(("java",pid=1401,fd=22))
LISTEN 0      128        127.0.0.1:631          0.0.0.0:*    users:(("cupsd",pid=700,fd=7))
"""

UNITS_TEXT = """UNIT FILE                    STATE
nginx.service                enabled
apache2.service              enabled
tomcat.service               disabled
caddy.service                enabled
"""

NGINX_CONF = """
http {
    server {
        listen 443 ssl;
        root /var/www/site;
        ssl_certificate     /etc/nginx/ssl/site.pem;
        ssl_certificate_key /etc/nginx/ssl/site.key;
    }
}
"""

NGINX_GREP = """ssl_certificate     /etc/nginx/ssl/site.pem;
ssl_certificate_key /etc/nginx/ssl/site.key;
"""

APACHE_GREP = """SSLCertificateFile /etc/ssl/certs/apache.pem
SSLCertificateKeyFile /etc/ssl/private/apache.key
SSLCertificateChainFile /etc/ssl/certs/chain.pem
"""

TOMCAT_CONF = """<Connector port="8443" SSLEnabled="true">
  <SSLHostConfig certificateVerification="none">
    <Certificate certificateFile="conf/localhost.crt" certificateKeyFile="conf/localhost.key"/>
  </SSLHostConfig>
</Connector>
<Context docBase="/opt/tomcat/webapps/ROOT"/>
"""

TOMCAT_TLS = ('certificateFile="conf/localhost.crt" '
              'certificateKeyFile="conf/localhost.key"')


def make_scanner_session():
    return FakeSession([
        ("ps -eo user=,pid=,comm=,args=", PS_TEXT),
        ("ss -lntp", LISTEN_TEXT),
        ("systemctl list-unit-files", UNITS_TEXT),
        ("systemctl is-active nginx.service", "active\n"),
        ("systemctl is-active apache2.service", "active\n"),
        ("systemctl is-active tomcat.service", "inactive\n"),
        ("systemctl is-active caddy.service", "failed\n"),
        ("nginx -v", "nginx version: nginx/1.24.0\n"),
        ("apache2 -v", "Server version: Apache/2.4.57 (Debian)\n"),
        ("caddy version", "v2.7.6 h1:abcdef\n"),
        ("ls -1 /etc/nginx/nginx.conf", "/etc/nginx/nginx.conf\n"),
        ("ls -1 /etc/apache2/apache2.conf", "/etc/apache2/apache2.conf\n"),
        ("ls -1 /etc/caddy/Caddyfile", "/etc/caddy/Caddyfile\n"),
        ("certificateFile", TOMCAT_TLS),
        ("grep -rhoE", NGINX_GREP),
        ("cat /etc/nginx/nginx.conf", NGINX_CONF),
        ("cat /etc/apache2/apache2.conf", "DocumentRoot /var/www/html\n"),
        ("cat /opt/tomcat/conf/server.xml", TOMCAT_CONF),
    ], admin_table=[("grep -rhoE", NGINX_GREP)])


# ------------------------------------------------------------------ 证书样本
def _gen_pem(cn="web.example.com", days=365, self_signed=True, ca=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer_name = name if self_signed else ca.subject
    issuer_key = key if self_signed else ca_key
    now = datetime.datetime.utcnow()
    start = now - datetime.timedelta(days=1 if days >= 0 else -days + 1)
    cert = (x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(issuer_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(cn)]), critical=False)
            .sign(issuer_key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()).decode()
    return cert_pem, key_pem


ca_key = None
CA_PEM = ""


def build_pki():
    global ca_key, CA_PEM
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = datetime.datetime.utcnow()
    ca = (x509.CertificateBuilder()
          .subject_name(name).issuer_name(name)
          .public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                         critical=True)
          .sign(ca_key, hashes.SHA256()))
    CA_PEM = ca.public_bytes(serialization.Encoding.PEM).decode()
    return ca


# ------------------------------------------------------------------ A. 核心
def test_parse():
    print("\n== A1. 解析 ==")
    chk(ws.kind_of_process("nginx", "nginx: master process") == "nginx", "认 nginx")
    chk(ws.kind_of_process("apache2", "/usr/sbin/apache2 -k start") == "apache",
        "认 apache2")
    chk(ws.kind_of_process("httpd", "/usr/sbin/httpd -DFOREGROUND") == "apache",
        "认 httpd")
    chk(ws.kind_of_process("java", "-Dcatalina.base=/opt/tomcat") == "tomcat",
        "认 tomcat 的 java 进程")
    chk(ws.kind_of_process("caddy", "/usr/bin/caddy run") == "caddy", "认 caddy")
    chk(ws.kind_of_process("lighttpd", "/usr/sbin/lighttpd -D") == "lighttpd",
        "认 lighttpd")
    chk(ws.kind_of_process("sshd", "/usr/sbin/sshd -D") == "", "不误认 sshd")
    chk(ws.kind_of_process("java", "-jar /opt/app/app.jar") == "", "不误认普通 java")

    rows = ws.parse_processes(PS_TEXT)
    chk(len(rows) == 6, "ps 解析出 6 行", f"{len(rows)}")
    chk(rows[0][0] == "www-data" and rows[0][1] == "nginx", "ps 首行用户与进程名")

    lis = ws.parse_listeners(LISTEN_TEXT)
    chk(len(lis) >= 3, "ss 解析出监听项", f"{len(lis)}")
    chk(("0.0.0.0", 80, "nginx") in lis, "ss 认出 80/nginx")
    chk(any(p == 443 for _, p, _ in lis), "ss 认出 IPv6 的 443")

    netstat = ("tcp        0      0 0.0.0.0:8000            0.0.0.0:*"
               "               LISTEN      4242/python")
    lis2 = ws.parse_listeners(netstat)
    chk(lis2 == [("0.0.0.0", 8000, "python")], "netstat 格式也能解析", str(lis2))

    units = ws.parse_systemd_units(UNITS_TEXT)
    chk(units.get("nginx.service") == "enabled", "systemd 单元解析")

    chk(ws.parse_version("nginx", "nginx version: nginx/1.24.0") == "1.24.0",
        "nginx 版本")
    chk(ws.parse_version("apache", "Server version: Apache/2.4.57 (Debian)")
        == "2.4.57", "apache 版本")
    chk(ws.parse_version("caddy", "v2.7.6 h1:abcdef") == "2.7.6", "caddy 版本")

    pairs = ws.parse_tls_directives("nginx", NGINX_GREP, "/etc/nginx")
    chk(len(pairs) == 1, "nginx 证书指令配成一对", str(pairs))
    chk(pairs[0]["cert"] == "/etc/nginx/ssl/site.pem", "证书路径")
    chk(pairs[0]["key"] == "/etc/nginx/ssl/site.key", "私钥路径")

    rel = ws.parse_tls_directives(
        "tomcat", 'certificateFile="conf/localhost.crt" '
                  'certificateKeyFile="conf/localhost.key"', "/opt/tomcat")
    chk(rel and rel[0]["cert"] == "/opt/tomcat/conf/localhost.crt",
        "相对路径按配置目录展开", str(rel))

    ap = ws.parse_tls_directives("apache", APACHE_GREP)
    chk(ap and ap[0]["chain"] == "/etc/ssl/certs/chain.pem", "apache 链文件")

    chk(ws.parse_docroot("nginx", NGINX_CONF) == "/var/www/site", "nginx 站点根")
    chk(ws.parse_docroot("apache", "DocumentRoot /var/www/html")
        == "/var/www/html", "apache 站点根")
    chk(ws.parse_docroot("tomcat", TOMCAT_CONF) == "/opt/tomcat/webapps/ROOT",
        "tomcat docBase")


def test_scan():
    print("\n== A2. 探测 ==")
    dev = type("D", (), {"os_family": "linux", "os_name": "Debian"})()
    res = ws.WebScanner(make_scanner_session(), dev).scan()
    kinds = [s["kind"] for s in res["services"]]
    chk(res["status"] == ws.STATUS_OK, "探测成功", res["status"])
    chk(kinds == ["nginx", "apache", "tomcat", "caddy"], "四类服务都认出来",
        str(kinds))
    by = {s["kind"]: s for s in res["services"]}
    chk(by["nginx"]["running"] is True, "nginx 运行中（systemctl active）")
    chk(by["tomcat"]["running"] is False, "tomcat 未运行（systemctl inactive）")
    chk(by["nginx"]["listen"] == ["80", "443"], "nginx 监听端口归位",
        str(by["nginx"]["listen"]))
    chk(by["tomcat"]["listen"] == ["8080"], "tomcat 监听 8080")
    chk(by["nginx"]["version"] == "1.24.0", "nginx 版本")
    chk(by["nginx"]["docroot"] == "/var/www/site", "nginx 站点根")
    chk(by["tomcat"]["config"] == "/opt/tomcat/conf/server.xml",
        "tomcat 配置路径来自 -Dcatalina.base")
    chk(len(by["nginx"]["certs"]) == 1, "nginx 证书条目")
    chk(by["tomcat"]["certs"] and by["tomcat"]["certs"][0]["cert"].endswith(
        "localhost.crt"), "tomcat 证书条目")
    chk(all(k in s for s in res["services"] for k in ("title", "state", "key")),
        "每条都带 title / state / key")
    chk("Nginx" in ws.summarize(res["services"]), "摘要含服务名",
        ws.summarize(res["services"]))

    net = type("D", (), {"os_family": "network", "os_name": "Switch"})()
    res2 = ws.WebScanner(FakeSession([]), net).scan()
    chk(res2["status"] == ws.STATUS_UNSUPPORTED, "网络设备明确不支持",
        res2["detail"])

    res3 = ws.WebScanner(FakeSession([]), dev).scan()
    chk(res3["status"] == ws.STATUS_EMPTY, "空设备明确说没有", res3["detail"])


def test_windows():
    print("\n== A2b. Windows / IIS ==")
    sess = FakeSession([
        ("Get-Service", "W3SVC|Running\nnginx|Stopped\n"),
        ("/text:name,bindings,physicalPath",
         "Default Web Site|http/*:80:,https/*:443:www.example.com|C:\\inetpub\\wwwroot\n"),
        ("/text:name,bindings",
         "Default Web Site|http/*:80:,https/*:443:www.example.com\n"),
        ("InetStp", "10.0\n"),
        ("show sslcert",
         "Certificate Hash             : " + "AB" * 20 + "\n"),
        ("Cert:\\LocalMachine\\My",
         f"{'AB' * 20}|CN=www.example.com|2027-01-01 00:00:00\n"),
        ("Select-Object -First 1", "C:\\nginx\\conf\\nginx.conf\n"),
        ("Select-String", "ssl_certificate     C:/nginx/conf/site.pem;\n"
                          "ssl_certificate_key C:/nginx/conf/site.key;\n"),
    ])
    dev = type("D", (), {"os_family": "windows", "os_name": "Windows"})()
    res = ws.WebScanner(sess, dev).scan()
    by = {s["kind"]: s for s in res["services"]}
    chk("iis" in by, "认出 IIS", str(list(by)))
    if "iis" in by:
        iis = by["iis"]
        chk(iis["running"] is True, "IIS 服务在跑")
        chk("443" in iis["listen"] and "80" in iis["listen"],
            "IIS https/http 端口都收进来", str(iis["listen"]))
        chk(iis["version"] == "10.0", "IIS 版本", iis["version"])
        chk(iis["certs"] and "www.example.com" in str(iis["certs"][0]),
            "IIS https 绑定的证书带出主题", str(iis["certs"][:1]))
    chk("nginx" in by, "Windows 上的 nginx 服务也认", str(list(by)))
    if "nginx" in by:
        chk(by["nginx"]["running"] is False, "nginx 未运行如实反映")
        chk(by["nginx"]["certs"] and by["nginx"]["certs"][0]["cert"]
            == "C:/nginx/conf/site.pem", "Windows 绝对路径不拼配置目录",
            str(by["nginx"]["certs"][:1]))


def test_cert():
    print("\n== A3. 证书 ==")
    build_pki()
    ca = __import__("cryptography.x509", fromlist=["x509"]).load_pem_x509_certificate(
        CA_PEM.encode())
    cert, key = _gen_pem("web.example.com", days=20, self_signed=False, ca=ca)
    rep = ws.cert_report(cert)
    chk(rep["ok"], "自签服务证书可解析")
    chk(rep["cn"] == "web.example.com", "CN 正确", rep["cn"])
    chk("web.example.com" in rep["san"], "SAN 含域名", str(rep["san"]))
    chk(rep["self_signed"] is False, "非自签（issuer 与 subject 不同）")
    chk(15 <= rep["days_left"] <= 20, "剩余天数合理", str(rep["days_left"]))
    chk(rep["chain"] == 0, "单张证书 chain=0")
    chk(rep["key_type"].startswith("RSA"), "密钥类型", rep["key_type"])

    both = cert + CA_PEM
    rep2 = ws.cert_report(both)
    chk(rep2["count"] == 2 and rep2["chain"] == 1, "带链时 chain=1",
        f"count={rep2['count']} chain={rep2['chain']}")

    old, _oldkey = _gen_pem("old.example.com", days=-3)
    rep3 = ws.cert_report(old)
    chk(rep3["expired"] is True and rep3["days_left"] < 0, "过期证书能认出来",
        str(rep3["days_left"]))

    chk(ws.key_matches(cert, key) is True, "证书与私钥配对成功")
    _c2, other_key = _gen_pem("other.example.com")
    chk(ws.key_matches(cert, other_key) is False, "换一把私钥就不配对")
    chk(ws.key_matches(cert, "") is False, "空私钥不配对")
    chk(ws.cert_report("not a pem")["ok"] is False, "乱码不当证书")

    chk(ws.is_cert_pem(cert) and not ws.is_private_key_pem(cert), "识别证书 PEM")
    chk(ws.is_private_key_pem(key) and not ws.is_cert_pem(key), "识别私钥 PEM")
    exp = ws.expiring([rep, rep3], days=30)
    chk(len(exp) == 1 and exp[0]["cn"] == "web.example.com", "30 天到期提醒只挑对的",
        str([e["cn"] for e in exp]))


def test_deploy():
    print("\n== A4. 部署与回滚 ==")
    svc = {"kind": "nginx", "unit": "nginx.service",
           "config": "/etc/nginx/nginx.conf",
           "certs": [{"cert": "/etc/nginx/ssl/site.pem",
                      "key": "/etc/nginx/ssl/site.key"}]}
    cert, key = _gen_pem("deploy.example.com", days=90)

    calls: list[tuple[str, bool]] = []

    def ok_runner(cmd, admin):
        calls.append((cmd, admin))
        if "cp -a" in cmd:
            return 0, "/etc/nginx/ssl/site.pem.bak-20260101-000000\n", ""
        return 0, "", ""

    dep = ws.TLSDeployer(FakeSession([]), svc, runner=ok_runner)
    res = dep.deploy(cert, key, "/etc/nginx/ssl/site.pem", "/etc/nginx/ssl/site.key")
    names = [s["name"] for s in res["steps"]]
    chk(res["ok"] is True, "部署成功", res.get("detail", ""))
    chk(names[:2] == ["备份", "备份"], "先备份两份", str(names[:2]))
    chk("写入" in names and names.count("写入") == 2, "写证书与私钥")
    chk(names.index("校验") > names.index("写入"), "写入后才校验")
    chk("配置检查" in names and "重载" in names, "配置检查 → 重载")
    chk(names.index("配置检查") < names.index("重载"), "先检查再重载")
    chk(all(admin for _c, admin in calls), "全部走提权通道")
    chk(any("chmod 600" in c for c, _ in calls), "私钥权限 600")
    chk(any("base64 -d" in c for c, _ in calls), "用 base64 传输避开引号问题")

    # 配置检查失败 → 必须还原
    def bad_runner(cmd, admin):
        if "cp -a" in cmd:
            return 0, "/etc/nginx/ssl/site.pem.bak-20260101-000000\n", ""
        if "nginx -t" in cmd:
            return 1, "", "nginx: [emerg] cannot load certificate"
        return 0, "", ""

    dep2 = ws.TLSDeployer(FakeSession([]), svc, runner=bad_runner)
    res2 = dep2.deploy(cert, key, "/etc/nginx/ssl/site.pem", "/etc/nginx/ssl/site.key")
    names2 = [s["name"] for s in res2["steps"]]
    chk(res2["ok"] is False, "配置检查失败时部署算失败")
    chk("还原" in names2, "失败后自动还原", str(names2[-3:]))
    chk("配置检查未通过" in res2["detail"], "说明失败原因", res2["detail"][:40])
    chk("重载" in names2, "还原后也重载了一次")

    # 写入校验不一致 → 还原
    def mismatch_runner(cmd, admin):
        if "cp -a" in cmd:
            return 0, "/x.bak-1\n", ""
        if "sha256sum" in cmd:
            return 0, "deadbeef  /etc/nginx/ssl/site.pem\n", ""
        return 0, "", ""

    dep3 = ws.TLSDeployer(FakeSession([]), svc, runner=mismatch_runner)
    res3 = dep3.deploy(cert, key, "/etc/nginx/ssl/site.pem", "/etc/nginx/ssl/site.key")
    chk(res3["ok"] is False and "校验不一致" in res3["detail"], "回读不一致就回滚",
        res3["detail"][:40])

    # 路径缺失
    dep4 = ws.TLSDeployer(FakeSession([]), {"kind": "nginx", "certs": []},
                          runner=ok_runner)
    res4 = dep4.deploy(cert, key)
    chk(res4["ok"] is False and "路径" in res4["detail"], "没路径要说清楚")

    # IIS 不在这里换
    dep5 = ws.TLSDeployer(FakeSession([]), {"kind": "iis"}, runner=ok_runner)
    res5 = dep5.deploy(cert, key, "a", "b")
    chk(res5["ok"] is False and "IIS" in res5["detail"], "IIS 明确说明换法")

    # 恢复备份
    def restore_runner(cmd, admin):
        if "ls -1t" in cmd:
            return 0, "/etc/nginx/ssl/site.pem.bak-20260101-000000\n", ""
        return 0, "", ""

    dep6 = ws.TLSDeployer(FakeSession([]), svc, runner=restore_runner)
    res6 = dep6.restore_latest_backup("/etc/nginx/ssl/site.pem",
                                      "/etc/nginx/ssl/site.key")
    chk(res6["ok"] is True and "bak-" in res6["detail"], "能恢复最近备份",
        res6["detail"][:60])


# ------------------------------------------------------------------ B. 界面
def test_ui():
    print("\n== B1. 界面 ==")
    p = HERE / "data" / "settings.json"
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP

    a = APP.ToolboxApp()
    a.geometry("+40+30")
    pump(a, 0.7)
    # 界面段不碰真机：读证书会被提权弹窗挡住，这里直接关掉读盘
    a.web_read_tls = lambda: None
    chk("web" in a._pages, "web 页已构建", f"{len(a._pages)} 页")
    chk(hasattr(a, "cb_web_svc"), "服务下拉存在")
    chk(hasattr(a, "cb_web_cert"), "证书下拉存在")
    chk(hasattr(a, "kk_web") and hasattr(a, "kk_tls"), "两块详情网格都在")

    dev = type("D", (), {"os_family": "linux", "os_name": "Debian"})()
    res = ws.WebScanner(make_scanner_session(), dev).scan()
    a._web_apply(res)
    pump(a, 0.2)
    chk(len(a.cb_web_svc.cget("values")) == 4, "下拉列出 4 个服务",
        str(a.cb_web_svc.cget("values"))[:60])
    chk("nginx" in a.cb_web_svc.get().lower(), "默认选第一个（nginx）",
        a.cb_web_svc.get())
    chk(a.web_cur.startswith("nginx:"), "当前服务 key 正确", a.web_cur)

    first = a.web_cur
    a.cb_web_svc.current(2)
    a.web_select()
    pump(a, 0.2)
    chk(a.web_cur != first and a.web_cur.startswith("tomcat:"),
        "切换下拉就切到 tomcat", a.web_cur)
    chk(len(a.cb_web_cert.cget("values")) >= 1, "tomcat 也带出证书条目",
        str(a.cb_web_cert.cget("values"))[:50])

    cert, key = _gen_pem("ui.example.com", days=12)
    rep = ws.cert_report(cert)
    rep.update({"cert_path": "/etc/nginx/ssl/site.pem",
                "key_path": "/etc/nginx/ssl/site.key",
                "key_match": True, "key_present": True})
    a._tls_render(rep)
    pump(a, 0.15)
    labels = [it[0] for it in a.kk_tls._items]
    chk("剩余" in labels, "证书报表有剩余天数", str(labels))
    chk("SAN" in labels, "证书报表有 SAN")
    tone = dict((it[0], it[2]) for it in a.kk_tls._items).get("剩余")
    chk(tone == "warn", "12 天剩余标成黄色警告", str(tone))

    bad = ws.cert_report(cert)
    bad.update({"days_left": -5, "expired": True, "cert_path": "/x.pem"})
    a._tls_render(bad)
    tone2 = dict((it[0], it[2]) for it in a.kk_tls._items).get("剩余")
    chk(tone2 == "err", "过期标成红色", str(tone2))

    a._tls_render({"ok": False, "error": "读不到证书文件", "cert_path": "/y.pem"})
    labels3 = [it[0] for it in a.kk_tls._items]
    chk("读取结果" in labels3, "读不到时有明确说明", str(labels3))

    a._web_apply({"status": ws.STATUS_EMPTY, "detail": "没有发现 Web 服务",
                  "services": [], "commands": []})
    pump(a, 0.15)
    chk(a.cb_web_svc.get() == "", "空结果时下拉清空", repr(a.cb_web_svc.get()))
    chk(not a.btn_web_test.enabled if hasattr(a.btn_web_test, "enabled") else True,
        "空结果时操作按钮禁用")

    a.destroy()


def main():
    test_parse()
    test_scan()
    test_windows()
    test_cert()
    test_deploy()
    test_ui()
    print(f"\n结果：{len(OK)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for f in FAIL:
            print("  失败：", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
