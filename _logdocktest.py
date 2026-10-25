# -*- coding: utf-8 -*-
"""日志底栏自测：状态栏按钮、开关、高度记忆、内容数据。"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.argv = [a for a in sys.argv if a != "--smoke"] + ["--no-probe"]

FAILS = []
N = 0


def chk(cond, name, info=""):
    global N
    N += 1
    if cond:
        print(f"  \u2713 {name}")
    else:
        FAILS.append(name)
        print(f"  \u2717 {name}" + (f"   [{info}]" if info else ""))


def pump(w, s=0.3):
    t0 = time.time()
    while time.time() - t0 < s:
        w.update()
        time.sleep(0.01)


import app as APP

tmp = tempfile.mkdtemp(prefix="wbt_log_")
real = APP.DATA_DIR
APP.DATA_DIR = Path(tmp)
a = None
try:
    a = APP.ToolboxApp()
    pump(a, 1.0)

    chk(hasattr(a, "status") and getattr(a.status, "btn_log", None) is not None,
        "状态栏有「日志」按钮")
    chk("log" not in a._pages, "日志不再是一个页签")
    chk("log" not in a.nav.items, "导航里没有「日志管理」了")
    chk(not a._log_dock_on, "默认不展开")

    a.log_dock_toggle()
    pump(a, 0.5)
    chk(a._log_dock_on, "点状态栏按钮能打开")
    chk(a.log_dock_host.winfo_ismapped(), "面板真的显示出来了")
    chk(getattr(a, "tbl_audit", None) is not None, "日志表格建出来了")
    chk(a.status._log_on, "按钮进入点亮状态")
    h1 = a.log_dock_host.winfo_height()
    chk(h1 > 100, "面板有高度", h1)

    # 用户反馈「默认高度太小，显示都不完整」：截图里只剩表头和分页。
    # 现在的判据是「表格本体要能露出至少 5 行」—— 只看面板总高会漏掉
    # 工具栏吃掉的份额（面板 210px 时表格只剩 50px）。
    from pages import log_dock as LD
    row_h = 30 * LD.U.S if hasattr(LD.U, "S") else 45
    chk(a.tbl_audit.winfo_height() >= row_h * 5,
        "表格本体至少露出 5 行",
        f"面板 {h1}px / 表格 {a.tbl_audit.winfo_height()}px")
    chk(a._log_dock_h >= LD.U.px(LD.MIN_H), "默认高度不低于下限",
        a._log_dock_h)

    # 老版本存下来的「太扁」高度必须被抬上来（用户机器上就是 210）
    chk(LD.USABLE_H > 160, "旧默认（≈160 逻辑像素）被判为不可用",
        LD.USABLE_H)
    chk(LD.DEFAULT_H >= LD.USABLE_H, "新默认高于可用下限",
        f"{LD.DEFAULT_H} >= {LD.USABLE_H}")

    a.log_dock_toggle()
    pump(a, 0.3)
    chk(not a._log_dock_on, "再点一次能收起")
    chk(not a.status._log_on, "按钮回到未点亮")

    a.log_dock_toggle(True)
    pump(a, 0.4)
    a.log_dock_host.configure(height=260)
    pump(a, 0.2)
    a._log_dock_h = 260
    a._win_state_save()
    data = json.loads((Path(tmp) / "window.json").read_text(encoding="utf-8"))
    chk(data.get("log_open") is True, "落盘记住「面板是开的」", data.get("log_open"))
    chk(int(data.get("log_h") or 0) > 100, "落盘记住高度", data.get("log_h"))

    # 写一条终端命令审计，确认底部面板能读到（含口令打码）
    a._audit_command("mysql -uroot -pSecret9 -e 'show databases'", 1,
                     "ERROR 1045", "Access denied", 12)
    a.audit_reload()
    pump(a, 0.4)
    rows = a._audit_rows
    hit = [r for r in rows if "mysql -uroot" in str(r.get("detail") or "")]
    chk(bool(hit), "面板里能看到刚跑的命令", len(rows))
    if hit:
        d = str(hit[0].get("detail") or "")
        chk("Secret9" not in d, "口令已打码")
        chk("Access denied" in d, "stderr 也在里面")
finally:
    try:
        if a is not None:
            a.destroy()
    except Exception:  # noqa: BLE001
        pass
    APP.DATA_DIR = real

print("\n" + "=" * 50)
print(f"{'失败 ' + str(len(FAILS)) if FAILS else '全部通过'}：{N - len(FAILS)}/{N}")
for f in FAILS:
    print("  ✗", f)
raise SystemExit(1 if FAILS else 0)
