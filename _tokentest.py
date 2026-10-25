# -*- coding: utf-8 -*-
"""令牌必须打星号、并带复制按钮。

用户原话：
  「这里的令牌要打星号，并且带复制按钮」

两件事分开验证，因为它们是两条独立的通路：
  · **显示** —— KVGrid 上只能出现 `ttb_************`，明文一个字符都不能漏；
  · **复制** —— 按钮复制的是**真令牌**（界面上看不见，但智能体要用），
    复制成掩码就等于把这个功能做废了。

分段：
  1. ApiConfig.token_masked：前缀保留、长度固定、不泄露明文任何片段
  2. KVGrid 四元组：第 4 项（按钮文字, 回调）不被丢掉，且真的画出了按钮
  3. 点一下按钮确实回调
  4. ApiPage.api_copy_token 复制的是明文而不是掩码
  5. 页面源码里确实把「复制」挂到了令牌那一行（回归时别被顺手删掉）
"""
import inspect
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PASS = FAIL = 0
FAILS: list[str] = []

import crypto_store  # noqa: E402
import wbtui as U  # noqa: E402
import tkinter as tk  # noqa: E402
from api.server import ApiConfig  # noqa: E402
from pages import api_page as AP  # noqa: E402
from ui.table import KVGrid  # noqa: E402


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


def pump(root, seconds=0.12):
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


# ================================================================ 1
sec("1. token_masked 只露前缀")
td = tempfile.mkdtemp(prefix="tok-")
crypto_store.set_key_dir(td)
cfg = ApiConfig(Path(td) / "api.json")
cfg.load()
real = cfg.token()
chk(real.startswith("ttb_"), "令牌默认带 ttb_ 前缀", real[:4])
masked = cfg.token_masked()
chk(masked == "ttb_" + "*" * 12, "掩码是 ttb_ + 12 个星号", masked)
chk(real not in masked, "掩码里没有明文")
chk(masked.count("*") == 12, "星号个数固定", str(masked.count("*")))

# 换一个长度完全不同的令牌，掩码长度必须一样（否则等于泄露长度）
cfg2 = ApiConfig(Path(td) / "api2.json")
cfg2.data["token_enc"] = crypto_store.protect("ttb_" + "Z" * 90)
chk(cfg2.token_masked() == masked, "不同长度的令牌掩码一致（不泄露长度）",
    cfg2.token_masked())
chk(not any(real[i:i + 6] and real[i:i + 6] in masked
            for i in range(len(real) - 5)),
    "明文里任何 6 连字符都没出现在掩码里")

# ================================================================ 2
sec("2. KVGrid 四元组不丢按钮")
root = tk.Tk()
root.withdraw()
calls: list[str] = []
grid = KVGrid(root, columns=1)
grid.set_items([
    ("监听地址", "127.0.0.1:41483", None),
    ("令牌", masked, "brand", ("复制", lambda: calls.append("copy"))),
])
chk(len(grid._items) == 2, "两行都在")
chk(len(grid._items[1]) == 4, "第 4 项被保留了", str(len(grid._items[1])))
chk(grid._items[1][3] is not None, "第 4 项不是 None")
pump(root, 0.3)


def descendants(w):
    for c in w.winfo_children():
        yield c
        yield from descendants(c)


btns = [w for w in descendants(grid) if getattr(w, "text", None) == "复制"]
chk(len(btns) == 1, "画出了 1 个「复制」按钮", str(len(btns)))
labels = [getattr(w, "cget", lambda k: "")("text") for w in descendants(grid)
          if w.winfo_class() in ("Label", "TLabel")]
chk(any(masked in str(t) for t in labels), "令牌那一行显示的是掩码")
chk(not any(real in str(t) for t in labels), "界面上任何地方都没有明文")

# ================================================================ 3
sec("3. 按钮真的能点")
if btns:
    btn = btns[0]
    chk(callable(getattr(btn, "command", None)), "按钮带回调")
    btn.command()
    chk(calls == ["copy"], "点一下回调执行了", str(calls))
else:
    chk(False, "按钮不存在，没法点")
root.destroy()

# ================================================================ 4
sec("4. 复制的是明文")


class FakeCfg:
    def __init__(self, tok):
        self._t = tok

    def token(self):
        return self._t

    def token_masked(self):
        return "ttb_" + "*" * 12


class FakeSrv:
    def __init__(self, tok):
        self.config = FakeCfg(tok)

    def running(self):
        return True


page = object.__new__(AP.ApiPage)
page.api_server = FakeSrv(real)
got: list[str] = []


def fake_copy(text, label=""):
    got.append(text)
    return True


page.copy_text = fake_copy
page.api_copy_token()
chk(got == [real], "复制进剪贴板的是真令牌")
chk(got and got[0] != masked, "复制的不是掩码")

page.api_server = None
page.api_copy_token()
chk(len(got) == 1, "没有服务时不动剪贴板（不崩）")

# ================================================================ 5
sec("5. 页面把复制按钮挂在了令牌那行")
src = inspect.getsource(AP.ApiPage.api_refresh)
chk('("复制", self.api_copy_token)' in src, "api_refresh 里挂了复制回调")
chk("cfg.token_masked()" in src, "显示走的是 token_masked")
chk("cfg.token()" not in src, "api_refresh 里没有直接取明文")

print(f"\n合计 {PASS + FAIL} 项：通过 {PASS}，失败 {FAIL}")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  -", f)
sys.exit(1 if FAIL else 0)
