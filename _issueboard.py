# -*- coding: utf-8 -*-
"""需求及问题处理清单的「实时看板」生成器。

用法：
    python _issueboard.py            # 重新生成看板区块并写回
    python _issueboard.py --check    # 只校验看板是否过期（过期退出码 1，可挂发版闸门）

为什么要有它：父条目标了「已完成」，子项却没做完（历史教训：R-005b 那类），
人眼扫总表是看不出来的。看板把「子项」单独统计一遍，并列出
「父已完成但子项没做」「已完成但折扣不为无」两类矛盾，逼着收尾时对齐。

设计约束（为了 --check 幂等）：
  · 看板**只从上表推导**，不写时间戳、不写随机量；
  · 同一份正文跑两次，产出必须逐字节相同。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE / "需求及问题处理清单.md"

BEGIN = "<!-- BOARD:BEGIN -->"
END = "<!-- BOARD:END -->"
TB = "<!-- TABLE:BEGIN -->"
TE = "<!-- TABLE:END -->"

STATES = ("已完成", "未开始", "正在进行", "挂起", "无法完成", "作废")
DONE = ("已完成", "作废")

SUB = re.compile(r"^(R-\d{3})([a-z])$")
DETAIL = re.compile(r"^###\s+(R-\d{3}[a-z]?)\b", re.M)


def rows_of(text: str) -> list[list[str]]:
    """取总表的行（总表必须夹在 TABLE:BEGIN/END 之间）。"""
    m = re.search(re.escape(TB) + r"(.*?)" + re.escape(TE), text, re.S)
    if not m:
        raise SystemExit("找不到 TABLE:BEGIN / TABLE:END 标记，无法解析总表")
    out: list[list[str]] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 6:
            continue
        if cells[0] == "编号" or set(cells[0]) <= set("-: "):
            continue
        if not re.match(r"^R-\d{3}[a-z]?$", cells[0]):
            continue
        out.append(cells)
    return out


def discounts(text: str) -> dict[str, str]:
    """明细里每条「折扣与缺口」的值（找不到就不计入）。"""
    got: dict[str, str] = {}
    parts = DETAIL.split(text)
    # split 后：parts[0] 是首个标题前的内容，之后 [id, body, id, body, ...]
    for i in range(1, len(parts) - 1, 2):
        rid, body = parts[i], parts[i + 1]
        for line in body.splitlines():
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == "折扣与缺口":
                got[rid] = cells[1]
                break
    return got


def build(rows: list[list[str]], disc: dict[str, str]) -> str:
    main = [r for r in rows if not SUB.match(r[0])]
    subs = [r for r in rows if SUB.match(r[0])]
    nl = "\n"

    def cnt(group):
        return {s: sum(1 for r in group if r[4] == s) for s in STATES}

    cm, cs = cnt(main), cnt(subs)
    lines = [
        "> 本区块由 `python _issueboard.py` 生成（`--check` 校验是否过期），**不要手改**。",
        "> 只从上表推导、不含时间戳，保证同一份正文重复生成结果一致。",
        "",
        "### 状态分布",
        "",
        "| 状态 | 主条目 | 子项 | 合计 |",
        "|---|---|---|---|",
    ]
    for s in STATES:
        lines.append(f"| {s} | {cm[s]} | {cs[s]} | {cm[s] + cs[s]} |")
    lines.append(f"| **合计** | **{len(main)}** | **{len(subs)}** | **{len(rows)}** |")

    open_main = [r for r in main if r[4] not in DONE]
    lines += ["", f"### 未收口的主条目（{len(open_main)} 条）", ""]
    if open_main:
        lines += ["| 编号 | 状态 | 需求内容 |", "|---|---|---|"]
        for r in open_main:
            lines.append(f"| {r[0]} | {r[4]} | {r[2]} |")
    else:
        lines.append("无 —— 所有主条目都已收口。")

    # 子项单独过：父已完成但子项没做，是最容易漏的一类
    by_id = {r[0]: r for r in rows}
    bad_parent, open_sub = [], [r for r in subs if r[4] not in DONE]
    for r in open_sub:
        m = SUB.match(r[0])
        parent = by_id.get(m.group(1)) if m else None
        if parent and parent[4] == "已完成":
            bad_parent.append(r)
    lines += ["", f"### 子项单独过（未收口子项 {len(open_sub)} 条）", ""]
    if open_sub:
        lines += ["| 子项 | 状态 | 父条目状态 | 需求内容 |", "|---|---|---|---|"]
        for r in open_sub:
            m = SUB.match(r[0])
            p = by_id.get(m.group(1)) if m else None
            tag = "⚠️ 父已完成" if r in bad_parent else (p[4] if p else "—")
            lines.append(f"| {r[0]} | {r[4]} | {tag} | {r[2]} |")
    else:
        lines.append("无 —— 没有遗留子项。")

    # 已完成但写了折扣：说明它是「带缺口收口」，缺口必须已经拆成新条目
    gaps = [(r[0], disc.get(r[0], "")) for r in rows
            if r[4] == "已完成" and r[0] in disc
            and disc[r[0]] not in ("", "无", "无。", "不适用", "不适用。")]
    lines += ["", f"### 带缺口的「已完成」（{len(gaps)} 条）", ""]
    if gaps:
        lines += ["| 编号 | 折扣与缺口 |", "|---|---|"]
        for rid, g in gaps:
            lines.append(f"| {rid} | {g} |")
        lines.append("")
        lines.append("> 上表每条都必须能在总表里找到对应的后续条目，否则等于让缺口消失了。")
    else:
        lines.append("无 —— 所有已完成条目折扣均为「无」。")
    return nl.join(lines)


def main() -> int:
    if not DOC.is_file():
        print(f"找不到 {DOC.name}")
        return 2
    text = DOC.read_text(encoding="utf-8", newline="")
    nl = "\r\n" if "\r\n" in text else "\n"
    try:
        body = build(rows_of(text), discounts(text)).replace("\n", nl)
    except SystemExit as e:
        print(e)
        return 2

    i, j = text.find(BEGIN), text.find(END)
    if i < 0 or j < 0 or j < i:
        print("正文里缺 BOARD:BEGIN / BOARD:END 标记")
        return 2
    cur = text[i + len(BEGIN):j]
    new = nl + body + nl

    if "--check" in sys.argv:
        if cur == new:
            print("看板是最新的（--check 通过）")
            return 0
        print("看板已过期：正文改过但没重跑 `python _issueboard.py`")
        return 1

    DOC.write_text(text[:i + len(BEGIN)] + new + text[j:],
                   encoding="utf-8", newline="")
    n = len(rows_of(text))
    print(f"看板已更新（{n} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
