# -*- coding: utf-8 -*-
"""把本机 SDK 里的 adb / hdc 复制进应用目录的 bin/。

为什么需要它：仓库按「只放源码」的原则不收二进制，`bin/` 里的 exe/dll
是打包时才放进去的。换一台机器 clone 下来，跑一次这个脚本就能补齐。

用法：
    python _fetch_bins.py              # 自动找常见安装位置
    python _fetch_bins.py --adb <dir>  # 指定 platform-tools 目录
    python _fetch_bins.py --hdc <dir>  # 指定 OpenHarmony toolchains 目录
    python _fetch_bins.py --check      # 只报告现状，不复制
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin"
WIN = sys.platform == "win32"

ADB_FILES = ["adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll",
             "libwinpthread-1.dll", "NOTICE.txt"]
HDC_FILES = ["hdc.exe", "libusb_shared.dll"]


def _candidates_adb() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if env:
        out.append(Path(env) / "platform-tools")
    for base in (os.environ.get("LOCALAPPDATA"),
                 os.environ.get("USERPROFILE")):
        if base:
            out.append(Path(base) / "Android" / "Sdk" / "platform-tools")
    # 本机实测：SDK 装在了独立盘符上
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        out.append(Path(f"{drive}:/Android/Sdk/platform-tools"))
        out.append(Path(f"{drive}:/Android/SDK/platform-tools"))
    return out


def _candidates_hdc() -> list[Path]:
    out: list[Path] = []
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        out.append(Path(f"{drive}:/Huawei/OpenHarmony/sdk"))
    for base in (os.environ.get("LOCALAPPDATA"), os.environ.get("USERPROFILE")):
        if base:
            out.append(Path(base) / "Huawei" / "Sdk")
    # 目录结构是 …/sdk/<版本>/toolchains，所以再往下找一层
    found: list[Path] = []
    for p in out:
        if not p.is_dir():
            continue
        for sub in sorted(p.glob("*/toolchains")):
            found.append(sub)
        found.append(p)
    return found


def _find(files: list[str], dirs: list[Path]) -> Path | None:
    for d in dirs:
        try:
            if all((d / f).is_file() for f in files):
                return d
        except OSError:
            continue
    return None


def _copy(src: Path, files: list[str], dst: Path) -> list[str]:
    dst.mkdir(parents=True, exist_ok=True)
    done = []
    for f in files:
        s = src / f
        if not s.is_file():
            continue
        target = dst / ("NOTICE-android-platform-tools.txt" if f == "NOTICE.txt"
                        else f)
        shutil.copy2(s, target)
        done.append(target.name)
    return done


def _version(path: Path) -> str:
    try:
        p = subprocess.run([str(path), "version" if path.name.startswith("adb")
                            else "-v"], capture_output=True, timeout=15,
                           creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                          if WIN else 0))
        sys.path.insert(0, str(ROOT))
        from core.session import decode_bytes
        text = (decode_bytes(p.stdout) + "\n" + decode_bytes(p.stderr)).strip()
        return text.splitlines()[0] if text else ""
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", default="", help="platform-tools 目录")
    ap.add_argument("--hdc", default="", help="OpenHarmony toolchains 目录")
    ap.add_argument("--check", action="store_true", help="只检查，不复制")
    args = ap.parse_args()

    adb_dir = Path(args.adb) if args.adb else _find(ADB_FILES[:3], _candidates_adb())
    hdc_dir = Path(args.hdc) if args.hdc else _find(HDC_FILES, _candidates_hdc())

    for name, src, files in (("adb", adb_dir, ADB_FILES),
                             ("hdc", hdc_dir, HDC_FILES)):
        cur = BIN / (name + (".exe" if WIN else ""))
        if cur.is_file():
            print(f"[{name}] 已有：{cur}  {_version(cur)}")
            if args.check:
                continue
        if src is None:
            print(f"[{name}] 没找到源目录。请用 --{name} 指定，或手动放到 {BIN}")
            if name == "adb":
                print("       官方下载：https://developer.android.com/tools/releases/platform-tools")
            else:
                print("       官方下载：https://developer.huawei.com/consumer/cn/download/")
            continue
        if args.check:
            print(f"[{name}] 可以复制：{src}")
            continue
        done = _copy(src, files, BIN)
        print(f"[{name}] 已从 {src} 复制：{', '.join(done) or '（无）'}")
        if (BIN / (name + '.exe')).is_file():
            print(f"[{name}] 版本：{_version(BIN / (name + '.exe'))}")
    print(f"\nbin 目录：{BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
