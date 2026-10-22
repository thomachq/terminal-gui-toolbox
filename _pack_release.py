# -*- coding: utf-8 -*-
"""打 v1.0.4 的四个交付包。

为什么用脚本而不是右键压缩：
  · 排除项（data / .git / build / __pycache__ / *.bak）漏一个就会把 CA 私钥
    或几百兆缓存塞进包里；
  · 四个包的取舍不一样（知识库只放文档、工具包放源码、portable 只放能跑的），
    写死在一处，下次发版改版本号就能复用。

用法：  python _pack_release.py
产物：  _release/<版本>/ 下的四个 zip
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

VER = "1.0.4"
HERE = Path(__file__).resolve().parent
OUT = HERE / "_release" / f"v{VER}"
STAMP = (2026, 10, 23, 1, 0, 0)

# 绝不能进包的：机密档案、构建缓存、编辑器/系统垃圾
JUNK_DIRS = {"data", "build", "__pycache__", ".git", ".venv", "node_modules",
             "_release", "_test_data", "screenshots_tmp"}
JUNK_EXT = {".pyc", ".pyo", ".bak", ".log", ".spec.bak", ".tmp", ".dbg"}
JUNK_PREFIX = ("dbg", "err.txt", "out.txt", "build_log")


def skip(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & JUNK_DIRS:
        return True
    if rel.suffix.lower() in JUNK_EXT:
        return True
    if rel.name.startswith(JUNK_PREFIX):
        return True
    return False


def add_tree(zf: zipfile.ZipFile, root: Path, arc_prefix: str = "") -> int:
    n = 0
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(root)
        if skip(rel):
            continue
        arc = (Path(arc_prefix) / rel) if arc_prefix else rel
        info = zipfile.ZipInfo(str(arc).replace("\\", "/"), STAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o644 & 0xFFFF) << 16
        with p.open("rb") as fh:
            zf.writestr(info, fh.read())
        n += 1
    return n


def add_file(zf: zipfile.ZipFile, src: Path, arc: str) -> bool:
    if not src.is_file():
        return False
    info = zipfile.ZipInfo(arc.replace("\\", "/"), STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    zf.writestr(info, src.read_bytes())
    return True


# ---------------------------------------------------------------- 1. 知识库
def make_kb() -> Path:
    """文档集合：给接手的人看的，不含任何可执行文件。"""
    out = OUT / "terminal-gui-toolbox项目知识库.zip"
    docs = [
        (HERE / "README.md", "README.md"),
        (HERE / "使用说明.txt", "使用说明.txt"),
        (HERE / "LICENSE", "LICENSE"),
        (HERE / "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md"),
        (HERE / "docs" / "改造说明-AI运维与宝塔对齐.md", "docs/改造说明-AI运维与宝塔对齐.md"),
        (OUT / "terminal-gui-toolbox项目交接说明.md", "terminal-gui-toolbox项目交接说明.md"),
        (OUT / f"terminal-gui-toolbox-deb构建说明-{VER}.md",
         f"terminal-gui-toolbox-deb构建说明-{VER}.md"),
    ]
    extra_docs = [p for p in (HERE / "docs").glob("*.md")]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in docs:
            add_file(zf, src, arc)
        for p in extra_docs:
            add_file(zf, p, f"docs/{p.name}")
        # 几张关键界面图，纯文档用
        for shot in ("ai_stream.png", "ai_md.png", "model_dialog.png",
                     "model_menu.png", "ai_dock_welcome.png"):
            add_file(zf, HERE / "screenshots" / shot, f"screenshots/{shot}")
    return out


# ---------------------------------------------------------------- 2. 工具包
def make_kit() -> Path:
    """源码 + exe + 构建脚本 + 自测：接手就能改、能跑。"""
    out = OUT / "terminal-gui-toolbox工具包.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        add_tree(zf, HERE, "terminal-gui-toolbox")
        add_file(zf, OUT / "terminal-gui-toolbox项目交接说明.md",
                 "terminal-gui-toolbox/项目交接说明.md")
        add_file(zf, OUT / f"terminal-gui-toolbox-deb构建说明-{VER}.md",
                 f"terminal-gui-toolbox/deb构建说明-{VER}.md")
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
def make_github_clone() -> Path | None:
    """GitHub 仓库的 git clone 快照（含 .git，可继续提交）。"""
    out = OUT / "terminal-gui-toolbox(github).zip"
    tmp = HERE / "_release" / "_clone"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        subprocess.run(["git", "clone", "--quiet",
                        "git@github.com:thomachq/terminal-gui-toolbox.git",
                        str(tmp)], check=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] clone 失败：{e}（网络或密钥问题），跳过这个包")
        return None
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        add_tree(zf, tmp, "terminal-gui-toolbox")
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    made.append(make_kb())
    made.append(make_kit())
    made.append(make_portable())
    gh = make_github_clone()
    if gh:
        made.append(gh)
    print("\n产物：")
    for p in made:
        print(f"  {p.name}  {p.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
