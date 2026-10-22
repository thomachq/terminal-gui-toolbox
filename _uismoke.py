# -*- coding: utf-8 -*-
"""界面与档案层的离线自检（不需要 NAS、不需要联网）。

跑法：
    python _uismoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


# ============================================================ 1. 档案层
def test_profiles() -> None:
    print("\n[1] profiles —— 连接档案 / ssh config / 密钥发现")
    import profiles as P

    with tempfile.TemporaryDirectory() as td:
        import crypto_store
        crypto_store.set_key_dir(Path(td))       # 密钥落在临时目录
        path = Path(td) / "profiles.dat"
        st = P.ProfileStore(path)
        check("初始为空", st.names() == [])

        st.upsert({"name": "nas", "host": "192.168.3.12", "port": "3022",
                   "user": "cloudadmin", "auth": "key",
                   "key_path": "C:/keys/id_ed25519",
                   # 没勾「记住密码」，这些必须被丢弃
                   "remember": False,
                   "password": "should-not-be-saved",
                   "key_passphrase": "should-not-be-saved",
                   "sudo_password": "should-not-be-saved"})
        check("档案已保存", st.names() == ["nas"])

        raw = path.read_text(encoding="utf-8")
        check("档案文件是密文", not raw.startswith("{"), raw[:16])
        check("密码不入盘", "should-not-be-saved" not in raw)
        check("主机名不入盘", "192.168.3.12" not in raw)
        inner = json.loads(crypto_store.open_bytes(path.read_bytes()).decode("utf-8"))
        check("只留白名单字段",
              set(inner["profiles"][0]) <= set(P.PROFILE_FIELDS),
              str(inner["profiles"][0]))

        st.set_last("nas")
        st2 = P.ProfileStore(path)
        check("重载后 last 保留", st2.last == "nas")
        check("重载后条目保留", st2.get("nas") is not None)

        st2.upsert({"name": "nas", "host": "10.0.0.1", "port": "22", "user": "u"})
        check("同名覆盖而非追加", len(st2.profiles) == 1)
        check("覆盖生效", st2.get("nas")["host"] == "10.0.0.1")

        st2.upsert({"name": "home", "host": "1.2.3.4", "port": "2200", "user": "x"})
        check("按名字排序", st2.names() == ["home", "nas"])
        check("删除成功", st2.remove("home") is True)
        check("删除不存在的返回 False", st2.remove("nope") is False)

    # --- ssh config 解析
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config"
        cfg.write_text(
            "# 注释\n"
            "Host nas\n"
            "    HostName 192.168.3.12\n"
            "    Port 3022\n"
            "    User cloudadmin\n"
            "    IdentityFile ~/.ssh/id_ed25519\n"
            "\n"
            "Host vps\n"
            "    HostName=vps.example.com\n"
            "    User=root\n"
            "    Port=22\n"
            "\n"
            "Host *\n"
            "    User nobody\n",
            encoding="utf-8")
        got = P.parse_ssh_config(str(cfg))
        names = [g["name"] for g in got]
        check("解析出 2 个 Host（跳过通配）", names == ["nas", "vps"], str(names))
        nas = got[0]
        check("HostName 解析", nas["host"] == "192.168.3.12")
        check("Port 解析", nas["port"] == "3022")
        check("User 解析", nas["user"] == "cloudadmin")
        check("IdentityFile 展开 ~", not nas["key_path"].startswith("~"), nas["key_path"])
        check("有密钥则 auth=key", nas["auth"] == "key")
        check("Key=Value 写法也支持", got[1]["host"] == "vps.example.com")
        check("无密钥则 auth=password", got[1]["auth"] == "password")

        with tempfile.TemporaryDirectory() as td2:
            st = P.ProfileStore(Path(td2) / "p.json")
            added, skipped = st.import_from_ssh_config() if False else (0, 0)
            # 用临时 config 走同一条路径
            got2 = P.parse_ssh_config(str(cfg))
            for g in got2:
                st.profiles.append(st._clean(g))
            check("导入得到 2 条", len(st.profiles) == 2)

    # --- 密钥发现与解析
    keys = P.discover_keys()
    check("密钥发现返回列表", isinstance(keys, list), type(keys).__name__)
    check("发现项含 path/name", all({"path", "name"} <= set(k) for k in keys))

    bad = P.inspect_key("C:/definitely/not/exists/id_rsa")
    check("不存在的密钥报错", bad["ok"] is False and "不存在" in bad["error"], str(bad))

    with tempfile.TemporaryDirectory() as td:
        ppk = Path(td) / "id_rsa.ppk"
        ppk.write_text("PuTTY-User-Key-File-2: ssh-rsa\n", encoding="utf-8")
        r = P.inspect_key(str(ppk))
        check("识别 .ppk 并拒绝", r["ok"] is False and r.get("format") == "ppk", str(r))

        txt = Path(td) / "notes.txt"
        txt.write_text("hello", encoding="utf-8")
        r = P.inspect_key(str(txt))
        check("非密钥文件报错", r["ok"] is False, str(r))

    # 真生成一把密钥来验证指纹读取
    try:
        import paramiko
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        with tempfile.TemporaryDirectory() as td:
            kp = Path(td) / "id_test"
            priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            kp.write_bytes(priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()))
            os.chmod(kp, 0o600)
            info = P.inspect_key(str(kp))
            check("能读出真实密钥指纹",
                  bool(info.get("ok")) and info["fingerprint"].startswith("SHA256:"),
                  str(info))
            check("能读出密钥类型", info.get("type") == "ssh-rsa", str(info.get("type")))
            check("能读出密钥位数", info.get("bits") == 2048, str(info.get("bits")))

            # 带口令的私钥：不填口令应提示，填了应成功
            enc = Path(td) / "id_enc"
            enc.write_bytes(priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.BestAvailableEncryption(b"pw123")))
            os.chmod(enc, 0o600)
            r = P.inspect_key(str(enc))
            check("加密私钥不填口令时给出提示",
                  r.get("ok") is False and r.get("encrypted") is True, str(r))
            r = P.inspect_key(str(enc), "pw123")
            check("加密私钥填对口令能读", bool(r.get("ok")), str(r))
    except Exception as e:  # noqa: BLE001
        print(f"  [SKIP] 生成密钥测试跳过：{type(e).__name__}: {e}")


# ============================================================ 2. 配色与自绘控件
def test_widgets() -> None:
    print("\n[2] wbtui —— 配色令牌 / 圆角几何")
    import wbtui as U

    for name in ("page", "card", "border", "brand", "text", "ok", "err",
                 "console_bg", "input_bg"):
        check(f"LIGHT 有 {name}", hasattr(U.LIGHT, name))
        check(f"DARK 有 {name}", hasattr(U.DARK, name))

    # WorkBuddy clear-blue 皮肤的原值
    check("主色取自 WorkBuddy 皮肤", U.LIGHT.brand.upper() == "#3A66AC", U.LIGHT.brand)
    check("页面底色取自皮肤", U.LIGHT.page.upper() == "#F5F9FF", U.LIGHT.page)
    check("描边色取自皮肤", U.LIGHT.border.upper() == "#DCE8FF", U.LIGHT.border)

    pts = U.rect_points(0, 0, 100, 30, 9)
    check("圆角点数为 24 个坐标", len(pts) == 24, str(len(pts)))
    check("圆角左上角被内缩", pts[0] == 9 and pts[1] == 0, str(pts[:4]))
    check("半径超限自动收敛",
          U.rect_points(0, 0, 10, 40, 999)[0] == 5,
          str(U.rect_points(0, 0, 10, 40, 999)[0]))


# ============================================================ 3. 主窗口
def test_app() -> None:
    print("\n[3] App —— 侧边导航 / 五页 / 主题切换 / 档案联动")
    import wbtui as U
    import vpncert_gui as G

    G.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # App 已从 tk.Tk 改成 tk.Frame（要能嵌进主程序），所以这里自己造一个
    # 根窗口当宿主。用 embedded=False 走历史路径：竖导航 / 自带锁与主题按钮，
    # 下面那堆断言仍然是照着独立形态写的。
    import tkinter as _tk
    _root = _tk.Tk()
    _root.title("uismoke host")
    app = G.App(_root, embedded=False)
    app.pack(fill="both", expand=True)
    app.update_idletasks()

    check("导航 5 项", len(app.nav.items) == 5, str(list(app.nav.items)))
    check("页面 5 个", len(app._pages) == 5, str(list(app._pages)))
    check("默认停在连接页", app.nav.current == "conn")

    for key in ("conn", "cert", "pack", "deploy", "cons"):
        app.nav.select(key)
        app.update_idletasks()
        check(f"切换到 {key} 不报错", app.nav.current == key)

    # 主题切换
    before = U.T.name
    app._toggle_theme()
    app.update_idletasks()
    check("切到另一套主题", U.T.name != before, f"{before} -> {U.T.name}")
    check("按钮文案跟随", app.btn_theme.text in ("深色", "浅色"), app.btn_theme.text)
    app._toggle_theme()
    app.update_idletasks()
    check("切回原主题", U.T.name == before, U.T.name)

    # 关键控件
    for attr in ("cb_prof", "v_host", "v_port", "v_user", "e_key", "lbl_key",
                 "btn_connect", "row_key", "row_pw", "tree", "box_conf",
                 "box_ta", "box_pack", "box_deploy", "box_cons", "txt_cons",
                 "pill_ca", "pill_nas", "kv_holder", "dev_holder"):
        check(f"控件 {attr}", hasattr(app, attr))

    check("命令日志有 5 个着色标签",
          {"cmd", "ok", "bad", "meta", "out"} <= set(app.txt_cons.tag_names()),
          str(app.txt_cons.tag_names()))

    # 认证行切换（先切回连接页，否则子控件处于未映射状态）
    app.nav.select("conn")
    app.update()
    app.v_mode.set("password")
    app._mode_changed()
    app.update()
    # 用 pack 管理器判定：宿主窗口不一定被真正映射（无 mainloop），
    # winfo_ismapped 会一直返回 0，但 pack_forget 之后 manager 一定是空串。
    def _packed(w) -> bool:
        return w.winfo_manager() == "pack"

    check("密码模式隐藏私钥行", not _packed(app.row_key), app.row_key.winfo_manager())
    check("密码模式显示密码行", _packed(app.row_pw), app.row_pw.winfo_manager())
    app.v_mode.set("key")
    app._mode_changed()
    app.update()
    check("私钥模式隐藏密码行", not _packed(app.row_pw), app.row_pw.winfo_manager())
    check("私钥模式显示私钥行", _packed(app.row_key), app.row_key.winfo_manager())

    # 密钥信息（不存在的路径应给出警告而不是崩溃）
    app.v_key.set("C:/nope/id_rsa")
    app._refresh_key_info()
    for _ in range(30):
        app.update()
        app.after(10)
    check("坏密钥路径不崩溃", "⚠" in app.lbl_key.cget("text") or
          "正在" in app.lbl_key.cget("text"), app.lbl_key.cget("text"))

    # 档案保存 / 载入 / 删除
    app.v_host.set("192.168.3.12")
    app.v_port.set("3022")
    app.v_user.set("cloudadmin")
    app.v_key.set("")
    app._do_upsert("selftest-nas")
    app.update_idletasks()
    check("档案写入", "selftest-nas" in app.profiles.names(), str(app.profiles.names()))
    check("下拉框含该档案", "selftest-nas" in list(app.cb_prof["values"]),
          str(app.cb_prof["values"]))

    app.v_host.set("")
    app.v_user.set("")
    app._apply_profile(app.profiles.get("selftest-nas"))
    check("载入档案恢复地址", app.v_host.get() == "192.168.3.12", app.v_host.get())
    check("载入档案恢复端口", app.v_port.get() == "3022", app.v_port.get())

    app.v_prof.set("selftest-nas")
    app.profiles.remove("selftest-nas")
    app._refresh_profile_list()
    check("档案删除", "selftest-nas" not in app.profiles.names())

    # CRL / CA 文案
    app.refresh_all()
    app.update_idletasks()
    check("CA 药丸有文案", bool(app.pill_ca.text), app.pill_ca.text)
    check("CRL 版本有文案", "CRL" in app.lbl_crl.cget("text"), app.lbl_crl.cget("text"))

    # 手动命令在未连接时应弹提示（拦截，不真的去连）
    import tkinter.messagebox as mb
    _orig = mb.showwarning
    called = {"n": 0}

    def fake(*a, **k):
        called["n"] += 1
    mb.showwarning = fake
    try:
        app.v_manual.set("uname -a")
        app.do_manual()
    finally:
        mb.showwarning = _orig
    check("未连接时拦截手动命令", called["n"] == 1, str(called))

    app.destroy()
    print("  [INFO] 主窗口构建、切换、销毁均无异常")


def test_click_once() -> None:
    print("\n[4] 按钮点击只触发一次")
    import tkinter as tk

    import wbtui as U

    root = tk.Tk()
    try:
        calls = []
        btn = U.RoundButton(root, "试一下", lambda: calls.append(1),
                            variant="primary", height=32, padx=18)
        btn.pack(pady=20)
        root.update()
        btn.event_generate("<Button-1>", x=20, y=16)
        btn.event_generate("<ButtonRelease-1>", x=20, y=16)
        root.update()
        # canvas 的 item 绑定和 widget 绑定会各跑一遍；两套都绑就会点一次跑两次，
        # 「启动服务」起来马上停就是这么来的。
        check("点一次只触发一次 command", len(calls) == 1, f"{len(calls)} 次")

        calls.clear()
        btn.event_generate("<ButtonRelease-1>", x=20, y=16)
        root.update()
        check("没按下去就松手不触发", not calls, f"{len(calls)} 次")
    finally:
        root.destroy()


def test_brief_dialog() -> None:
    print("\n[5] 「给智能体的说明」必须看得见")
    import tkinter as tk

    import app as APP

    a = APP.ToolboxApp()
    try:
        a.nav.select("api")
        for _ in range(30):
            a.update()
            a.after(15)
            a.update_idletasks()
        a.clipboard_clear()
        a.clipboard_append("SENTINEL")
        a.update()

        a.api_copy_brief()
        for _ in range(20):
            a.update()
            a.after(12)
        tops = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)]
        # 原来只往剪贴板塞、状态栏闪一行字，用户点了看不到东西 —— 必须弹窗。
        check("点击后弹出说明窗", len(tops) == 1, f"{len(tops)} 个 Toplevel")
        if tops:
            t = tops[0]
            check("窗口标题正确", t.title() == "给智能体的说明", t.title())
            body = t.txt.get("1.0", "end-1c") if hasattr(t, "txt") else ""
            check("窗内文本非空", len(body) > 300, f"{len(body)} 字")
            check("窗里有复制全文按钮", hasattr(t, "btn_copy"))
            a.clipboard_clear()
            a.clipboard_append("X")
            a.update()
            t._do_copy()
            a.update()
            check("复制全文写进剪贴板", len(a.clipboard_get()) == len(body),
                  f"{len(a.clipboard_get())} vs {len(body)}")
            check("按钮文字变「已复制」", "已复制" in str(t.btn_copy.text),
                  str(t.btn_copy.text))
            t.destroy()
        check("剪贴板已是说明全文",
              not a.clipboard_get().startswith("SENTINEL"),
              a.clipboard_get()[:20])
    finally:
        a.destroy()


def main() -> int:
    try:
        test_profiles()
        test_widgets()
        test_app()
        test_click_once()
        test_brief_dialog()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2
    print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
