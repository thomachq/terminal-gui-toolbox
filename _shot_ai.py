# -*- coding: utf-8 -*-
"""抓一张助手侧栏的图，用来核对观感（不入库，纯本地核对用）。

    python _shot_ai.py          欢迎态
    python _shot_ai.py busy     带消息/工具卡片的会话
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.argv = [sys.argv[0], "--no-probe"]
sys.path.insert(0, str(HERE))

import _shots as SH  # noqa: E402

import app as appmod  # noqa: E402

# 清锁，别让截图停在锁屏上。改的是临时副本 —— 以前直接清真实
# settings.json，跑一次就把用户设的锁屏密码删了，而且不还原。
SH.isolate("tbshot_ai_")
_cfg = Path(appmod.DATA_DIR) / "settings.json"
if _cfg.is_file():
    try:
        d = json.loads(_cfg.read_text(encoding="utf-8"))
        for k in ("lock_on", "lock_pass_enc", "lock_idle_sec"):
            d.pop(k, None)
        _cfg.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass

want = (sys.argv[1] if len(sys.argv) > 1 else "welcome").strip()


def pump(win, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("1180x760+40+40")
win.deiconify()
pump(win, 1.0)
win.ai_dock_open()
pump(win, 1.2)

if want == "busy":
    win._ai_bubble("这台机器的磁盘是不是快满了？顺便看看有没有异常登录。")
    win._ai_reply("先看磁盘水位，再看最近的登录记录 —— 两步都只读。", step=1)
    rec = win._ai_tool_card({"tool": "exec", "risk": "read",
                             "args": {"cmd": "df -h; echo; last -n 5"},
                             "why": "先确认是容量还是 inode 吃紧，再看登录"})
    pump(win, 0.2)
    win._ai_tool_result(
        rec, True,
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/sda1        98G   74G   20G  79% /\n"
        "tmpfs           3.8G     0  3.8G   0% /dev/shm\n"
        "/dev/sdb1       1.8T  1.7T   61G  97% /data\n\n"
        "root  pts/0  10.0.0.5  Mon Oct 20 09:12  still logged in\n"
        "ops   pts/1  10.0.0.9  Sun Oct 19 22:04 - 23:10  (01:06)")
    win._ai_reply("根分区 79% 正常，**/data 已经 97%**（剩 61G）。登录记录里没有陌生来源。"
                  "建议下一步看 /data 下谁最占地方。")
    pump(win, 0.4)

pump(win, 0.8)
win.update_idletasks()
win.update()

hits = SH.find_titled()
print("windows:", hits)
if hits:
    img = SH.capture(hits[-1])
    if img is not None:
        out = HERE / "screenshots"
        out.mkdir(exist_ok=True)
        p = out / f"ai_dock_{want}.png"
        img.save(p)
        print("saved", p, img.size)
    else:
        print("capture 返回空")
try:
    win.destroy()
except Exception:  # noqa: BLE001
    pass
