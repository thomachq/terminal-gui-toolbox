# -*- coding: utf-8 -*-
"""多设备会话自测：SessionManager + 顶部设备标签。

    python _mdevsmoke.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from core.detect import DeviceInfo  # noqa: E402
from core.sessionmgr import SessionManager  # noqa: E402

PASS = 0
FAIL = 0
FAILED: list[str] = []


def chk(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


class FakeSession:
    def __init__(self, host="10.0.0.1", user="root", port=22, name=""):
        self.host = host
        self.user = user
        self.port = port
        self.kind = "ssh"
        self.label = name or f"{user}@{host}"
        self.alive = True
        self.host_kind = "unix"
        self.vendor = ""
        self.closed = False
        self.tracer = None

    def close(self):
        self.closed = True
        self.alive = False

    def run(self, cmd, timeout=30, admin=False):
        return 0, "", ""

    def bind_device(self, dev):
        pass


def dev(name, family="linux"):
    d = DeviceInfo()
    d.hostname = name
    d.os_family = family
    d.os_name = "Linux" if family == "linux" else family
    return d


# ---------------------------------------------------------------- A. 管理器
def test_manager():
    print("\n[A] SessionManager")
    m = SessionManager()
    chk(m.active is None and len(m) == 0, "新建是空的", len(m))
    chk(m.session is None and m.device is None, "空时视图为 None", "")

    s1 = FakeSession("10.0.0.1", name="web-01")
    slot = m.add(s1, dev("web-01"))
    chk(len(m) == 1 and m.active_id == slot.sid, "加一台并自动激活", m.active_id)
    chk(m.session is s1 and m.device is slot.device, "视图指向这一台", "")

    s2 = FakeSession("10.0.0.2", name="db-01")
    m.add(s2, dev("db-01"))
    chk(len(m) == 2, "两台都在", len(m))
    chk(m.session is s2, "新加的成为当前", m.session)

    m.set_active(slot.sid)
    chk(m.session is s1, "能切回去", m.session)

    # 同一台重连：替换而不是堆两个
    s1b = FakeSession("10.0.0.1", name="web-01")
    m.add(s1b, dev("web-01"))
    chk(len(m) == 2, "同一台重连不新增", len(m))
    chk(m.session is s1b, "重连后指向新会话", m.session)
    chk(s1.closed, "旧会话被关掉", s1.closed)

    # 关掉当前这台，自动回退到剩下那台
    m.close(m.active_id)
    chk(len(m) == 1, "关掉后剩一台", len(m))
    chk(m.session is s2, "自动切到剩下那台", m.session)

    m.clear()
    chk(len(m) == 0 and m.session is None, "清空后回本机", m.session)
    chk(s2.closed, "清空会关掉会话", s2.closed)


# ---------------------------------------------------------------- B. 界面
def test_ui():
    print("\n[B] 顶部设备标签")
    import tkinter as tk
    import app as APP

    a = APP.ToolboxApp()
    try:
        for _ in range(20):
            a.update()
            a.after(10)

        chk(hasattr(a, "host_tabs") and a.host_tabs.winfo_exists(),
            "标签栏已建", "")
        chk(a.sessions.active_id == "", "启动时没有活动设备", a.sessions.active_id)
        labels = [w for w in a.host_tabs.winfo_children()]
        chk(len(labels) >= 2, "至少有「设备」字样和本机按钮",
            [type(w).__name__ for w in labels])
        chk(a.session is None, "没连设备时 session 是 None", a.session)
        chk(a.device is not None, "本机也有一份 device 兜底", type(a.device).__name__)

        # 塞两台假设备进去
        s1 = FakeSession("10.0.0.1", name="web-01")
        s2 = FakeSession("10.0.0.2", name="db-01")
        sid1 = a.sessions.add(s1, dev("web-01")).sid
        sid2 = a.sessions.add(s2, dev("db-01")).sid
        a._rebuild_devtabs()
        chk(len(a.sessions) == 2, "两台都进去了", len(a.sessions))
        chk(a.sessions.active_id == sid2, "最后加的为当前", a.sessions.active_id)

        # 标签栏里应该有 web-01 与 db-01 的文字
        texts = []

        def walk(w):
            try:
                if isinstance(w, tk.Canvas):
                    texts.append(str(w.text) if hasattr(w, "text") else "")
                for c in w.winfo_children():
                    walk(c)
            except tk.TclError:
                pass

        walk(a.host_tabs)
        joined = " ".join(t for t in texts if t)
        chk("web-01" in joined and "db-01" in joined, "两个标签都在",
            joined[:120])
        chk("本机" in joined, "本机标签也在", joined[:120])

        # 切换
        a._switch_device(sid1)
        chk(a.session is s1, "切到 web-01", a.session)
        chk(a.sessions.active_id == sid1, "活动 id 跟着变", a.sessions.active_id)
        chk(a.device.hostname == "web-01", "device 视图跟着变", a.device.hostname)

        # 断开当前那台 → 自动回到剩下那台
        a._disconnect()
        chk(len(a.sessions) == 1, "只断一台", len(a.sessions))
        chk(a.session is s2, "剩下一台接手", a.session)
        chk(s1.closed and not s2.closed, "断的是对的那台", (s1.closed, s2.closed))

        # 全部断完 → 回本机
        a._disconnect()
        chk(len(a.sessions) == 0, "全断完", len(a.sessions))
        chk(a.session is None, "回到本机", a.session)
        chk(str(a.pill_dev.text) in ("未连接", ""), "状态胶囊复位", a.pill_dev.text)

        # × 关闭（会弹确认，这里直接调 manager 验证等价路径）
        s3 = FakeSession("10.0.0.3", name="nas")
        sid3 = a.sessions.add(s3, dev("nas")).sid
        a._rebuild_devtabs()
        a.sessions.close(sid3)
        a._after_switch()
        chk(a.sessions.active_id == "", "关掉最后一台后没有活动 id",
            a.sessions.active_id)
        chk(not s3.alive, "会话被关", s3.alive)
    finally:
        a.destroy()


def main() -> int:
    try:
        test_manager()
        test_ui()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
