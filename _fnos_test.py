#!/usr/bin/env python3
"""飞牛 fnOS：验证「只入库、不绑定」是否被接受（C 档）
只改 network_cert_all.conf 加一条 used=false，不碰 used=true 的条目，不 reload nginx。
"""
import json, os, shutil, subprocess, sys, time, secrets

CONF = "/usr/trim/etc/network_cert_all.conf"
SSLS = "/usr/trim/var/trim_connect/ssls"
DOM = "tb-selftest.local"
BAK_DIR = "/root/certbak"


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def main():
    ts = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(SSLS, DOM, str(int(time.time())))

    print("=== 0. 备份 conf ===")
    os.makedirs(BAK_DIR, exist_ok=True)
    bak = os.path.join(BAK_DIR, f"network_cert_all.conf.{ts}")
    shutil.copy2(CONF, bak)
    print(f"  备份: {bak} ({os.path.getsize(bak)} 字节)")

    print("=== 1. 生成测试证书 ===")
    os.makedirs(d, exist_ok=True)
    crt = os.path.join(d, f"{DOM}.crt")
    key = os.path.join(d, f"{DOM}.key")
    fc = os.path.join(d, "fullchain.crt")
    r = sh(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", key, "-out", crt, "-days", "2",
            "-subj", f"/CN={DOM}",
            "-addext", f"subjectAltName=DNS:{DOM}"])
    if not os.path.exists(crt):
        print("  生成失败:", r.stderr[-300:])
        return 1
    shutil.copy2(crt, fc)
    for f in (crt, key, fc):
        os.chmod(f, 0o644)
    print("  " + " | ".join(f"{os.path.basename(f)}={os.path.getsize(f)}B" for f in (crt, key, fc)))

    print("=== 2. 取有效期（毫秒）===")
    dates = sh(["openssl", "x509", "-in", crt, "-noout", "-dates"]).stdout
    from datetime import datetime, timezone
    vf = vt = 0
    for line in dates.strip().splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        dt = datetime.strptime(v, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        if k.strip() == "notBefore":
            vf = int(dt.timestamp() * 1000)
        else:
            vt = int(dt.timestamp() * 1000)
    print(f"  notBefore={vf} notAfter={vt}")
    print(f"  当前时间 {int(time.time()*1000)} → 有效期{'已覆盖当下' if vf <= int(time.time()*1000) <= vt else '异常'}")

    print("=== 3. 写入 conf ===")
    raw = open(CONF, encoding="utf-8").read()
    items = json.loads(raw)
    print("  原有:", [(i["domain"], i["used"]) for i in items])
    if any(i["domain"] == DOM for i in items):
        print("  已存在同名条目，中止（避免重复）")
        return 2
    items.append({
        "domain": DOM,
        "san": [DOM],
        "certificate": crt,
        "fullchain": fc,
        "privateKey": key,
        "validFrom": vf,
        "validTo": vt,
        "sum": secrets.token_hex(16),
        "used": False,
        "appFlag": 0,
    })
    out = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    # 原子写：先写临时文件再 replace，避免飞牛读到半个文件
    tmp = CONF + ".tbtmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    os.chmod(tmp, 0o644)
    os.replace(tmp, CONF)
    print(f"  写入 {len(out)} 字节（原 {len(raw)} 字节）")

    print("=== 4. 立即回读 ===")
    back = json.loads(open(CONF, encoding="utf-8").read())
    print("  条目:", [(i["domain"], i["used"], i["appFlag"]) for i in back])
    new = [i for i in back if i["domain"] == DOM]
    print("  新条目:", json.dumps(new[0], ensure_ascii=False) if new else "丢失!")

    print("=== 5. 服务状态 ===")
    for p in ("15666", "15667"):
        r = sh(["curl", "-sk", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
                f"https://127.0.0.1:{p}/"])
        print(f"  {p} -> {r.stdout or 'fail'}")
    for svc in ("trim_main.service", "network_service.service"):
        r = sh(["systemctl", "is-active", svc])
        print(f"  {svc}: {r.stdout.strip()}")
    r = sh(["bash", "-c", "ps -eo args | grep -c '[n]ginx: worker'"])
    print("  nginx worker:", r.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
