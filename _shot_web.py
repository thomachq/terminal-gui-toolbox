# -*- coding: utf-8 -*-
"""Web 服务页截图：真探测一次（本机），整页拍下来。

    python _shot_web.py            # 存到 screenshots/web.png
    python _shot_web.py --demo     # 用假服务填满界面再拍（只看布局，存到临时目录）

带 --demo 的那份只用来核对排版，不进仓库（界面里没有「这是演示数据」的标注，
放进 README 会误导人）。
"""
from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import _shots as SH  # noqa: E402

OUT = HERE / "screenshots"

DEMO = {
    "status": "ok",
    "detail": "",
    "count": 3,
    "commands": [],
    "services": [
        {"kind": "nginx", "label": "Nginx", "unit": "nginx.service", "version": "1.24.0",
         "running": True, "listen": ["80", "443"], "user": "www-data",
         "config": "/etc/nginx/nginx.conf", "docroot": "/var/www/site",
         "state": "运行中", "key": "nginx:nginx.service", "title": "Nginx 1.24.0",
         "certs": [{"cert": "/etc/nginx/ssl/site.pem",
                    "key": "/etc/nginx/ssl/site.key", "chain": ""}]},
        {"kind": "apache", "label": "Apache", "unit": "apache2.service",
         "version": "2.4.57", "running": True, "listen": ["8080"], "user": "www-data",
         "config": "/etc/apache2/apache2.conf", "docroot": "/var/www/html",
         "state": "运行中", "key": "apache:apache2.service",
         "title": "Apache 2.4.57",
         "certs": [{"cert": "/etc/ssl/certs/apache.pem",
                    "key": "/etc/ssl/private/apache.key",
                    "chain": "/etc/ssl/certs/chain.pem"}]},
        {"kind": "tomcat", "label": "Tomcat", "unit": "tomcat.service",
         "version": "10.1.18", "running": False, "listen": ["8443"], "user": "tomcat",
         "config": "/opt/tomcat/conf/server.xml",
         "docroot": "/opt/tomcat/webapps/ROOT",
         "state": "未运行", "key": "tomcat:tomcat.service",
         "title": "Tomcat 10.1.18", "certs": []},
    ],
}


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


def main():
    demo = "--demo" in sys.argv
    import _lockoff

    # 备份 + 退出还原，别把用户的锁屏密码真删了
    _lockoff.unlock(HERE / "data" / "settings.json")

    import app as APP

    a = APP.ToolboxApp()
    a.geometry("+50+30")
    pump(a, 0.8)
    a._web_probed = True            # 关掉自动探测，下面手动控制
    a.nav.select("web")
    pump(a, 0.6)

    if demo:
        # 演示数据不读真机：真读会弹提权口令框，把截图脚本挂住
        a.web_read_tls = lambda: None
        from core import webstack as ws
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        ck = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nas.example.com")])
        now = datetime.datetime.utcnow()
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(ck.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=340))
                .not_valid_after(now + datetime.timedelta(days=18))
                .add_extension(x509.SubjectAlternativeName(
                    [x509.DNSName("nas.example.com"),
                     x509.DNSName("www.example.com")]), critical=False)
                .sign(ck, hashes.SHA256()))
        a._web_clear_log()
        a._web_apply(DEMO)
        pump(a, 0.2)
        rep = ws.cert_report(cert.public_bytes(serialization.Encoding.PEM).decode())
        rep.update({"cert_path": "/etc/nginx/ssl/site.pem",
                    "key_path": "/etc/nginx/ssl/site.key",
                    "key_match": True, "key_present": True})
        a._tls_render(rep)
        a.badge_tls.set("已读取", "ok")
        a._web_log("$ ps -eo user=,pid=,comm=,args= | grep -E 'nginx|apache|java'")
        a._web_log("$ ss -lntp")
        a._web_log("$ grep -rhoE 'ssl_certificate(_key)?' /etc/nginx")
        a._web_log("[证书] /etc/nginx/ssl/site.pem · 剩余 18 天")
        target = Path(tempfile.gettempdir()) / "web_demo.png"
    else:
        a.web_probe()
        pump(a, 6.0)
        target = OUT / "web.png"

    pump(a, 0.5)
    hits = SH.find_titled()
    # 同名窗口可能不止一个（上一轮没退干净的残留），挑面积最大的那个
    best, best_area = 0, 0
    for hw in hits:
        r = SH.wt.RECT()
        try:
            SH.u.GetWindowRect(hw, ctypes.byref(r))
        except Exception:  # noqa: BLE001
            continue
        area = (r.right - r.left) * (r.bottom - r.top)
        if area > best_area:
            best, best_area = hw, area
    h = best
    if h:
        full = SH.capture(h)
        if full:
            full.save(target)
            print("OK ->", target, full.size)
        else:
            print("抓不到窗口位图")
    else:
        print("找不到窗口句柄")
    print("services =", len(a.web_res.get("services") or []),
          "| status =", a.web_res.get("status"))
    a.destroy()


if __name__ == "__main__":
    main()
