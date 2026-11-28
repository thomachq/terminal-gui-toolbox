# -*- coding: utf-8 -*-
"""core 函数白名单桥 —— Electron 全功能迁移的适配层。

为什么不写一个专用端点：16 个页签里有大量「解析 / 拼命令 / 校验」纯函数，
逐个包 HTTP 端点等于把 60+ 个函数复制一遍，改一次 core 就要改两处。
这里用显式白名单注册表：前端按名字调，服务端按注册表分派，
**core 一行不重写**，新增页面只加一条注册项。

安全：只有注册过的 (module, func) 能调；`session=True` 的函数由服务端注入
当前连接对象，前端永远碰不到 session 本身；不注册下划线开头的私有函数。
"""
from __future__ import annotations

import importlib
from typing import Any, Callable


# (模块, 函数名) → 说明；session=True 表示第一个参数是连接对象（Conn）
REGISTRY: dict[str, tuple[str, str, bool, str]] = {
    # ---- 脚本库 ----
    "scripts.summary":       ("scripts", "summary", False, "脚本统计摘要"),
    "scripts.filter":        ("scripts", "filter_scripts", False, "过滤脚本列表"),
    "scripts.validate":      ("scripts", "validate", False, "校验脚本"),
    "scripts.shell_cmd":     ("scripts", "shell_cmd", False, "脚本转命令"),
    "scripts.preview":       ("scripts", "preview", False, "脚本预览"),
    # ---- 计划任务 ----
    "cron.parse_crontab":    ("cronjob", "parse_crontab", False, "解析 crontab 文本"),
    "cron.parse_timers":     ("cronjob", "parse_timers", False, "解析 systemd timer"),
    "cron.parse_schtasks":   ("cronjob", "parse_schtasks", False, "解析 schtasks 输出"),
    "cron.validate":         ("cronjob", "validate_schedule", False, "校验 cron 表达式"),
    "cron.describe":         ("cronjob", "describe", False, "人话解释 cron 表达式"),
    "cron.toggle_raw":       ("cronjob", "toggle_raw", False, "启停一行 cron"),
    "cron.replace_raw":      ("cronjob", "replace_raw", False, "替换一行 cron"),
    "cron.build_line":       ("cronjob", "build_job_line", False, "构造 cron 行"),
    "cron.backup_cmd":       ("cronjob", "backup_cmd", False, "写前备份命令"),
    "cron.write_cmd_for":    ("cronjob", "write_cmd_for", False, "生成写回 crontab/文件的命令"),
    "cron.rollback_hint":    ("cronjob", "rollback_hint", False, "回滚命令"),
    "cron.job_id":           ("cronjob", "job_id", False, "任务稳定 ID"),
    "cron.parse_bundle":     ("cronjob", "parse_bundle", False, "解析一次读全的输出"),
    "cron.jobs_from_bundle": ("cronjob", "jobs_from_bundle", False, "从 bundle 构造任务列表"),
    # ---- 路由表 ----
    "routes.prefix_to_mask": ("routes", "prefix_to_mask", False, "CIDR 转掩码"),
    "routes.dst_to_net_mask":("routes", "dst_to_net_mask", False, "目标转网络/掩码"),
    "routes.edit_cmds":      ("routes", "edit_cmds", False, "生成修改路由命令"),
    "routes.default_cmds":   ("routes", "default_cmds", False, "生成默认路由命令"),
    "routes.plan":           ("routes", "plan", False, "生成路由操作计划"),
    "routes.undo_cmds":      ("routes", "undo_cmds", False, "生成撤销命令"),
    "routes.apply":          ("routes", "apply", True,  "执行路由变更"),
    # ---- Web 服务 ----
    "web.parse_listeners":   ("webstack", "parse_listeners", False, "解析监听端口"),
    "web.parse_processes":   ("webstack", "parse_processes", False, "解析进程列表"),
    "web.parse_systemd":     ("webstack", "parse_systemd_units", False, "解析 systemd 单元"),
    "web.parse_version":     ("webstack", "parse_version", False, "从输出解析版本号"),
    "web.parse_docroot":     ("webstack", "parse_docroot", False, "解析站点根目录"),
    "web.parse_tls":         ("webstack", "parse_tls_directives", False, "解析 TLS 配置"),
    "web.load_certs":        ("webstack", "load_pem_certs", False, "解析 PEM 证书"),
    # ---- 数据库 ----
    "db.parse_ps":           ("dbscan", "parse_ps", False, "解析数据库进程"),
    "db.kind_of":            ("dbscan", "kind_of", False, "识别数据库类型"),
    "db.parse_listeners":    ("dbscan", "parse_listeners", False, "解析数据库监听"),
    "db.merge_services":     ("dbscan", "merge_services", False, "合并进程与监听"),
    "db.sort_services":      ("dbscan", "sort_services", False, "服务排序"),
    "db.parse_version":      ("dbscan", "parse_version", False, "解析版本号"),
    "db.summarize":          ("dbscan", "summarize", False, "数据库摘要"),
    "db.mysql_cmd":          ("dbscan", "mysql_cmd", False, "构造 mysql 命令"),
    # ---- 安全中心 ----
    "sec.score_of":          ("secscan", "score_of", False, "体检打分"),
    "sec.parse_kv":          ("secscan", "parse_kv_conf", False, "解析 kv 配置"),
    "sec.audit_sshd":        ("secscan", "audit_sshd", False, "sshd 配置体检"),
    "sec.parse_listeners":   ("secscan", "parse_listeners", False, "解析监听"),
    "sec.audit_exposure":    ("secscan", "audit_exposure", False, "暴露面体检"),
    "sec.audit_accounts":    ("secscan", "audit_accounts", False, "账号体检"),
    "sec.audit_failed":      ("secscan", "audit_failed", False, "登录失败体检"),
    # ---- 证书 / 区域策略 ----
    "cert.split_sans":       ("servercert", "split_sans", False, "拆分 SAN 列表"),
    "cert.make_selfsigned":  ("servercert", "make_selfsigned", False, "生成自签证书"),
    "cert.parse_pem":        ("servercert", "parse_pem", False, "解析 PEM"),
    "cert.deploy_hint":      ("servercert", "deploy_hint", False, "部署提示"),
    "devcert.list_synology": ("devicecert", "list_synology", True,  "群晖证书清单"),
    "devcert.list_fnos":     ("devicecert", "list_fnos", True,  "飞牛证书清单"),
    "region.probe":          ("region", "probe", True,  "探测区域策略能力"),
    "region.make_plan":      ("region", "make_plan", False, "生成区域策略计划"),
    "region.covers":         ("region", "covers", False, "判断地址是否命中策略"),
    "region.lockout":        ("region", "lockout", False, "检查是否自锁"),
    "region.peer_addrs":     ("region", "peer_addrs", True,  "取对端地址"),
    "region.nft_script":     ("region", "nft_script", False, "生成 nft 脚本"),
    "region.apply":          ("region", "apply", True,  "应用区域策略"),
    # ---- 代理链 ----
    "proxy.tcp_probe":       ("proxycheck", "tcp_probe", False, "单节点 TCP 探测"),
    "proxy.check_chain":     ("proxycheck", "check_chain", False, "整条代理链验证"),
    "proxy.format_report":   ("proxycheck", "format_report", False, "链路报告排版"),
    # ---- 日志 ----
    "audit.groups":          ("audit", "groups", False, "审计分组"),
    "audit.event_view":      ("audit", "event_view", False, "审计事件视图"),
    "audit.title_of":        ("audit", "title_of", False, "事件标题"),
    "audit.status_label":    ("audit", "status_label", False, "状态文案"),
    "audit.day_key":         ("audit", "day_key", False, "日期键"),
    # ---- 模型能力 ----
    "modelsync.clean_caps":  ("modelsync", "clean_caps", False, "规范模型能力"),
    "modelsync.clean_modal": ("modelsync", "clean_modalities", False, "规范模态"),
}


def catalog() -> dict:
    return {
        "items": [{"name": k, "module": v[0], "func": v[1],
                   "session": v[2], "desc": v[3]}
                  for k, v in sorted(REGISTRY.items())],
        "count": len(REGISTRY)
    }


def call(name: str, args: dict, conn=None) -> Any:
    """按注册表调一个 core 函数。未知名字直接拒绝。"""
    item = REGISTRY.get(str(name or ""))
    if item is None:
        raise KeyError(f"没注册的 core 函数：{name}（GET /v1/tools/core/catalog 看清单）")
    module, func, need_conn, _desc = item
    fn: Callable = getattr(importlib.import_module("core." + module), func)
    if need_conn:
        if conn is None:
            raise ValueError(f"{name} 需要一条活动连接（先 POST /v1/sessions）")
        return fn(conn.session if hasattr(conn, "session") else conn, **(args or {}))
    return fn(**(args or {}))
