# -*- coding: utf-8 -*-
"""打包后 exe 的验收：起窗 / 截图 / 数据落点 / 证书控制台子进程。

    python _texe.py            全部跑一遍
    python _texe.py --keep     截图留下来看

判定全部 PASS 才算通过。
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
EXE = HERE / "TerminalToolbox.exe"
OUT = HERE / "screenshots"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    ctypes.windll.user32.SetProcessDPIAware()

u = ctypes.windll.user32
g = ctypes.windll.gdi32

RESULTS = []


def chk(ok, name, detail=""):
    RESULTS.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    return bool(ok)


def find_titled(title):
    hits = []

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.strip() == title:
                    hits.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return hits


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wt.DWORD * 3)]


def capture(hwnd):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)
    bi = BINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if not ok:
        return None
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def wait_window(title, before, limit=30.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        fresh = [h for h in find_titled(title) if h not in before]
        if fresh:
            return fresh[0]
        time.sleep(0.25)
    return None


def close(p, wait=12.0):
    """关掉一个测试实例，连它的子进程一起收干净。

    onefile 打包出来是「bootloader + 子进程」两条同名进程，**窗口和单实例锁都
    在子进程里**。只 terminate bootloader，子进程会带着窗口活下来 —— 2026-10-25
    实测：terminate 之后 20 秒，子进程还在、窗口还在、互斥量还被它握着。

    后果有两层：
      1. 后面想复用同一个 --instance-key 会被正确拦下（看着像"测试失败"，其实
         是上一个实例压根没关）；
      2. 每跑一遍就攒下一整套活实例，任务栏里一排托盘图标 —— 用户 2026-10-25
         报的就是这个。

    taskkill /T 按父进程连根杀才收得干净，而且必须赶在 bootloader 还活着的时候
    调：它一死，pid 就没了，子进程再也认不回来。
    """
    if p is None or p.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        p.wait(timeout=wait)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def main():
    chk(EXE.is_file(), "exe 存在", EXE.name)
    if not EXE.is_file():
        return 1
    chk(EXE.stat().st_size > 10 * 2 ** 20, "exe 体积合理",
        f"{EXE.stat().st_size / 2**20:.1f} MB")

    # ---- 1. --smoke 能自己开自己关
    t0 = time.time()
    p = subprocess.run([str(EXE), "--smoke"], cwd=str(HERE), timeout=60)
    dt = time.time() - t0
    chk(p.returncode == 0, "--smoke 正常退出", f"rc={p.returncode}  {dt:.1f}s")
    chk(dt < 25, "--smoke 启动不慢", f"{dt:.1f}s")

    # ---- 2. data 目录落在 exe 同级，不在 %TEMP%
    chk((HERE / "data").is_dir(), "data 在 exe 同级")
    chk((HERE / "data" / ".vpncert-key").is_file(), "便携密钥在 data 下")
    chk((HERE / "data" / "profiles.dat").is_file(), "档案密文在 data 下")
    tmpmei = list(Path(tempfile.gettempdir()).glob("_MEI*"))
    stray = [d for d in tmpmei if (d / "data" / "profiles.dat").is_file()]
    chk(not stray, "没有数据落进临时解压目录", f"{len(stray)} 个")

    # ---- 3. 起主窗口 + 截图
    before = set(find_titled("Terminal GUI Toolbox"))
    proc = subprocess.Popen([str(EXE), "--page", "tools",
                                "--instance-key", "exe1"], cwd=str(HERE))
    try:
        hwnd = wait_window("Terminal GUI Toolbox", before)
        chk(hwnd is not None, "exe 起出主窗口")
        if hwnd:
            time.sleep(2.5)
            img = capture(hwnd)
            chk(img is not None, "主窗口可截图")
            if img:
                OUT.mkdir(exist_ok=True)
                dst = OUT / "exe_tools.png"
                img.save(dst)
                ncolors = img.getcolors(maxcolors=1 << 22)
                chk(len(ncolors or []) > 50, "界面不是空白",
                    f"{img.size} / {len(ncolors or [])} 色")
                # 底部状态栏区域不应该和页面底色一样（说明画上去了）
                bottom = img.crop((0, img.height - 90, img.width, img.height - 20))
                nb = bottom.getcolors(maxcolors=1 << 22)
                chk(len(nb or []) > 5, "底部状态栏有内容",
                    f"{len(nb or [])} 色")
    finally:
        close(proc)

    # ---- 4. --page vpn：证书控制台现在是内嵌页，不再另起子进程
    before2 = set(find_titled("Terminal GUI Toolbox"))
    p2 = subprocess.Popen([str(EXE), "--page", "vpn",
                                "--instance-key", "exe2"], cwd=str(HERE))
    try:
        hwnd2 = wait_window("Terminal GUI Toolbox", before2)
        chk(hwnd2 is not None, "--page vpn 起出主窗口")
        if hwnd2:
            time.sleep(3.2)
            img2 = capture(hwnd2)
            chk(img2 is not None, "VPN 页可截图")
            if img2:
                OUT.mkdir(exist_ok=True)
                img2.save(OUT / "exe_vpn.png")
                chk(len(img2.getcolors(maxcolors=1 << 22) or []) > 50,
                    "VPN 页不是空白")
            # 内嵌 = 只有一个顶层窗口，没有第二条 Client Cert Console
            extra = [h for h in find_titled("Client Cert Console")]
            chk(not extra, "没有多余的证书控制台窗口", f"{len(extra)} 个")
    finally:
        close(p2)

    # ---- 5. --page web：新加的 Web 服务页（core.webstack / pages.web_page 有没有打进去）
    before3 = set(find_titled("Terminal GUI Toolbox"))
    p3 = subprocess.Popen([str(EXE), "--page", "web",
                                "--instance-key", "exe3"], cwd=str(HERE))
    try:
        hwnd3 = wait_window("Terminal GUI Toolbox", before3)
        chk(hwnd3 is not None, "--page web 起出主窗口")
        if hwnd3:
            time.sleep(4.0)
            img3 = capture(hwnd3)
            chk(img3 is not None, "Web 服务页可截图")
            if img3:
                OUT.mkdir(exist_ok=True)
                img3.save(OUT / "exe_web.png")
                chk(len(img3.getcolors(maxcolors=1 << 22) or []) > 50,
                    "Web 服务页不是空白")
    finally:
        close(p3)

    # ---- 6. --page ai：AI 运维页（core.aiops / ai_skills / ai_skills_builtin 有没有打进去）
    # 技能正文是打进 exe 的常量，这一页能起来且不空白，说明那 8 份 SKILL.md 也在包里。
    before4 = set(find_titled("Terminal GUI Toolbox"))
    p4 = subprocess.Popen([str(EXE), "--page", "ai",
                                "--instance-key", "exe4"], cwd=str(HERE))
    try:
        hwnd4 = wait_window("Terminal GUI Toolbox", before4)
        chk(hwnd4 is not None, "--page ai 起出主窗口")
        if hwnd4:
            time.sleep(4.0)
            img4 = capture(hwnd4)
            chk(img4 is not None, "AI 运维页可截图")
            if img4:
                OUT.mkdir(exist_ok=True)
                img4.save(OUT / "exe_ai.png")
                chk(len(img4.getcolors(maxcolors=1 << 22) or []) > 50,
                    "AI 运维页不是空白")
    finally:
        close(p4)

    # ---- 7. 点 × 必须真退出（2026-10-24 修的那个 bug）
    # 以前 close_to_tray 默认 True，WM_CLOSE 只 withdraw，进程留在后台；Win11 又把
    # 托盘图标塞进「︿」溢出区，用户既看不到图标也只能去任务管理器强杀。
    # 这里直接给主窗口发 WM_CLOSE，等价于点 ×，然后看进程有没有自己走掉。
    before5 = set(find_titled("Terminal GUI Toolbox"))
    p5 = subprocess.Popen([str(EXE), "--page", "tools",
                                "--instance-key", "exe5"], cwd=str(HERE))
    try:
        hwnd5 = wait_window("Terminal GUI Toolbox", before5)
        chk(hwnd5 is not None, "退出检查：主窗口起出来了")
        if hwnd5:
            time.sleep(1.5)
            u.PostMessageW(hwnd5, 0x0010, 0, 0)      # WM_CLOSE
            gone = False
            end = time.time() + 20.0
            while time.time() < end:
                if p5.poll() is not None:
                    gone = True
                    break
                time.sleep(0.3)
            chk(gone, "点 × 后进程自己退出（不再残留后台）",
                "rc=%r" % p5.poll())
    finally:
        # 这一段本来就是「点 × 该自己退出」，poll 只反映 bootloader；万一子进程
        # 还赖着，close() 会按进程树连根收掉，不给任务栏留托盘图标。
        close(p5)

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print("\n===== %d 项，通过 %d，失败 %d =====" % (len(RESULTS), npass, nfail))
    for ok, name, detail in RESULTS:
        if not ok:
            print("  FAIL · %s   %s" % (name, detail))
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
