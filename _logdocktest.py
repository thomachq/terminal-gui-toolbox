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

    # 用户 2026-10-25 改口径：「日志高度太高了，每行的高度可以适当缩小一点，
    # 日志能展示 3 条就可以」。所以判据从「表格露出 5 行」改成「刚好 3 条」，
    # 并且行高必须是紧凑档 —— 只看面板总高会漏掉工具栏吃掉的份额。
    from pages import log_dock as LD
    from ui.table import ROW_H_DENSE
    tree = a.tbl_audit.tree
    rh = int(tree.tk.call("ttk::style", "lookup", tree.cget("style"),
                          "-rowheight"))
    chk(abs(rh - LD.U.px(ROW_H_DENSE)) <= 1, "日志用紧凑行高（26 逻辑像素）",
        f"实际 {rh}px，期望 {LD.U.px(ROW_H_DENSE)}px")
    chk(a._log_dock_h >= LD.U.px(LD.MIN_H), "默认高度不低于下限",
        a._log_dock_h)

    # 老版本存下来的「太扁」高度必须被抬上来（用户机器上就是 210）
    chk(LD.USABLE_H > 160, "旧默认（≈160 逻辑像素）被判为不可用",
        LD.USABLE_H)
    chk(LD.DEFAULT_H >= LD.USABLE_H, "新默认高于可用下限",
        f"{LD.DEFAULT_H} >= {LD.USABLE_H}")

    # 先凑几条数据，才能看「默认到底露几条」。**必须放在任何人为改高度之前**
    # ——后面那段记忆测试会把面板压到 260 物理像素，那时候数出来的不是默认值。
    a._audit_command("mysql -uroot -pSecret9 -e 'show databases'", 1,
                     "ERROR 1045", "Access denied", 12)
    for i in range(4):
        a._audit_command(f"echo probe-{i}", 0, f"probe-{i}", "", 1)
    a.audit_reload()
    pump(a, 0.5)

    # 默认高度到底露几条：只数真的画出来的行（bbox 空 = 没显示），
    # 不能拿「tree 高度 ÷ 行高」估 —— 表头也占 tree 的高度。
    # 注意「刚好在边界」的那一行：bbox 仍然非空，但可见高度可能是 0，
    # 不能算成半截行（那是排版正好合上，不是裁坏）。
    def visible_rows() -> tuple[int, int]:
        """(完整可见行数, 露出 3px 以上但不满一行的行数)"""
        full = part = 0
        th = a.tbl_audit.tree.winfo_height()
        for iid in a.tbl_audit.tree.get_children():
            bb = a.tbl_audit.tree.bbox(iid)
            if not bb:
                continue
            shown = min(bb[1] + bb[3], th) - max(0, bb[1])
            if shown >= bb[3]:
                full += 1
            elif shown >= 3:
                part += 1
        return full, part

    seen, part = visible_rows()
    chk(seen >= 3, "默认高度露出至少 3 条数据",
        f"可见 {seen} 条 / 面板 {h1}px / 表格 {a.tbl_audit.tree.winfo_height()}px")
    chk(seen <= 6, "也没有又变回一大片（用户要的是 3 条就够）", f"可见 {seen} 条")
    # 半截行看着像「表格被裁坏了」。要么整整 3 条，要么整整 4 条，
    # 不能卡在 3 条半 —— DEFAULT_H 的取值就是按这个挑的。
    chk(part == 0, "没有只露半截的数据行", f"{part} 条半截")

    rows = a._audit_rows
    hit = [r for r in rows if "mysql -uroot" in str(r.get("detail") or "")]
    chk(bool(hit), "面板里能看到刚跑的命令", len(rows))
    if hit:
        d = str(hit[0].get("detail") or "")
        chk("Secret9" not in d, "口令已打码")
        chk("Access denied" in d, "stderr 也在里面")

    a.log_dock_toggle()
    pump(a, 0.3)
    chk(not a._log_dock_on, "再点一次能收起")
    chk(not a.status._log_on, "按钮回到未点亮")

    a.log_dock_toggle(True)
    pump(a, 0.4)
    a.log_dock_host.configure(height=260)
    pump(a, 0.2)
    # 等价于「用户拖着抓手松手」：内存值、落盘、auto 标记一起走
    a._log_drag_done()
    pump(a, 0.2)
    data = json.loads((Path(tmp) / "window.json").read_text(encoding="utf-8"))
    chk(data.get("log_open") is True, "落盘记住「面板是开的」", data.get("log_open"))
    chk(int(data.get("log_h") or 0) > 100, "落盘记住高度", data.get("log_h"))
    # 只存数字不够：改默认值时无法判断「这是他拖的还是我们定的」，结果新版
    # 默认值永远推不下去（v1.0.19 / v1.0.22 都栽在这上面）。
    chk("log_h_auto" in data, "落盘记住「高度是谁定的」", data.get("log_h_auto"))
    chk(data.get("log_h_auto") is False, "手动设过高度之后标记为用户自己的",
        data.get("log_h_auto"))

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
