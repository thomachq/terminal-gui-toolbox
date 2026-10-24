# -*- coding: utf-8 -*-
"""交付文档模板化：模板进仓库，打包时代入版本号生成。

为什么要有这个测试：`交接说明` 与 `deb构建说明` 当年是手工放进
`_release/<版本>/` 的，忘了放 -> `add_file()` 静默返回 False ->
`_deploy_share.py` 静默打印「跳过（本地没有）」。共享盘上那两份从
v1.0.6 一直卡到 v1.0.12，**七个版本**没人发现，因为每一环都在「正常返回」。

所以这里的断言重点不是「能不能生成」，而是**生成不出来的时候会不会叫**：
  · 占位符漏替换 -> 必须抛错（不能让 `{{VER}}` 印在交付文档上）
  · 交付清单缺文件 -> `required=True` 必须抛错、部署预检必须报全
  · 知识库里不许出现模板本体（否则两份「交接说明」并排，看的人会懵）

分段：
  1. 模板本体：存在、够长、占位符齐
  2. 渲染结果：文件名、无残留、版本号与日期正确
  3. _fill 规则：能替换、遇未知占位符抛错
  4. add_file 语义：required 缺失必须抛，可选缺失老实返回 False
  5. 部署清单：六项、版本号内联、空目录预检报全
  6. 知识库实测：真打一个包，验里面有两份成品、没有模板本体

不需要窗口，不需要联网。
"""
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []

import _deploy_share as DP  # noqa: E402
import _pack_release as PK  # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def sec(t):
    print(f"\n-- {t}")


TPL_HAND = PK.DOC_TPL / "模板-项目交接说明.md"
TPL_DEB = PK.DOC_TPL / "模板-deb构建说明.md"
VER = PK.VER

# 判「还有没有没替换的占位符」只能按占位符的形状判：文档正文里出现花括号
# 本身是合法的（说明语法时会提到），但那不是漏替换。
PH = re.compile(r"\{\{[A-Za-z_]+\}\}")


# ---------------------------------------------------------------- 1
sec("1. 模板本体")
for tpl in (TPL_HAND, TPL_DEB):
    chk(tpl.is_file() and tpl.stat().st_size > 3000,
        f"模板存在且够长：{tpl.name}",
        f"{tpl.stat().st_size if tpl.is_file() else '缺失'} bytes")

h = TPL_HAND.read_text(encoding="utf-8")
chk("{{VER_V}}" in h, "交接模板用了 {{VER_V}} 占位符")
chk("{{DATE}}" in h, "交接模板用了 {{DATE}} 占位符")
chk("{{VER}}" in h, "交接模板用了 {{VER}} 占位符（文件名/命令示例）")

d = TPL_DEB.read_text(encoding="utf-8")
chk("{{VER}}" in d, "deb 模板用了 {{VER}} 占位符")
chk("{{VER_V}}" in d, "deb 模板用了 {{VER_V}} 占位符（git checkout 用）")
chk("{{DATE_RFC2822}}" in d, "deb 模板用了 {{DATE_RFC2822}}（changelog 日期）")
chk("模板" in h.splitlines()[4] or "模板" in h[:600],
    "交接模板首段说明了「改模板别改产物」")
chk("模板" in d[:900], "deb 模板首段说明了「改模板别改产物」")


# ---------------------------------------------------------------- 2
sec("2. 渲染结果")
PK.OUT.mkdir(parents=True, exist_ok=True)
made = PK.render_docs()
chk(len(made) == 2, "render_docs 产出两份", str(len(made)))

names = [p.name for p in made]
chk("terminal-gui-toolbox项目交接说明.md" in names, "交接说明文件名固定")
chk(f"terminal-gui-toolbox-deb构建说明-{VER}.md" in names,
    "deb 说明文件名带版本号", str(names))

for p in made:
    txt = p.read_text(encoding="utf-8")
    chk(PH.search(txt) is None, f"无占位符残留：{p.name}",
        (PH.search(txt).group(0) if PH.search(txt) else ""))
    chk(VER in txt, f"含当前版本号：{p.name}")

hand_txt = (PK.OUT / "terminal-gui-toolbox项目交接说明.md").read_text(encoding="utf-8")
chk(f"版本：**v{VER}**" in hand_txt, "交接说明头部版本行渲染正确")
from datetime import date as _d  # noqa: E402
chk(_d.today().isoformat() in hand_txt, "交接说明含今天日期")

deb_txt = (PK.OUT / f"terminal-gui-toolbox-deb构建说明-{VER}.md").read_text(
    encoding="utf-8")
chk(f"terminal-gui-toolbox_{VER}_all.deb" in deb_txt, "deb 说明里产物名代入了版本")
chk(f"terminal-gui-toolbox ({VER}) unstable" in deb_txt, "changelog 版本行代入了版本")


# ---------------------------------------------------------------- 3
sec("3. _fill 规则")
ctx = {"VER": "9.9.9", "VER_V": "v9.9.9", "DATE": "2030-01-01",
       "DATE_RFC2822": "Tue, 01 Jan 2030 00:00:00 +0800"}
chk(PK._fill("{{VER}}|{{VER_V}}", ctx) == "9.9.9|v9.9.9",
    "_fill 区分 VER 与 VER_V（前缀不能互相吃掉）")
chk(PK._fill("版本 {{VER_V}} 于 {{DATE}}", ctx) == "版本 v9.9.9 于 2030-01-01",
    "_fill 正常替换多个占位符")
chk(PK._fill("无关内容", ctx) == "无关内容", "_fill 无占位符时原样返回")

try:
    PK._fill("漏了 {{BOGUS}} 这个", ctx, "测试.md")
except ValueError as e:
    chk("BOGUS" in str(e), "_fill 遇未知占位符抛 ValueError 并点名")
else:
    chk(False, "_fill 遇未知占位符抛 ValueError 并点名", "没抛")

try:
    PK._fill("{{VER}} 和 {{NOPE}}", ctx, "X.md")
except ValueError:
    chk(True, "_fill 部分替换后仍能查出剩余占位符")
else:
    chk(False, "_fill 部分替换后仍能查出剩余占位符", "没抛")


# ---------------------------------------------------------------- 4
sec("4. add_file 语义")
ghost = PK.OUT / "_不存在的文件_用于测试.txt"
ghost.unlink(missing_ok=True)

with zipfile.ZipFile(PK.OUT / "_addfile_测试.zip", "w") as zf:
    chk(PK.add_file(zf, ghost, "x.txt") is False,
        "可选文件缺失 -> 返回 False 不抛")
    try:
        PK.add_file(zf, ghost, "x.txt", required=True)
    except FileNotFoundError:
        chk(True, "required 文件缺失 -> 抛 FileNotFoundError")
    else:
        chk(False, "required 文件缺失 -> 抛 FileNotFoundError", "没抛")
    tpl_ok = PK.add_file(zf, TPL_HAND, "t.md", required=True)
    chk(tpl_ok is True and "t.md" in zf.namelist(),
        "required 文件存在 -> 写入成功")
(PK.OUT / "_addfile_测试.zip").unlink(missing_ok=True)


# ---------------------------------------------------------------- 5
sec("5. 部署清单与预检")
lst = DP.files("1.0.99")
chk(len(lst) == 6, "交付清单六项", str(lst))
chk(any("1.0.99" in n for n in lst), "deb 说明项内联了版本号")
chk(any(".zip" in n for n in lst) and sum(".zip" in n for n in lst) == 4,
    "四个 zip 包都在清单里")

empty = PK.OUT / "_空目录_预检测试"
empty.mkdir(exist_ok=True)
miss = DP.missing("1.0.99", empty)
chk(len(miss) == 6, "空目录预检报全六项", str(len(miss)))
miss2 = DP.missing("1.0.99", PK.HERE / "docs")   # 有别的文件但没这些包
chk(len(miss2) == 6, "错目录预检同样报全（不会因为有文件就放行）")
empty.rmdir()


# ---------------------------------------------------------------- 6
sec("6. 知识库实测")
kb = PK.make_kb()
with zipfile.ZipFile(kb) as zf:
    nl = zf.namelist()
    base = [n.split("/")[-1] for n in nl]
    chk("terminal-gui-toolbox项目交接说明.md" in nl, "知识库含交接说明成品")
    chk(f"terminal-gui-toolbox-deb构建说明-{VER}.md" in nl, "知识库含 deb 说明成品")
    chk(not [n for n in base if n.startswith("模板-")],
        "知识库不含模板本体", str([n for n in base if n.startswith("模板-")][:3]))
    chk(not [n for n in nl if n.endswith("/query") or n == "query"],
        "知识库不含 query 垃圾文件")
    body = zf.read("terminal-gui-toolbox项目交接说明.md").decode("utf-8")
    chk(f"v{VER}" in body.splitlines()[2], "包内交接说明头部版本正确",
        body.splitlines()[2])
    chk(PH.search(body) is None, "包内交接说明无占位符残留")


print(f"\n{'=' * 52}")
print(f"通过 {PASS}，失败 {FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAIL else 0)
