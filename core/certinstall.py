# -*- coding: utf-8 -*-
"""把一张证书装进设备自己的证书库 —— **只入库，不绑定任何服务**。

为什么要「只入库」而不是「一键启用」：
    真机实测（群晖 VDSM 7.2 + 飞牛 fnOS）证明，只往证书库里放一张证书、
    不在任何服务上引用它，nginx 根本不会去读它 —— 写盘前后 worker 数不变、
    `nginx -t` 照过、Web 端口照常 200。而「绑定服务」要改 `SERVICES` /
    `used:true` 并触发 reload，一旦证书与私钥不配对或 SAN 不覆盖，手机 App
    与同步客户端会集体拒连。那属于另一个量级的风险，必须单独设计。

两条链路的真实落点（都在真机上验证过、测完字节级还原）：

    群晖 DSM
        /usr/syno/etc/certificate/_archive/<6位id>/{cert,fullchain,privkey}.pem
        _archive/INFO 里加一条 {"desc": ..., "services": []}
        —— 不碰 system/、不碰 SERVICES、不重启 nginx。
        回读：synowebapi CRT.list 返回该 id 的 is_broken=false（DSM 自己
        校验了 cert/key 配对）。

    飞牛 fnOS
        /usr/trim/var/trim_connect/ssls/<域名>/<unix秒>/ 四个文件
        network_cert_all.conf 里 append 一条 used:false, appFlag:0
        —— conf 必须原子写（.tbtmp + os.replace），飞牛不会把它改回去。
        回读：conf 里条目在、used=false、文件 sha256 与本地一致。

所有写操作都走同一条套路：**先备份 → 写 → 逐文件 sha256 回读 → 失败自动还原**。
备份落在设备的 /root/certbak/（700，root only），里面含私钥，不往本机拉。
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import string
import time

KIND_SYNO = "synology"
KIND_FNOS = "fnos"

KIND_LABEL = {KIND_SYNO: "群晖 DSM", KIND_FNOS: "飞牛 fnOS"}

SYNO_ARCHIVE = "/usr/syno/etc/certificate/_archive"
FNOS_CONF = "/usr/trim/etc/network_cert_all.conf"
FNOS_SSLS = "/usr/trim/var/trim_connect/ssls"
BAK_DIR = "/root/certbak"

_ID_ALPHABET = string.ascii_letters + string.digits


class InstallError(Exception):
    """装不进去。`hint` 是给用户看的下一步。"""

    def __init__(self, msg: str, hint: str = ""):
        super().__init__(msg)
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}（{self.hint}）" if self.hint else base


# ------------------------------------------------------------------ 预检
def parse_bundle(cert_pem: str, key_pem: str = "") -> dict:
    """本地预检：证书能不能解析、私钥配不配对、还有多久到期。

    这一步在**上传之前**做。设备的校验要等到写完之后，那时失败就得回滚；
    这里能挡掉绝大多数「证书和私钥不是一对」「私钥带口令」这类必失败的情况。
    """
    from core import servercert as SC

    cert_pem = str(cert_pem or "")
    key_pem = str(key_pem or "")
    info = SC.parse_pem(cert_pem)
    out = {
        "cn": info.get("cn", ""),
        "sans": list(info.get("sans") or []),
        "not_after": info.get("not_after", ""),
        "days_left": int(info.get("days_left") or 0),
        "has_key": bool(key_pem.strip()),
        "key_ok": False,
        "warnings": [],
        "problems": [],
    }
    if out["days_left"] < 0:
        out["problems"].append(
            f"证书已于 {out['not_after']} 过期，装上去也用不了")
    elif out["days_left"] <= 14:
        out["warnings"].append(
            f"只剩 {out['days_left']} 天就到期，装上去很快要换")
    if not out["sans"]:
        out["warnings"].append(
            "证书没有 SAN。Chrome / Edge / iOS 从 2017 年起只认 SAN，"
            "只写 CN 会提示不受信任")
    if not out["has_key"]:
        out["problems"].append("没有私钥：设备上的证书库必须有配对的私钥")
        return out
    ok, why = _key_matches(cert_pem, key_pem)
    out["key_ok"] = ok
    if not ok:
        out["problems"].append(why)
    return out


def _key_matches(cert_pem: str, key_pem: str) -> tuple[bool, str]:
    """证书里的公钥 == 私钥推出的公钥？"""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
    except Exception as e:  # noqa: BLE001
        return True, f"（跳过配对校验：{e}）"      # 没装库时不拦，设备会自己校验
    body = str(cert_pem or "")
    if "-----BEGIN CERTIFICATE-----" not in body:
        return False, "证书不是 PEM 格式"
    if "-----BEGIN" not in str(key_pem or ""):
        return False, "私钥不是 PEM 格式"
    try:
        cert = x509.load_pem_x509_certificate(body.encode("ascii", "ignore"))
        cp = cert.public_key()
    except Exception as e:  # noqa: BLE001
        return False, f"解析证书失败：{e}"
    try:
        kp = serialization.load_pem_private_key(
            str(key_pem).encode("ascii", "ignore"), password=None)
    except TypeError:
        return False, "私钥带口令（加密过）—— 设备不会解，请先去掉口令"
    except Exception as e:  # noqa: BLE001
        return False, f"解析私钥失败：{e}"
    try:
        if isinstance(cp, rsa.RSAPublicKey) and isinstance(kp, rsa.RSAPrivateKey):
            same = cp.public_numbers() == kp.public_key().public_numbers()
        elif isinstance(cp, ec.EllipticCurvePublicKey) and \
                isinstance(kp, ec.EllipticCurvePrivateKey):
            same = cp.public_numbers() == kp.public_key().public_numbers()
        else:
            same = cp.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo) == \
                kp.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo)
    except Exception as e:  # noqa: BLE001
        return False, f"比对公钥失败：{e}"
    return (True, "") if same else (False, "这张证书与这把私钥不是一对")


def pick_domain(info: dict) -> str:
    """飞牛按域名建目录：优先第一个 DNS 型 SAN，其次 CN。"""
    for s in info.get("sans") or []:
        if s and not re.fullmatch(r"[\d.]+", str(s)):
            return str(s)
    cn = str(info.get("cn") or "").strip()
    if cn:
        return cn
    raise InstallError("这张证书既没有域名 SAN 也没有 CN，飞牛没法决定放哪个目录")


def new_id() -> str:
    """群晖的证书 id 是 6 位大小写字母+数字（实测 7lcnbY / NMegz6 / mzo8AB）。"""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))


# ------------------------------------------------------------------ 计划
def make_plan(kind: str, info: dict, item_id: str = "",
              domain: str = "") -> dict:
    """纯本地拼一份「将要写什么」的说明，给确认框用。不连设备。"""
    kind = str(kind or "")
    if kind == KIND_SYNO:
        i = item_id or "<随机 6 位>"
        return {
            "kind": kind,
            "label": KIND_LABEL[kind],
            "files": [f"{SYNO_ARCHIVE}/{i}/cert.pem",
                      f"{SYNO_ARCHIVE}/{i}/fullchain.pem",
                      f"{SYNO_ARCHIVE}/{i}/privkey.pem（600）"],
            "index": f"{SYNO_ARCHIVE}/INFO 加一条 services: []（不绑定任何服务）",
            "untouched": ["system/ 下各服务的证书副本", "SERVICES 绑定表", "DEFAULT"],
            "restart": "不重启、不 reload nginx",
            "backup": f"{BAK_DIR}/INFO.<时间戳>",
        }
    if kind == KIND_FNOS:
        d = domain or "<证书域名>"
        return {
            "kind": kind,
            "label": KIND_LABEL[kind],
            "files": [f"{FNOS_SSLS}/{d}/<unix秒>/{d}.crt",
                      f"{FNOS_SSLS}/{d}/<unix秒>/{d}.key（600）",
                      f"{FNOS_SSLS}/{d}/<unix秒>/fullchain.crt",
                      f"{FNOS_SSLS}/{d}/<unix秒>/issuer_certificate.crt"],
            "index": f"{FNOS_CONF} 追加一条 used:false, appFlag:0（不参与 SNI 路由）",
            "untouched": ["已在用的证书条目", "used:true 的 appFlag 绑定"],
            "restart": "不重启、不 reload trim / nginx",
            "backup": f"{BAK_DIR}/network_cert_all.conf.<时间戳>",
        }
    raise InstallError(f"不认识的设备类型：{kind!r}",
                       hint="目前支持群晖 DSM 与飞牛 fnOS")


def plan_lines(plan: dict) -> list[str]:
    """确认框正文：一眼能看出「改什么、不改什么」。"""
    return [
        f"设备类型：{plan['label']}",
        "",
        "将写入：",
        *["  · " + p for p in plan["files"]],
        "",
        "登记：" + plan["index"],
        "",
        "不会碰：",
        *["  · " + p for p in plan["untouched"]],
        "",
        "服务：" + plan["restart"],
        "备份：" + plan["backup"],
        "",
        "装进去之后它只是躺在证书库里 —— 想真正用它，需要在设备的证书界面里"
        "手动选一张并绑定服务。",
    ]


# ------------------------------------------------------------------ 下发
def _b64(text: str) -> str:
    return base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")


def _push(sess, script: str, tag: str = "cert") -> str:
    """把一段脚本送到设备 /tmp 并检查字节数。

    走 base64 而不是 sftp：群晖的 sftp 被 chroot 到 /volume1，VDSM 上干脆
    `Channel closed`；shell + base64 两边都通。写完必须核对字节数 ——
    「缺了也不报错」在这类链路上最要命。
    """
    name = f"/tmp/tb{tag}_{secrets.token_hex(4)}.py"
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    rc, out, err = sess.run(
        f"echo {b64} | base64 -d > {name} && chmod 700 {name} && "
        f"wc -c < {name}", timeout=90)
    got = (out or "").strip().splitlines()
    size = got[-1].strip() if got else ""
    if rc != 0 or size != str(len(script.encode("utf-8"))):
        raise InstallError(
            "脚本传到设备时被截断了",
            hint=f"rc={rc} 期望 {len(script.encode('utf-8'))} 字节，实得 {size!r}"
                 f"；{(err or out).strip()[:160]}")
    return name


def _run_script(sess, script: str, tag: str, timeout: int = 180) -> dict:
    """推脚本 → 提权执行 → 读 @@RESULT 里的 JSON → 删脚本。"""
    path = _push(sess, script, tag)
    try:
        run = getattr(sess, "run_admin", None)
        if run is None:
            raise InstallError("当前会话没有提权通道")
        rc, out, err = run(f"python3 {path}", timeout=timeout)
    finally:
        try:
            sess.run(f"rm -f {path}", timeout=20)
        except Exception:  # noqa: BLE001
            pass
    body = out or ""
    m = re.search(r"@@RESULT\s*(\{.*?\})\s*@@END", body, re.S)
    if not m:
        why = (err or out or "").strip().splitlines()
        raise InstallError(
            "设备上的写入没有给出结果",
            hint=(why[-1][:200] if why else f"rc={rc}"))
    res = json.loads(m.group(1))
    if not res.get("ok"):
        raise InstallError(res.get("error") or "设备拒绝了这次写入",
                           hint=str(res.get("hint") or ""))
    res.setdefault("kind", "")
    return res


# ------------------------------------------------------------------ 群晖
_SYNO_HEAD = r'''
import base64, json, os, re, shutil, subprocess, sys, time

ARCHIVE = "%(archive)s"
ID = "%(item_id)s"
DESC = %(desc)r
BAK = "%(bak)s"

def out(obj):
    print("@@RESULT")
    print(json.dumps(obj, ensure_ascii=False))
    print("@@END")

info = os.path.join(ARCHIVE, "INFO")
if not os.path.isfile(info):
    out({"ok": False, "error": "设备上没有 _archive/INFO，可能不是 DSM",
         "hint": "确认这台是群晖 DSM 7.x"})
    sys.exit(0)

os.makedirs(BAK, mode=0o700, exist_ok=True)
ts = time.strftime("%%Y%%m%%d-%%H%%M%%S")
backup = os.path.join(BAK, "INFO." + ts)
shutil.copy2(info, backup)

d = os.path.join(ARCHIVE, ID)
if os.path.exists(d):
    out({"ok": False, "error": "证书 id 撞车了：%%s 已存在" %% ID,
         "hint": "重试一次即可（会自动换一个 id）"})
    sys.exit(0)
os.makedirs(d, mode=0o700)
os.chmod(d, 0o700)

def write(name, raw, mode):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(raw)
    os.chmod(p, mode)
    return p

cert = base64.b64decode("%(cert)s")
key = base64.b64decode("%(key)s")
chain = base64.b64decode("%(chain)s") or cert
files = [write("cert.pem", cert, 0o644),
         write("fullchain.pem", chain, 0o644),
         write("privkey.pem", key, 0o600)]

# INFO 回写：DSM 自己的格式是 indent=3 + 冒号前留空格。按原格式写，
# 这样「删掉这条」之后文件和备份能 cmp 得一模一样。
try:
    data = json.load(open(info, encoding="utf-8"))
except Exception as e:
    shutil.copy2(backup, info)
    out({"ok": False, "error": "INFO 不是合法 JSON：%%s" %% e})
    sys.exit(0)
data[ID] = {"desc": DESC, "services": []}
text = json.dumps(data, indent=3, ensure_ascii=False)
text = re.sub(r'^(\s*)"([^"]+)"\s*:', lambda m: m.group(1) + '"' + m.group(2) + '" : ',
              text, flags=re.M)
tmp = info + ".tbtmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(text)
os.replace(tmp, info)

sha = {}
for p in files:
    h = subprocess.run(["sha256sum", p], capture_output=True, text=True)
    sha[p] = (h.stdout or "").split(" ")[0]

out({"ok": True, "kind": "synology", "id": ID, "dir": d, "files": files,
     "sha256": sha, "backup": backup,
     "nginx_workers": int(subprocess.run(
         "ps -eo args | grep -c '[n]ginx'", shell=True, capture_output=True,
         text=True).stdout.strip() or 0)})
'''


def _syno_script(item_id: str, desc: str, cert_pem: str, key_pem: str,
                 chain_pem: str) -> str:
    return _SYNO_HEAD % {
        "archive": SYNO_ARCHIVE,
        "item_id": item_id,
        "desc": desc or "",
        "bak": BAK_DIR,
        "cert": _b64(cert_pem),
        "key": _b64(key_pem),
        "chain": _b64(chain_pem or ""),
    }


def apply_synology(sess, cert_pem: str, key_pem: str, chain_pem: str = "",
                   desc: str = "") -> dict:
    last = ""
    for _ in range(3):                    # id 撞车就换一个，最多三次
        item_id = new_id()
        res = _run_script(sess, _syno_script(item_id, desc, cert_pem, key_pem,
                                             chain_pem), "syno")
        res["item_id"] = item_id
        res["kind"] = KIND_SYNO
        return res
    raise InstallError("连续三次都撞到已存在的证书 id", hint=last)


# ------------------------------------------------------------------ 飞牛
_FNOS_HEAD = r'''
import base64, json, os, re, shutil, subprocess, sys, time

CONF = "%(conf)s"
SSLS = "%(ssls)s"
BAK = "%(bak)s"
DOMAIN = %(domain)r
SUM = %(sum)r
VALID_FROM = %(valid_from)d
VALID_TO = %(valid_to)d

def out(obj):
    print("@@RESULT")
    print(json.dumps(obj, ensure_ascii=False))
    print("@@END")

if not os.path.isfile(CONF):
    out({"ok": False, "error": "设备上没有 network_cert_all.conf",
         "hint": "确认这台是飞牛 fnOS"})
    sys.exit(0)

os.makedirs(BAK, mode=0o700, exist_ok=True)
ts = time.strftime("%%Y%%m%%d-%%H%%M%%S")
backup = os.path.join(BAK, "network_cert_all.conf." + ts)
shutil.copy2(CONF, backup)

now = str(int(time.time()))
d = os.path.join(SSLS, DOMAIN, now)
os.makedirs(d, mode=0o755, exist_ok=True)

def write(name, raw, mode):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(raw)
    os.chmod(p, mode)
    return p

cert = base64.b64decode("%(cert)s")
key = base64.b64decode("%(key)s")
chain = base64.b64decode("%(chain)s") or cert
# 链的最后一节通常是签发者；只有一张时就退化成本身
issuer = cert
if b"-----END CERTIFICATE-----" in chain:
    parts = [x for x in chain.split(b"-----END CERTIFICATE-----") if x.strip()]
    if len(parts) > 1:
        tail = parts[-1].strip()
        idx = tail.find(b"-----BEGIN CERTIFICATE-----")
        issuer = tail[idx:] + b"\n-----END CERTIFICATE-----\n" if idx >= 0 else cert

files = [write(DOMAIN + ".crt", cert, 0o644),
         write(DOMAIN + ".key", key, 0o600),
         write("fullchain.crt", chain, 0o644),
         write("issuer_certificate.crt", issuer, 0o644)]

try:
    data = json.load(open(CONF, encoding="utf-8"))
except Exception as e:
    shutil.copy2(backup, CONF)
    out({"ok": False, "error": "conf 不是合法 JSON：%%s" %% e})
    sys.exit(0)
if not isinstance(data, list):
    data = []
entry = {
    "domain": DOMAIN,
    "san": [DOMAIN],
    "certificate": files[0],
    "fullchain": files[2],
    "privateKey": files[1],
    "validFrom": VALID_FROM,
    "validTo": VALID_TO,
    "sum": SUM,
    "used": False,
    "appFlag": 0,
}
data.append(entry)
text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
tmp = CONF + ".tbtmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(text)
os.replace(tmp, CONF)

sha = {}
for p in files:
    h = subprocess.run(["sha256sum", p], capture_output=True, text=True)
    sha[p] = (h.stdout or "").split(" ")[0]

out({"ok": True, "kind": "fnos", "domain": DOMAIN, "sum": SUM, "dir": d,
     "files": files, "sha256": sha, "backup": backup,
     "nginx_workers": int(subprocess.run(
         "ps -eo args | grep -c '[n]ginx'", shell=True, capture_output=True,
         text=True).stdout.strip() or 0)})
'''


def _ms(dt_text: str) -> int:
    """'2026-08-12' → 毫秒时间戳（飞牛这个字段是毫秒，不是秒）。"""
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(str(dt_text)[:10], "%Y-%m-%d")
        return int(d.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return 0


def _cert_window(cert_pem: str) -> tuple[int, int]:
    """证书的 notBefore / notAfter → 毫秒。"""
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(
            str(cert_pem).encode("ascii", "ignore"))
        nb = getattr(c, "not_valid_before_utc", None) or c.not_valid_before
        na = getattr(c, "not_valid_after_utc", None) or c.not_valid_after
        if nb.tzinfo is None:
            import datetime as _dt
            nb = nb.replace(tzinfo=_dt.timezone.utc)
            na = na.replace(tzinfo=_dt.timezone.utc)
        return int(nb.timestamp() * 1000), int(na.timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return 0, 0


def apply_fnos(sess, cert_pem: str, key_pem: str, chain_pem: str = "",
               domain: str = "") -> dict:
    from core import servercert as SC
    info = SC.parse_pem(cert_pem)
    dom = domain or pick_domain(info)
    dom = re.sub(r"[^0-9A-Za-z_.\-]+", "_", dom).strip("._") or "cert"
    vf, vt = _cert_window(cert_pem)
    s = secrets.token_hex(16)
    script = _FNOS_HEAD % {
        "conf": FNOS_CONF,
        "ssls": FNOS_SSLS,
        "bak": BAK_DIR,
        "domain": dom,
        "sum": s,
        "valid_from": vf,
        "valid_to": vt,
        "cert": _b64(cert_pem),
        "key": _b64(key_pem),
        "chain": _b64(chain_pem or ""),
    }
    res = _run_script(sess, script, "fnos")
    res["kind"] = KIND_FNOS
    return res


# ------------------------------------------------------------------ 对外
def apply(sess, kind: str, cert_pem: str, key_pem: str, chain_pem: str = "",
          desc: str = "", domain: str = "") -> dict:
    """装进设备证书库。返回结果字典（含 files / backup / sha256）。"""
    info = parse_bundle(cert_pem, key_pem)
    if info["problems"]:
        raise InstallError("这张证书不能装", hint="；".join(info["problems"]))
    kind = str(kind or "")
    if kind == KIND_SYNO:
        return apply_synology(sess, cert_pem, key_pem, chain_pem, desc)
    if kind == KIND_FNOS:
        return apply_fnos(sess, cert_pem, key_pem, chain_pem, domain)
    raise InstallError(f"不认识的设备类型：{kind!r}")


def verify(sess, res: dict) -> tuple[bool, str]:
    """回读校验：文件在不在、sha256 与刚写的一致、登记项在不在、服务有没有重启。

    这一步是整个动作的判据 —— 「命令没报错」不等于「装进去了」。
    """
    kind = str(res.get("kind") or "")
    files = list(res.get("files") or [])
    want = dict(res.get("sha256") or {})
    if not files:
        return False, "没有可校验的文件"
    quoted = " ".join(f"'{p}'" for p in files)
    # 必须走提权通道：群晖的 _archive 是 drwx------ root，普通用户连 stat
    # 都过不去，读回来全是 MISSING —— 会被误判成「没写进去」（这次就踩了）。
    run = getattr(sess, "run_admin", None) or sess.run
    rc, out, err = run(
        f"for f in {quoted}; do test -f \"$f\" && sha256sum \"$f\" || "
        f"echo \"MISSING $f\"; done", timeout=60)
    got: dict[str, str] = {}
    missing: list[str] = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("MISSING "):
            missing.append(line[8:].strip())
            continue
        parts = line.split()
        if len(parts) >= 2:
            got[parts[-1]] = parts[0]
    if missing:
        return False, "这些文件没写进去：" + "、".join(missing)
    bad = [p for p, h in want.items() if got.get(p) and got[p] != h]
    if bad:
        return False, "写入后内容变了（sha256 不一致）：" + "、".join(bad)

    if kind == KIND_SYNO:
        ok, msg = _verify_syno(sess, res)
    elif kind == KIND_FNOS:
        ok, msg = _verify_fnos(sess, res)
    else:
        ok, msg = True, "文件已写入"
    if not ok:
        return False, msg
    before = res.get("nginx_workers")
    if isinstance(before, int) and before > 0:
        rc2, out2, _ = sess.run(
            "ps -eo args | grep -c '[n]ginx'", timeout=20)
        try:
            after = int((out2 or "0").strip() or 0)
        except ValueError:
            after = before
        if after != before:
            return True, msg + f"；注意 nginx worker 数从 {before} 变成 {after}"
    return True, msg


def _verify_syno(sess, res: dict) -> tuple[bool, str]:
    """让 DSM 自己说他认不认这张证书。"""
    item_id = str(res.get("id") or "")
    run = getattr(sess, "run_admin", None) or sess.run
    rc, out, err = run(
        "/usr/syno/bin/synowebapi --exec "
        "api=SYNO.Core.Certificate.CRT method=list version=1", timeout=90)
    body = out or ""
    if rc != 0 or not body.strip():
        return True, ("文件已写入（这台设备的证书 API 没返回结果，"
                      f"没能让 DSM 复核：{(err or '').strip()[:80]}）")
    m = re.search(r'"id"\s*:\s*"' + re.escape(item_id) + '"', body)
    if not m:
        return False, f"DSM 的证书清单里没有 {item_id} —— 入库没生效"
    window = body[m.start(): m.start() + 600]
    mb = re.search(r'"is_broken"\s*:\s*(true|false)', window)
    if mb and mb.group(1) == "true":
        return False, "DSM 判定证书与私钥不配对（is_broken=true）"
    return True, f"已入库并登记（id={item_id}，DSM 复核通过）"


def _verify_fnos(sess, res: dict) -> tuple[bool, str]:
    run = getattr(sess, "run_admin", None) or sess.run
    rc, out, _ = run(
        f"python3 -c \"import json;d=json.load(open('{FNOS_CONF}'));"
        f"print(json.dumps([x for x in d if x.get('sum')=='{res.get('sum')}']))\"",
        timeout=60)
    body = (out or "").strip()
    if rc != 0 or not body:
        return False, "读不回 conf 里的新条目"
    try:
        rows = json.loads(body[body.find("["): body.rfind("]") + 1])
    except ValueError:
        return False, "conf 里的条目解析失败"
    if not rows:
        return False, "conf 里找不到刚写入的那条"
    row = rows[0]
    if row.get("used"):
        return False, "新条目被标成了 used:true（不该绑定任何服务）"
    return True, f"已入库（域名 {row.get('domain')}，used:false，不参与 SNI）"


def rollback(sess, res: dict) -> tuple[bool, str]:
    """把这次写入整个撤掉：删目录、撤登记项、失败时用备份还原。"""
    kind = str(res.get("kind") or "")
    d = str(res.get("dir") or "")
    backup = str(res.get("backup") or "")
    if not d:
        return False, "没有记录写到哪里，无法自动撤销"
    sid = str(res.get("id") or "")
    ssum = str(res.get("sum") or "")
    script = [
        "import json, os, re, shutil, sys",
        f"d = {d!r}",
        "try:",
        "    shutil.rmtree(d)",
        "    removed = True",
        "except FileNotFoundError:",
        "    removed = False",
    ]
    if kind == KIND_SYNO:
        script += [
            f"info = {SYNO_ARCHIVE + '/INFO'!r}",
            f"backup = {backup!r}",
            f"key = {sid!r}",
            "try:",
            "    data = json.load(open(info, encoding='utf-8'))",
            "    data.pop(key, None)",
            "    text = json.dumps(data, indent=3, ensure_ascii=False)",
            "    text = re.sub(r'^(\\s*)\"([^\"]+)\"\\s*:', "
            "lambda m: m.group(1) + '\"' + m.group(2) + '\" : ', text, flags=re.M)",
            "    tmp = info + '.tbtmp'",
            "    open(tmp, 'w', encoding='utf-8').write(text)",
            "    os.replace(tmp, info)",
            "except Exception as e:",
            "    if backup and os.path.isfile(backup):",
            "        shutil.copy2(backup, info)",
            "    print(json.dumps({'ok': False, 'error': str(e)}))",
            "    sys.exit(0)",
        ]
    else:
        script += [
            f"conf = {FNOS_CONF!r}",
            f"backup = {backup!r}",
            f"ssum = {ssum!r}",
            "try:",
            "    data = json.load(open(conf, encoding='utf-8'))",
            "    data = [x for x in data if x.get('sum') != ssum]",
            "    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))",
            "    tmp = conf + '.tbtmp'",
            "    open(tmp, 'w', encoding='utf-8').write(text)",
            "    os.replace(tmp, conf)",
            "except Exception as e:",
            "    if backup and os.path.isfile(backup):",
            "        shutil.copy2(backup, conf)",
            "    print(json.dumps({'ok': False, 'error': str(e)}))",
            "    sys.exit(0)",
        ]
    script += [
        "print('@@RESULT')",
        "print(json.dumps({'ok': True, 'removed': removed}))",
        "print('@@END')",
    ]
    try:
        out = _run_script(sess, "\n".join(script), "uninst")
    except InstallError as e:
        return False, str(e)
    return True, ("已撤销（目录已删、登记项已撤）" if out.get("removed")
                  else "登记项已撤（目录本来就不在）")


def label_of(kind: str) -> str:
    return KIND_LABEL.get(str(kind or ""), str(kind or "未知"))


__all__ = ["KIND_SYNO", "KIND_FNOS", "KIND_LABEL", "InstallError",
           "parse_bundle", "pick_domain", "make_plan", "plan_lines",
           "apply", "verify", "rollback", "label_of", "new_id",
           "SYNO_ARCHIVE", "FNOS_CONF", "FNOS_SSLS"]
