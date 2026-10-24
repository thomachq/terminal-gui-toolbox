# -*- coding: utf-8 -*-
"""抓一张「AI 活动流」的图：左边终端在滚 AI 的命令与输出，右边是助手侧栏。

    python _shot_aistream.py

纯本地核对用，不入库。
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
from core import aitrace  # noqa: E402

# 清锁，别让截图停在锁屏上。改的是临时副本 —— 以前直接清真实
# settings.json，跑一次就把用户设的锁屏密码删了，而且不还原。
SH.isolate("tbshot_aistream_")
_cfg = Path(appmod.DATA_DIR) / "settings.json"
if _cfg.is_file():
    try:
        d = json.loads(_cfg.read_text(encoding="utf-8"))
        for k in ("lock_on", "lock_pass_enc", "lock_idle_sec"):
            d.pop(k, None)
        _cfg.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def pump(win, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        try:
            win.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.01)


win = appmod.ToolboxApp()
win.geometry("1520x880+20+20")
win.deiconify()
pump(win, 1.0)
win.ai_dock_open()
pump(win, 1.0)

goal = "这台机器的 /data 快满了，帮我查一下谁最占地方，能清的先清掉。"
win._ai_bubble(goal)

win.ai_trace(aitrace.banner() + aitrace.note("任务：" + goal, tone="ok"),
             ensure=True)
win.ai_trace(aitrace.api_request(
    base="https://api.deepseek.com/v1", model="deepseek-v4-flash",
    messages=[{"role": "system", "content": "你是 TerminalToolbox 的运维智能体，"
                                            "只能通过 exec 工具在远端执行命令…"},
              {"role": "user", "content": "设备：web-01 · ssh · Ubuntu 22.04\n"
                                          "任务：" + goal}]))
win.ai_trace(aitrace.api_response(
    ok=True, ms=1240, tokens={"total_tokens": 812},
    text='{"analysis":"先看 /data 各目录占用，再决定清哪个。",'
         '"tool":"exec","args":{"cmd":"du -sh /data/* 2>/dev/null | sort -h | tail -5"},'
         '"risk":"read"}'))
win.ai_trace(aitrace.cmd(host="web-01",
                         command="du -sh /data/* 2>/dev/null | sort -h | tail -5"))
win.ai_trace([("1.7G\t/data/backup\n"
               "12G\t/data/docker\n"
               "38G\t/data/logs\n"
               "41G\t/data/mysql\n", "out")] + aitrace.result(rc=0, ms=2310,
                                                              out="x\ny"))
win.ai_trace(aitrace.cmd(host="web-01",
                         command="find /data/logs -type f -mtime +30 | wc -l"))
win.ai_trace([("1284\n", "out")] + aitrace.result(rc=0, ms=180, out="1284\n"))
win.ai_trace(aitrace.cmd(host="web-01",
                         command="find /data/logs -type f -mtime +30 -delete",
                         risk="write"))
win.ai_trace([("find: '/data/logs/archive': Permission denied\n", "err")]
             + aitrace.result(rc=1, ms=640, out="", err="x"))
win.ai_trace(aitrace.note("等待确认 · rm -rf /data/logs/2024", tone="warn"))

replies = [
    ("/data 一共 92G：mysql 41G、logs 38G 是大头。logs 里 1284 个文件超过 30 天，"
     "我试着删了一批，但 `archive/` 子目录没有权限。", 1),
    ("要么用带 sudo 的会话重跑，要么先只清 `logs/*.log` 不动 archive。你选一个。", 2),
]
for text, step in replies:
    win._ai_reply(text, step=step)
win._ai_tool_card({"tool": "exec", "risk": "write",
                   "args": {"cmd": "find /data/logs -type f -mtime +30 -delete"},
                   "why": "释放 30 天前的日志"})
pump(win, 0.8)
win.update_idletasks()
win.update()

print("dock at capture:", win.ai_dock.winfo_ismapped(), win.ai_dock.winfo_width())
hits = SH.find_titled()
print("windows:", hits)
if hits:
    img = SH.capture(hits[-1])
    if img is not None:
        out = HERE / "screenshots"
        out.mkdir(exist_ok=True)
        p = out / "ai_stream.png"
        img.save(p)
        print("saved", p, img.size)
    else:
        print("capture 返回空")
try:
    win.destroy()
except Exception:  # noqa: BLE001
    pass
