# -*- coding: utf-8 -*-
"""设备证书读取的闸门 —— 群晖 DSM / 飞牛 fnOS。

起因（用户 2026-10-27）：
  「安全中心里的证书管理，并没有同步设备的服务器证书管理模块的数据，
    包括群晖和飞牛」

真机实测（只读）：
  · 群晖 /usr/syno/etc/certificate/_archive ：drwx------ root，5 张证书，
    `INFO` 是 {id: {desc, services[]}}，`DEFAULT` 是默认证书 id，
    `<id>/cert.pem` + `privkey.pem`。**必须 run_admin()**；
  · 飞牛 /usr/trim/etc/network_cert_all.conf ：JSON 数组（644 可读），
    证书在 /usr/trim/var/trim_connect/ssls/<domain>/<ts>/（root:root 但 o+r）。

这里钉住的是「解析与降级」这条链 —— 真机连通性由 _devcerttry.py 另外验：
  A. 群晖：@@ 标记切分、DEFAULT、服务去重、PEM 缺失不炸、sudo 噪声不污染；
  B. 飞牛：毫秒时间戳、非法路径不许进命令、坏 JSON 要说人话；
  C. 对外：认不出的系统要明确说「支持哪两套」，别装作「设备上没有证书」。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import devicecert as DC                             # noqa: E402
from core import servercert as SC                             # noqa: E402

_p = _f = 0


def chk(ok, label, extra=""):
    global _p, _f
    if ok:
        _p += 1
        print(f"  ok   {label}")
    else:
        _f += 1
        print(f"  FAIL {label}" + (f"  → {extra}" if extra else ""))


def sec(t):
    print(f"\n{t}")


# ---------------------------------------------------------------- fixtures
CERT_A, KEY_A = SC.make_selfsigned("nas-a.local", ["nas-a.local", "10.0.0.5"])
CERT_B, KEY_B = SC.make_selfsigned("nas-b.local", ["nas-b.local"])

SYNO_INFO = """{
   "aaa111" : {
      "desc" : "",
      "services" : [
         {"display_name": "WebDAVServer", "service": "webdav",
          "subscriber": "WebDAVServer", "isPkg": true},
         {"display_name": "WebDAVServer", "service": "webdav",
          "subscriber": "WebDAVServer", "isPkg": true},
         {"display_name": "*:19820", "service": "x1",
          "subscriber": "ReverseProxy", "user_setable": true},
         {"display_name": "DSM Desktop Service", "service": "default",
          "subscriber": "system", "user_setable": true}
      ]
   },
   "bbb222" : {
      "desc" : "家里那张通配符",
      "services" : []
   }
}
"""

SYNO_OUT = (
    "sudo: a password is required\n"          # 噪声：必须在 @@BEGIN 之前
    "@@BEGIN\n"
    "@@DEFAULT\nbbb222\n\n"
    "@@INFO\n" + SYNO_INFO + "\n"
    "@@CERTS\n"
    "@@ID aaa111 key=1\n" + CERT_A + "\n"
    "@@ID bbb222 key=0\n" + CERT_B + "\n"
    "@@END\n"
)

# 第三个目录故意只留 ID、没有 PEM（模拟读不到 cert.pem）
SYNO_OUT_NO_PEM = (
    "@@BEGIN\n@@DEFAULT\naaa111\n\n@@INFO\n{}\n@@CERTS\n"
    "@@ID aaa111 key=1\n" + CERT_A + "\n"
    "@@ID ccc333 key=0\n"
    "@@END\n"
)

FNOS_CONF = (
    '[{"domain":"chromas.synology.me","san":["chromas.synology.me"],'
    '"certificate":"/usr/trim/var/trim_connect/ssls/chromas.synology.me/1791/'
    'a.crt","fullchain":"/usr/trim/var/trim_connect/ssls/chromas.synology.me/'
    '1791/fullchain.crt","privateKey":"/usr/trim/var/trim_connect/ssls/'
    'chromas.synology.me/1791/a.key","validFrom":1786568633000,'
    '"validTo":1794344632000,"used":true,"appFlag":14},'
    '{"domain":"fnOS","san":["fnOS"],'
    '"certificate":"/etc/shadow","fullchain":"",'
    '"privateKey":"/usr/trim/var/trim_connect/ssls/fnOS/1759/fnOS.key",'
    '"validFrom":1790909874000,"validTo":1798858674000,"used":false,'
    '"appFlag":0}]'
)

FNOS_OUT = (
    "@@BEGIN\n" + FNOS_CONF + "\n"
    "@@FILE /usr/trim/var/trim_connect/ssls/chromas.synology.me/1791/a.crt\n"
    + CERT_A + "\n"
    "@@END\n"
)


class FakeSess:
    """回放输出；`run_out` 给列表时按调用顺序依次返回（conf → PEM）。"""

    def __init__(self, run_out="", admin_out="", admin=True):
        self.run_out = run_out
        self.admin_out = admin_out
        self.admin = admin
        self.user, self.host = "cloudadmin", "192.168.3.12"
        self.calls: list[tuple[str, str]] = []
        self._i = 0

    def _next(self):
        if isinstance(self.run_out, list):
            i = min(self._i, len(self.run_out) - 1)
            self._i += 1
            return self.run_out[i]
        return self.run_out

    def run(self, cmd, timeout=30):
        self.calls.append(("run", cmd))
        return 0, self._next(), ""

    def run_admin(self, cmd, timeout=60):
        self.calls.append(("admin", cmd))
        return 0, self.admin_out, ""

    def can_admin(self):
        return (self.admin, "" if self.admin else "需要 sudo 密码")


# ---------------------------------------------------------------- A 群晖
sec("A. 群晖 DSM：_archive 的 INFO / DEFAULT / cert.pem")
s = FakeSess(run_out="synology\n", admin_out=SYNO_OUT)
chk(DC.detect(s) == DC.KIND_SYNO, "detect 认出群晖")
rows = DC.list_synology(s)
chk(len(rows) == 2, "两张证书都在", rows)
byid = {r["id"]: r for r in rows}
chk(byid["bbb222"]["used"] is True, "DEFAULT 指向的那张标「在用」")
chk(byid["aaa111"]["used"] is False, "非默认的不标在用")
chk(byid["bbb222"]["name"] == "家里那张通配符", "有 desc 用 desc 当名字",
    byid["bbb222"]["name"])
chk(byid["aaa111"]["name"] == "nas-a.local", "没 desc 用 CN 当名字",
    byid["aaa111"]["name"])
chk(byid["bbb222"]["has_key"] is False, "key=0 → 无私钥")
chk(byid["aaa111"]["has_key"] is True, "key=1 → 有私钥")
chk(byid["aaa111"]["cn"] == "nas-a.local", "CN 解析正确")
chk("10.0.0.5" in byid["aaa111"]["sans"], "SAN 解析正确（含 IP）",
    byid["aaa111"]["sans"])
chk(byid["aaa111"]["pem"].startswith("-----BEGIN CERTIFICATE-----"),
    "PEM 原文保留（详情/保存要用）")
chk(rows[0]["used"] is True, "默认证书排第一")
svc = byid["aaa111"]["services"]
chk(svc.count("WebDAVServer") == 1, "重复服务去重", svc)
chk(any(x.startswith("反向代理") for x in svc), "反向代理带前缀", svc)
chk(not any("sudo:" in r.get("name", "") for r in rows),
    "sudo 提示没混进证书名")

sec("A2. 群晖：cert.pem 读不到时降级，不整批失败")
rows2 = DC._parse_syno(SYNO_OUT_NO_PEM)
chk(len(rows2) == 2, "两行都在（有一行没 PEM）", len(rows2))
bad = [r for r in rows2 if r.get("error")]
chk(len(bad) == 1 and "cert.pem" in bad[0]["error"],
    "缺 PEM 的那行带 error 说明", bad)

sec("A3. 群晖：没提权就抛 NeedElevation（app 弹密码框），不是「读不到」")
from core.session import NeedElevation                         # noqa: E402
s2 = FakeSess(run_out="synology\n", admin_out=SYNO_OUT, admin=False)
try:
    DC.list_synology(s2)
    chk(False, "无权限应抛 NeedElevation")
except NeedElevation as e:
    chk(e.kind == "sudo", "NeedElevation.kind = sudo", e.kind)


# ---------------------------------------------------------------- B 飞牛
sec("B. 飞牛 fnOS：network_cert_all.conf")
f = FakeSess(run_out="fnos\n")
chk(DC.detect(f) == DC.KIND_FNOS, "detect 认出飞牛（整行匹配）", DC.detect(f))
f2 = FakeSess(run_out='@@BEGIN\n[{"domain":"chromas.synology.me"}]\n@@END\n')
chk(DC.detect(f2) == "", "conf 文本里的 synology 字样不会把飞牛认成群晖")
rows3 = DC.list_fnos(FakeSess(run_out=[FNOS_OUT.split("@@FILE")[0] + "@@END\n",
                                       "@@BEGIN\n@@FILE /usr/trim/var/trim_connect/"
                                       "ssls/chromas.synology.me/1791/a.crt\n"
                                       + CERT_A + "\n@@END\n"]))
chk(len(rows3) == 2, "两条都在", len(rows3))
byid3 = {r["id"]: r for r in rows3}
chk(byid3["chromas.synology.me"]["used"] is True, "used=true → 在用")
chk(byid3["fnOS"]["used"] is False, "used=false → 备用")
chk(byid3["chromas.synology.me"]["has_key"] is True, "privateKey 非空 → 有私钥")
chk(byid3["chromas.synology.me"]["pem"].startswith("-----BEGIN"),
    "PEM 读到了")
chk(byid3["chromas.synology.me"]["name"] == "chromas.synology.me",
    "显示名用 conf 里的 domain（不被 CN 顶掉）",
    byid3["chromas.synology.me"]["name"])

# 时间戳/SAN 这类字段以**证书文件本身**为准（conf 里的只是缓存），
# 所以这两条要在「还没合并 PEM」的纯 conf 解析上验。
conf_only = {r["id"]: r for r in DC._parse_fnos_conf("@@BEGIN\n" + FNOS_CONF
                                                      + "\n@@END")}
chk(conf_only["chromas.synology.me"]["not_after"] == "2026-11-10",
    "毫秒时间戳转成日期", conf_only["chromas.synology.me"]["not_after"])
chk(conf_only["chromas.synology.me"]["sans"] == ["chromas.synology.me"],
    "conf 里的 SAN 解析正确")
chk(conf_only["chromas.synology.me"]["days_left"] < 0
    or conf_only["chromas.synology.me"]["days_left"] > 0,
    "days_left 算出来了", conf_only["chromas.synology.me"]["days_left"])
chk(conf_only["fnOS"]["pem_path"] == "",
    "conf 里的非法路径（/etc/shadow）不许进命令",
    conf_only["fnOS"]["pem_path"])

sec("B2. 飞牛：坏 JSON 要报人话，不能静默返回空")
try:
    DC._parse_fnos_conf("@@BEGIN\n{not json\n@@END")
    chk(False, "坏 JSON 应抛 DeviceCertError")
except DC.DeviceCertError as e:
    chk("JSON" in str(e), "报错说明是 JSON 问题", str(e))

sec("B3. 飞牛：空 conf / 读不到")
try:
    DC.list_fnos(FakeSess(run_out="@@BEGIN\n\n@@END\n"))
    chk(True, "空清单 → 由调用方判空（不抛）")
except DC.DeviceCertError:
    chk(True, "空清单 → DeviceCertError 也合理")


# ---------------------------------------------------------------- C 对外
sec("C. 对外接口")
try:
    DC.list_device(FakeSess(), kind="")
    chk(False, "认不出的系统要抛 DeviceCertError")
except DC.DeviceCertError as e:
    chk("群晖" in e.hint and "飞牛" in e.hint,
        "提示里点名支持哪两套", e.hint)

chk(DC.summary([]) == "", "空列表 → 空摘要")
msg = DC.summary([{"days_left": -3}, {"days_left": 10}, {"days_left": 200}])
chk("1 张 30 天内到期" in msg, "摘要含 30 天内到期", msg)
chk("1 张已过期" in msg, "摘要含已过期", msg)

sec("C2. 表格行渲染（src / 用途 / 默认标记）")
from pages.security_page import SecurityPage                    # noqa: E402
row = SecurityPage._dev_row(None, {
    "name": "chromas.synology.me", "kind": DC.KIND_SYNO, "used": True,
    "cn": "chromas.synology.me", "sans": ["chromas.synology.me"],
    "not_after": "2027-01-10", "days_left": 75, "has_key": True,
    "services": ["WebDAVServer", "DSM Desktop Service", "反向代理 *:19820"],
    "source": "device", "pem": "x",
})
chk(row["src"] == "群晖 DSM", "来源列显示设备类型", row["src"])
chk("默认" in row["name"], "默认证书在名字上标出来", row["name"])
chk(row["usage"].endswith("等 3 项"), "服务多了收成「等 N 项」", row["usage"])
chk(row["_raw"]["source"] == "device", "_raw 保留 source，供菜单判权限")
chk(row["days"] == "75 天", "剩余天数格式化", row["days"])

sec("D. 编译闸门")
chk("RSA-cert.pem" in DC.SYNO_CMD,
    "群晖命令里留了 RSA-cert.pem 兜底（双证书归档）")
chk("@@BEGIN" in DC.SYNO_CMD and "@@END" in DC.SYNO_CMD,
    "群晖命令带 @@ 标记（挡住 sudo 提示）")
chk(DC.SYNO_CMD.count(">") == DC.SYNO_CMD.count(">/dev/null")
    and "rm " not in DC.SYNO_CMD and "mv " not in DC.SYNO_CMD
    and "cp " not in DC.SYNO_CMD and "tee" not in DC.SYNO_CMD,
    "群晖命令是只读的（重定向只有 2>/dev/null）")
chk("--update" not in DC.FNOS_CONF_CMD,
    "飞牛只读 conf，不碰 trim_connect 的写接口")
import py_compile                                               # noqa: E402
for fn in ("core/devicecert.py", "pages/security_page.py"):
    try:
        py_compile.compile(str(HERE / fn), doraise=True)
        chk(True, f"{fn} 编译通过")
    except Exception as e:  # noqa: BLE001
        chk(False, f"{fn} 编译通过", e)

print(f"\n结果：{_p} 通过 / {_f} 失败")
if _f:
    print(" 失败项见上面的 FAIL 行")
sys.exit(1 if _f else 0)
