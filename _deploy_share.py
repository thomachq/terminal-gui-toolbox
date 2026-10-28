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
    src = (ROOT / "_pack_release.py").read_text(encoding="utf-8")
    # _pack_release 的 VER 现在是从 app.py 的 APP_VER 算出来的（v1.0.30 起
    # 不再手抄），两处都试：先认字面量，认不到就从 app.py 直接拿。
    m = re.search(r'^VER\s*=\s*"([^"]+)"', src, re.M)
    if m:
        return m.group(1)
    m = re.search(r'APP_VER\s*=\s*"([\d.]+)"',
                  (ROOT / "app.py").read_text(encoding="utf-8"))
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


def missing(v: str, src_dir: Path) -> list[str]:
    """六个交付件里缺了哪些（抽成函数是为了能脱离网络单测）。

    这些是**交付清单**，不是「有就带上」的附件：少一个就说明这一版没打全，
    此时部署只会把共享盘刷成半新半旧的状态。
    """
    return [n for n in files(v) if not (src_dir / n).is_file()]


def archive(p: Path, name: str) -> None:
    """把旧文件挪进 old/。挪不动不算错，最多少一份留档。

    这个 SMB 共享**不允许删除或覆盖已存在的文件**（unlink / MoveFileEx
    REPLACE_EXISTING 都返回「拒绝访问」），只允许**新建**和**改名到不冲突的名字**。
    所以目标名绝不能撞车：`name` → `name.prev` → `name.r2` → `name.r3` …
    撞了顺延，而不是覆盖 —— v1.0.6 第二轮就是栽在这里（.prev 已存在 → 整个文件写不进去）。
    """
    if not p.is_file():
        return
    cands = [name, name + ".prev"] + [f"{name}.r{i}" for i in range(2, 100)]
    tgt = next((OLD / c for c in cands if not (OLD / c).exists()), None)
    if tgt is None:
        print("  old/ 里同名留档太多，跳过归档")
        return
    for how, fn in (("rename", lambda: p.replace(tgt)),
                    ("move", lambda: shutil.move(str(p), str(tgt)))):
        try:
            fn()
            print(f"  归档 → {tgt.name} ({how})")
            return
        except OSError:
            pass
    try:
        shutil.copyfile(p, tgt)
        print(f"  留档 → {tgt.name}（原文件删不掉，两边都在）")
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

    # 预检：六个交付件一个都不能少。以前是「缺了就跳过」，于是交接说明与 deb
    # 说明在共享盘上从 v1.0.6 卡到 v1.0.12 —— 那行「跳过（本地没有）」混在
    # 几十行输出里，谁也没看见。现在缺一个就整轮拒绝：**半套交付比不交付更糟**，
    # 用户拿到一份旧文档，会以为那就是当前版本的说明。
    miss = missing(v, src_dir)
    if miss:
        print(f"!! 交付不完整（v{v}），缺 {len(miss)} 个文件，拒绝部署：")
        for m in miss:
            print("   -", m)
        print("   先跑 python _pack_release.py 补齐，再来分发。")
        return 2

    OLD.mkdir(exist_ok=True)

    print(f"→ {DST}   (v{v})")
    for name in files(v):
        src = src_dir / name
        if not src.is_file():          # 预检已保证不会走到这
            print("!! 预检漏了，运行时才发现缺文件：", name)
            return 1
        dst = DST / name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            print("尺寸一致，跳过覆盖", name)
            continue
        if dst.is_file():
            archive(dst, name)
            if dst.is_file():
                # 归档没把它挪走 → 现在写进去一定被拒（共享盘不允许覆盖）
                print("  !! 旧文件挪不走，无法写入：", name)
                continue
        try:
            shutil.copyfile(src, dst)
        except PermissionError:
            print("  !! 写入被拒（共享盘不允许覆盖已存在文件）：", name)
            continue
        print("  已写 %-52s %7.1f MB" % (name, dst.stat().st_size / 1048576))

    # 复核：逐个比大小，别把「复制了」当成「复制对了」
    bad = [n for n in files(v)
           if not (DST / n).is_file()
           or (DST / n).stat().st_size != (src_dir / n).stat().st_size]
    if bad:
        print("!! 大小不符（这几个没真正落地）：", bad)
        return 1
    print("全部就位（大小逐个核对过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
