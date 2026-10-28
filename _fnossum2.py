import json, hashlib, os, subprocess, itertools

CONF = "/usr/trim/etc/network_cert_all.conf"

def shapes(data):
    """对一段字节算各种摘要形态"""
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1[:32]": hashlib.sha1(data).hexdigest()[:32],
        "sha256[:32]": hashlib.sha256(data).hexdigest()[:32],
        "sha256[32:]": hashlib.sha256(data).hexdigest()[32:],
        "blake2b[:32]": hashlib.blake2b(data).hexdigest()[:32],
        "sha384[:32]": hashlib.sha384(data).hexdigest()[:32],
        "sha512[:32]": hashlib.sha512(data).hexdigest()[:32],
    }

def main():
    items = json.load(open(CONF, encoding="utf-8"))
    for it in items:
        target = it.get("sum", "")
        crtp = it.get("certificate") or ""
        keyp = it.get("privateKey") or ""
        print("=" * 56)
        print("domain:", it.get("domain"), "target:", target, "len:", len(target))
        if not (os.path.exists(crtp) and os.path.exists(keyp)):
            print("  文件缺失")
            continue
        crt = open(crtp, "rb").read()
        key = open(keyp, "rb").read()
        fc = b""
        fcp = it.get("fullchain") or ""
        if fcp and os.path.exists(fcp):
            fc = open(fcp, "rb").read()

        cands = {}
        # 单文件
        for name, data in [("crt", crt), ("key", key), ("fullchain", fc)]:
            if data:
                for k, v in shapes(data).items():
                    cands[f"{name}:{k}"] = v
        # 拼接
        combos = [("crt+key", crt + key), ("key+crt", key + crt)]
        if fc:
            combos.append(("fc+key", fc + key))
        for name, data in combos:
            for k, v in shapes(data).items():
                cands[f"{name}:{k}"] = v
        # domain 参与
        dom = (it.get("domain") or "").encode()
        for k, v in shapes(dom).items():
            cands[f"domain:{k}"] = v
        for k, v in shapes(dom + crt).items():
            cands[f"domain+crt:{k}"] = v

        hit = [(n, v) for n, v in cands.items() if v == target]
        print("  候选数:", len(cands), "| 命中:", hit or "无")
        if not hit:
            # 打印最接近的（同长度）
            print("  crt:md5 =", cands.get("crt:md5"))
            print("  crt:sha256[:32] =", cands.get("crt:sha256[:32]"))

if __name__ == "__main__":
    main()
