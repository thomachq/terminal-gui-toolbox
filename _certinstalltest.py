# -*- coding: utf-8 -*-
"""证书入库自测：预检、计划文本、下发脚本、回滚脚本。

    python _certinstalltest.py

不连设备：这一层要保证的是「生成出来的东西本身是对的」——
脚本语法能编译、不碰不该碰的路径、写入是原子的、备份跑在写之前。
真机验证在 VDSM / 飞牛上已经做过（见 _TODO_20261029.md 任务 B）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import servercert as SC  # noqa: E402
from core import certinstall as CI  # noqa: E402

PASS = 0
FAIL = 0
FAILED: list[str] = []


def chk(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


class FakeSess:
    """只模拟「下发脚本」这条通道。

    `_push` 会把脚本 base64 后 echo|base64 -d 到 /tmp，并要求 wc -c 等于
    原始字节数 —— 这里真的解码回来核对，顺便把脚本留档给断言用。
    """

    def __init__(self, result: dict | None = None, files: dict | None = None):
        self.scripts: list[str] = []
        self.cmds: list[str] = []
        self.result = result or {"ok": True, "id": "AbCd12", "dir": "/x",
                                 "files": [], "sha256": {}, "backup": "/b"}
        self.files = files or {}

    def _decode(self, cmd):
        m = re.search(r"echo (\S+) \| base64 -d", cmd)
        if not m:
            return None
        try:
            return base64.b64decode(m.group(1)).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None

    def run(self, cmd, timeout=30, admin=False):
        self.cmds.append(cmd)
        body = self._decode(cmd)
        if body is not None:
            self.scripts.append(body)
            return 0, str(len(body.encode("utf-8"))), ""
        if cmd.strip().startswith("rm -f"):
            return 0, "", ""
        if "sha256sum" in cmd:
            out = []
            for p in re.findall(r"'([^']+)'", cmd):
                if p in self.files:
                    out.append(f"{self.files[p]}  {p}")
                else:
                    out.append(f"MISSING {p}")
            return 0, "\n".join(out), ""
        if "ps -eo args" in cmd:
            return 0, "3\n", ""
        return 0, "", ""

    def run_admin(self, cmd, timeout=60):
        self.cmds.append("sudo:" + cmd)
        if cmd.startswith("python3 /tmp/"):
            return 0, ("@@RESULT\n" + json.dumps(self.result) + "\n@@END\n"), ""
        return self.run(cmd, timeout=timeout)


def cert_pair(cn="tb.local", sans=("tb.local",), days=365):
    return SC.make_selfsigned(cn, list(sans), days)


def main() -> int:
    print("\n[A] 预检")
    c1, k1 = cert_pair()
    c2, k2 = cert_pair("other.local", ("other.local",))
    info = CI.parse_bundle(c1, k1)
    chk(info["key_ok"] and not info["problems"], "证书与私钥配对时通过",
        str(info["problems"]))
    chk(info["cn"] == "tb.local", "认得出 CN", info["cn"])
    chk("tb.local" in info["sans"], "认得出 SAN", str(info["sans"]))

    bad = CI.parse_bundle(c1, k2)
    chk(not bad["key_ok"], "证书与私钥不配对被抓出来")
    chk(any("不是一对" in p for p in bad["problems"]), "不配对的说法是人话",
        str(bad["problems"]))

    nokey = CI.parse_bundle(c1, "")
    chk(any("没有私钥" in p for p in nokey["problems"]), "缺私钥被抓出来",
        str(nokey["problems"]))

    # make_selfsigned 会强制把 CN 补进 SAN（对，但不方便造「没 SAN」的样本），
    # 所以这里把 parse_pem 临时换掉，专测「没 SAN 时给警告、但不拦」。
    _orig = SC.parse_pem
    SC.parse_pem = lambda text: {**_orig(text), "sans": []}
    try:
        info_nosan = CI.parse_bundle(c1, k1)
    finally:
        SC.parse_pem = _orig
    chk(any("SAN" in w for w in info_nosan["warnings"]),
        "没 SAN 给警告但不拦", str(info_nosan["warnings"]))
    chk(not info_nosan["problems"], "没 SAN 不算致命", str(info_nosan["problems"]))

    print("\n[B] 域名 / id")
    chk(CI.pick_domain(info) == "tb.local", "优先用域名 SAN",
        CI.pick_domain(info))
    ip_c, ip_k = cert_pair("10.0.0.1", ("10.0.0.1", "web.local"))
    chk(CI.pick_domain(CI.parse_bundle(ip_c, ip_k)) == "web.local",
        "避开纯 IP 的 SAN")
    ids = {CI.new_id() for _ in range(50)}
    chk(len(ids) == 50 and all(len(i) == 6 for i in ids), "id 是 6 位且不重复",
        str(list(ids)[:3]))

    print("\n[C] 计划文本（确认框里给人看的）")
    p1 = CI.make_plan(CI.KIND_SYNO, info, item_id="AbCd12")
    text1 = "\n".join(CI.plan_lines(p1))
    chk("_archive/AbCd12" in text1, "写出群晖的落点", text1[:60])
    chk("services: []" in text1, "说明只入库不绑定")
    chk("不重启" in text1, "说明不重启服务")
    chk(not any("system/" in f for f in p1["files"]),
        "群晖计划里没有 system/ 下的文件", str(p1["files"]))
    chk(not any("SERVICES" in f for f in p1["files"]),
        "群晖计划里没有 SERVICES", str(p1["files"]))

    p2 = CI.make_plan(CI.KIND_FNOS, info, domain="tb.local")
    text2 = "\n".join(CI.plan_lines(p2))
    chk("ssls/tb.local" in text2, "写出飞牛的落点", text2[:60])
    chk("used:false" in text2, "说明不参与 SNI 路由")

    print("\n[D] 群晖下发脚本")
    sg = CI._syno_script("AbCd12", "工具箱", c1, k1, "")
    try:
        compile(sg, "<syno>", "exec")
        ok = True
        why = ""
    except SyntaxError as e:  # noqa: BLE001
        ok, why = False, str(e)
    chk(ok, "脚本语法能编译", why)
    chk("shutil.copy2(info, backup)" in sg, "先备份 INFO 再动手")
    chk(sg.index("shutil.copy2(info, backup)") < sg.index("os.makedirs(d"),
        "备份在创建目录之前")
    chk("os.replace(tmp, info)" in sg, "INFO 是原子替换写")
    chk('data[ID] = {"desc": DESC, "services": []}' in sg,
        "登记项明确 services 为空")
    chk("system/" not in sg, "脚本里完全不出现 system/")
    chk("SERVICES" not in sg, "脚本里完全不出现 SERVICES")
    chk("DEFAULT" not in sg, "脚本里不碰 DEFAULT")
    chk('write("privkey.pem", key, 0o600)' in sg, "私钥权限 600")
    chk("nginx" not in sg.lower() or "reload" not in sg,
        "不会 reload nginx")
    chk("indent=3" in sg and 'm.group(2) + \'" : \'' in sg,
        "INFO 按 DSM 原格式（indent=3 + 冒号前空格）回写")

    print("\n[E] 飞牛下发脚本")
    sf = CI._FNOS_HEAD % {
        "conf": CI.FNOS_CONF, "ssls": CI.FNOS_SSLS, "bak": CI.BAK_DIR,
        "domain": "tb.local", "sum": "a" * 32,
        "valid_from": 1, "valid_to": 2,
        "cert": base64.b64encode(c1.encode()).decode(),
        "key": base64.b64encode(k1.encode()).decode(),
        "chain": "",
    }
    try:
        compile(sf, "<fnos>", "exec")
        ok = True
        why = ""
    except SyntaxError as e:  # noqa: BLE001
        ok, why = False, str(e)
    chk(ok, "飞牛脚本语法能编译", why)
    chk('"used": False' in sf and '"appFlag": 0' in sf,
        "条目是 used:false / appFlag:0")
    chk('separators=(",", ":")' in sf, "conf 按飞牛原格式（紧凑）回写")
    chk("os.replace(tmp, CONF)" in sf, "conf 原子替换")
    chk("shutil.copy2(CONF, backup)" in sf, "先备份 conf")
    chk('write(DOMAIN + ".key", key, 0o600)' in sf, "私钥权限 600")
    chk("systemctl" not in sf and "reload" not in sf, "不重启、不 reload")

    print("\n[F] 下发通道（base64 → 字节数核对 → @@RESULT）")
    fs = FakeSess(result={"ok": True, "id": "AbCd12", "dir": "/x",
                          "files": ["/x/cert.pem"], "sha256": {},
                          "backup": "/b", "kind": "synology"})
    res = CI._run_script(fs, sg, "syno")
    chk(res.get("id") == "AbCd12", "读到 @@RESULT 里的结果", str(res)[:60])
    chk(any(c.startswith("rm -f /tmp/") for c in fs.cmds), "跑完删掉临时脚本",
        "")
    chk(any(c.startswith("sudo:python3") for c in fs.cmds), "用提权通道执行", "")

    # 截断必须报错，而不是静默
    class Short(FakeSess):
        def run(self, cmd, timeout=30, admin=False):
            if "base64 -d" in cmd:
                return 0, "12", ""
            return super().run(cmd, timeout=timeout, admin=admin)

    try:
        CI._push(Short(), "print(1)\n")
        chk(False, "传输出错要抛异常")
    except CI.InstallError as e:
        chk("截断" in str(e), "字节数不对就报错", str(e)[:60])

    print("\n[G] 回读校验")
    shas = {"/x/cert.pem": "deadbeef"}
    fs2 = FakeSess(result={}, files=shas)
    ok, msg = CI.verify(fs2, {"kind": "synology", "files": ["/x/cert.pem"],
                              "sha256": shas, "id": "AbCd12"})
    chk(ok, "sha256 一致时校验通过", msg)

    fs3 = FakeSess(result={}, files={})
    ok2, msg2 = CI.verify(fs3, {"kind": "synology", "files": ["/x/cert.pem"],
                                "sha256": shas, "id": "AbCd12"})
    chk(not ok2 and "没写进去" in msg2, "文件缺失时报失败", msg2)

    fs4 = FakeSess(result={}, files={"/x/cert.pem": "0" * 8})
    ok3, msg3 = CI.verify(fs4, {"kind": "synology", "files": ["/x/cert.pem"],
                                "sha256": shas, "id": "AbCd12"})
    chk(not ok3 and "sha256" in msg3, "内容被改时报失败", msg3)

    print("\n[H] 回滚脚本")
    captured: list[str] = []

    class CapSess(FakeSess):
        def run_admin(self, cmd, timeout=60):
            # rollback 走 _run_script → run_admin("python3 /tmp/..")
            if cmd.startswith("python3 /tmp/"):
                captured.append(self.scripts[-1])
                return 0, '@@RESULT\n{"ok": true, "removed": true}\n@@END', ""
            return self.run(cmd, timeout=timeout)

    cap = CapSess()
    ok4, msg4 = CI.rollback(cap, {"kind": "synology", "id": "AbCd12",
                                  "dir": "/usr/syno/etc/certificate/_archive/AbCd12",
                                  "backup": "/root/certbak/INFO.1"})
    chk(ok4, "回滚返回成功", msg4)
    body = captured[0] if captured else ""
    try:
        compile(body, "<uninst>", "exec")
        okc = True
    except SyntaxError:
        okc = False
    chk(okc, "回滚脚本能编译")
    chk("shutil.rmtree(d)" in body, "删掉刚建的目录")
    chk("data.pop(key, None)" in body, "撤掉 INFO 里的登记项")
    chk("shutil.copy2(backup, info)" in body, "出错时用备份还原")

    cap2 = CapSess()
    ok5, _ = CI.rollback(cap2, {"kind": "fnos", "sum": "b" * 32,
                                "dir": "/usr/trim/var/trim_connect/ssls/x/1",
                                "backup": "/root/certbak/c.1"})
    body2 = captured[1] if len(captured) > 1 else ""
    chk(ok5 and "ssum" in body2, "飞牛回滚按 sum 摘条目", body2[:40])

    print("\n[I] 不认识的设备")
    try:
        CI.make_plan("openwrt", {})
        chk(False, "不认识的类型要抛异常")
    except CI.InstallError as e:
        chk("不认识" in str(e), "不认识的类型报错清楚", str(e)[:40])

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    if FAILED:
        for n in FAILED:
            print("  ·", n)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
