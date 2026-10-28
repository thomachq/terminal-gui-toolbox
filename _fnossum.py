import json, hashlib, subprocess, os, sys

CONF = "/usr/trim/etc/network_cert_all.conf"

def run(args):
    r = subprocess.run(args, capture_output=True)
    return r.stdout

def main():
    items = json.load(open(CONF, encoding="utf-8"))
    print("条目数:", len(items))
    for it in items:
        print("=" * 50)
        print("domain:", it.get("domain"), "| used:", it.get("used"), "| appFlag:", it.get("appFlag"))
        print("conf sum:", it.get("sum"))
        p = it.get("certificate") or ""
        k = it.get("privateKey") or ""
        if not os.path.exists(p):
            print("  证书文件不存在:", p)
            continue
        cands = {}
        cands["crt 原始字节 md5"] = hashlib.md5(open(p, "rb").read()).hexdigest()
        cands["crt DER md5"] = hashlib.md5(run(["openssl", "x509", "-in", p, "-outform", "DER"])).hexdigest()
        cands["crt pubkey(PEM) md5"] = hashlib.md5(run(["openssl", "x509", "-in", p, "-pubkey", "-noout"])).hexdigest()
        cands["crt pubkey DER md5"] = hashlib.md5(
            run(["openssl", "x509", "-in", p, "-pubkey", "-noout"])).hexdigest()
        if k and os.path.exists(k):
            cands["key 原始字节 md5"] = hashlib.md5(open(k, "rb").read()).hexdigest()
            cands["key DER md5"] = hashlib.md5(run(["openssl", "pkey", "-in", k, "-outform", "DER"])).hexdigest()
        # 公钥真正 DER：先取 pubkey PEM 再转 DER
        pubpem = run(["openssl", "x509", "-in", p, "-pubkey", "-noout"])
        if pubpem:
            try:
                der = subprocess.run(["openssl", "rsa", "-pubin", "-outform", "DER"],
                                     input=pubpem, capture_output=True).stdout
                if not der:
                    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "DER"],
                                         input=pubpem, capture_output=True).stdout
                if der:
                    cands["crt pubkey 真DER md5"] = hashlib.md5(der).hexdigest()
            except Exception:
                pass
        hit = [n for n, v in cands.items() if v == it.get("sum")]
        for n, v in cands.items():
            mark = "  <== 命中" if v == it.get("sum") else ""
            print(f"  {n:26s} {v}{mark}")
        print("  命中:", hit or "无")
        # 有效期核对
        out = run(["openssl", "x509", "-in", p, "-noout", "-dates"]).decode(errors="ignore")
        print("  证书日期:", out.strip().replace("\n", " | "))
        print("  conf validFrom/To:", it.get("validFrom"), it.get("validTo"))

if __name__ == "__main__":
    main()
