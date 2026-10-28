# -*- coding: utf-8 -*-
"""设备侧「服务器证书」读取 —— 群晖 DSM / 飞牛 fnOS。

安全中心「证书管理」页原先只列本机 `data/certs` 里的证书库，用户明明在
DSM「控制面板 → 安全性 → 证书」/ fnOS「系统设置 → 证书」里已经装过证书，
页面上却一张也看不见，看起来像「同步坏了」。这个模块负责把设备自己那份
清单读出来（**只读**）：

* 群晖：`/usr/syno/etc/certificate/_archive/`
    - `INFO`     JSON：`{证书id: {desc, services[]}}`（服务 → 证书的绑定）
    - `DEFAULT`  默认证书 id
    - `<id>/cert.pem` / `privkey.pem`
  目录是 `drwx------ root`，普通管理员读不到 → 必须 `run_admin()`。
  注意**每次都要走 `run_admin`**：sudo 凭证是按 tty 缓存的，`run()` 走的是
  exec 通道，`sudo -n` 在那边永远 rc=1（实测 DSM 7.2）。

* 飞牛 fnOS：`/usr/trim/etc/network_cert_all.conf`
    JSON 数组：`[{domain, san[], certificate, fullchain, privateKey,
    validFrom, validTo(毫秒), sum, used, appFlag}]`
    证书文件在 `/usr/trim/var/trim_connect/ssls/<domain>/<ts>/`，
    `-rwxr-xr-x root:root` → 普通管理员**可读**，不需要提权。

两条硬规则：
1. **私钥永远不下发**。设备上有没有私钥只标 `has_key`，内容一个字节都不取。
2. 读不到就老实说读不到（返回 `error` + `hint`），不要用「0 张证书」
   冒充「设备上没有证书」—— 这两种情况的处置完全相反。
"""
from __future__ import annotations

import datetime
import json
import re
import shlex

from core import servercert as SC

KIND_SYNO = "synology"
KIND_FNOS = "fnos"

KIND_LABEL = {KIND_SYNO: "群晖 DSM", KIND_FNOS: "飞牛 fnOS"}

SYNO_ARCHIVE = "/usr/syno/etc/certificate/_archive"
FNOS_CONF = "/usr/trim/etc/network_cert_all.conf"
FNOS_SSL_ROOT = "/usr/trim/var/trim_connect/ssls"

# 探测：一条命令同时问两件事，省一次往返。
DETECT_CMD = (
    'test -d /usr/syno/etc/certificate && echo synology; '
    'test -f /usr/trim/etc/network_cert_all.conf && echo fnos'
)

SYNO_CMD = (
    'A=' + SYNO_ARCHIVE + '; echo "@@BEGIN"; '
    'echo "@@DEFAULT"; cat "$A/DEFAULT" 2>/dev/null; echo; '
    'echo "@@INFO"; cat "$A/INFO" 2>/dev/null; echo; '
    'echo "@@CERTS"; '
    'for d in "$A"/*/; do i=$(basename "$d"); '
    # 绝大多数是 cert.pem；只有双证书的归档可能只留 RSA-cert.pem
    'f="$d/cert.pem"; [ -f "$f" ] || f="$d/RSA-cert.pem"; '
    '[ -f "$f" ] || continue; '
    'k="$d/privkey.pem"; [ -f "$k" ] || k="$d/RSA-privkey.pem"; '
    'echo "@@ID $i key=$([ -f "$k" ] && echo 1 || echo 0)"; '
    'cat "$f" 2>/dev/null; echo; done; '
    'echo "@@END"'
)

FNOS_CONF_CMD = 'echo "@@BEGIN"; cat ' + FNOS_CONF + ' 2>/dev/null; echo; echo "@@END"'

# 只允许从 conf 里接受这个前缀下的路径，避免把任意文件当证书 cat 出来。
_FNOS_PATH_RE = re.compile(r"^/usr/trim/var/trim_connect/ssls/[\w./@+%-]+$")


class DeviceCertError(Exception):
    """读不到 —— 带一句给人看的 hint。"""

    def __init__(self, msg: str, hint: str = ""):
        super().__init__(msg)
        self.hint = hint


# ------------------------------------------------------------------ 工具
def _slice(text: str) -> str:
    """取 `@@BEGIN` 与 `@@END` 之间 —— sudo 提示、motd 之类全被挡在外面。"""
    body = str(text or "")
    a = body.find("@@BEGIN")
    b = body.find("@@END", a + 1 if a >= 0 else 0)
    if a < 0:
        return body
    return body[a + len("@@BEGIN"):b if b > 0 else len(body)]


def _ms_to_date(ms) -> str:
    try:
        v = float(ms) / 1000.0
        return datetime.datetime.fromtimestamp(
            v, datetime.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _days_left(date_text: str) -> int:
    if not date_text:
        return 0
    try:
        d = datetime.datetime.strptime(date_text, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return 0
    return (d - datetime.datetime.now(datetime.timezone.utc)).days


def _row_from_pem(pem: str, base: dict) -> dict:
    """PEM → 一行；解析失败也返回一行（带 error），不要整批丢掉。"""
    row = dict(base)
    try:
        info = SC.parse_pem(pem)
    except SC.CertError as e:
        row.update({"cn": "", "sans": [], "not_after": "", "days_left": 0,
                    "error": str(e)})
        return row
    row.update(info)
    row["pem"] = pem
    if not row.get("name"):
        row["name"] = info.get("cn") or row.get("id") or "（无名）"
    return row


def _services_of(info: dict) -> list[str]:
    """INFO 里的服务绑定 → 去重的中文/英文可读名。

    DSM 会把「同一个证书」绑到很多服务上（反向代理每加一条规则就多一项），
    所以去重；`display_name` 像 `*:19820` 这种，前面补一个来源更好认。
    """
    out: list[str] = []
    seen = set()
    for s in (info.get("services") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("display_name") or "").strip()
        sub = str(s.get("subscriber") or "").strip()
        if not name:
            continue
        if sub == "ReverseProxy" and name.startswith("*:"):
            name = "反向代理 " + name
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ------------------------------------------------------------------ 群晖
def _parse_syno(out: str) -> list[dict]:
    text = _slice(out)
    default_id = ""
    info_raw = ""
    # 分段：@@DEFAULT … @@INFO … @@CERTS
    m = re.search(r"@@DEFAULT\s*(.*?)@@INFO", text, re.S)
    if m:
        default_id = m.group(1).strip().splitlines()[0].strip() if \
            m.group(1).strip() else ""
    m = re.search(r"@@INFO\s*(.*?)@@CERTS", text, re.S)
    if m:
        info_raw = m.group(1)
    infos: dict[str, dict] = {}
    if info_raw.strip():
        try:
            loaded = json.loads(info_raw)
            if isinstance(loaded, dict):
                infos = {str(k): v for k, v in loaded.items()
                         if isinstance(v, dict)}
        except ValueError:
            infos = {}
    rows: list[dict] = []
    # @@ID <id> key=<0|1> 后面紧跟 PEM，直到下一个 @@ID
    chunks = re.split(r"^@@ID ", text, flags=re.M)
    for ch in chunks[1:]:
        head, _, rest = ch.partition("\n")
        parts = head.split()
        if not parts:
            continue
        cid = parts[0].strip()
        has_key = "key=1" in head
        pem = rest.strip()
        meta = infos.get(cid) or {}
        desc = str(meta.get("desc") or "").strip()
        base = {
            "id": cid,
            "name": desc,
            "has_key": has_key,
            "used": (cid == default_id),
            "services": _services_of(meta),
            "source": "device",
            "kind": KIND_SYNO,
            "where": f"{SYNO_ARCHIVE}/{cid}/cert.pem",
        }
        if not pem:
            rows.append({**base, "cn": "", "sans": [], "not_after": "",
                         "days_left": 0,
                         "error": "读不到 cert.pem（可能需要 root）"})
            continue
        row = _row_from_pem(pem, base)
        if not desc:
            row["name"] = row.get("cn") or cid
        rows.append(row)
    # 默认证书排前面，其余按名字排
    rows.sort(key=lambda r: (not r.get("used"), str(r.get("name") or "")))
    return rows


def _syno_session(sess):
    """群晖必须提权：返回可用的 run 函数。

    没权限时抛 **NeedElevation**（app 会弹一次 sudo 密码框并自动重跑），
    而不是一句「读不到」—— 后者用户不知道下一步该干什么。
    """
    ok, why = False, ""
    try:
        r = sess.can_admin()
        ok, why = r if isinstance(r, tuple) else (bool(r), "")
    except Exception:  # noqa: BLE001
        ok, why = False, ""
    if not ok:
        from core.session import NeedElevation
        raise NeedElevation(
            "读群晖的证书目录要 root 权限（/usr/syno/etc/certificate 是 "
            "drwx------ root）。" + (f"（{why}）" if why else ""),
            kind="sudo",
            target=f"{getattr(sess, 'user', '')}@{getattr(sess, 'host', '')}")
    return sess.run_admin


def list_synology(sess) -> list[dict]:
    run = _syno_session(sess)
    rc, out, err = run(SYNO_CMD, timeout=60)
    body = _slice(out)
    if "@@INFO" not in body:
        raise DeviceCertError(
            "群晖证书目录读不出来",
            hint=(err or out or "命令没有返回内容").strip()[:300])
    return _parse_syno(out)


# ------------------------------------------------------------------ 飞牛
def _fnos_pem_cmd(paths: list[str]) -> str:
    quoted = " ".join(shlex.quote(p) for p in paths)
    return ('echo "@@BEGIN"; for f in ' + quoted +
            '; do echo "@@FILE $f"; cat "$f" 2>/dev/null; echo; done; '
            'echo "@@END"')


def _parse_fnos_conf(text: str) -> list[dict]:
    body = _slice(text).strip()
    if not body:
        return []
    try:
        data = json.loads(body)
    except ValueError as e:
        raise DeviceCertError(
            "飞牛的证书配置不是合法 JSON",
            hint=f"{FNOS_CONF}：{e}") from None
    if not isinstance(data, list):
        return []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        domain = str(it.get("domain") or "").strip()
        cert_path = str(it.get("certificate") or "").strip()
        full = str(it.get("fullchain") or "").strip()
        valid_to = _ms_to_date(it.get("validTo"))
        out.append({
            "id": domain or cert_path,
            "name": domain or "（无名）",
            "cn": domain,
            "sans": [str(x) for x in (it.get("san") or [])
                     if str(x).strip()],
            "not_before": _ms_to_date(it.get("validFrom")),
            "not_after": valid_to,
            "days_left": _days_left(valid_to),
            "has_key": bool(str(it.get("privateKey") or "").strip()),
            "used": bool(it.get("used")),
            "services": ["当前使用" if it.get("used") else "备用"],
            "source": "device",
            "kind": KIND_FNOS,
            "where": cert_path,
            "pem_path": cert_path if _FNOS_PATH_RE.match(cert_path) else "",
            "fullchain_path": full if _FNOS_PATH_RE.match(full) else "",
            "sum": str(it.get("sum") or ""),
        })
    return out


def _parse_fnos_pems(out: str) -> dict[str, str]:
    text = _slice(out)
    got: dict[str, str] = {}
    chunks = re.split(r"^@@FILE ", text, flags=re.M)
    for ch in chunks[1:]:
        head, _, rest = ch.partition("\n")
        path = head.strip()
        if path:
            got[path] = rest.strip()
    return got


def list_fnos(sess) -> list[dict]:
    rc, out, err = sess.run(FNOS_CONF_CMD, timeout=30)
    rows = _parse_fnos_conf(out)
    if not rows:
        raise DeviceCertError(
            "飞牛的证书清单是空的或读不出来",
            hint=(err or f"预期读取 {FNOS_CONF}").strip()[:300])
    paths = [r["pem_path"] for r in rows if r.get("pem_path")]
    if paths:
        rc2, out2, _ = sess.run(_fnos_pem_cmd(paths), timeout=45)
        pems = _parse_fnos_pems(out2)
        for r in rows:
            pem = pems.get(r.get("pem_path") or "") or ""
            if not pem:
                r["error"] = "证书文件读不到（权限？）"
                continue
            name = r["name"]
            row = _row_from_pem(pem, r)
            row["name"] = name          # 保留 domain 作为显示名
            row["used"] = r.get("used")
            row["services"] = r.get("services")
            row["where"] = r.get("where")
            r.clear()
            r.update(row)
    return rows


# ------------------------------------------------------------------ 对外
def detect(sess) -> str:
    """认出是哪套系统的证书模块；认不出返回 ""。

    按**整行**匹配，不用 `in`：飞牛的 conf 里就可能是
    `"chromas.synology.me"`，子串匹配会把飞牛认成群晖，然后去读一个
    根本不存在的 `_archive`。
    """
    rc, out, _ = sess.run(DETECT_CMD, timeout=20)
    words = {ln.strip() for ln in (out or "").splitlines() if ln.strip()}
    if KIND_SYNO in words:
        return KIND_SYNO
    if KIND_FNOS in words:
        return KIND_FNOS
    return ""


def list_device(sess, kind: str = "") -> list[dict]:
    """读设备证书清单。`kind` 空则先探测。

    返回的每一行都带 `source="device"`、`kind`、`pem`（可能为空）、
    `error`（读不到的原因）。抛 `DeviceCertError` 只在「整批读不出」时。
    """
    kind = kind or detect(sess)
    if kind == KIND_SYNO:
        return list_synology(sess)
    if kind == KIND_FNOS:
        return list_fnos(sess)
    raise DeviceCertError(
        "这台设备上没有认识的证书管理模块",
        hint="目前支持群晖 DSM（/usr/syno/etc/certificate）和 "
             "飞牛 fnOS（/usr/trim/etc/network_cert_all.conf）。")


def summary(rows: list[dict]) -> str:
    """一句话摘要，给动作条右边那行字用。"""
    if not rows:
        return ""
    exp = sum(1 for r in rows if int(r.get("days_left") or 0) < 0)
    soon = sum(1 for r in rows if 0 <= int(r.get("days_left") or 0) <= 30)
    msg = f"{len(rows)} 张"
    if soon:
        msg += f" · {soon} 张 30 天内到期"
    if exp:
        msg += f" · {exp} 张已过期"
    return msg


__all__ = ["KIND_SYNO", "KIND_FNOS", "KIND_LABEL", "DeviceCertError",
           "detect", "list_device", "list_synology", "list_fnos", "summary"]
