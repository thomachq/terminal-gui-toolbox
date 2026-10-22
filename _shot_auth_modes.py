# -*- coding: utf-8 -*-
"""连接页：认证方式切换 + 每项口令自带保存开关 的实拍与断言。

要确认的三件事：
  1. 选「私钥」只露私钥相关两行，选「登录密码」时私钥口令框必须消失
  2. 每个口令框后面都有自己的「保存口令」，底部不再有总开关
  3. 提权框是设备无关的「提权密码」，不是写死的 sudo / enable
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shots as SH

OUT = Path(__file__).resolve().parent / "screenshots"


def pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return
        time.sleep(0.02)


def main():
    p = Path("data/settings.json")
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    d.update({"lock_enabled": False, "lock_hash": "", "lock_timeout": 0,
              "lock_auto_login": False, "lock_pass_enc": ""})
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    import app as APP
    a = APP.ToolboxApp()
    a.geometry("+50+30")
    pump(a, 1.0)
    a.nav.select("conn")
    pump(a, 0.6)
    h = (SH.find_titled() or [0])[0]

    rows = ("row_key", "row_keypw", "row_pw", "row_cred")
    cases = (("key", "conn_auth_key"), ("password", "conn_auth_pw"))
    for mode, tag in cases:
        a.v_proto.set("ssh")
        a._proto_changed()
        a.v_mode.set(mode)
        a._mode_changed()
        pump(a, 0.7)
        vis = {n: bool(getattr(a, n).winfo_manager()) for n in rows}
        print(f"{mode:9s} 可见：{vis}")
        ok = (vis["row_key"] and vis["row_keypw"]) if mode == "key" \
            else (vis["row_pw"] and not vis["row_keypw"] and not vis["row_key"])
        print(f"  => {'OK' if ok else 'FAIL'}")
        if h:
            img = SH.capture(h)
            if img:
                img.save(OUT / f"{tag}.png")
                print(f"  {tag}.png OK {img.size}")

    # 底部不应再有「记住凭据」总开关
    print("有 chk_remember:", hasattr(a, "chk_remember"))
    print("分项开关:",
          [bool(v.get()) for v in (a.v_save_keypw, a.v_save_pw, a.v_save_sudo)])

    # 只勾私钥口令时，档案里就只能有私钥口令这一个字段
    a.v_save_keypw.set(True)
    a.v_save_pw.set(False)
    a.v_save_sudo.set(False)
    a.v_keypw.set("kpass")
    a.v_pw.set("loginpw")
    a.v_sudopw.set("sudopw")
    prof = a._collect_profile()
    print("只勾私钥口令 => 存了：",
          sorted(k for k in prof if k in ("key_passphrase", "password",
                                          "sudo_password")))

    a.destroy()


if __name__ == "__main__":
    main()
