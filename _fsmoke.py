# -*- coding: utf-8 -*-
"""文件管理（SFTP 双栏 / 上传下载）自测。

没有可用的 SSH 服务器，所以分两层验：

  · 后端：LocalFS 的增删改名 + transfer 的实际字节校验 + 取消
    （拿两个 LocalFS 当两端，走的完全是生产代码路径）
  · 界面：真把 ToolbarApp 起起来，看模式切换、列表填充、
    以及「选一个文件 → 上传 → 目标端真的多出这个文件」

跑法：python _fsmoke.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.argv = ["x"]
import tkinter as tk

from core import files as fl

ok_n = fail_n = 0
FAILS: list[str] = []


def chk(cond, name, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"  \033[32mPASS\033[0m {name}" + (f"  · {detail}" if detail else ""))
    else:
        fail_n += 1
        FAILS.append(name)
        print(f"  \033[31mFAIL\033[0m {name}" + (f"  · {detail}" if detail else ""))


def pump(a, sec=0.12):
    end = time.time() + sec
    while time.time() < end:
        try:
            a.update()
        except tk.TclError:
            return
        time.sleep(0.01)


def wait_for(a, cond, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        pump(a, 0.15)
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


ROOT = Path(tempfile.mkdtemp(prefix="ttb-files-"))
SRC = ROOT / "src"
DST = ROOT / "dst"

print("== 1. 路径处理 ==")
fs = fl.LocalFS(str(SRC))
chk(fs.normalize("~/") == os.path.abspath(os.path.expanduser("~")),
    "~ 能展开成绝对路径", fs.normalize("~/"))
chk(fs.normalize("C:/Users/Thomas") == os.path.normpath("C:/Users/Thomas"),
    "正斜杠归一成反斜杠", fs.normalize("C:/Users/Thomas"))
chk(fs.parent(r"C:\Users\Thomas") == r"C:\Users", "父目录正确")
chk(fs.parent("C:\\") == "C:\\", "盘符根不再往上退", fs.parent("C:\\"))
chk(fs.join(r"C:\Users\Thomas", "a", "b.txt") == r"C:\Users\Thomas\a\b.txt",
    "join 拼路径")

sfs = fl.SFTPFS(None, start="/home/thomas")
chk(sfs.normalize("") == "/home/thomas", "远端正斜杠开始目录")
chk(sfs.normalize("/home//thomas/../thomas/x") == "/home/thomas/x",
    "远端折叠 . / .. / 重复斜杠", sfs.normalize("/home//thomas/../thomas/x"))
chk(sfs.parent("/home/thomas") == "/home", "远端父目录")
chk(sfs.parent("/") == "/", "远端根不再往上")

print("== 2. 本地增删改名 ==")
SRC.mkdir(parents=True, exist_ok=True)
DST.mkdir(parents=True, exist_ok=True)
fs.mkdir(str(SRC / "a"))
chk((SRC / "a").is_dir(), "mkdir 建出目录")
payload = SRC / "a" / "payload.bin"
blob = os.urandom(1024 * 1024 + 137)          # 1MB 出头，跨多个 CHUNK
payload.write_bytes(blob)
big = SRC / "big.log"
big.write_bytes(b"x" * (3 * 1024 * 1024))
fs.rename(str(payload), str(SRC / "a" / "renamed.bin"))
chk((SRC / "a" / "renamed.bin").is_file() and not payload.exists(), "rename 生效")
rows = fs.listdir(str(SRC))
names = [r.name for r in rows]
chk("a" in names and "big.log" in names, "listdir 列出两项", str(names))
chk(rows[0].is_dir, "目录排在文件前面")
chk(any(r.mode.startswith("d") for r in rows if r.name == "a"),
    "目录权限串以 d 开头")
row = [r for r in rows if r.name == "big.log"][0].row()
chk(row["size"].endswith("MB") or row["size"].endswith("KB"), "大小已成人话",
    row["size"])

print("== 3. 传输（LocalFS → LocalFS，真实字节） ==")
prog: list[tuple[int, int]] = []


def on_prog(done, total, _n):
    prog.append((done, total))


def on_file(n):
    prog.append((-1, 0))


st = fl.transfer(fs, str(SRC / "a"), fs, str(DST / "out"),
                 on_progress=on_prog, on_file=on_file)
chk(st["files"] == 1, "传了 1 个文件", str(st))
d1 = DST / "out" / "a" / "renamed.bin"
chk(d1.is_file(), "目标端出现嵌套目录里的文件", str(d1))
chk(sha(d1) == sha(SRC / "a" / "renamed.bin"), "字节完全一致（sha256）")
chk(sum(1 for d, _t in prog if d == -1) == 1, "on_file 触发一次")
dones = [d for d, _t in prog if d >= 0]
chk(dones == sorted(dones) and dones[-1] == len(blob), "进度单调递增到文件大小",
    f"{len(dones)} 次回调")

n, size = fl.collect(fs, str(SRC))
chk(n == 2 and size > 4 * 1024 * 1024, "collect 预扫出 2 个文件 / 4MB+",
    f"{n} 个 · {size} 字节")

print("== 4. 取消 ==")


def on_prog_cancel(done, total, _n):
    raise fl.Cancelled()


try:
    fl.transfer(fs, str(big), fs, str(DST / "cancel"),
                on_progress=on_prog_cancel)
    chk(False, "取消应该抛 Cancelled")
except fl.Cancelled as e:
    chk(str(e) == "已取消", "取消能中断传输", str(e))

print("== 5. WinSCP 命令行 ==")


class _Spec:
    host = "nas.local"
    port = 22
    user = "thomas"
    auth = "key"
    key_path = r"C:\Users\Thomas\.ssh\id_ed25519"


argv = fl.winscp_command(_Spec(), "/volume1/docker")
chk(bool(argv), "能拼出命令行（装了才有）", " ".join(argv[:2]) if argv else "未装 WinSCP")
if argv:
    chk(argv[1].startswith("sftp://thomas@nas.local:22/"), "URL 形如 sftp://user@host:port/",
        argv[1])
    chk(any(a.startswith("-privatekey=") for a in argv), "密钥登录带上 -privatekey")
    chk(not any(a.startswith("-password") for a in argv),
        "命令行里绝不出现明文密码")

print("== 6. 界面构建 ==")
import app as A

a = A.ToolboxApp()
pump(a, 0.6)
# v1.0.7 T1：日志改成底边栏，页面数 16 → 15
# v1.0.27 T1：新增「远程管理」页 → 16
chk(len(a._pages) == 16, "十六个页面都已构建（日志已到底边栏）",
    f"{list(a._pages)}")
a.nav.select("files")
pump(a, 0.4)
chk(a.nav.current == "files", "导航能切到文件管理")
chk(hasattr(a, "_fl_remote_ui") and a._fl_remote_ui["tbl"].winfo_exists(),
    "远程栏表格存在")
chk(hasattr(a, "_fl_local_ui") and a._fl_local_ui["tbl"].winfo_exists(),
    "本地栏表格存在")
chk(a._fl_mode == "remote", "默认是设备单栏", a._fl_mode)
chk(a._fl_remote is None, "未连接时没有远程 FS")
chk(a.badge_fl is not None and "未连接" in a.lbl_fl_side.cget("text"),
    "标题写明未连接", a.lbl_fl_side.cget("text"))
chk(not a.btn_fl_up.enabled if hasattr(a.btn_fl_up, "enabled") else True,
    "上传按钮已禁用")

got = wait_for(a, lambda: len(a._fl_local_ui["tbl"].all_rows()) > 1)
chk(got, "本地栏自动读出目录",
    f"{len(a._fl_local_ui['tbl'].all_rows())} 行")

print("== 7. 三种模式 ==")
for mode in ("local", "both", "remote"):
    a.seg_fl.select(mode)
    pump(a, 0.25)
    chk(a._fl_mode == mode, f"切到 {mode}")
chk(a._fl_card_r.winfo_manager() == "grid" and
    a._fl_card_l.winfo_manager() == "", "单栏模式下另一张卡已移出布局")
a.seg_fl.select("both")
pump(a, 0.25)
chk(a._fl_card_l.winfo_manager() == "grid", "双栏模式下两张卡都在")

# 双栏必须是**左右**（用户 2026-10-25 明确要求，参考 FileZilla）：
# 同一个 row、不同 column 才是并排；上下分栏会变成同一个 column 的 row 0/1。
gi_l = a._fl_card_l.grid_info()
gi_r = a._fl_card_r.grid_info()
chk(int(gi_l.get("row", -1)) == int(gi_r.get("row", -2)),
    "双栏两张卡在同一行（左右并排）",
    f"本机 row={gi_l.get('row')} 设备 row={gi_r.get('row')}")
chk(int(gi_l.get("column", -1)) != int(gi_r.get("column", -1)),
    "双栏两张卡在不同列", f"本机 col={gi_l.get('column')} 设备 col={gi_r.get('column')}")
chk(int(gi_l.get("column", -1)) < int(gi_r.get("column", -1)),
    "本机在左、设备在右（跟 FileZilla 一致）",
    f"本机 col={gi_l.get('column')} 设备 col={gi_r.get('column')}")
pump(a, 0.4)
chk(a._fl_card_l.winfo_width() > 100 and a._fl_card_r.winfo_height() > 100,
    "双栏时两栏都拿到了实际尺寸",
    f"本机 {a._fl_card_l.winfo_width()}x{a._fl_card_l.winfo_height()} "
    f"设备 {a._fl_card_r.winfo_width()}x{a._fl_card_r.winfo_height()}")
# 切回单栏时右列的 weight 必须清掉，否则卡片只占半宽
a.seg_fl.select("remote")
pump(a, 0.4)
chk(int(a._fl_holder.grid_columnconfigure(1)["weight"]) == 0,
    "单栏时右列 weight 已清零",
    a._fl_holder.grid_columnconfigure(1)["weight"])
# 后面的传输用例要在双栏里点本地栏，测完必须切回去
a.seg_fl.select("both")
pump(a, 0.3)

print("== 8. 界面里真的传一次 ==")
src_dir = ROOT / "gui_src"
dst_dir = ROOT / "gui_dst"
src_dir.mkdir(parents=True, exist_ok=True)
dst_dir.mkdir(parents=True, exist_ok=True)
(src_dir / "hello.txt").write_text("hello from local pane\n", encoding="utf-8")

a._fl_remote = fl.LocalFS(str(dst_dir))     # 拿本地 FS 冒充远端
a._fl_remote_ui["var"].set(str(dst_dir))
a._fl_goto("local", str(src_dir))
got = wait_for(a, lambda: any(r["name"].startswith("hello.txt")
                              for r in a._fl_local_ui["tbl"].all_rows()))
chk(got, "本地栏进到测试目录")

tbl = a._fl_local_ui["tbl"]
target_i = None
for i, r in enumerate(tbl.all_rows()):
    if r["name"].startswith("hello.txt"):
        target_i = i
        break
kids = tbl.tree.get_children()
if target_i is not None and target_i < len(kids):
    tbl.tree.selection_set(kids[target_i])
pump(a, 0.15)
sel = tbl.selected()
chk(sel and sel["name"].startswith("hello.txt"), "选中了 hello.txt",
    sel["name"] if sel else "无")

a.fl_upload()
got = wait_for(a, lambda: (dst_dir / "hello.txt").is_file()
                and not a._fl_busy, 20)
chk(got, "上传后目标目录真的多出文件", str(dst_dir / "hello.txt"))
if (dst_dir / "hello.txt").is_file():
    chk((dst_dir / "hello.txt").read_text(encoding="utf-8")
        == "hello from local pane\n", "内容一致")
chk("完成" in a.lbl_fl_prog.cget("text"), "进度条给出完成提示",
    a.lbl_fl_prog.cget("text"))
# _fl_reload 是异步的（后台线程读目录），_fl_busy 变 false 只代表传完了。
# 直接断言列表必然偶尔踩空 —— 等它自己刷出来，超时才算失败。
chk(wait_for(a, lambda: any(r["name"].startswith("hello.txt")
                            for r in a._fl_remote_ui["tbl"].all_rows()), 20),
    "远程栏已自动刷新")

print("== 9. 未连接时的防守 ==")
a._fl_remote = None
a._fl_remote_ui["tbl"].set_rows([])
a.fl_upload()
pump(a, 0.2)
chk(a._fl_jobs == [] and not a._fl_busy, "没有远程会话时不会排队任务")
a.fl_download()
pump(a, 0.2)
chk(a._fl_jobs == [], "没有远程会话时下载也不排队")

print("== 10. 主题切换后仍在 ==")
a.toggle_theme() if hasattr(a, "toggle_theme") else None
a._apply_theme() if hasattr(a, "_apply_theme") else None
pump(a, 0.3)
chk(a._fl_local_ui["tbl"].winfo_exists(), "切主题后表格还在")

try:
    a.destroy()
except Exception:  # noqa: BLE001
    pass
shutil.rmtree(ROOT, ignore_errors=True)

print()
print(f"结果：{ok_n} 通过 / {fail_n} 失败")
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  ·", f)
sys.exit(0 if fail_n == 0 else 1)
