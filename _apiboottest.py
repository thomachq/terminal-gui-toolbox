"""API 自动恢复的语义：退出停服不许把 enabled 落成 false。

背景（v1.0.19 实机发现）：`stop()` 原来无条件写 `enabled=False`，而退出流程
`_quit_app()` 必然调 `stop()` —— 于是 `_boot_api()` 里那句「上次退出前是开着的
就自动恢复」**永远不成立**，用户每次都得手动去界面点一次「启动」。
这类「两处约定互相抵消」的毛病不看代码根本发现不了，所以用测试钉住。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import api.server as SV  # noqa: E402

FAIL = []
PASS = [0]


def chk(ok, title, detail=""):
    if ok:
        PASS[0] += 1
        print(f"  ok   {title}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL.append(title)
        print(f"  FAIL {title}" + (f"  [{detail}]" if detail else ""))


def sec(t):
    print(f"\n-- {t}")


def make(tmp):
    cfg, pool, srv = SV.make_default(Path(tmp))
    return cfg, pool, srv


def enabled_on_disk(tmp):
    p = Path(tmp) / "api.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("enabled")


sec("1. 语义：stop(keep_enabled=True) 不落盘")
with tempfile.TemporaryDirectory() as tmp:
    cfg, pool, srv = make(tmp)
    cfg.data["enabled"] = True
    cfg.save()
    chk(enabled_on_disk(tmp) is True, "前置：盘上是开着的")
    srv.stop(keep_enabled=True)
    chk(enabled_on_disk(tmp) is True, "退出式停服后，盘上仍是开着的",
        "下次启动才会自动恢复")

sec("2. 语义：stop() 默认落成关")
with tempfile.TemporaryDirectory() as tmp:
    cfg, pool, srv = make(tmp)
    cfg.data["enabled"] = True
    cfg.save()
    srv.stop()
    chk(enabled_on_disk(tmp) is False, "用户手动关服务后，盘上记成关着")

sec("3. 恢复链路：_boot_api 读到开着就会 start")
with tempfile.TemporaryDirectory() as tmp:
    cfg, pool, srv = make(tmp)
    cfg.data["enabled"] = True
    cfg.save()
    srv.stop(keep_enabled=True)
    # 重新读一遍，模拟下次启动
    cfg2, pool2, srv2 = make(tmp)
    chk(bool(cfg2.data.get("enabled")) is True, "重新加载后 enabled 仍为 True（会触发自动恢复）")

sec("4. 调用点：退出 / 换端口 都不许落 false")
app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
srv_src = (ROOT / "api" / "server.py").read_text(encoding="utf-8", errors="replace")
page_src = (ROOT / "pages" / "api_page.py").read_text(encoding="utf-8", errors="replace")

chk("self.api_server.stop(keep_enabled=True)" in app_src,
    "_quit_app 用 keep_enabled=True")
chk("self.stop(keep_enabled=True)" in srv_src,
    "relocate（换端口前停服）用 keep_enabled=True")
chk("srv.stop(keep_enabled=True)" in page_src,
    "api_page 换端口前停服用 keep_enabled=True")
chk("if srv.running:\n            srv.stop()" in page_src,
    "api_page 手动开关仍是 stop()（要落盘记成关）")

sec("5. 回归：_boot_api 的约定还在")
chk("上次退出前是开着的" in app_src, "_boot_api 注释仍然承诺自动恢复")
chk('if bool(self.api_cfg.data.get("enabled"))' in app_src,
    "_boot_api 仍按 enabled 决定是否 start")

print("\n" + "=" * 52)
print(f"通过 {PASS[0]}，失败 {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  失败项：" + f)
sys.exit(1 if FAIL else 0)
