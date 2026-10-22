# -*- coding: utf-8 -*-
"""多设备会话管理：一次连多台，用顶部标签切换当前工作的那台。

为什么要单独一层：整套页面的代码都默认「当前只有一台设备」，
读的是 app.session / app.device / app.dialect / app.sampler。
把多台放进 Slot 里、让 app 的那四个属性变成指向「当前 Slot」的视图，
页面一行都不用改，切换标签就等于换了一台设备。

没连任何设备时 active 是 None —— 页面按「本机」处理，和以前一致。
"""
from __future__ import annotations

import uuid
from typing import Iterator

from core.metrics import Sampler
from core.detect import dialect_for


class DeviceSlot:
    """一台已连接的设备。标签、方言、采样器都挂在这上面。"""

    def __init__(self, session, device, label: str = ""):
        self.sid = uuid.uuid4().hex[:8]
        self.session = session
        self.device = device
        self.label = (label or getattr(device, "hostname", "")
                      or getattr(session, "label", "") or "未命名设备")
        self.dialect = dialect_for(device)
        self._sampler = None
        self.elev = ""

    @property
    def sampler(self):
        # 采样器要绑会话，建晚了会不会漏采？不会 —— 它只是按需 exec，
        # 所以惰性建，省得每台都开一个线程。
        if self._sampler is None and self.session is not None:
            self._sampler = Sampler(self.session, self.dialect)
        return self._sampler

    def reset_sampler(self):
        self._sampler = None

    def alive(self) -> bool:
        try:
            return bool(self.session and self.session.alive)
        except Exception:  # noqa: BLE001
            return False

    def close(self):
        try:
            if self.session is not None:
                self.session.close()
        except Exception:  # noqa: BLE001
            pass
        self.session = None
        self._sampler = None


class SessionManager:
    def __init__(self):
        self._slots: list[DeviceSlot] = []
        self.active_id = ""

    # ------------------------------------------------------------ 增删
    def add(self, session, device, label: str = "") -> DeviceSlot:
        """同一台设备（host + user）重复连时，替换旧的而不是堆一堆。"""
        key = self._key(session, device)
        for i, s in enumerate(self._slots):
            if self._key(s.session, s.device) == key:
                s.close()
                slot = DeviceSlot(session, device, label)
                slot.elev = getattr(s, "elev", "")
                self._slots[i] = slot
                self.active_id = slot.sid
                return slot
        slot = DeviceSlot(session, device, label)
        self._slots.append(slot)
        self.active_id = slot.sid
        return slot

    @staticmethod
    def _key(session, device) -> str:
        host = getattr(session, "host", "") or getattr(device, "hostname", "")
        user = getattr(session, "user", "") or ""
        port = getattr(session, "port", "") or ""
        return f"{user}@{host}:{port}"

    def close(self, sid: str) -> bool:
        for i, s in enumerate(self._slots):
            if s.sid == sid:
                s.close()
                self._slots.pop(i)
                if self.active_id == sid:
                    self.active_id = (self._slots[i - 1].sid if self._slots
                                      else "")
                return True
        return False

    def clear(self):
        for s in self._slots:
            s.close()
        self._slots.clear()
        self.active_id = ""

    # ------------------------------------------------------------ 查询
    def get(self, sid: str) -> DeviceSlot | None:
        for s in self._slots:
            if s.sid == sid:
                return s
        return None

    def set_active(self, sid: str) -> DeviceSlot | None:
        slot = self.get(sid)
        if slot is not None:
            self.active_id = sid
        return slot

    @property
    def active(self) -> DeviceSlot | None:
        return self.get(self.active_id)

    def slots(self) -> list[DeviceSlot]:
        return list(self._slots)

    def __len__(self) -> int:
        return len(self._slots)

    def __iter__(self) -> Iterator[DeviceSlot]:
        return iter(self._slots)

    # ------------------------------------------------------------ 视图
    @property
    def session(self):
        a = self.active
        return a.session if a else None

    @property
    def device(self):
        a = self.active
        return a.device if a else None

    @property
    def dialect(self):
        a = self.active
        return a.dialect if a else None

    @property
    def sampler(self):
        a = self.active
        return a.sampler if a else None
