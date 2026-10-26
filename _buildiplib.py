# -*- coding: utf-8 -*-
"""构建内置地址库：把公开数据源拉下来，落到 `assets/iplib/*.json`。

只跑一次（数据更新时再跑），产出的 JSON 随程序打包走，用户离线也能用。
运行时用户点「更新地址库」走的是 `core/iplib.update()`，逻辑同一份。

用法：
    python _buildiplib.py            # 拉省级 + 国家
    python _buildiplib.py --prov     # 只拉省级
    python _buildiplib.py --dry      # 只检查网络，不写文件
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core import iplib  # noqa: E402


def main() -> int:
    dry = "--dry" in sys.argv
    prov = "--country" not in sys.argv          # 默认两项都拉
    ctry = "--prov" not in sys.argv
    out_dir = HERE / "assets" / "iplib"
    if not dry:
        out_dir.mkdir(parents=True, exist_ok=True)

    if dry:
        try:
            got = iplib._fetch("cncity/110000.txt")
            print(f"网络可用：北京 {len(got)} 条，首条 {got[0]}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"网络不可用：{type(e).__name__}: {e}")
            return 1

    t0 = time.time()
    last = [0.0]

    def prog(done, total, name):
        now = time.time()
        if now - last[0] < 0.6 and done < total:
            return
        last[0] = now
        pct = done * 100 // max(1, total)
        print(f"  [{done:>3}/{total}] {pct:>3}%  {name}", flush=True)

    # 写到临时 data 目录，然后拷进 assets —— 复用 update() 的下载与合并逻辑
    import os
    import shutil
    import tempfile
    td = tempfile.mkdtemp(prefix="tb_iplib_")
    os.environ["TB_DATA_DIR"] = td
    ok, msg = iplib.update(province=prov, country=ctry, progress=prog)
    print(f"\n{msg}")
    if not ok:
        return 2
    for name in ("province.json", "country.json"):
        src = Path(td) / "iplib" / name
        if src.is_file():
            dst = out_dir / name
            shutil.copyfile(src, dst)
            data = json.loads(dst.read_text(encoding="utf-8"))
            items = sum(1 for v in data.values() if v)
            cidrs = sum(len(v) for v in data.values())
            print(f"  {dst.name:<14} {dst.stat().st_size/1024/1024:5.2f} MB  "
                  f"{items} 项 / {cidrs} 网段")
    print(f"总耗时 {time.time() - t0:.0f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
