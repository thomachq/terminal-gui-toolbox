# -*- coding: utf-8 -*-
"""脚本库「按主机批量执行 + 结果对比」的纯逻辑（R-005）。

界面只负责挑主机、发确认、摆结果；**跑与比**都在这里，能脱离 Tk 测。

两条设计决定（写进需求清单，别悄悄改）：
  · 批量执行的目标是**已连接的设备**（`SessionManager.slots()`），不替用户
    静默新建连接 —— 新建连接要填凭据、可能弹 UAC，那是交互动作，不能在
    「点一下批量运行」里偷偷发生。要加主机，先去主机页连上。
  · 「相同」的判据是**规范化后的输出**：逐行去行尾空白、去末尾空行。
    不去的话，一台多打一个空行就被判成「不同」，对比全是噪声。
    但**不**去行首空白、不排序、不忽略大小写 —— 那些差异是真的差异。
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from dataclasses import dataclass, field


@dataclass
class HostResult:
    label: str
    sid: str = ""
    rc: int = -1                 # -1 = 没跑成（异常/超时），不是远端退出码
    out: str = ""
    err: str = ""
    ms: int = 0
    error: str = ""              # 本地异常的人话描述
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rc == 0 and not self.error

    @property
    def body(self) -> str:
        """给人看的完整输出（stdout + stderr 分开标）。"""
        parts = []
        if self.out:
            parts.append(self.out.rstrip("\n"))
        if self.err:
            parts.append("==== 错误输出 ====\n" + self.err.rstrip("\n"))
        if self.error:
            parts.append("==== 执行异常 ====\n" + self.error)
        return "\n".join(parts) if parts else "(没有输出)"


def normalize(text: str) -> str:
    """对比用的规范化：逐行去行尾空白 + 去末尾空行。其余原样。"""
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def run_batch(targets: list, cmd: str, timeout: int = 180,
              max_workers: int = 4) -> list[HostResult]:
    """在多台已连接设备上并发跑同一条命令。

    `targets` 是 `(label, sid, session)` 三元组列表；session 只要有
    `.run(cmd, timeout=...) -> (rc, out, err)` 就行（真会话与假会话同接口）。
    一台抛异常不影响其它台 —— 批量执行最怕「一台卡住，整批陪葬」。
    """
    results: list[HostResult] = []
    if not targets:
        return results
    workers = max(1, min(int(max_workers or 4), len(targets)))

    def one(t) -> HostResult:
        label, sid, sess = t
        t0 = time.monotonic()
        try:
            rc, out, err = sess.run(cmd, timeout=timeout)
            return HostResult(label=label, sid=sid, rc=int(rc),
                              out=out or "", err=err or "",
                              ms=int((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001  一台的失败要变成一行结果
            return HostResult(label=label, sid=sid, rc=-1,
                              error=f"{type(e).__name__}: {e}",
                              ms=int((time.monotonic() - t0) * 1000))

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, t): t for t in targets}
        for fut in cf.as_completed(futs):
            results.append(fut.result())
    # 按传入顺序摆回去（as_completed 是完成序，界面要稳定序）
    order = {str(t[1] or t[0]): i for i, t in enumerate(targets)}
    results.sort(key=lambda r: order.get(str(r.sid or r.label), 999))
    return results


def compare(results: list[HostResult]) -> dict:
    """把一批结果分成「相同 / 不同 / 失败」，给界面着色与汇总用。

    返回：
      status: {sid或label: "same"|"diff"|"fail"}
      groups: [[HostResult, ...], ...]  按规范化输出分组（失败不参与分组）
      all_same: 全部成功且输出一致
      summary: 一句人话
    """
    status: dict[str, str] = {}
    groups: list[list[HostResult]] = []
    ok_results = [r for r in results if r.ok]
    by_norm: dict[str, list[HostResult]] = {}
    for r in results:
        key = r.sid or r.label
        if not r.ok:
            status[key] = "fail"
            continue
        by_norm.setdefault(normalize(r.out), []).append(r)
    groups = list(by_norm.values())
    n_ok = len(ok_results)
    # 「相同」= 与**多数派**一致，不是与全部成功者一致：
    # 三台里两台一样、一台不一样，那两台该标绿，不能因为第三台把大家都染黄。
    biggest = max((len(g) for g in groups), default=0)
    for g in groups:
        tag = "same" if len(g) == biggest else "diff"
        for r in g:
            status[r.sid or r.label] = tag
    all_same = bool(results) and n_ok == len(results) and len(groups) == 1
    n_fail = len(results) - n_ok
    n_diff = sum(1 for v in status.values() if v == "diff")
    if not results:
        summary = "没有可对比的结果"
    elif all_same:
        summary = f"{len(results)} 台全部成功，输出一致"
    else:
        bits = [f"{n_ok} 台成功"]
        if n_diff:
            bits.append(f"{n_diff} 台输出不同")
        if n_fail:
            bits.append(f"{n_fail} 台失败")
        summary = "，".join(bits)
    return {"status": status, "groups": groups, "all_same": all_same,
            "summary": summary}


def diff_report(results: list[HostResult]) -> str:
    """逐行对比文本：只列**有差异的行**，每台一列。全同则一句话。"""
    oks = [r for r in results if r.ok]
    if len(oks) < 2:
        return "成功的主机不足两台，无法逐行对比。"
    if compare(results)["all_same"]:
        return "所有主机输出一致。"
    grids = [normalize(r.out).splitlines() for r in oks]
    width = max(len(g) for g in grids)
    lines = ["行号\t" + "\t".join(r.label for r in oks)]
    n_diff = 0
    for i in range(width):
        cells = [g[i] if i < len(g) else "" for g in grids]
        if len(set(cells)) > 1:
            n_diff += 1
            lines.append(f"{i + 1}\t" + "\t".join(cells))
    if not n_diff:
        return "逐行对比没有差异（差异只在行尾空白或末尾空行）。"
    lines.insert(0, f"共 {n_diff} 行不同：")
    return "\n".join(lines)
