# -*- coding: utf-8 -*-
"""智能体接入页：本地 API 的开关、令牌、端点速查、连接池。"""
from __future__ import annotations

import base64
import json
import tkinter as tk

import wbtui as U
from api.server import PORT_RANGE, port_busy
from ui import forms
from ui.table import Column, DataTable, KVGrid

ENDPOINTS = [
    {"m": "GET", "path": "/v1/health", "desc": "探活，不需要令牌"},
    {"m": "GET", "path": "/v1/openapi.json", "desc": "接口自描述，智能体可先读这个"},
    {"m": "POST", "path": "/v1/sessions", "desc": "建连接并保持（host/user/password）"},
    {"m": "GET", "path": "/v1/sessions", "desc": "列出连接池（含空闲秒数、设备信息）"},
    {"m": "GET", "path": "/v1/sessions/{id}", "desc": "单条连接详情"},
    {"m": "POST", "path": "/v1/sessions/{id}/exec", "desc": "执行命令（连接复用）"},
    {"m": "GET", "path": "/v1/sessions/{id}/metrics", "desc": "CPU / 内存 / 网络速率"},
    {"m": "POST", "path": "/v1/sessions/{id}/upload", "desc": "SFTP 上传"},
    {"m": "GET", "path": "/v1/sessions/{id}/download", "desc": "SFTP 下载"},
    {"m": "POST", "path": "/v1/sessions/{id}/keepalive", "desc": "调整保活间隔"},
    {"m": "DELETE", "path": "/v1/sessions/{id}", "desc": "关闭连接"},
    # ---- 工具箱的具体能力（不止是终端）
    {"m": "GET", "path": "/v1/tools", "desc": "能力清单，智能体先读这个"},
    {"m": "GET", "path": "/v1/hosts", "desc": "档案里的设备清单（不含口令）"},
    {"m": "GET", "path": "/v1/tools/sysinfo", "desc": "系统概览：CPU / 内存 / 磁盘"},
    {"m": "GET", "path": "/v1/tools/docker", "desc": "容器 / 镜像 / 卷 / 占用"},
    {"m": "GET", "path": "/v1/tools/services", "desc": "监听端口与 Web 服务识别"},
    {"m": "GET", "path": "/v1/tools/files", "desc": "列目录 / 读 / 写 / 删（SFTP）"},
]

SESSION_COLS = [
    Column("id", "连接 ID", 110, mono=True),
    Column("name", "别名 / 主机", 170, stretch=True,
           format=lambda v, r: str(v) if v and v != r.get("id")
           else f"{r.get('user') or ''}@{r.get('host') or '本机'}"),
    Column("device", "设备", 200, stretch=True),
    Column("alive", "状态", 80,
           format=lambda v, r: "在线" if v else "已断开"),
    Column("idle_seconds", "空闲", 80, anchor="e",
           format=lambda v, r: f"{int(v or 0)}s"),
    Column("execs", "命令数", 76, anchor="e"),
    Column("reconnects", "重连", 64, anchor="e"),
]

EP_COLS = [
    Column("m", "方法", 76, mono=True),
    Column("path", "路径", 300, mono=True, stretch=True),
    Column("desc", "说明", 380, stretch=True),
]


class ApiPage:
    # ------------------------------------------------------------ 构建
    def build_api(self, pg):
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)

        c = U.Card(pg, title="智能体接入", hint="本地 HTTP API · 连接保持由服务端负责")
        c.grid(row=0, column=0, sticky="we")
        b = c.body
        head = U.wframe(b, "card")
        head.pack(fill="x")
        self.lbl_api_state = U.wlabel(head, "服务未启动", "card", U.FONT_B)
        self.lbl_api_state.pack(side="left")
        self.badge_api = U.Pill(head, "已停止", "mute", height=24)
        self.badge_api.pack(side="left", padx=(U.px(10), 0))
        self.lbl_api_url = U.wlabel(head, "", "mute", U.FONT_XS)
        self.lbl_api_url.pack(side="right")

        U.wlabel(
            b, "AI 智能体的 SSH 技能可以直接调这些接口：建一次连接，之后反复执行命令，"
               "服务端每 25 秒发 SSH keepalive，掉线自动重连 —— 智能体不必自己管连接。",
            "dim", U.FONT_S, justify="left", wraplength=U.px(760)).pack(
            anchor="w", pady=(U.px(6), 0))

        info = U.wframe(b, "card")
        info.pack(fill="x", pady=(U.px(10), 0))
        self.kk_api = KVGrid(info, columns=3)
        self.kk_api.pack(fill="x")

        row = U.wframe(b, "card")
        row.pack(fill="x", pady=(U.px(12), 0))
        self.btn_api_toggle = U.RoundButton(row, "启动服务", self.api_toggle,
                                            variant="primary", height=32, padx=18)
        self.btn_api_toggle.pack(side="left")
        U.RoundButton(row, "复制令牌", self.api_copy_token, variant="secondary",
                      height=32, padx=14).pack(side="left", padx=(U.px(8), 0))
        U.RoundButton(row, "重置令牌", self.api_rotate_token, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))
        U.RoundButton(row, "给智能体的说明", self.api_copy_brief, variant="secondary",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))
        # 端口和监听地址、保活一起在「API 设置」里改，不再单开一个「换端口」按钮
        U.RoundButton(row, "API 设置", self.api_settings, variant="ghost",
                      height=32, padx=14).pack(side="left", padx=(U.px(6), 0))

        # 连接池
        c2 = U.Card(pg, title="连接池", hint="智能体建的连接会一直留在这里，直到被关掉")
        c2.grid(row=1, column=0, sticky="ns", pady=(U.px(10), 0))
        c2.configure(height=U.px(210))
        self.tbl_sess = DataTable(c2.body, SESSION_COLS, height=5, searchable=False,
                                  empty_title="连接池是空的",
                                  empty_hint="智能体调用 POST /v1/sessions 后，"
                                             "连接会出现在这里。")
        self.tbl_sess.pack(fill="both", expand=True)
        self.tbl_sess.set_menu([
            ("断开这条连接", self.api_close_session, lambda r: bool(r)),
            ("复制连接 ID", self.api_copy_sid, lambda r: bool(r)),
            ("连接详情", self.api_session_detail, lambda r: bool(r)),
        ])

        # 端点速查
        c3 = U.Card(pg, title="接口速查", hint="全部接口，含请求字段说明见 /v1/openapi.json")
        c3.grid(row=2, column=0, sticky="nsew", pady=(U.px(10), 0))
        self.tbl_ep = DataTable(c3.body, EP_COLS, height=8, searchable=True,
                                empty_title="—")
        self.tbl_ep.pack(fill="both", expand=True)
        self.tbl_ep.set_rows(ENDPOINTS)

        U.RoundButton(c2.body, "断开全部", self.api_close_all, variant="danger",
                      height=26, padx=10).pack(anchor="e", pady=(U.px(6), 0))

    # ------------------------------------------------------------ 刷新
    def api_refresh(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        cfg = srv.config
        running = srv.running
        self.badge_api.set("运行中" if running else "已停止",
                           "ok" if running else "mute")
        self.btn_api_toggle.set_text("停止服务" if running else "启动服务")
        self.lbl_api_state.configure(
            text="本地 API 正在运行" if running else "服务未启动",
            fg=U.T.text)
        self.lbl_api_url.configure(text=srv.url() if running else "")
        self.kk_api.set_items([
            ("监听地址", f"{cfg.data.get('host')}:{cfg.data.get('port')}", None),
            ("令牌", cfg.token_masked(), "brand", ("复制", self.api_copy_token)),
            ("保活间隔", f"{int(cfg.data.get('keepalive') or 0)} 秒" if
             int(cfg.data.get("keepalive") or 0) else "关闭", None),
            ("连接数", str(len(srv.pool.list())), None),
            ("请求数", str(srv.requests), None),
            ("闲置回收", f"{int(cfg.data.get('idle_ttl') or 0)} 秒" if
             int(cfg.data.get("idle_ttl") or 0) else "不回收", None),
        ])
        rows = []
        for s in srv.pool.list():
            d = s.get("device") or {}
            rows.append({
                **s,
                "device": d.get("os_name") or d.get("os_family") or "—",
                "_tone": "ok" if s.get("alive") else "err",
            })
        self.tbl_sess.set_rows(rows)

    # ------------------------------------------------------------ 动作
    def api_toggle(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        if srv.running:
            srv.stop()
            self.toast("API 服务已停止", "warn")
            self.api_log_line("api", "停止 API 服务")
        else:
            was = srv.port
            ok, msg = srv.start()
            self.toast(msg, "ok" if ok else "err")
            if not ok:
                forms.confirm(self, "启动失败", msg)
                # 启动失败不写「停止」：之前这里按 running 反推，
                # 于是启动失败也记成「停止 API 服务」，看着像莫名停了一次。
                self.api_log_line("bad", f"启动失败：{msg}")
            else:
                moved = srv.port != was
                self.api_log_line(
                    "api", f"启动 API 服务 · 端口 {srv.port}"
                    + (f"（原 {was} 被占用，已自动换）" if moved else ""))
        self.api_refresh()

    def api_copy_token(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        tok = srv.config.token()
        if not self.copy_text(tok, "令牌"):
            forms.InfoDialog.show(self, "令牌", tok, width=560, height=200,
                                  copyable=True)

    def api_rotate_token(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        if not forms.confirm(
                self, "重置令牌",
                "重置后旧令牌立刻失效，已经接入的智能体需要换新令牌。继续？",
                danger=True, ok_text="重置"):
            return
        srv.config.rotate()
        self.api_refresh()
        self.toast("令牌已重置", "ok")

    def api_copy_brief(self):
        brief = self._brief_text()
        if not brief:
            return
        # 这个按钮叫「说明」，那就要让人看见说明。
        # 之前只往剪贴板里塞、状态栏闪一行字，看上去像没反应；现在先弹窗，
        # 全文同时进剪贴板，「复制全文」也留在窗里，两条路都给。
        forms.InfoDialog.show(self, "给智能体的说明", brief,
                              subtitle="全文已同时放入剪贴板",
                              width=880, height=640)
        self.copy_text(brief, "说明")

    def _brief_text(self) -> str:
        srv = getattr(self, "api_server", None)
        if srv is None:
            self.toast("API 还没初始化，稍后再试", "warn")
            return ""
        tok = srv.config.token()
        url = srv.url()
        return f"""# Terminal GUI Toolbox · SSH 连接 API

基础地址：{url}
鉴权头：Authorization: Bearer {tok}

## 用法（连接会一直保持，不必自己重建）

1) 建连接
curl -s -X POST {url}/v1/sessions \\
  -H "Authorization: Bearer {tok}" \\
  -H "Content-Type: application/json" \\
  -d '{{"host":"192.168.3.12","user":"admin","password":"***","name":"NAS"}}'
→ 返回 {{"id":"a1b2c3d4e5f6","device":{{...}}}}

2) 执行命令（可反复调用）
curl -s -X POST {url}/v1/sessions/<id>/exec \\
  -H "Authorization: Bearer {tok}" \\
  -H "Content-Type: application/json" \\
  -d '{{"cmd":"df -h","timeout":60}}'
→ {{"rc":0,"out":"...","err":"","ms":120}}

3) 需要 root：exec 时传 "admin": true（需建连时提供 sudo_password）

4) 用完关掉
curl -s -X DELETE {url}/v1/sessions/<id> -H "Authorization: Bearer {tok}"

## 说明
- 服务端按 keepalive 间隔发 SSH 心跳，断线自动重连（最多 5 次）
- 单条命令输出上限 200000 字符，超出会标 truncated
- 设备类型/系统在返回的 device 字段里，可据此选命令方言
- 完整接口：GET {url}/v1/openapi.json

## Python 片段
import requests
S = "{url}"
H = {{"Authorization": "Bearer {tok}"}}
sid = requests.post(f"{{S}}/v1/sessions", headers=H, json={{"host":"192.168.3.12","user":"admin","password":"***"}}).json()["id"]
r = requests.post(f"{{S}}/v1/sessions/{{sid}}/exec", headers=H, json={{"cmd":"uptime"}}).json()
print(r["out"])
"""

    def api_settings(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        cfg = srv.config
        fields = [
            # 端口留空 = 每次启动自动挑一个空闲端口，和「换端口」是同一件事，
            # 所以只留这一个入口，不再单摆一个按钮。
            {"key": "port", "label": "端口", "kind": "int",
             "default": int(cfg.data.get("port") or 0), "min": 0, "max": 65535,
             "hint": f"留空或填 0 = 自动挑一个空闲端口；{PORT_RANGE[0]}-"
                     f"{PORT_RANGE[1]} 这一段比较不容易撞"},
            {"key": "host", "label": "监听地址", "kind": "select",
             "options": [("127.0.0.1", "仅本机（推荐）"),
                         ("0.0.0.0", "局域网可访问（注意风险）")],
             "default": cfg.data.get("host") or "127.0.0.1"},
            {"key": "keepalive", "label": "保活间隔", "kind": "int",
             "default": int(cfg.data.get("keepalive") or 25), "min": 0, "max": 600,
             "unit": "秒", "hint": "0 = 不发心跳（不推荐，连接容易被设备踢掉）"},
            {"key": "idle_ttl", "label": "闲置回收", "kind": "int",
             "default": int(cfg.data.get("idle_ttl") or 0), "min": 0, "max": 86400,
             "unit": "秒", "hint": "0 = 不回收（智能体可长期挂着）"},
        ]
        data = forms.FormDialog.open(self, "API 设置", fields,
                                     subtitle="改端口或监听地址会重启服务")
        if not data:
            return
        was_running = srv.running
        old_port = int(cfg.data.get("port") or 0)
        host = str(data.get("host") or "127.0.0.1")
        raw = str(data.get("port") or "").strip()
        new_port = int(raw) if raw.isdigit() else 0
        if new_port == 0:
            new_port = pick_free_port(host)
        if was_running:
            srv.stop(keep_enabled=True)
        if new_port != old_port and port_busy(new_port, host):
            self.toast(f"端口 {new_port} 现在被占用，启动时会自动换一个空闲的", "warn")
        cfg.data["port"] = new_port
        cfg.data["host"] = host
        cfg.data["keepalive"] = int(data["keepalive"])
        cfg.data["idle_ttl"] = int(data["idle_ttl"])
        srv.pool.keepalive_default = int(data["keepalive"])
        srv.pool.idle_ttl = int(data["idle_ttl"])
        cfg.save()
        if was_running:
            ok, msg = srv.start()
            self.toast(msg, "ok" if ok else "err")
            moved = srv.port != old_port
            self.api_log_line(
                "api" if ok else "bad",
                (f"API 服务已重启 · 端口 {srv.port}" if ok
                 else f"重启失败：{msg}")
                + (f"（原 {old_port}）" if moved else ""))
            if ok and moved:
                # 智能体存的是完整地址，换了端口必须重新给它一份
                forms.InfoDialog.show(
                    self, "端口已更换",
                    f"{old_port} → {srv.port}\n\n"
                    f"新地址：{srv.url()}\n\n"
                    "智能体里存的旧地址已经失效，把新地址和令牌重新给它一份"
                    "（「给智能体的说明」里是新地址）。",
                    width=520, height=200)
        self.api_refresh()

    def api_close_session(self, row):
        srv = getattr(self, "api_server", None)
        if srv is None or not row:
            return
        srv.pool.close(row.get("id", ""), "界面手动断开")
        self.api_refresh()

    def api_close_all(self):
        srv = getattr(self, "api_server", None)
        if srv is None:
            return
        n = len(srv.pool.list())
        if not n:
            return
        if not forms.confirm(self, "断开全部",
                             f"断开 {n} 条连接？智能体下次调用会收到「会话不存在」。",
                             danger=True, ok_text="断开"):
            return
        srv.pool.close_all()
        self.api_refresh()

    def api_copy_sid(self, row):
        if not row:
            return
        self.copy_text(str(row.get("id", "")), "连接 ID")

    def api_session_detail(self, row):
        if not row:
            return
        body = json.dumps(row, ensure_ascii=False, indent=2)
        forms.InfoDialog.show(self, "连接详情", body,
                              subtitle=row.get("id", ""), width=760, height=520)
