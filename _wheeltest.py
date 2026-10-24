# -*- coding: utf-8 -*-
"""滚动容器的滚轮路由自测。

以前的实现用 <Enter> 抢 bind_all、<Leave> 解绑：指针一移到滚动画布里的
卡片（卡片盖住了内胆）就收到 Leave，滚轮当场失效 —— 用户看到的就是
「这个区域滚轮不灵光，得挪到卡片之间的缝里」。

现在改成「绑 toplevel + 沿 master 链找内胆」，所以这里的断言必须真的
把事件发给**子控件**，而不是画布本身。

跑法：python _wheeltest.py
"""
from __future__ import annotations

import sys
import tkinter as tk
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wbtui as U  # noqa: E402

PASS, FAIL = 0, 0


def chk(ok, name: str, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(("  [PASS] " if ok else "  [FAIL] ") + name
          + (f"  —— {detail}" if detail else ""))
    return bool(ok)


def pump(w, t: float = 0.15) -> None:
    end = w.tk.call("clock", "milliseconds") + int(t * 1000)
    while w.tk.call("clock", "milliseconds") < end:
        w.update()


def main() -> int:
    root = tk.Tk()
    root.geometry("520x360+60+60")
    sb = U.ScrollBox(root)
    sb.pack(fill="both", expand=True)
    # 塞满内容：一串卡片，每张卡里再嵌一层标签（模拟助手侧栏的层级）
    cards = []
    for i in range(40):
        card = tk.Frame(sb.inner, bg=U.T.card)
        card.pack(fill="x", pady=U.px(3))
        lb = tk.Label(card, text=f"第 {i} 行内容", bg=U.T.card, fg=U.T.text,
                      anchor="w")
        lb.pack(fill="x")
        cards.append((card, lb))
    pump(root, 0.3)

    def top() -> float:
        return float(sb.cv.yview()[0])

    eq0 = top()
    chk(eq0 == 0.0, "初始在顶端", f"yview={sb.cv.yview()}")

    # 1. 事件打在最深处那个 Label 上（以前的实现在这里滚不动）
    cards[2][1].event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    pump(root, 0.15)
    y1 = top()
    chk(y1 > eq0, "指针停在卡片里的文字上，滚轮也能滚下去",
        f"{eq0:.3f} → {y1:.3f}")

    # 2. 往上滚回来
    cards[2][1].event_generate("<MouseWheel>", delta=120, x=5, y=5)
    pump(root, 0.15)
    chk(top() < y1, "往上滚也生效", f"{y1:.3f} → {top():.3f}")

    # 3. 卡片本身（不是文字）同样管用
    y2 = top()
    cards[5][0].event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    pump(root, 0.15)
    chk(top() > y2, "指针停在卡片空白处也能滚", f"{y2:.3f} → {top():.3f}")

    # 4. 画布本体
    y3 = top()
    sb.cv.event_generate("<MouseWheel>", delta=-120, x=40, y=40)
    pump(root, 0.15)
    chk(top() > y3, "指针在画布本体上也能滚")

    # 5. 滚动条自身
    y4 = top()
    sb.vs.event_generate("<MouseWheel>", delta=120, x=3, y=20)
    pump(root, 0.15)
    chk(top() < y4, "指针压在滚动条上也能滚")

    # 6. 容器外面（窗口别处的按钮）不该被这个容器抢走
    outside = tk.Frame(root, bg=U.T.page, height=40)
    outside.pack(fill="x")
    btn = tk.Label(outside, text="别处", bg=U.T.page, fg=U.T.text)
    btn.pack()
    pump(root, 0.2)
    before = top()
    btn.event_generate("<MouseWheel>", delta=-120, x=2, y=2)
    pump(root, 0.15)
    chk(top() == before, "指针在容器外面时，这个容器不动", f"{before:.3f}")
    sb.destroy()
    pump(root, 0.15)

    # 7. 嵌套滚动条：里面的先滚，外面的不动
    outer = U.ScrollBox(root)
    outer.pack(fill="both", expand=True)
    tall = tk.Frame(outer.inner, bg=U.T.page, height=U.px(900))
    tall.pack(fill="x")
    pump(root, 0.2)
    inner = U.ScrollBox(tall)
    inner.pack(fill="both", expand=True, padx=U.px(6), pady=U.px(6))
    for i in range(40):
        tk.Label(inner.inner, text=f"内层 {i}", bg=U.T.card, fg=U.T.text,
                 anchor="w").pack(fill="x")
    pump(root, 0.3)
    deep = inner.inner.winfo_children()[3]
    deep.event_generate("<MouseWheel>", delta=-120, x=3, y=3)
    pump(root, 0.2)
    chk(float(inner.cv.yview()[0]) > 0, "嵌套时滚轮交给里层",
        f"里层 {inner.cv.yview()[0]:.3f}")
    chk(float(outer.cv.yview()[0]) == 0.0, "嵌套时外层不动",
        f"外层 {outer.cv.yview()[0]:.3f}")

    # 8. Text 有自己的滚轮行为，不该被抢
    txt = tk.Text(inner.inner, height=4)
    txt.pack(fill="x")
    txt.insert("1.0", "一\n二\n三\n四\n五\n六\n七\n八\n九\n十\n" * 5)
    pump(root, 0.2)
    before_inner = float(inner.cv.yview()[0])
    txt.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    pump(root, 0.15)
    chk(float(inner.cv.yview()[0]) == before_inner, "指针在文本框里时不抢滚轮")
    txt.destroy()
    pump(root, 0.1)

    root.destroy()
    print(f"\n合计 {PASS} 项通过，{FAIL} 项失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
