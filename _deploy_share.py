# -*- coding: utf-8 -*-
"""把当前版本的交付物推到医院共享目录。

用法：python _deploy_share.py [1.0.6]
不给版本号就用 _pack_release.py 里的 VER。

共享盘是 SMB，`os.replace`（MoveFileEx + REPLACE_EXISTING）在这台 NAS 上
会被拒（WinError 5：拒绝访问），但 `shutil.move` 走 copy+unlink 能成。
所以归档按三级降级：rename → move → copy 留档（原文件删不掉就留着）。

原则：**留档是 bonus，覆盖才是目的**。旧文件挪不动也必须把新文件写进去，
不能像 v1.0.6 那轮一样 `continue` 掉——那次工具包和知识库的旧文件没挪动，
结果两个包整轮都没更新（虽然内容是旧的同版本，但逻辑上就是漏了）。
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DST = Path(r"\\192.168.3.12\Public\软件\AI生成的应用\terminal-gui-toolbox")
OLD = DST / "old"


def ver() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].lstrip("v")
    m = re.search(r'^VER\s*=\s*"([^"]+)"', (ROOT / "_pack_release.py").read_text(
        encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def files(v: str) -> list[str]:
    return [
        f"Terminal-GUI-Toolbox-{v}-windows-x64-portable.zip",
        "terminal-gui-toolbox工具包.zip",
        "terminal-gui-toolbox项目知识库.zip",
        "terminal-gui-toolbox(github).zip",
        f"terminal-gui-toolbox-deb构建说明-{v}.md",
        "terminal-gui-toolbox项目交接说明.md",
    ]


def archive(p: Path, name: str) -> None:
    """把旧文件挪进 old/。挪不动不算错，最多少一份留档。"""
    if not p.is_file():
        return
    tgt = OLD / name
    if tgt.exists():
        tgt = OLD / (name + ".prev")
    try:
        p.replace(tgt)
        print("  归档 →", tgt.name)
        return
    except OSError:
        pass
    try:
        shutil.move(str(p), str(tgt))
        print("  归档 →", tgt.name, "(move)")
        return
    except OSError:
        pass
    try:
        shutil.copyfile(p, tgt)
        print("  留档 →", tgt.name, "(原文件删不掉，两边都在)")
    except OSError as e:
        print("  留档也失败（不影响交付）:", e)


def main() -> int:
    v = ver()
    if not v:
        print("拿不到版本号")
        return 2
    src_dir = ROOT / "_release" / f"v{v}"
    if not src_dir.is_dir():
        print("本地没有这个版本的产物目录：", src_dir)
        return 2
    if not DST.is_dir():
        print("共享目录不可达：", DST)
        return 2
    OLD.mkdir(exist_ok=True)

    print(f"→ {DST}   (v{v})")
    for name in files(v):
        src = src_dir / name
        if not src.is_file():
            print("跳过（本地没有）", name)
            continue
        dst = DST / name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            print("尺寸一致，跳过覆盖", name)
            continue
        if dst.is_file():
            archive(dst, name)
        shutil.copyfile(src, dst)
        print("  已写 %-52s %7.1f MB" % (name, dst.stat().st_size / 1048576))

    # 复核：逐个比大小，别把「复制了」当成「复制对了」
    bad = []
    for name in files(v):
        src, dst = src_dir / name, DST / name
        if src.is_file() and (not dst.is_file()
                              or dst.stat().st_size != src.stat().st_size):
            bad.append(name)
    if bad:
        print("!! 大小不符：", bad)
        return 1
    print("全部就位（大小逐个核对过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
