# -*- coding: utf-8 -*-
"""把当前版本的 exe 与使用说明覆盖到安装目录 D:\\Tools\\Terminal-GUI-Toolbox。

exe 常被用户自己开着的那个实例占用（WinError 32）——**只等，不杀**：
识别出来是谁占着，然后每 5 秒重试，最多 15 分钟。等不到就如实报出来，
让用户自己关掉再跑一次，绝不 taskkill 用户的进程。
"""
import hashlib
import shutil
import time
from pathlib import Path

# 源目录就用脚本自己所在的位置：项目从 N:\AI-workspace\WorkBuddy 搬到
# L:\AI-Workspace\terminal-gui-toolbox\gh 那次，写死的旧路径没跟着改，
# 脚本会去一个不存在的目录取 exe。改成 __file__ 之后，再搬家也不用动这里。
SRC = Path(__file__).resolve().parent
DST = Path(r"D:\Tools\Terminal-GUI-Toolbox")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


print("源 :", SRC)
print("目标:", DST)

# bin/：ADB / HDC 的内置组件（adb.exe + 三个 dll、hdc.exe + libusb_shared.dll）。
# portable 包里一直带着它，但本机安装这里以前只换 exe 与说明 —— 结果安装版里
# 压根没有 bin/，「内置 bin」这条路径等于不存在，只能吃环境变量里那个 adb。
# 口径与 _pack_release.py 的 KEEP_IGNORED 保持一致：只带程序、dll 与说明。
bin_src, bin_dst = SRC / "bin", DST / "bin"
if bin_src.is_dir():
    bin_dst.mkdir(exist_ok=True)
    for p in sorted(bin_src.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if not (low.endswith((".exe", ".dll")) or low.startswith("notice-")
                or low.endswith(".md")):
            continue
        d = bin_dst / p.name
        if d.is_file() and sha(d) == sha(p):
            print(f"[ok]   bin/{p.name} 已是最新")
            continue
        shutil.copy2(p, d)
        print(f"[ok]   bin/{p.name} 覆盖完成  {d.stat().st_size} 字节")

# 说明文件没人占用，直接覆盖
for name in ("使用说明.txt",):
    s, d = SRC / name, DST / name
    if not s.is_file():
        print(f"[skip] 源里没有 {name}")
        continue
    if d.is_file() and sha(d) == sha(s):
        print(f"[ok]   {name} 已是最新")
        continue
    shutil.copy2(s, d)
    print(f"[ok]   {name} 覆盖完成  {d.stat().st_size} 字节  "
          f"sha={sha(d)[:12]}")

# exe：可能正被用户那个实例占着
s = SRC / "TerminalToolbox.exe"
d = DST / "TerminalToolbox.exe"
want = sha(s)
if d.is_file() and sha(d) == want:
    print(f"[ok]   exe 已是最新  sha={want[:12]}")
else:
    end = time.time() + 15 * 60
    tries = 0
    while True:
        tries += 1
        try:
            shutil.copy2(s, d)
            got = sha(d)
            if got == want:
                print(f"[ok]   exe 覆盖完成  {d.stat().st_size} 字节  "
                      f"sha={got[:12]}  （第 {tries} 次尝试）")
                break
            print(f"[warn] 拷完了但哈希不对：{got[:12]} != {want[:12]}")
            break
        except OSError as e:
            if time.time() > end:
                print(f"[fail] 等了 15 分钟还是写不进去：{e}")
                print("       请关掉正在运行的「终端工具箱」再跑一次")
                break
            if tries == 1:
                print(f"[wait] exe 被占用（WinError {getattr(e, 'winerror', '?')}）"
                      f"，每 5 秒重试，最多 15 分钟…")
            time.sleep(5)

print("\n安装目录现状：")
for p in sorted(DST.iterdir()):
    if p.is_file():
        print(f"  {p.name:28s} {p.stat().st_size:>10d} 字节")
