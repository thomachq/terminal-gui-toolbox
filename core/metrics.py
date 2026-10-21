# -*- coding: utf-8 -*-
"""性能采样：CPU / 内存 / 网络速率 / 运行时长。

要点：
  · CPU 与网络速率都是**累计值**，必须两次采样做差分，所以 Sampler 要记住上次的计数。
  · 采样是一条命令一次取回（用 SEP 分段），避免多次 SSH 往返把开销做大。
  · 历史留一小段给状态栏画迷你曲线。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Sample:
    ok: bool = False
    cpu_pct: float = 0.0
    mem_used: int = 0
    mem_total: int = 0
    net_rx_bps: float = 0.0        # bytes/s
    net_tx_bps: float = 0.0
    uptime: float = 0.0            # 秒
    ncpu: int = 1
    error: str = ""
    unsupported: bool = False      # 这台设备天生采不到（交换机 / 路由器）
    at: float = field(default_factory=time.time)

    @property
    def mem_pct(self) -> float:
        return (self.mem_used / self.mem_total * 100.0) if self.mem_total else 0.0


class Sampler:
    """按需采样。每台会话一个实例。"""

    HISTORY = 60          # 迷你曲线保留的点数

    def __init__(self, session, dialect) -> None:
        self.session = session
        self.dialect = dialect
        self._prev: dict | None = None
        self._prev_at: float = 0.0
        self.cpu_hist: deque[float] = deque(maxlen=self.HISTORY)
        self.net_hist: deque[float] = deque(maxlen=self.HISTORY)
        self.last = Sample()

    def reset(self) -> None:
        self._prev = None
        self._prev_at = 0.0
        self.cpu_hist.clear()
        self.net_hist.clear()
        self.last = Sample()

    def sample(self, timeout: int = 12) -> Sample:
        cmd = self.dialect.metrics_cmd()
        if not cmd:
            # 网络设备（交换机 / 路由器）根本没有 /proc 这类计数器。
            # 返回「不支持」而不是「失败」，状态栏才会是 — 而不是一片红字。
            self.last = Sample(ok=False, error="", unsupported=True)
            return self.last
        try:
            # 不走 run_traced：性能采样每几秒一次，刷满日志没有意义
            rc, out, err = self.session.run(cmd, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            self.last = Sample(ok=False, error=str(e))
            return self.last

        raw = self.dialect.parse_metrics(out)
        now = time.time()
        s = Sample(ok=bool(raw), at=now)

        if not raw:
            s.error = (err or out or "采样失败").strip()[:200]
            self.last = s
            return s

        # ---- CPU
        if "cpu_pct_direct" in raw:
            s.cpu_pct = max(0.0, min(100.0, float(raw["cpu_pct_direct"])))
        elif "cpu_total" in raw and "cpu_idle" in raw:
            if self._prev and "cpu_total" in self._prev:
                dt_total = raw["cpu_total"] - self._prev["cpu_total"]
                dt_idle = raw["cpu_idle"] - self._prev["cpu_idle"]
                if dt_total > 0:
                    s.cpu_pct = max(0.0, min(100.0,
                                            (1.0 - dt_idle / dt_total) * 100.0))
                else:
                    s.cpu_pct = self.last.cpu_pct
            else:
                s.cpu_pct = 0.0          # 第一次没有参照，先给 0

        # ---- 内存
        s.mem_total = int(raw.get("mem_total", 0))
        s.mem_used = int(raw.get("mem_used", 0))

        # ---- 网络速率
        if "net_rx" in raw and "net_tx" in raw:
            if self._prev and "net_rx" in self._prev and self._prev_at > 0:
                dt = max(0.001, now - self._prev_at)
                d_rx = raw["net_rx"] - self._prev["net_rx"]
                d_tx = raw["net_tx"] - self._prev["net_tx"]
                # 计数器回绕/重启会产生负数，忽略这一轮
                s.net_rx_bps = max(0.0, d_rx / dt)
                s.net_tx_bps = max(0.0, d_tx / dt)

        # ---- 时长 / 核数
        s.uptime = float(raw.get("uptime", self.last.uptime or 0.0))
        s.ncpu = int(raw.get("ncpu", 1)) or 1

        self._prev = raw
        self._prev_at = now
        self.cpu_hist.append(s.cpu_pct)
        self.net_hist.append(s.net_rx_bps + s.net_tx_bps)
        self.last = s
        return s


# ------------------------------------------------------------------ 格式化
def human_bytes(n: float, decimals: int = 2) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.{decimals}f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} TB"


def human_gb(n: float) -> str:
    return f"{(n or 0) / (1024 ** 3):.2f} GB"


def human_rate(bps: float) -> str:
    """字节/秒 -> MobaXterm 那种 Mb/s 观感。

    这里按**字节**换算成 MB/s 再标 MB/s，不做 bit 换算 ——
    网络面板上两种标法都有人用，但标错了会误导，所以宁可标清单位。
    """
    bps = float(bps or 0)
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.2f} KB/s"
    return f"{bps / (1024 * 1024):.2f} MB/s"


def human_uptime(sec: float) -> str:
    sec = int(max(0, sec or 0))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days} days"
    if hours:
        return f"{hours} h"
    return f"{rem // 60} min"
