# -*- coding: utf-8 -*-
"""Windows 一次性 UAC 提权（不重启应用）：助手协议 + 接线。

用户原话（2026-10-26 00:25）：
  「勾选管理员权限后，只要点 UAC 通过，而不需要重新启动应用」；
  原症状是「以管理员权限重启后就闪退了」。

闪退真因：`_restart_as_admin()` 用 ShellExecuteW 拉起新进程，老进程还活
几百毫秒；新进程在 main() 里抢单实例互斥量失败 → 叫醒老窗口 → 自己 return。

本测试覆盖：
  1. ShellExecute 返回值翻人话（「点了否」不能说成「策略拦」）
  2. 进程存活判断（只看 SYNCHRONIZE，高完整性进程也判得出）
  3. data/ 定位认 TB_DATA_DIR
  4. 助手协议（真起一个 --elev-serve 子进程，走完整文件队列）：
     正常返回 / 非 0 退出码 / 超时 / 中文不乱码 / 坏请求不弄死循环 / 收摊
  5. LocalSession：接上通道后命令真的走助手、通道没了自动降级、
     没通道时 run_admin 抛 NeedElevation(kind=uac)
  6. 单实例：wait_acquire 等得到 / 等不到
  7. app.py 接线（静态断言，防止哪天又被改回「重启」）

不需要窗口。第 4 段要求当前进程具备管理员权限（否则子进程 serve 会写
error.json 直接退出），非管理员机器上这一段会记「跳过」而不是判红。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

TD = tempfile.mkdtemp(prefix="tb_uac_")
os.environ["TB_DATA_DIR"] = TD          # 别往真实 data 里写提权目录

PASS = FAIL = SKIP = 0
FAILS: list[str] = []

from core import elev                                     # noqa: E402
from core import singleinstance as SI                     # noqa: E402
from core.session import LocalSession, NeedElevation      # noqa: E402


def chk(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(f"{name} {extra}")
    print(("  ok   " if ok else "  FAIL ") + name
          + (f"  [{extra}]" if extra and not ok else ""))


def skip(name, why=""):
    global SKIP
    SKIP += 1
    print(f"  skip {name}" + (f"  [{why}]" if why else ""))


def sec(t):
    print(f"\n-- {t}")


def kill_tree(pid: int) -> None:
    """杀整棵树：Windows 上 python.exe / onefile bootloader 都会再起一层子进程，
    只 kill 顶层那个，真正干活的还在（单实例那轮踩过一次）。"""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


# ============================================================ 1. 报错翻人话
sec("1. ShellExecute 返回值翻成人话")
chk("取消" in elev.shell_err(5), "5 = 用户在 UAC 上点了「否」", elev.shell_err(5))
chk("策略" in elev.shell_err(1223), "1223 = 被组策略拦下", elev.shell_err(1223))
chk("找不到" in elev.shell_err(2), "2 = 找不到程序文件", elev.shell_err(2))
chk("1301" in elev.shell_err(1301) and "失败" in elev.shell_err(1301),
    "未知码带上原始数字（不许只说「提权失败」）", elev.shell_err(1301))
chk(elev.shell_err(5) != elev.shell_err(1223),
    "「点了否」与「被策略拦」不能混成一句话")

# ============================================================ 2. 进程存活
sec("2. 进程存活判断")
chk(elev._pid_alive(os.getpid()), "自己一定活着")
chk(not elev._pid_alive(0), "pid=0：按「不在」处理")
chk(not elev._pid_alive(999999), "不存在的 pid：判为已退出")

# ============================================================ 3. 目录定位
sec("3. data/ 定位认 TB_DATA_DIR")
chk(str(elev.data_dir()) == str(Path(TD).resolve()),
    "data_dir() 跟着 TB_DATA_DIR 走（测试不碰真实 data）", str(elev.data_dir()))
os.environ.pop("TB_DATA_DIR", None)
d0 = elev.data_dir()
os.environ["TB_DATA_DIR"] = TD
chk(str(d0) != str(Path(TD).resolve()), "去掉环境变量就回到程序自己的 data/")

# ============================================================ 4. 助手协议
sec("4. 助手协议（真起一个 --elev-serve 子进程）")

D = Path(TD) / "elev" / "h_test"
D.mkdir(parents=True, exist_ok=True)
code = (f"import sys; sys.path.insert(0, {str(HERE)!r})\n"
        "from core import elev\n"
        f"raise SystemExit(elev.serve({str(D)!r}, 0))\n")
child = subprocess.Popen([sys.executable, "-c", code],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)

ready = None
for _ in range(600):                       # 最多 30 秒
    try:
        ready = json.loads((D / "ready.json").read_text(encoding="utf-8"))
        break
    except (OSError, ValueError):
        time.sleep(0.05)
if not ready or not ready.get("admin"):
    err = ""
    try:
        err = (D / "error.json").read_text(encoding="utf-8")
    except OSError:
        pass
    skip("助手协议（当前进程不是管理员，serve 起不来）", err or "no ready.json")
    try:
        child.kill()
    except OSError:
        pass
else:
    chk(int(ready.get("pid") or 0) > 0 and elev._pid_alive(int(ready["pid"])),
        "ready.json 报的 pid 真的活着", str(ready))
    h = elev.Helper()
    # 用 ready.json 里的 pid 而不是 Popen 的：Windows 上 python.exe 可能是
    # 一层壳（venv 转发、onefile 的 bootloader），真正跑 serve 的是它的子进程。
    h.dir, h.pid = D, int(ready["pid"])

    rc, out, err = h.exec("echo hi", timeout=20)
    chk(rc == 0 and "hi" in out, "普通命令：rc=0 且拿到回显", f"{rc} {out!r} {err!r}")

    rc, out, err = h.exec("echo 中文测试", timeout=20)
    chk("中文测试" in out, "中文回显不解成乱码", repr(out))

    rc, out, err = h.exec('powershell -NoProfile -Command "exit 3"', timeout=20)
    chk(rc == 3, "退出码原样带回（不是一律 0）", str(rc))

    t0 = time.time()
    rc, out, err = h.exec('powershell -NoProfile -Command "Start-Sleep -Seconds 5"',
                          timeout=1)
    chk(rc == 124 and "超时" in err, "超时返回 124 并说明", f"{rc} {err!r}")
    chk(time.time() - t0 < 6, "超时是真的掐掉，不是干等命令跑完",
        f"{time.time() - t0:.1f}s")

    # 坏请求（半截 JSON / 空文件）不能把整个循环带走
    (D / "req-999.json").write_text("{不是 json", encoding="utf-8")
    time.sleep(0.3)
    rc2, out2, _ = h.exec("echo alive", timeout=20)
    chk(rc2 == 0 and "alive" in out2, "坏请求被跳过，助手继续干活",
        f"{rc2} {out2!r}")
    chk(child.poll() is None, "坏请求没把助手进程弄死")

    chk(not list(D.glob("req-*.json")), "命令处理完请求文件被清掉（不堆积）")
    chk(not list(D.glob("res-*.json")), "结果读走后就删（不堆积）")

    # ---- 5. 通道没了要能自己降级
    sec("5. 通道断开：降级而不是继续假装提权")
    s = LocalSession()
    s.is_admin = False                     # 假装普通用户
    elev._h = h                            # 注入全局单例
    s.set_helper(True)
    seed = h._seq
    rc, out, err = s.run("echo via-helper", timeout=20)
    chk(rc == 0 and "via-helper" in out, "接上通道后 run() 走助手", f"{rc} {out!r}")
    chk(h._seq == seed + 1, "确实是发给助手了（请求序号 +1）", f"{h._seq} vs {seed}")
    chk(s.elevated and s.elev_method == "uac", "会话标记为已提权（走 UAC 通道）")

    rc, out, err = s.run_admin("echo admin-path", timeout=20)
    log = ""
    try:
        log = (D / "serve.log").read_text(encoding="utf-8")[-400:]
    except OSError:
        pass
    chk(rc == 0 and "admin-path" in out, "run_admin() 也走同一条通道",
        f"{rc} {out!r} {err!r} seq={h._seq} log={log!r}")

    ok, why = s.verify_root()
    chk(ok, "verify_root 实测通过（IsInRole=True）", why)

    kill_tree(child.pid)
    child.wait(timeout=10)
    time.sleep(0.3)
    chk(not h.alive, "助手被杀后 Helper.alive 为假")
    rc, out, err = s.run("echo after-death", timeout=20)
    chk(rc == 0, "通道没了仍然能跑命令（退回直连）", f"{rc} {err!r}")
    chk(s.use_helper is False and s.elevated is False,
        "降级后会话不再显示「已提权」")
    chk("断开" in (s.elev_msg or ""), "降级原因留在 elev_msg 里", s.elev_msg)

sec("5b. 没有通道时的行为")
s2 = LocalSession()
s2.is_admin = False
try:
    s2.run_admin("echo x")
    chk(False, "没有通道 + 非管理员：应该抛 NeedElevation")
except NeedElevation as e:
    chk(e.kind == "uac", "抛 NeedElevation 且 kind=uac（界面据此弹 UAC）", e.kind)
    chk("不用重启" in str(e) or "无需重启" in str(e),
        "提示语明说「不用重启」，只点一次 UAC 就行", str(e))
ok, why = s2.can_admin()
chk(not ok and "UAC" in why, "can_admin 给的是「弹一次 UAC」", why)

elev._h = None

# ============================================================ 6. 单实例
sec("6. wait_acquire：提权重启时等锁释放")
k1 = "TBUacFree"
chk(SI.wait_acquire(k1, timeout=2.0), "锁没人占：立刻拿到")
SI.release(k1)
k2 = "TBUacBusy"
chk(SI.acquire(k2), "自己先占住 k2")
t0 = time.time()
got = SI.wait_acquire(k2, timeout=0.8)
chk(not got, "锁被占着：等不到就返回 False（不无限等）", str(got))
chk(time.time() - t0 >= 0.7, "确实等了一会儿才放弃", f"{time.time() - t0:.2f}s")
SI.release(k2)

# ============================================================ 7. 接线
sec("7. app.py 接线（不许改回「重启应用」）")
src = (HERE / "app.py").read_text(encoding="utf-8")

chk("--elev-serve" in src and "raise SystemExit(elev.serve" in src,
    "main() 有 --elev-serve 助手分支")
m = re.search(r"_NO_SINGLETON\s*=\s*\(([^)]*)\)", src)
names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
chk("--elev-serve" in names, "助手进程跳过单实例检查（否则自己挡自己）", str(names))

i_wait = src.find("wait_acquire")
i_acq = src.find("got = singleinstance.acquire(key)")
chk(i_wait > 0 and "--elev-restart\" in sys.argv" in src,
    "带 --elev-restart 的新进程会等锁，而不是一撞就退")

i_rel = src.find("singleinstance.release(key)")
i_se = src.find("ShellExecuteW(\n                None, \"runas\"")
chk(i_rel > 0 and i_se > 0 and i_rel < i_se,
    "放锁在 ShellExecuteW **之前**（这是闪退的真因）", f"{i_rel} vs {i_se}")
chk('args.append("--elev-restart")' in src, "重启时给新进程带上标记")

chk("elev.shutdown()" in src, "退出时把管理员助手一起收掉")
chk("self._elevate_now(then=self.do_connect)" in src,
    "连接时改成「点一次 UAC 然后自动继续」")
chk("self._elevate_now(then=redo)" in src,
    "页面操作缺权限时改成「点一次 UAC 然后自动重试」")
chk(src.count("_restart_as_admin") == 1,
    "除了它自己的定义，没有人再调「重启应用」",
    str(src.count("_restart_as_admin")))
chk("def _admin_ready" in src and "def _elevate_now" in src,
    "新入口都在（_admin_ready / _elevate_now）")

# ============================================================ 汇总
try:
    child.kill()
except Exception:  # noqa: BLE001
    pass
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过 =====")
if FAILS:
    for f in FAILS:
        print("   ✗", f)
sys.exit(1 if FAIL else 0)
