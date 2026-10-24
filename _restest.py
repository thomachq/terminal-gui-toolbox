# -*- coding: utf-8 -*-
"""主机资源四块数据层的离线自测：hosts / creds / proxies / scripts / audit。

全部是纯函数 + 临时目录里的真实读写，不需要连主机、不开窗口。
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import audit as A          # noqa: E402
from core import creds as C          # noqa: E402
from core import hosts as H          # noqa: E402
from core import proxies as P        # noqa: E402
from core import scripts as S        # noqa: E402

PASS = FAIL = 0
FAILS: list[str] = []


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")


def eq(a, b, name):
    chk(a == b, name, f"期望 {b!r} 实得 {a!r}")


def sec(t):
    print(f"\n-- {t}")


# ================================================================== hosts
sec("1. 主机清单纯函数")

eq(H.parse_tags("web, 生产 ; 上海"), ["web", "生产", "上海"], "标签切分")
eq(H.parse_tags("web,,web，WEB"), ["web"], "标签去重（忽略大小写）")
eq(H.parse_tags("#db prod"), ["db", "prod"], "标签去掉 #")
eq(H.parse_tags(None), [], "空标签")
eq(H.join_tags(["a", "b"]), "a, b", "标签合并")
eq(H.group_of({"group": ""}), H.UNGROUPED, "空分组归一")
eq(H.group_of({"group": " 机房A "}), "机房A", "分组去空白")
eq(H.proto_of({"proto": ""}), "ssh", "协议缺省 ssh")
eq(H.proto_of({"port": "23"}), "telnet", "23 端口推 telnet")
eq(H.proto_of({"proto": "local"}), "local", "本机协议保留")

eq(H.addr_of({"host": "10.0.0.1", "user": "root", "port": "22"}),
   "root@10.0.0.1", "22 端口不显示")
eq(H.addr_of({"host": "10.0.0.1", "user": "root", "port": "2222"}),
   "root@10.0.0.1:2222", "非标端口要显示")
eq(H.addr_of({"host": "10.0.0.1", "port": "23", "proto": "telnet"}),
   "10.0.0.1:23", "telnet 显式端口")
eq(H.addr_of({}), "—", "无地址给破折号")
eq(H.addr_of({"proto": "local"}), "localhost", "本机显示 localhost 而不是破折号")
eq(H.addr_of({"proto": "local", "host": "127.0.0.1"}), "localhost",
   "本机即使存了 host 也统一写 localhost")
eq(H.display_name({"name": "", "host": "h1"}), "h1", "无名字回落主机名")
eq(H.display_name({}), "未命名主机", "全空兜底")

p1 = {"name": "web-1", "host": "10.0.0.1", "port": "22", "user": "root",
      "group": "生产", "tags": "web, 上海", "cred_id": "aaa",
      "last_used": "1700000000"}
v = H.host_view(p1, cred_name="运维账号")
eq(v["name"], "web-1", "行-名称")
eq(v["addr"], "root@10.0.0.1", "行-地址")
eq(v["group"], "生产", "行-分组")
eq(v["tags"], "web, 上海", "行-标签")
eq(v["cred"], "运维账号", "行-凭证名")
eq(v["proto"], "SSH", "行-协议显示")
chk(v["last_used"] != "从未连接", "行-最近使用", v["last_used"])
chk(v["_raw"] is p1, "行-保留原始引用")

v2 = H.host_view({"name": "x", "host": "h", "cred_id": "gone"})
eq(v2["cred"], "已失效引用", "凭证被删后的显示")

rows = [
    {"name": "web-1", "host": "10.0.0.1", "group": "生产", "tags": "web"},
    {"name": "db-1", "host": "10.0.0.2", "group": "生产", "tags": "db"},
    {"name": "test", "host": "192.168.1.5", "group": "", "user": "ops"},
]
eq(len(H.filter_hosts(rows, q="web")), 1, "搜索-标签")
eq(len(H.filter_hosts(rows, q="生产")), 2, "搜索-分组")
eq(len(H.filter_hosts(rows, q="ops")), 1, "搜索-用户")
eq(len(H.filter_hosts(rows, q="web 生产")), 1, "多词 AND")
eq(len(H.filter_hosts(rows, q="web db")), 0, "多词 AND 不跨界")
eq(len(H.filter_hosts(rows, group="生产")), 2, "分组筛选")
eq(len(H.filter_hosts(rows, group=H.UNGROUPED)), 1, "未分组筛选")
eq(len(H.filter_hosts(rows, proto="ssh")), 3, "协议筛选")

gl = H.group_list(rows)
eq(gl[0], ("生产", 2), "分组统计排序")
eq(H.group_choices(rows)[0], "全部分组", "分组下拉首项")
chk(H.UNGROUPED in H.group_choices(rows), "分组下拉含未分组")

eq(H.summary([]), "还没有主机", "空清单文案")
eq(H.summary(rows), "共 3 台 · 2 个分组", "清单摘要")
chk("筛选出 1 台" in H.summary(rows[:1], total=3), "筛选摘要带全量数")

sr = H.sort_rows([{"name": "b", "last_used": "0"},
                  {"name": "a", "last_used": "200"},
                  {"name": "c", "last_used": "100"}], key="last_used", desc=True)
eq([r["name"] for r in sr], ["a", "c", "b"], "按最近使用排序，空值沉底")

eq(H.validate({"name": "", "host": "h", "port": "22"}), ["名称不能为空"], "校验-空名")
eq(H.validate({"name": "a", "host": "", "port": "22"}), ["地址不能为空"], "校验-空地址")
chk("空格" in " ".join(H.validate({"name": "a", "host": "a b", "port": "22"})),
    "校验-地址有空格")
chk("斜杠" in " ".join(H.validate({"name": "a", "host": "a/b", "port": "22"})),
    "校验-地址有斜杠")
eq(H.validate({"name": "a", "host": "h", "port": "70000"}),
   ["端口必须在 1–65535 之间"], "校验-端口越界")
eq(H.validate({"name": "a", "host": "h", "port": "abc"}), ["端口必须是数字"],
   "校验-端口非数字")
eq(H.validate({"name": "a", "host": "h", "port": "22"}, others=[{"name": "a"}]),
   ["名称「a」已被占用"], "校验-重名")
# 编辑场景：主机没有 id，名字就是主键，必须能按「改之前的名字」跳过自己。
# 不传 skip_name 时老逻辑靠 `o is rec` 判身份，而调用方交来的永远是合并后的新
# dict，于是编辑任何一台主机都被判重名（用户看到「名称已被占用」）。
eq(H.validate({"name": "a", "host": "h", "port": "22"},
              others=[{"name": "a"}], skip_name="a"),
   [], "校验-编辑改自己不算重名")
eq(H.validate({"name": "b", "host": "h", "port": "22"},
              others=[{"name": "a"}, {"name": "b"}], skip_name="a"),
   ["名称「b」已被占用"], "校验-编辑时撞到别人仍拦住")
eq(H.validate({"name": "a", "host": "h", "port": "22"},
              others=[{"name": "a"}], skip_name=""),
   ["名称「a」已被占用"], "校验-新增时同名仍拦住")
eq(H.validate({"name": "a", "host": "h", "port": "22"},
              others=[{"name": " a "}], skip_name="a"),
   [], "校验-跳过自己时也去空白")
eq(H.validate({"name": "a", "host": "h", "port": "22"},
              others=[{"name": "a"}, {"name": "a"}], skip_name="a"),
   [], "校验-老档里有同名的两条也能存")
eq(H.validate({"name": "a", "proto": "local"}), [], "校验-本机不需要地址")

rec = H.record_from_form({"name": " n1 ", "host": " h1 ", "tags": "a, b",
                          "group": " g ", "port": "2222"}, base=p1)
eq(rec["name"], "n1", "表单-去空白")
eq(rec["tags"], "a, b", "表单-标签归一")
eq(rec["cred_id"], "aaa", "表单-保留不可见字段")
eq(rec["_raw"] if "_raw" in rec else "", "", "表单-不残留 _raw")

eq(H.import_hint(3, 2), "新增 3 台，跳过 2 台（同名已存在）", "导入提示-两者")
eq(H.import_hint(0, 0), "没在 ~/.ssh/config 里找到可用主机", "导入提示-全无")
eq(H.revoke_cred({"cred_id": "x"})["cred_id"], "", "撤销凭证引用")
eq(H.blank_record("telnet")["port"], "23", "空记录默认端口")

# ================================================================== creds
sec("2. 凭证管理")

eq(C.kind_label("password"), "登录密码", "凭证类型名")
eq(C.kind_label("nope"), "登录密码", "未知类型回落")
eq(C.secret_state("")[1], "err", "空口令色调")
eq(C.secret_state("x")[1], "ok", "有口令色调")
eq(C.secret_len_hint("abcdef"), "6 位", "口令长度提示")
eq(C.secret_brief({"kind": "password", "secret": "abcdef"}), "已设置 · 6 位",
   "口令不显示原文")
eq(C.secret_brief({"kind": "key", "secret": "/k/id_rsa"}), "/k/id_rsa",
   "私钥路径可显示")
chk("abcdef" not in C.secret_brief({"kind": "password", "secret": "abcdef"}),
    "口令绝不外泄")

cv = C.cred_view({"id": "c1", "name": "运维", "kind": "password",
                  "user": "root", "secret": "pw", "created": "1700000000"},
                 used_by=["web-1", "db-1"])
eq(cv["usage"], "2 台", "凭证使用计数")
eq(cv["_id"], "c1", "凭证 id")
eq(C.cred_view({"name": "x"})["usage"], "未使用", "未被使用")

cl = [C.cred_view({"id": "c1", "name": "运维", "kind": "password"}),
      C.cred_view({"id": "c2", "name": "密钥", "kind": "key"})]
eq(len(C.filter_creds(cl, q="运维")), 1, "凭证搜索")
eq(len(C.filter_creds(cl, kind="key")), 1, "凭证类型筛选")
eq(C.summary([]), "还没有凭证", "凭证空文案")

chk("名称不能为空" in C.validate({"name": "", "kind": "password"}), "凭证-空名")
chk("口令不能为空" in " ".join(C.validate({"name": "a", "kind": "password"})),
    "凭证-口令必填")
eq(C.validate({"name": "a", "kind": "password", "secret": "x"}), [], "凭证-通过")
chk("名称「a」已存在" in C.validate({"name": "a", "kind": "password",
                                    "secret": "x"}, others=[{"name": "A"}]),
    "凭证-重名忽略大小写")
_tilde = C.validate({"name": "a", "kind": "key", "secret": "~/.ssh/id_rsa"})
chk(bool(_tilde) if __import__("os").name == "nt" else True,
    "凭证-私钥路径不能带 ~（Windows）", str(_tilde))

um = C.usage_map([{"id": "c1"}, {"id": "c2"}],
                 [{"name": "b", "cred_id": "c1"}, {"name": "a", "cred_id": "c1"},
                  {"name": "c", "cred_id": ""}])
eq(um["c1"], ["a", "b"], "引用表排序")
chk("c2" not in um, "无引用的凭证不出现")
chk("还有 2 台主机在用" in C.delete_block_reason(["a", "b"]), "删除拦截理由")
eq(C.delete_block_reason([]), "", "无引用可删")

base = {"id": "c1", "name": "a", "kind": "password", "secret": "old"}
keep = C.cred_from_form({"name": "a2", "secret": ""}, base=base)
eq(keep["secret"], "old", "编辑时留空保留原口令")
new = C.cred_from_form({"name": "a", "secret": "new"}, base=base)
eq(new["secret"], "new", "编辑时填了就换")
sw = C.cred_from_form({"name": "a", "kind": "key", "secret": ""}, base=base)
eq(sw["secret"], "", "换类型不沿用旧口令")

with tempfile.TemporaryDirectory() as td:
    st = C.CredStore(Path(td) / "creds.dat")
    r1 = st.upsert({"name": "运维账号", "kind": "password", "user": "root",
                    "secret": "s3cret"})
    chk(bool(r1["id"]), "凭证 id 自动生成")
    st.upsert({"name": "密钥A", "kind": "key", "secret": "/k/id_rsa"})
    eq(len(st.creds), 2, "凭证落盘两条")
    st2 = C.CredStore(Path(td) / "creds.dat")
    eq(len(st2.creds), 2, "重开读回两条")
    eq(st2.secret_of(r1["id"]), "s3cret", "密文往返口令不丢")
    chk(st2.encrypted, "凭证文件是密文")
    raw = (Path(td) / "creds.dat").read_bytes()
    chk(b"s3cret" not in raw, "密文里看不到明文口令")
    st2.upsert({**st2.get(r1["id"]), "note": "备注"})
    eq(len(st2.creds), 2, "同名 id 是更新不是新增")
    chk(st2.remove(r1["id"]), "凭证删除")
    eq(len(st2.creds), 1, "删除后剩一条")

# ================================================================== proxies
sec("3. 代理池")

eq(P.kind_label("ssh"), "SSH 跳板", "代理类型名")
eq(P.default_port("socks5"), "1080", "默认端口")

px = [{"id": "p1", "name": "东京", "kind": "ssh", "host": "1.1.1.1",
       "port": "22", "parent_id": ""},
      {"id": "p2", "name": "香港", "kind": "ssh", "host": "2.2.2.2",
       "port": "22", "parent_id": "p1"}]
eq([p["name"] for p in P.chain_of(px, "p2")], ["东京", "香港"], "链路顺序")
eq(P.chain_text(px, "p2"), "东京 → 香港 → 目标", "链路文案")
eq(P.chain_text(px, ""), "直连", "空链路")
eq(P.depth_of(px, "p2"), 2, "链路跳数")
chk(not P.detect_cycle(px, "p2"), "正常链无环")
cyc = px + [{"id": "p0", "name": "A", "parent_id": "p2"}]
cyc[0]["parent_id"] = "p0"
chk(P.detect_cycle(cyc, "p0"), "识别出环")
eq(P.children_of(px, "p1"), ["香港"], "子节点查询")

eq(P.addr_of({"host": "h", "port": "1080"}), "h:1080", "代理地址")
pv = P.proxy_view(px[1], px, used_by=["web-1"])
eq(pv["chain"], "东京 → 香港 → 目标", "代理行-链路")
eq(pv["depth"], "2 跳", "代理行-跳数")
eq(pv["usage"], "1 台", "代理行-使用数")
eq(len(P.filter_proxies([pv], q="香港")), 1, "代理搜索中文")
eq(len(P.filter_proxies([pv], kind="ssh")), 1, "代理类型筛选")

chk("成环" in " ".join(P.validate({"name": "A", "kind": "ssh", "host": "h",
                                  "port": "22", "id": "p0",
                                  "parent_id": "p2"}, others=cyc)),
    "校验拦住环")
eq(P.validate({"name": "A", "kind": "ssh", "host": "h", "port": "22"}),
   [], "代理校验通过")
eq(P.validate({"name": "A", "kind": "xx", "host": "h", "port": "22"}),
   ["类型不对（只能是 SSH 跳板 / SOCKS5 / HTTP 代理）"], "代理类型校验")
chk("上一跳不能是它自己" in " ".join(
    P.validate({"name": "A", "kind": "ssh", "host": "h", "port": "22",
                "id": "p9", "parent_id": "p9"})), "自指拦截")
chk("2 台主机在用" in P.delete_block_reason(["a", "b"], []), "代理删除拦截-主机")
chk("当上一跳" in P.delete_block_reason([], ["x"]), "代理删除拦截-链")

f = P.proxy_from_form({"name": "n", "kind": "socks5", "host": "h",
                       "port": "22"}, base={"kind": "ssh", "port": "22",
                                            "name": "o"})
eq(f["port"], "1080", "换类型默认端口跟随")

with tempfile.TemporaryDirectory() as td:
    pst = P.ProxyStore(Path(td) / "proxies.dat")
    a = pst.upsert({"name": "跳板A", "kind": "ssh", "host": "1.1.1.1"})
    b = pst.upsert({"name": "跳板B", "kind": "ssh", "host": "2.2.2.2",
                    "parent_id": a["id"]})
    eq(len(pst.proxies), 2, "代理落盘两条")
    eq(pst.chain_text_of(b["id"]), "跳板A → 跳板B → 目标", "落盘后链路正确")
    pst2 = P.ProxyStore(Path(td) / "proxies.dat")
    eq(pst2.get(b["id"])["parent_id"], a["id"], "重开保留上一跳")
    chk(pst2.remove(a["id"]), "代理删除")

# ================================================================== scripts
sec("4. 脚本库")

ids = [b["id"] for b in S.BUILTIN]
eq(len(ids), len(set(ids)), "内置脚本 id 唯一")
chk(all(b.get("body", "").strip() for b in S.BUILTIN), "内置脚本都有内容")
chk(all(b.get("kind") in S.KINDS for b in S.BUILTIN), "内置脚本类型合法")
chk(all(b.get("name") for b in S.BUILTIN), "内置脚本都有名字")
chk(len(S.BUILTIN) >= 20, "内置脚本数量够用", str(len(S.BUILTIN)))
chk(any(b["kind"] == "powershell" for b in S.BUILTIN), "有 Windows 脚本")
chk(any(b["kind"] == "shell" for b in S.BUILTIN), "有 Linux 脚本")

eq(S.preview("# 注释\n\n  df -h  "), "df -h", "预览跳过注释空行")
eq(S.preview(""), "(空脚本)", "空脚本预览")
chk(S.preview("x" * 100).endswith("…"), "预览截断加省略号")
eq(S.line_count("a\n\n b \n"), 2, "有效行数")

sv = S.script_view(S.BUILTIN[0])
eq(sv["source"], "内置", "内置来源标记")
eq(S.script_view({"name": "x", "body": "y"})["source"], "自定义", "自定义来源")

sl = [S.script_view(b) for b in S.BUILTIN]
eq(len(S.filter_scripts(sl, kind="powershell")),
   len([b for b in S.BUILTIN if b["kind"] == "powershell"]), "脚本类型筛选")
eq(len(S.filter_scripts(sl, source="内置")), len(S.BUILTIN), "来源筛选-内置")
chk(len(S.filter_scripts(sl, q="磁盘")) >= 2, "脚本关键词搜索")
chk("内置" in S.summary(sl), "脚本摘要含内置数")

eq(S.validate({"name": "", "kind": "shell", "body": "x"}), ["名称不能为空"],
   "脚本-空名")
eq(S.validate({"name": "a", "kind": "shell", "body": ""}), ["脚本内容不能为空"],
   "脚本-空内容")
eq(S.validate({"name": "a", "kind": "shell", "body": "x" * 20001})[0][:4], "脚本太长",
   "脚本-超长")
eq(S.validate({"name": "a", "kind": "shell", "body": "x"}), [], "脚本-通过")
chk("同名" in S.validate({"name": "a", "kind": "shell", "body": "x"},
                        others=[{"name": "a", "body": "y"}])[0], "脚本-重名")
eq(S.validate({"name": "a", "kind": "shell", "body": "x"},
              others=[{"name": "a", "body": "y", "builtin": True}]), [],
   "脚本-可与内置重名")

cmd = S.shell_cmd({"kind": "shell", "body": "df -h"})
chk("bash -s <<'__TB_SH__'" in cmd, "shell 命令包装")
chk("df -h" in cmd, "shell 正文进入命令")
eq(S.shell_cmd({"kind": "powershell", "body": "Get-Process"})[:9], "powershell"[:9],
   "powershell 命令")
chk("-NoProfile" in S.shell_cmd({"kind": "powershell", "body": "x"}), "PS 不带 profile")
py = S.shell_cmd({"kind": "python", "body": "print(1)"})
chk("mktemp" in py and "rm -f" in py, "python 用临时文件并在结束时删掉")
eq(S.kind_for_os("windows"), "powershell", "系统对应脚本类型")
eq(S.kind_for_os("linux"), "shell", "Linux 脚本类型")

cl = S.clone_as_custom(S.BUILTIN[0], ["u111"])
chk(cl["name"].endswith("（副本）"), "副本改名")
eq(cl["builtin"], False, "副本不是内置")
chk(cl["id"] != S.BUILTIN[0]["id"], "副本换 id")

with tempfile.TemporaryDirectory() as td:
    sst = S.ScriptStore(Path(td) / "scripts.json")
    eq(len(sst.scripts), 0, "初始没有自定义脚本")
    eq(len(sst.all()), len(S.BUILTIN), "全部脚本=内置")
    u = sst.upsert({"name": "我的脚本", "kind": "shell", "body": "echo hi"})
    eq(len(sst.scripts), 1, "自定义脚本落盘")
    eq(len(sst.all()), len(S.BUILTIN) + 1, "全部脚本含自定义")
    chk(sst.is_builtin(S.BUILTIN[0]["id"]), "识别内置 id")
    chk(not sst.remove(S.BUILTIN[0]["id"]), "内置脚本删不掉")
    chk(sst.remove(u["id"]), "自定义脚本可删")
    sst.upsert({"name": "b", "kind": "shell", "body": "b"})
    sst.upsert({"name": "a", "kind": "shell", "body": "a"})
    sst2 = S.ScriptStore(Path(td) / "scripts.json")
    eq([s["name"] for s in sst2.scripts], ["a", "b"], "重开读回并按名排序")
    eq(sst2.find_any(S.BUILTIN[0]["id"])["name"], S.BUILTIN[0]["name"],
       "find_any 找得到内置")
    eq(sst2.find_any(sst2.scripts[0]["id"])["name"], "a", "find_any 找得到自定义")

# ================================================================== audit
sec("5. 操作日志")

eq(A.title_of("capability.terminal.exec"), "AI 在终端执行命令", "事件标题")
eq(A.title_of("approval.approved"), "批准审批", "审批标题")
eq(A.title_of("fs.write"), "写入本地文件", "文件标题")
eq(A.title_of("unknown.event.x"), "unknown.event.x", "未知事件原样显示")
eq(A.title_of(""), "(未命名事件)", "空事件名")
eq(A.group_of("fs.write"), "文件", "事件分类")
eq(A.group_of("fs.upload"), "文件", "前缀兜底分类")
eq(A.group_of("weird"), "其他", "未知分类")
eq(A.status_label("ok"), ("成功", "ok"), "状态-成功")
eq(A.status_label("fail"), ("失败", "err"), "状态-失败")
eq(A.status_label("zzz"), ("信息", "dim"), "未知状态")

e1 = A.make_event("fs.write", "ok", "写 /tmp/a", device="web-1")
chk(len(e1["id"]) == 18, "事件 id 长度", e1["id"])
e2 = A.make_event("fs.write", "ok")
chk(e1["id"] != e2["id"], "同秒事件 id 不撞")
eq(e1["status"], "ok", "事件状态")
eq(A.make_event("x", "bad")["status"], "info", "非法状态归 info")

ev = A.event_view(e1)
eq(ev["title"], "写入本地文件", "事件行-标题")
eq(ev["status"], "成功", "事件行-状态")
eq(ev["tone"], "ok", "事件行-色调")
eq(ev["device"], "web-1", "事件行-设备")
chk(len(ev["time"]) == 19, "事件行-时间到秒", ev["time"])

evs = [A.make_event("terminal.session.create", "ok", ts=1700000000),
       A.make_event("fs.write", "fail", "权限不足", ts=1700000100),
       A.make_event("approval.approved", "ok", ts=1700000200)]
eq(len(A.filter_events(evs, status="fail")), 1, "按状态筛选")
eq(len(A.filter_events(evs, group="文件")), 1, "按分类筛选")
eq(len(A.filter_events(evs, q="权限")), 1, "按详情搜索")
eq(len(A.filter_events(evs, q="写入 权限")), 1, "多词 AND")
eq(len(A.filter_events(evs, since=1700000150)), 1, "按时间起点筛选")
eq([A.event_view(e)["title"] for e in A.sort_events(evs)][0], "批准审批",
   "时间倒序首条")

st = A.stats(evs)
eq(st["total"], 3, "统计总数")
eq(st["fail"], 1, "统计失败数")

eq(A.summary([]), "还没有日志", "日志空文案")
eq(A.summary(evs), "共 3 条日志", "日志摘要")

page, pages, p = A.paginate(list(range(235)), 1, 10)
eq((len(page), pages, p), (10, 24, 1), "分页-首页")
page, pages, p = A.paginate(list(range(235)), 99, 10)
eq(p, 24, "分页-越界夹回末页")
eq(len(page), 5, "末页只剩 5 条")
page, pages, p = A.paginate([], 3, 10)
eq((pages, p, len(page)), (1, 1, 0), "空数据也有 1 页")
eq(A.paginate(list(range(20)), 1, 7)[1], 2, "非法页长回落 10")

eq(A.page_window(1, 24), [1, 2, 3, None, 24], "页码条-首屏")
eq(A.page_window(24, 24), [1, None, 22, 23, 24], "页码条-末屏")
eq(A.page_window(5, 5), [1, 2, 3, 4, 5], "页码条-页数少全列")
chk(None in A.page_window(12, 24), "页码条-中间有省略")
eq(A.range_text(1, 10, 235), "第 1-10 条，共 235 条", "范围文案")
eq(A.range_text(24, 10, 235), "第 231-235 条，共 235 条", "末页范围文案")
eq(A.range_text(1, 10, 0), "共 0 条", "空范围文案")

with tempfile.TemporaryDirectory() as td:
    ast = A.AuditStore(Path(td))
    ast.record("host.connect", "ok", "连上 web-1", "web-1")
    ast.record("fs.write", "fail", "权限不足", "db-1")
    got = ast.load()
    eq(len(got), 2, "日志落盘并读回")
    eq(A.event_view(got[0])["title"], "写入本地文件", "读回仍是倒序")
    got2 = A.AuditStore(Path(td)).load()
    eq(len(got2), 2, "重开实例读到同一份")
    csvp = Path(td) / "out.csv"
    eq(ast.export(csvp, got), 2, "导出 csv 条数")
    txt = csvp.read_text(encoding="utf-8-sig")
    chk("时间,事件,标题" in txt.splitlines()[0], "csv 表头")
    chk("写入本地文件" in txt, "csv 含中文标题")
    jp = Path(td) / "out.jsonl"
    eq(ast.export(jp, got, "jsonl"), 2, "导出 jsonl 条数")
    eq(len(jp.read_text(encoding="utf-8").strip().splitlines()), 2, "jsonl 行数")
    # 跨天
    old = A.make_event("host.connect", "ok", ts=time.time() - 86400 * 3)
    ast.log(old)
    eq(len(ast.files()), 2, "跨天写出两个文件")
    eq(len(ast.load()), 3, "跨天能一起读回")
    eq(len(ast.load(days=1)), 2, "days 限制回看天数")

print(f"\n=== {PASS} 通过 / {FAIL} 失败")
for f in FAILS:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
