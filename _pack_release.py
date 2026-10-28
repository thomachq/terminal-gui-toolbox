# -*- coding: utf-8 -*-
"""打四个交付包（知识库 / 工具包 / 便携包 / github 快照）。

为什么用脚本而不是右键压缩：
  · 排除项（data / .git / build / __pycache__ / *.bak）漏一个就会把 CA 私钥
    或几百兆缓存塞进包里；
  · 四个包的取舍不一样（知识库只放文档、工具包放源码、portable 只放能跑的），
    写死在一处，下次发版改版本号就能复用；
  · 交付文档（交接说明 / deb 构建说明）从这里由 `render_docs()` 代入版本号生成，
    不再依赖有人记得手工往 `_release/<版本>/` 里放。

用法：  python _pack_release.py [--only kb,kit,portable,github]
产物：  _release/<版本>/ 下的四个 zip + 两份 Markdown
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from email.utils import formatdate
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 版本号只认 app.py 的 APP_VER（import 出来），不再手抄 —— v1.0.30 那次
# 忘了改这里，四个包里有三个是 1.0.30、portable 还叫 1.0.29（静默错版）。
import re as _re

_m = _re.search(r'APP_VER\s*=\s*"([\d.]+)"',
                (HERE / "app.py").read_text(encoding="utf-8"))
if not _m:
    raise SystemExit("[error] 从 app.py 读不到 APP_VER")
VER = _m.group(1)
OUT = HERE / "_release" / f"v{VER}"
STAMP = (2026, 10, 24, 17, 0, 0)

# 绝不能进包的：机密档案、构建缓存、编辑器/系统垃圾
JUNK_DIRS = {"data", "build", "__pycache__", ".git", ".venv", "node_modules",
             "_release", "_test_data", "screenshots_tmp"}
JUNK_EXT = {".pyc", ".pyo", ".bak", ".log", ".spec.bak", ".tmp", ".dbg"}
JUNK_PREFIX = ("dbg", "err.txt", "out.txt", "build_log", "_TODO")
# 系统查询命令被误重定向到项目根目录时留下的空壳文件。`sc query ... > query`
# 就这么在源码树里留了个 13 字节的 `query`，而且从 v1.0.4 一路带进每个发布包。
JUNK_NAMES = {"query", "out.txt", "err.txt", "nul", "con",
              "desktop.ini", "thumbs.db"}

# 被 .gitignore 挡住、但工具包确实要带的东西。只有 exe：它是零安装交付件，
# 而仓库不允许跟踪二进制。其余一律以 git 清单为准。
KEEP_IGNORED = ("*.exe",)


def skip(rel: Path, keep_git: bool = False) -> bool:
    parts = set(rel.parts)
    junk = JUNK_DIRS - ({".git"} if keep_git else set())
    if parts & junk:
        return True
    if rel.suffix.lower() in JUNK_EXT:
        return True
    if ".bak" in rel.name.lower():
        return True
    if rel.name.startswith(JUNK_PREFIX):
        return True
    if rel.name.lower() in JUNK_NAMES:
        return True
    return False


def git_listing(root: Path) -> set[str] | None:
    """git 眼里的文件清单（已跟踪 + 未跟踪但没被忽略）。

    拿它当白名单的理由：`data/`、`/query`、`*.local.cmd` 这些排除规则在
    `.gitignore` 里已经维护过一遍，打包脚本再抄一份必然漏 —— `/query` 就是这么
    从 v1.0.4 一路漏到 v1.0.12 的。直接问 git，谁也漏不掉。

    拿不到（不是仓库 / 没装 git）返回 None，调用方退回目录遍历 + JUNK_NAMES。
    """
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z",
                            "-co", "--exclude-standard"],
                           capture_output=True, timeout=180)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    names = {s.replace("\\", "/") for s in
             r.stdout.decode("utf-8", "replace").split("\0") if s}
    return names or None


def add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str = "",
             keep_git: bool = False, honor_git: bool = True) -> int:
    """把一个目录塞进包。honor_git=True 时以 git 清单为准（见 git_listing）。

    keep_git=True（打 `git clone` 快照）时不能套白名单 —— 清单里没有 .git
    内部文件，套上会把整个 .git 目录丢掉。
    """
    allow = git_listing(root) if (honor_git and not keep_git) else None
    n = 0
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        key = rel.as_posix()
        if allow is not None and key not in allow \
                and not any(fnmatch.fnmatch(key, pat) for pat in KEEP_IGNORED):
            continue
        if skip(rel, keep_git=keep_git):
            continue
        arc = (Path(arc_prefix) / rel) if arc_prefix else rel
        info = zipfile.ZipInfo(str(arc).replace("\\", "/"), STAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o644 & 0xFFFF) << 16
        with p.open("rb") as fh:
            zf.writestr(info, fh.read())
        n += 1
    return n


def add_file(zf: zipfile.ZipFile, src: Path, arc: str,
             required: bool = False) -> bool:
    """把一个文件写进包。`required=True` 且文件不存在时**抛错**。

    默认静默返回 False 是有意的：可选文档（截图、第三方声明）缺了不该阻断发版。
    但**交付清单上的东西必须是 required** —— 那两份说明文档当年就栽在这里：
    `add_file` 悄悄返回 False，`_deploy_share.py` 又悄悄「跳过（本地没有）」，
    共享盘上的交接说明从 v1.0.6 一直停到 v1.0.12，七个版本没人发现。
    """
    if not src.is_file():
        if required:
            raise FileNotFoundError(f"必需文件缺失：{src}")
        return False
    info = zipfile.ZipInfo(arc.replace("\\", "/"), STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    zf.writestr(info, src.read_bytes())
    return True


# ---------------------------------------------------------------- 0. 文档渲染
DOC_TPL = HERE / "docs"


def _fill(text: str, ctx: dict, name: str = "模板") -> str:
    """替换 `{{KEY}}` 占位符；替换完还有残留就抛错。

    单独抽出来是为了能脱离文件系统测：`render_docs()` 里那层 try/except
    只负责把错误转成退出码，判断「有没有漏替换」的规则在这里。
    """
    for k, v in ctx.items():
        text = text.replace("{{" + k + "}}", v)
    left = sorted(set(re.findall(r"\{\{([A-Za-z_]+)\}\}", text)))
    if left:
        raise ValueError(f"{name} 里有未替换的占位符：{', '.join(left)}")
    return text


def render_docs() -> list[Path]:
    """把 `docs/模板-*.md` 渲染成当前版本的交付文档，写进 `_release/v<VER>/`。

    以前这两份是手工放进 `_release/<版本>/` 的：忘了放就等于没交付，而且
    `add_file()` 静默 False + 部署脚本静默跳过，整整七个版本没人发现。
    现在模板进仓库（能 diff、能 review），打包时自动代入版本号，渲染完还会
    扫一遍有没有漏替换的 `{{...}}`——有就直接抛错，绝不让半成品流出去。
    """
    ctx = {
        "VER": VER,
        "VER_V": "v" + VER,
        "DATE": date.today().isoformat(),
        # debian/changelog 要的格式：Sat, 24 Oct 2026 23:10:00 +0800
        "DATE_RFC2822": formatdate(localtime=True),
    }
    pairs = (
        ("模板-项目交接说明.md", "terminal-gui-toolbox项目交接说明.md"),
        ("模板-deb构建说明.md", f"terminal-gui-toolbox-deb构建说明-{VER}.md"),
    )
    made: list[Path] = []
    for tpl_name, out_name in pairs:
        tpl = DOC_TPL / tpl_name
        if not tpl.is_file():
            raise FileNotFoundError(f"文档模板不存在：{tpl}")
        text = _fill(tpl.read_text(encoding="utf-8"), ctx, tpl_name)
        dst = OUT / out_name
        dst.write_text(text, encoding="utf-8", newline="\n")
        made.append(dst)
    return made


# ---------------------------------------------------------------- 1. 知识库
def make_kb() -> Path:
    """文档集合：给接手的人看的，不含任何可执行文件。"""
    out = OUT / "terminal-gui-toolbox项目知识库.zip"
    docs = [
        (HERE / "README.md", "README.md", True),
        (HERE / "使用说明.txt", "使用说明.txt", True),
        (HERE / "LICENSE", "LICENSE", True),
        (HERE / "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md", True),
        (HERE / "docs" / "改造说明-AI运维与宝塔对齐.md",
         "docs/改造说明-AI运维与宝塔对齐.md", True),
        (OUT / "terminal-gui-toolbox项目交接说明.md",
         "terminal-gui-toolbox项目交接说明.md", True),
        (OUT / f"terminal-gui-toolbox-deb构建说明-{VER}.md",
         f"terminal-gui-toolbox-deb构建说明-{VER}.md", True),
    ]
    # docs/ 下的模板不进知识库：知识库要的是渲染好的成品，模板留给工具包
    # （接手的人改的是模板）。否则包里两份「交接说明」并排，看的人会懵。
    extra_docs = [p for p in (HERE / "docs").glob("*.md")
                  if not p.name.startswith("模板-")]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # 显式清单和 docs/*.md 通配会撞名（改造说明那篇两边都有）。
        # 记一下已经写进去的路径，重复的跳过 —— 否则 zipfile 会警告，
        # 而且同一份文档在包里出现两次，用户解开看着也奇怪。
        done: set[str] = set()

        def once(src: Path, arc: str, required: bool = False) -> None:
            key = arc.replace("\\", "/")
            if key in done:
                return
            if not add_file(zf, src, key, required=required):
                return
            done.add(key)

        for src, arc, required in docs:
            once(src, arc, required)
        for p in extra_docs:
            once(p, f"docs/{p.name}")
        # 几张关键界面图，纯文档用
        for shot in ("ai_stream.png", "ai_md.png", "model_dialog.png",
                     "model_menu.png", "ai_dock_welcome.png"):
            once(HERE / "screenshots" / shot, f"screenshots/{shot}")
    return out


# ---------------------------------------------------------------- 2. 工具包
def make_kit() -> Path:
    """源码 + exe + 构建脚本 + 自测：接手就能改、能跑。"""
    out = OUT / "terminal-gui-toolbox工具包.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        add_tree(zf, HERE, "terminal-gui-toolbox")
        add_file(zf, OUT / "terminal-gui-toolbox项目交接说明.md",
                 "terminal-gui-toolbox/项目交接说明.md", required=True)
        add_file(zf, OUT / f"terminal-gui-toolbox-deb构建说明-{VER}.md",
                 f"terminal-gui-toolbox/deb构建说明-{VER}.md", required=True)
    return out


# ---------------------------------------------------------------- 3. portable
PORTABLE_README = """Terminal GUI Toolbox v{ver} · Windows x64 便携版
=================================================

用法
----
1. 解压到任意目录（路径别用中文？可以，但别放在需要管理员权限的位置）。
2. 双击 TerminalToolbox.exe。
3. 首次运行会在同一目录下生成 data\\ ，里面是加密后的主机档案与日志。
   删掉 data\\ 就等于恢复出厂（会丢已保存的主机与凭证）。

说明
----
· 免安装：不写注册表、不装服务，拷到 U 盘也能跑。
· 数据都在 data\\ 下，备份/迁移直接拷整个目录。
· 需要 Windows 10/11 x64；自带 Python 运行时，不用另装。
· 口令、私钥、API Key 全部用 AES-GCM 加密后落盘，界面只显示「已设置」。
· 断网也能用：所有功能只依赖目标主机已有的 SSH/Telnet 与系统命令；
  只有「智能运维助手」需要联网调模型 API（也可指向本机 Ollama / Harness）。

版本：v{ver}    (2026-10-23)
仓库：https://github.com/thomachq/terminal-gui-toolbox
"""


def make_portable() -> Path:
    out = OUT / f"Terminal-GUI-Toolbox-{VER}-windows-x64-portable.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        add_file(zf, HERE / "TerminalToolbox.exe",
                 f"Terminal-GUI-Toolbox-{VER}-windows-x64/TerminalToolbox.exe")
        add_file(zf, HERE / "LICENSE",
                 f"Terminal-GUI-Toolbox-{VER}-windows-x64/LICENSE")
        # 使用说明单独写一份给最终用户看的（不含构建内容）
        tmp = OUT / f"_portable_README_{VER}.txt"
        tmp.write_text(PORTABLE_README.format(ver=VER), encoding="utf-8")
        add_file(zf, tmp, f"Terminal-GUI-Toolbox-{VER}-windows-x64/使用说明.txt")
        tmp.unlink(missing_ok=True)
        # 空的 data 目录占位，用户一眼知道数据在哪儿
        info = zipfile.ZipInfo(
            f"Terminal-GUI-Toolbox-{VER}-windows-x64/data/", STAMP)
        info.external_attr = (0o40755 & 0xFFFF) << 16 | 0x10
        zf.writestr(info, b"")
    return out


# ---------------------------------------------------------------- 4. github clone
GH_URL = "git@github.com:thomachq/terminal-gui-toolbox.git"


def make_github_clone() -> Path | None:
    """GitHub 仓库的 `git clone` 快照。

    两个容易踩的坑，都在这函数里处理掉了：

    1. **必须带 `.git`**。用户要的是「github 仓库的 git clone 版本」——没有 .git
       就只是一份源码拷贝，解压后 `git log` / `git push` 全用不了。所以这里给
       `add_tree` 传 keep_git=True，把 `.git` 目录一起打进去。
    2. **别删整个克隆目录**。几百个文件的一次性删除会被安全闸门拦
       （SAFE_DELETE_BULK_CONFIRM_REQUIRED），脚本会半路失败。所以改成复用：
       目录已在就 `fetch` + 硬切到 `origin/main`，不在才 clone。留着下次发版接着用。
    """
    out = OUT / "terminal-gui-toolbox(github).zip"
    tmp = HERE / "_release" / "_clone"
    if (tmp / ".git").is_dir():
        try:
            for cmd in (["fetch", "--prune", "--tags", "origin"],
                        ["checkout", "-B", "main", "origin/main"],
                        ["reset", "--hard", "origin/main"]):
                subprocess.run(["git", "-C", str(tmp)] + cmd, check=True,
                               timeout=300, capture_output=True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 复用已有克隆失败：{e}")
            return None
        head = subprocess.run(["git", "-C", str(tmp), "log", "--oneline", "-1"],
                              capture_output=True, text=True, timeout=60).stdout.strip()
        print(f"[info] 复用已有克隆，已更新到 {head}")
    else:
        try:
            subprocess.run(["git", "clone", "--quiet", GH_URL, str(tmp)],
                           check=True, timeout=300)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] clone 失败：{e}（网络或密钥问题），跳过这个包")
            return None
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        n = add_tree(zf, tmp, "terminal-gui-toolbox", keep_git=True)
    print(f"[info] 快照 {n} 个文件（含 .git），克隆目录留在 {tmp} 供下次复用")
    return out


def audit(paths: list[Path]) -> int:
    """打好之后逐个包复核：不许有垃圾文件，不许有 data/ 机密目录。

    这一步是补出来的 —— `/query` 在四个发布包里躺了 8 个版本没人发现，
    因为以前打完就走，没有任何一环会回头看包里到底装了什么。
    空目录条目（portable 包里的 `data/` 占位）不算；只查带内容的路径。
    """
    bad = 0
    for p in paths:
        with zipfile.ZipFile(p) as zf:
            hits = [n for n in zf.namelist()
                    if not n.endswith("/")
                    and (Path(n).name.lower() in JUNK_NAMES
                         or set(Path(n).parts) & {"data", "_test_data", ".venv"})]
        if hits:
            bad += 1
            print(f"[warn] {p.name} 里混进不该带的东西：")
            for h in hits[:10]:
                print(f"        {h}")
        else:
            print(f"[ok] {p.name} 无垃圾 / 机密残留")
    return bad


def main() -> int:
    """默认四个包全打。带 `--only kb,kit,portable,github` 可以只打其中几个。

    为什么要这个开关：`(github).zip` 是 `git clone` 出来的快照，**必须在推完
    四端之后**再打，否则包里的 main 还是上一版。发版时先打前三个、提交推送、
    再用 `--only github` 补最后一个，才不会反复重打。
    """
    only = set()
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a.startswith("--only"):
            if "=" in a:
                val = a.split("=", 1)[1]
            else:
                # 兼容 `--only github` 这种空格写法。以前只认 `--only=github`，
                # 写成空格时 only 会变成 {"--only github"}，四个 in 全不命中，
                # 结果**一个包都不打还返回成功** —— 发版时最难查的那种静默失败。
                val = args[i + 1] if i + 1 < len(args) else ""
            only = {s.strip() for s in val.split(",") if s.strip()}
    known = {"kb", "kit", "portable", "github"}
    bad = only - known
    if bad:
        print("[error] --only 不认识这些名字：" + ", ".join(sorted(bad))
              + "；可选 " + ", ".join(sorted(known)))
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    # 文档先渲染再打包：kb / kit 都要从 _release/<VER>/ 取那两份成品
    try:
        docs = render_docs()
    except (OSError, ValueError) as e:
        print(f"[error] 文档渲染失败，一个包都不打：{e}")
        return 1
    for d in docs:
        print(f"[info] 渲染 {d.name}  {d.stat().st_size} bytes")
    made = []
    if not only or "kb" in only:
        made.append(make_kb())
    if not only or "kit" in only:
        made.append(make_kit())
    if not only or "portable" in only:
        made.append(make_portable())
    if not only or "github" in only:
        gh = make_github_clone()
        if gh:
            made.append(gh)
    print("\n产物：")
    for p in made:
        print(f"  {p.name}  {p.stat().st_size / 1024 / 1024:.1f} MB")
    print()
    bad = audit(made)
    if bad:
        print(f"\n[error] {bad} 个包未通过复核，别分发。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
