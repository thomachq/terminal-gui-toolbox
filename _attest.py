# -*- coding: utf-8 -*-
"""Terminal GUI Toolbox · 助手输入区附件与两级菜单自测。

覆盖三块新能力：

  1. core/clipfile.py —— 不依赖 PIL 的剪贴板读图/读文件（PNG 手工编码、DIB 解析）
  2. 附件生命周期 —— 增删、去重、上限、注入上下文、Ctrl+V 两条分支
  3. ＋ 一级菜单 + 二级浮层 —— 技能 / 连接器 / 模式 / 引用文件，同一时刻只留一个

跑法：  python _attest.py [--dark]
判定：  全部 PASS 才算通过。
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import time
import traceback
import zlib
from pathlib import Path

RESULTS: list[tuple[bool, str, str]] = []


def chk(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + detail) if detail else ""))
    return bool(ok)


def pump(app, sec: float = 0.3) -> None:
    t0 = time.time()
    while time.time() - t0 < sec:
        app.update()
        time.sleep(0.01)


# ---------------------------------------------------------------- PNG 自检
def png_probe(data: bytes) -> dict:
    """按 PNG 规范解一遍：块长度、CRC、IHDR 字段、IDAT 解压结果。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "magic 不对"
    i = 8
    out: dict = {"chunks": [], "idat": b""}
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        payload = data[i + 8:i + 8 + n]
        crc = struct.unpack(">I", data[i + 8 + n:i + 12 + n])[0]
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"{tag} CRC 错"
        out["chunks"].append(tag.decode("ascii"))
        if tag == b"IHDR":
            (out["w"], out["h"], out["depth"], out["ctype"],
             comp, filt, inter) = struct.unpack(">IIBBBBB", payload)
            out["interlace"] = inter
        elif tag == b"IDAT":
            out["idat"] += payload
        i += 12 + n
    out["raw"] = zlib.decompress(out["idat"]) if out["idat"] else b""
    return out


def test_png() -> None:
    # 2x2 纯色 RGBA
    r = bytes([255, 0, 0, 255]) * 2
    g = bytes([0, 255, 0, 255]) * 2
    data = clipfile.png_bytes(2, 2, [r, g])
    info = png_probe(data)
    chk(info["chunks"][:3] == ["IHDR", "IDAT", "IEND"], "PNG 块顺序",
        "/".join(info["chunks"]))
    chk(info["w"] == 2 and info["h"] == 2, "PNG IHDR 尺寸",
        f"{info['w']}×{info['h']}")
    chk(info["depth"] == 8 and info["ctype"] == 6, "PNG 8bit RGBA",
        f"depth={info['depth']} ctype={info['ctype']}")
    chk(len(info["raw"]) == 2 * (1 + 2 * 4), "PNG 解压后行数",
        f"{len(info['raw'])} 字节 / 期望 {2 * 9}")
    chk(info["raw"][0] == 0 and info["raw"][9] == 0, "每行 filter=None")
    chk(info["raw"][1:9] == r, "第一行像素原样")

    # RGB（alpha=False）走 ctype=2
    data2 = clipfile.png_bytes(1, 1, [b"\x01\x02\x03"], alpha=False)
    info2 = png_probe(data2)
    chk(info2["ctype"] == 2 and info2["raw"] == b"\x00\x01\x02\x03",
        "PNG 无 alpha 走 ctype=2")

    # 行长不对必须报错，不能默默写出坏图
    try:
        clipfile.png_bytes(3, 1, [b"\x00\x00\x00"])
        chk(False, "PNG 行长不符时报错")
    except ValueError as e:
        chk("行长度不对" in str(e), "PNG 行长不符时报错", str(e)[:40])


# ---------------------------------------------------------------- DIB 自检
def dib(width: int, height: int, bpp: int, pixels: bytes,
        comp: int = 0, clr_used: int = 0) -> bytes:
    """拼一个 BITMAPINFOHEADER + 像素。pixels 按**文件顺序**给（bottom-up 自己排）。"""
    head = struct.pack("<IiiHHIIiiII", 40, width, height, 1, bpp, comp,
                       len(pixels), 2835, 2835, clr_used, 0)
    return head + pixels


def test_dib() -> None:
    # 24bpp · 2x2 · bottom-up（height 为正）
    # 上排：红(00 00 FF) 绿(00 FF 00)；下排：蓝(FF 00 00) 白(FF FF FF)，stride=8
    bottom = b"\xff\x00\x00" + b"\xff\xff\xff" + b"\x00\x00"
    top = b"\x00\x00\xff" + b"\x00\xff\x00" + b"\x00\x00"
    got = clipfile.parse_dib(dib(2, 2, 24, bottom + top))
    chk(got is not None, "DIB 24bpp 能解析")
    if got:
        w, h, rows = got
        chk(w == 2 and h == 2, "DIB 尺寸", f"{w}×{h}")
        chk(rows[0][:4] == bytes([255, 0, 0, 255]), "DIB 首行是红（顺序已翻正）",
            rows[0][:4].hex())
        chk(rows[1][:4] == bytes([0, 0, 255, 255]), "DIB 末行是蓝",
            rows[1][:4].hex())
        chk(rows[0][4:8] == bytes([0, 255, 0, 255]), "DIB 第二像素是绿",
            rows[0][4:8].hex())

    # 32bpp · alpha 全 0（截图工具的常态）必须补成不透明
    px = b"\x11\x22\x33\x00"
    got32 = clipfile.parse_dib(dib(1, 1, 32, px))
    chk(got32 is not None and got32[2][0][3] == 255,
        "DIB 32bpp 全零 alpha 补成 255",
        got32[2][0].hex() if got32 else "None")
    chk(got32 is not None and got32[2][0][:3] == bytes([0x33, 0x22, 0x11]),
        "DIB 32bpp BGR 已转 RGB",
        got32[2][0][:3].hex() if got32 else "None")

    # 真带 alpha 的图不能被我们改掉
    px2 = b"\x11\x22\x33\x80"
    got32b = clipfile.parse_dib(dib(1, 1, 32, px2))
    chk(got32b is not None and got32b[2][0][3] == 0x80,
        "DIB 真有 alpha 时保留原值",
        str(got32b[2][0][3]) if got32b else "None")

    # top-down（height 为负）
    td = clipfile.parse_dib(dib(1, -1, 24, b"\x00\x00\xff" + b"\x00\x00"))
    chk(td is not None and td[2][0][:3] == bytes([255, 0, 0]),
        "DIB top-down 不翻行")

    # 8bpp 调色板图直接放弃
    chk(clipfile.parse_dib(dib(1, 1, 8, b"\x00")) is None,
        "DIB 8bpp 拒绝")
    # 压缩方式不认识（BI_JPEG=4）
    chk(clipfile.parse_dib(dib(1, 1, 24, b"\x00" * 4, comp=4)) is None,
        "DIB 未知压缩方式拒绝")
    # 头都不够
    chk(clipfile.parse_dib(b"\x28" * 10) is None, "DIB 头部过短拒绝")

    # 缓冲区比声明的短：能读几行算几行，不许整张放弃
    cut = clipfile.parse_dib(dib(2, 8, 24, bottom + top))
    chk(cut is not None and cut[1] == 2, "DIB 短缓冲区按可用行裁剪",
        f"实读 {cut[1] if cut else 0} 行")


def test_text() -> None:
    chk(clipfile.looks_text("你好，世界".encode()), "中文判为文本")
    chk(clipfile.looks_text(b"hello"), "ASCII 判为文本")
    chk(not clipfile.looks_text(b"\x89PNG\r\n"), "二进制判为非文本")
    chk(not clipfile.looks_text(b"abc\x00def"), "含 NUL 判为非文本")


def test_grab_guard() -> None:
    """grab() 在非 Windows 上要给人话，不能抛异常。"""
    old = clipfile.sys.platform
    try:
        clipfile.sys.platform = "linux"
        paths, msg = clipfile.grab(Path(tempfile.gettempdir()) / "att-x")
        chk(paths == [] and "Windows" in msg, "非 Windows 上 grab 返回人话", msg)
    finally:
        clipfile.sys.platform = old


# ---------------------------------------------------------------- 附件
def test_attach(app) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="attest-"))
    txt = tmp / "note.txt"
    txt.write_text("第一行\n第二行 秘密=A1B2\n", encoding="utf-8")
    img = tmp / "shot.png"
    img.write_bytes(clipfile.png_bytes(3, 1, [bytes([9, 9, 9, 255]) * 3]))
    blob = tmp / "bin.dat"
    blob.write_bytes(bytes(range(256)) * 200)          # 含 NUL，且 > 32KB
    big = tmp / "big.txt"
    big.write_text("a" * (64 * 1024), encoding="utf-8")  # 纯文本但超限

    app._ai_attach_clear()
    chk(app._ai_attach == [], "初始附件为空")
    bar = app._ai_attach_bar
    chk(not bar.winfo_ismapped() or bar.winfo_height() <= 1,
        "没附件时附件条收起")

    n = app._ai_attach_paths([str(txt), str(img)])
    chk(n == 2 and len(app._ai_attach) == 2, "加两个附件", f"n={n}")
    app.update_idletasks()
    chk(bar.winfo_ismapped(), "有附件时附件条出现",
        f"mapped={bar.winfo_ismapped()} h={bar.winfo_height()}")
    chk(len(bar.winfo_children()) == 2, "附件条画出两个 chip",
        f"{len(bar.winfo_children())} 个")

    # 去重（大小写不敏感：Windows 路径同一份）
    n2 = app._ai_attach_paths([str(txt)])
    chk(n2 == 0 and len(app._ai_attach) == 2, "重复路径不重复加", f"n={n2}")

    # 不存在的路径跳过
    n3 = app._ai_attach_paths([str(tmp / "nope.txt")])
    chk(n3 == 0, "不存在的文件被跳过", f"n={n3}")

    # 名额上限
    app._ai_attach_clear()
    many = []
    for i in range(pages_module.MAX_ATTACH + 4):
        p = tmp / f"f{i}.txt"
        p.write_text(str(i), encoding="utf-8")
        many.append(str(p))
    n4 = app._ai_attach_paths(many)
    chk(n4 == pages_module.MAX_ATTACH
        and len(app._ai_attach) == pages_module.MAX_ATTACH,
        f"最多带 {pages_module.MAX_ATTACH} 个附件", f"n={n4}")

    # 单个移除
    first = app._ai_attach[0]["key"]
    app._ai_attach_remove(first)
    chk(len(app._ai_attach) == pages_module.MAX_ATTACH - 1, "单个附件可移除",
        f"{len(app._ai_attach)} 个")

    # ---------------------------------------------------------- 上下文注入
    app._ai_attach_clear()
    app._ai_attach_paths([str(txt), str(img), str(blob), str(big)])
    block = app._ai_attach_text()
    chk("note.txt" in block, "上下文里有文本文件路径")
    chk("第一行" in block and "秘密=A1B2" in block, "文本内容被内联")
    chk("shot.png" in block and "3×1" in block, "图片报出尺寸",
        " | ".join([ln for ln in block.splitlines() if "shot.png" in ln][:1]))
    chk("不要假装看过图里" in block, "图片明令不要编内容")
    chk(block.count("内容未内联") == 2, "二进制与超限文件都不内联",
        f"{block.count('内容未内联')} 处")
    chk("a" * 100 not in block, "超限文本没有被塞进去")

    app._ai_attach_clear()
    chk(app._ai_attach_text() == "", "没有附件时上下文字符串为空")

    # ---------------------------------------------------------- Ctrl+V
    app._ai_attach_clear()
    try:
        app.clipboard_clear()
        app.clipboard_append("纯文本")
        app.update()
    except Exception:  # noqa: BLE001
        pass
    r = app._ai_paste()
    chk(r is None, "剪贴板有文本时交给 Tk（返回 None）", repr(r))
    chk(app._ai_attach == [], "文本粘贴不产生附件")

    # 非文本分支：清空剪贴板 → clipboard_get 抛 TclError，走 clipfile.grab
    app.clipboard_clear()
    app.update()
    fake = tmp / "clip-paste.png"
    fake.write_bytes(clipfile.png_bytes(1, 1, [b"\xff\x00\x00\xff"]))
    real_grab = pages_module.clipfile.grab
    pages_module.clipfile.grab = lambda dest, max_files=8: (
        [str(fake)], "从剪贴板存了一张图（1×1）")
    try:
        r2 = app._ai_paste()
    finally:
        pages_module.clipfile.grab = real_grab
    chk(r2 == "break", "无文本时我们接管 Ctrl+V", repr(r2))
    chk(len(app._ai_attach) == 1 and app._ai_attach[0]["source"] == "粘贴",
        "粘贴的图进了附件",
        app._ai_attach[0]["name"] if app._ai_attach else "空")
    chk(app._ai_attach and app._ai_attach[0]["image"], "粘贴的文件被认成图片")

    # grab 什么都没读到时也要 return "break"（别让 Tk 再插一遍）
    app._ai_attach_clear()
    app.clipboard_clear()
    app.update()
    pages_module.clipfile.grab = lambda dest, max_files=8: ([], "剪贴板里没有文件或图片")
    try:
        r3 = app._ai_paste()
    finally:
        pages_module.clipfile.grab = real_grab
    chk(r3 == "break" and app._ai_attach == [], "剪贴板空着时不报错也不加附件",
        repr(r3))

    # ---------------------------------------------------------- 引用对话中的文件
    app._ai_files_seen.clear()
    app._ai_scan_files(f"输出如下：{txt}\n还有一行无关的 {tmp / 'ghost.txt'}")
    chk(str(txt) in app._ai_files_seen, "从输出里认出真实文件")
    chk(str(tmp / "ghost.txt") not in app._ai_files_seen,
        "不存在的路径不进引用列表（远端路径本地打不开）")
    app._ai_attach_paths([str(txt)])
    listed = [p for p, _ in app._ai_session_files()]
    chk(str(txt) not in listed, "已挂成附件的不在引用列表里重复出现",
        f"{len(listed)} 条")


# ---------------------------------------------------------------- 菜单
def menu_rows(top) -> list[tuple[str, bool]]:
    """一级浮层里的一行 = 一个有 text 子标签的 Frame。返回 [(名字, 有›)]。"""
    shell = top.winfo_children()[0]
    out = []
    for w in shell.winfo_children():
        try:
            kids = [c for c in w.winfo_children() if c.winfo_class() == "Label"]
        except Exception:  # noqa: BLE001
            continue
        if len(kids) < 2:
            continue
        texts = [str(k.cget("text")) for k in kids]
        name = ""
        for t in texts:
            if t and t not in ("›",) and len(t) > 1:
                name = t
                break
        out.append((name, "›" in texts))
    return out


def test_menu(app) -> None:
    top = app._ai_plus_menu()
    chk(top is not None and top.winfo_exists(), "＋ 弹出菜单")
    rows = menu_rows(top)
    names = [n for n, _ in rows]
    chk(len(rows) == 7, "一级菜单 7 项", " / ".join(names))
    for want in ("添加文件", "引用对话中的文件", "模式", "技能", "连接器",
                 "模型管理", "清空会话"):
        chk(want in names, f"一级菜单有「{want}」")
    arrows = [n for n, a in rows if a]
    chk(len(arrows) == 4, "四个分组带 ›", " / ".join(arrows))
    chk("添加文件" not in arrows and "清空会话" not in arrows,
        "直执行项不带 ›")

    # 每个分组都能开出二级
    for kind, label in (("skill", "技能"), ("mode", "模式"),
                        ("conn", "连接器"), ("quote", "引用对话中的文件")):
        sub = app._ai_open_sub(kind)
        child = getattr(top, "_child", None)
        ok = child is not None and child.winfo_exists()
        n = len(child.winfo_children()) if ok else 0
        chk(ok and n >= 1, f"二级「{label}」能打开", f"{n} 个直接子控件")
        # 同一时刻只能有一个二级
        for w in (top.winfo_children() if not ok else []):
            pass
        alive = [c for c in top.winfo_children()
                 if isinstance(c, __import__("tkinter").Toplevel)]
        chk(len(alive) == 1, f"开「{label}」后只剩一个二级浮层",
            f"{len(alive)} 个")

    chk(getattr(app, "_ai_plus_top", None) is top, "一级浮层有登记（供关闭用）")
    app._ai_close_menu()
    app.update()
    chk(not top.winfo_exists(), "关闭时一级与二级一起收掉")

    # 反复打开只留一个二级（点来点去不能叠成一片）
    top2 = app._ai_plus_menu()
    app._ai_open_sub("skill")
    c1 = getattr(top2, "_child", None)
    app._ai_open_sub("conn")
    c2 = getattr(top2, "_child", None)
    chk(c1 is not None and c2 is not None and str(c1) != str(c2),
        "换分组会换掉二级浮层")
    gone = True
    try:
        gone = not c1.winfo_exists()
    except Exception:  # noqa: BLE001
        gone = True
    chk(gone, "旧的二级浮层被销毁")
    app._ai_close_menu()
    app.update()

    # ---- 点别处要收：靠主窗口的 Button-1 全局绑定，不靠 FocusOut
    top3 = app._ai_plus_menu()
    app.update()
    chk(top3 is not None and top3.winfo_exists(), "再开一次一级菜单")
    app.event_generate("<Button-1>", x=5, y=5)
    app.update()
    closed = True
    try:
        closed = not top3.winfo_exists()
    except Exception:  # noqa: BLE001
        closed = True
    chk(closed, "点主窗口别处，菜单收起")

    # ---- 再点一次 ＋ 要收起（不是「点了没反应」）
    top4 = app._ai_plus_menu()
    app.update()
    chk(top4 is not None and top4.winfo_exists(), "＋ 能再打开")
    again = app._ai_plus_menu()
    app.update()
    chk(again is None, "第二次点 ＋ 不开新的")
    still = True
    try:
        still = not top4.winfo_exists()
    except Exception:  # noqa: BLE001
        still = True
    chk(still, "第二次点 ＋ 把菜单收起来")


def test_model_menu(app) -> None:
    top = app._ai_model_menu()
    chk(top is not None and top.winfo_exists(), "模型浮层能打开")
    texts: list[str] = []

    def walk(w, d=0):
        if d > 8:
            return
        for c in w.winfo_children():
            try:
                if c.winfo_class() == "Label":
                    texts.append(str(c.cget("text")))
            except Exception:  # noqa: BLE001
                pass
            walk(c, d + 1)

    walk(top)
    chk(any("配置自定义模型" in t for t in texts), "模型浮层底部有「配置自定义模型」")
    models = app.ai_cfg.models()
    chk(sum(1 for t in texts if t in [m.get("name") for m in models])
        >= min(len(models), 1), "模型行数不为零", f"{len(models)} 个档案")
    has_entry = False

    def seek(w, d=0):
        nonlocal has_entry
        if d > 8:
            return
        for c in w.winfo_children():
            if c.winfo_class() == "Entry":
                has_entry = True
            seek(c, d + 1)

    seek(top)
    chk(has_entry == (len(models) > 7),
        "搜索框只在模型多于 7 个时出现",
        f"模型 {len(models)} 个 / 搜索框 {'有' if has_entry else '无'}")
    top.destroy()


def test_clear(app) -> None:
    """清空会话：附件、引用列表、消息流一起复位。"""
    app._ai_attach_paths([str(Path(tempfile.gettempdir()) / "attest-clear.txt")])
    (Path(tempfile.gettempdir()) / "attest-clear.txt").write_text(
        "x", encoding="utf-8")
    app._ai_attach_paths([str(Path(tempfile.gettempdir()) / "attest-clear.txt")])
    app._ai_files_seen["x"] = "对话"
    real = pages_module.forms.confirm
    pages_module.forms.confirm = lambda *a, **k: True
    try:
        app.ai_dock_clear()
    finally:
        pages_module.forms.confirm = real
    pump(app, 0.2)
    chk(app._ai_attach == [], "清空会话后附件清空")
    chk(app._ai_files_seen == {}, "清空会话后引用列表清空")
    chk(app._ai_tool_cards == [], "清空会话后工具卡片清空")


def test_sessions(app) -> None:
    """多会话页签：新建 / 切换 / 关闭 / 记录留档。"""
    store = app._ai_sess_store
    made: list[str] = []
    # 这个测试会往真实 data/agent_sessions 里写东西（改名、记录）。
    # 先把整个目录拍个快照，跑完原样放回去 —— 自测不该污染用户的会话。
    snap: dict = {}
    try:
        store.ensure()
        snap = {p.name: p.read_bytes() for p in store.root.glob("*.json")}
    except OSError:
        snap = {}
    try:
        n0 = len(app._ai_sess_order)
        chk(n0 >= 1, "启动后至少有一个会话页签", f"{n0} 个")
        first = app._ai_sess.id

        # 在第一个会话里留一句话（走真实记录路径）
        app._ai_sess_remember("user", "第一个会话里说的话")
        app._ai_sess_remember("ai", "记下了")
        # 上下文（agent.history）只有真跑过模型才会长出来，这里手动补两条，
        # 模拟「这个会话聊过一轮」，才能验证切回来时上下文跟着回来。
        app.agent.history.append({"role": "user",
                                  "content": "第一个会话里说的话"})
        app.agent.history.append({"role": "assistant", "content": "记下了"})
        app._ai_sess_sync()
        chk(app._ai_sess.title != "新会话", "首问之后页签自动改名",
            app._ai_sess.title)

        s2 = app._ai_tab_new()
        made.append(s2.id)
        app.update_idletasks()
        chk(len(app._ai_sess_order) == n0 + 1, "＋ 开出一个新页签",
            f"{len(app._ai_sess_order)} 个")
        chk(app._ai_sess.id == s2.id, "新页签成为当前会话")
        chk(app.agent.history == s2.history, "上下文换成新会话的")
        chk(len(app.agent.history) == 0, "新会话上下文是空的",
            f"{len(app.agent.history)} 条")
        chips = [w for w in app._ai_tabs.winfo_children()]
        chk(len(chips) >= 2, "页签条画出了多个页签", f"{len(chips)} 个控件")

        # 切回第一个：上下文与标题都要回来
        app._ai_sess_activate(first)
        app.update_idletasks()
        chk(app._ai_sess.id == first, "能切回上一个会话")
        chk(len(app.agent.history) == 2, "切回后上下文跟着回来",
            f"{len(app.agent.history)} 条")
        chk(any("第一个会话里说的话" in str(h.get("content"))
                for h in app.agent.history), "上下文内容对得上")
        chk(app._ai_scroll is app._ai_open[first]["scroll"], "消息流换成该会话的")
        # 历史应该真的画出来（重放了 user + ai 两条）
        n_lbl = len(app._ai_flow_labels)
        chk(n_lbl >= 2, "切回后历史气泡重画出来", f"{n_lbl} 个标签")

        # 切到新会话 → 那边应该是空的引导页
        app._ai_sess_activate(s2.id)
        app.update_idletasks()
        chk(len(app.agent.history) == 0, "切到空会话后上下文为空")

        # 关闭页签
        app._ai_tab_close(s2.id)
        app.update_idletasks()
        chk(s2.id not in app._ai_open, "关掉的页签不再占着消息流")
        chk(app._ai_sess.id != s2.id, "关掉当前页签后落到别的会话",
            app._ai_sess.id)
        chk(store.load(s2.id) is not None, "关页签不删记录（还能翻回来）")

        # 全关掉要能自动补一个新会话，不能留下空白
        for sid in list(app._ai_sess_order)[1:]:
            app._ai_tab_close(sid)
        app.update_idletasks()
        chk(len([i for i in app._ai_sess_order if i in app._ai_open]) == 1,
            "关到一个不剩时自动补新会话", f"{len(app._ai_sess_order)} 个")

        # 「所有会话」窗口能开（不阻塞）
        app._ai_sess_list()
        app.update_idletasks()
        tops = [w for w in app.winfo_children()
                if w.winfo_class() == "Toplevel" and w.winfo_exists()]
        chk(bool(tops), "「所有会话」窗口能打开", f"{len(tops)} 个顶层窗")
        for t in tops:
            try:
                t.destroy()
            except Exception:  # noqa: BLE001
                pass

        # 落盘：文件真的在
        ondisk = [s.id for s in store.all()]
        chk(first in ondisk, "会话记录落了盘", f"{len(ondisk)} 个文件")
    finally:
        for sid in made:
            try:
                store.delete(sid)
            except Exception:  # noqa: BLE001
                pass
        # 把快照放回去（被测试改过的会话文件恢复原样）
        try:
            for p in store.root.glob("*.json"):
                if p.name not in snap:
                    p.unlink()
            for name, raw in snap.items():
                (store.root / name).write_bytes(raw)
        except OSError:
            pass


def test_width(app) -> None:
    """侧栏宽度：默认窄、有上下限、记忆用逻辑像素。"""
    chk(pages_module.DOCK_W_DEFAULT <= 400, "默认宽度收窄到 400 以内",
        str(pages_module.DOCK_W_DEFAULT))
    app._ai_dock_sash()
    app.update_idletasks()
    w = app.ai_dock.winfo_width()
    total = app.paned.winfo_width()
    cap = min(U.px(pages_module.DOCK_W_MAX), int(total * pages_module.DOCK_W_RATIO))
    chk(w <= cap + 6, "宽度不超过窗口的 42%", f"{w} <= {cap}（总宽 {total}）")
    chk(w >= U.px(240), "宽度没有窄到不可用", f"{w}")

    # 记忆：存逻辑像素
    app.agent_cfg.data["dock_width"] = 0
    app._ai_dock_remember()
    got = int(app.agent_cfg.data.get("dock_width") or 0)
    want = int(round(w / max(0.5, float(U.S))))
    chk(abs(got - want) <= 2, "记忆的是逻辑像素（不再重复乘缩放）",
        f"存 {got} / 期望 {want} / 实际 {w}px / 缩放 {U.S}")


def test_log(app) -> None:
    """日志管理要能读到「命令原文 + 输出 + 返回码」，且口令已打码。

    这是合并「命令日志」面板后的核心验收：能力并过去了，还得看得见。
    """
    app._audit_command("mysql -uroot -pTopSecret7 -e 'select 1'", 1,
                       "", "ERROR 1045 (28000): Access denied", 12)
    try:
        app.audit.clear_cache()
    except AttributeError:
        pass
    app.log_dock_open()          # v1.0.7 起日志在底部面板里，不再是页签
    pump(app, 0.5)
    app.audit_reload()
    pump(app, 0.3)

    def rows():
        return [r for r in getattr(app, "_audit_rows", [])
                if "mysql -uroot" in str((r.get("detail") or ""))]

    hit = rows()
    chk(bool(hit), "日志管理里能查到这条命令", f"{len(hit)} 条")
    if hit:
        d = str(hit[0].get("detail") or "")
        chk("TopSecret7" not in d, "口令没有出现在日志里", d[:70])
        chk("******" in d, "口令位置显示为打码", d[:70])
        chk("rc=1" in d, "带上了返回码")
        chk("Access denied" in d and "[stderr]" in d, "错误输出也记下来了",
            d[-70:])
        view = log_audit.event_view(hit[0])
        chk(view.get("group") == "终端", "归到「终端」分类",
            str(view.get("group")))

    # 按分类筛选：只看终端
    try:
        app.v_audit_group.set("终端")
        app._audit_reset()
        pump(app, 0.2)
        ok = bool(getattr(app, "_audit_rows", [])) and all(
            log_audit.event_view(r).get("group") == "终端"
            for r in app._audit_rows)
        chk(ok, "能按「终端」分类筛选",
            f"{len(getattr(app, '_audit_rows', []))} 条")
        app.v_audit_group.set("全部分类")
        app._audit_reset()
    except Exception:  # noqa: BLE001
        chk(False, "能按「终端」分类筛选")

    # 用输出里的字眼也能搜到 —— 事后排查就是这么找的
    try:
        app.tbl_audit.entry_search.delete(0, "end")
        app.tbl_audit.entry_search.insert(0, "Access denied")
        app._audit_reset()
        pump(app, 0.2)
        chk(bool([r for r in getattr(app, "_audit_rows", [])
                  if "mysql -uroot" in str(r.get("detail") or "")]),
            "输出内容可被搜索命中")
        app.tbl_audit.entry_search.delete(0, "end")
        app._audit_reset()
    except Exception:  # noqa: BLE001
        chk(False, "输出内容可被搜索命中")


def test_send_stop(app) -> None:
    """发送 / 停止合成一个按钮（WorkBuddy 形态）。

    侧栏就那么窄，并排两个按钮既挤又容易点错 —— 而且「现在按下去是发还是停」
    本来就该由状态决定，不该让人在两个按钮之间找。
    """
    btn = getattr(app, "btn_ai_send", None)
    chk(btn is not None, "发送按钮存在")
    if btn is None:
        return
    chk(getattr(app, "btn_ai_stop", None) is btn,
        "停止不是第二个按钮，是同一个对象")

    app.ai_busy = False
    app._ai_refresh_dock_state()
    pump(app, 0.1)
    chk(btn.text == "\u27a4", "空闲时是发送图标", btn.text)

    app.ai_busy = True
    app._ai_refresh_dock_state()
    pump(app, 0.1)
    chk(btn.text == "\u25a0", "忙碌时变停止图标", btn.text)

    calls: list[int] = []
    old = app.ai_dock_stop
    app.ai_dock_stop = lambda *a, **k: calls.append(1)
    try:
        app._ai_send_or_stop()
    finally:
        app.ai_dock_stop = old
    chk(calls == [1], "忙碌时点一下就是停止（不会又发一轮）", str(calls))

    app.ai_busy = False
    app._ai_refresh_dock_state()
    pump(app, 0.1)
    chk(btn.text == "\u27a4", "停下之后图标回到发送", btn.text)


def test_composer(app) -> None:
    """输入区三件：权限档位 / 思考档位 / 上下文用量，外加历史会话入口。"""
    for attr, label in (("btn_ai_perm", "权限档位"),
                        ("btn_ai_think", "思考档位"),
                        ("lbl_ai_ctx", "上下文用量"),
                        ("btn_dock_hist", "历史会话按钮")):
        chk(hasattr(app, attr), f"输入区有{label}")
    text, tone, tip = app._ai_ctx_usage()
    chk("/" in text or "未知" in text, "用量文案有形", text)
    chk(tone in ("mute", "warn", "err"), "用量有色调分档", tone)
    chk(bool(tip), "用量带一句提示", tip)


def main() -> int:
    import tkinter as tk  # noqa: F401  （确认 Tk 可用，报错信息更直白）

    import wbtui as U
    globals()["U"] = U
    if "--dark" in sys.argv:
        U.T = U.DARK

    import core.audit as log_audit
    globals()["log_audit"] = log_audit

    import pages.ai_dock as ad
    globals()["clipfile"] = ad.clipfile
    globals()["pages_module"] = ad

    try:
        test_png()
        test_dib()
        test_text()
        test_grab_guard()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        chk(False, "纯函数自测整体不崩")

    import app as A
    try:
        app = A.ToolboxApp()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        chk(False, "主窗口构建")
        return 1
    chk(True, "主窗口构建")
    pump(app, 0.8)

    app.ai_dock_open()
    pump(app, 0.5)
    chk(getattr(app, "ai_dock", None) is not None, "助手侧栏已打开")

    for fn in (test_attach, test_menu, test_model_menu, test_width,
               test_sessions, test_send_stop, test_composer, test_log,
               test_clear):
        try:
            fn(app)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            chk(False, f"{fn.__name__} 整体不崩")

    try:
        app.ai_dock_close()
    except Exception:  # noqa: BLE001
        pass

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    nfail = len(RESULTS) - npass
    print("\n===== %d 项，通过 %d，失败 %d =====" % (len(RESULTS), npass, nfail))
    if nfail:
        for ok, name, detail in RESULTS:
            if not ok:
                print("  FAIL", name, detail)
    return 0 if nfail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
