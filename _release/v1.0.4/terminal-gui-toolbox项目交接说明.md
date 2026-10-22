# Terminal GUI Toolbox · 项目交接说明

版本：**v1.0.4**（2026-10-23）
仓库：GitHub / Gitee / GitCode / Forgejo 四端同一 commit、同一 tag `v1.0.4`

---

## 1. 这是什么

一个 Windows 上的多协议终端工具箱：连 SSH / Telnet / 本机，连上后自动探系统，
把常用运维动作（网络工具、系统信息、文件管理、防火墙、Docker、Web 服务、数据库、
计划任务、证书管理）做成页面，另外带一个**智能运维助手**。

两条硬约束贯穿整个项目，改代码前必须先看懂：

1. **零安装**：不往被管机器上落任何 agent、脚本、守护进程。全部能力只用对方已有的
   SSH/Telnet + 系统自带命令与配置文件实现。判断标准是「断网还能不能用」——
   联网调模型 API 是允许的。
2. **凭据只在本地加密**：口令、私钥、Key 一律进 `data/*.dat`（cryptography AES-GCM），
   界面永不回显原文，只显示「已设置 · N 位」。

---

## 2. 目录结构与职责

```
TerminalToolbox.exe        打包产物（PyInstaller onefile，同目录读 data\）
app.py                     主程序：外壳、导航、页签、终端宿主、AI 通道
wbtui.py                   自绘控件库（主题、按钮、卡片、导航、Check…）
crypto_store.py            AES-GCM 封装（凭据落地）
lockmgr.py                 启动锁 / 空闲锁
profiles.py                profiles.dat 档案存取（主机记录）

core/                      纯逻辑层（不 import tkinter，可离线单测）
  detect.py                系统探测与方言（windows/unix/busybox…）
  session.py               LocalSession / SSHSession，统一 run()/run_admin()/tracer
  telnet_session.py        Telnet 会话
  sessionmgr.py            会话池（sid、存活、重连）
  hosts.py / creds.py      主机清单 / 凭证库（纯函数 + 薄存储）
  proxies.py               代理链（parent_id 串链、成环检测）
  scripts.py               脚本库（24 条内置 + 自定义；内置不可删改）
  audit.py                 操作审计（按天 jsonl、分页、导出）
  aiops.py                 模型配置（多模型）、OpenAI 兼容引擎、dsh 引擎
  agent.py                 智能体：人设 / 技能 / 工具 / 自驱循环
  aitrace.py               AI 活动流的行格式化（纯函数）
  redact.py                出口脱敏（私钥、口令、token）
  parsers.py               各命令输出 → 表格行

ui/                        控件与会话内视图
  table.py                 DataTable / Segmented / Pill / KVGrid
  forms.py                 FormDialog / InfoDialog / confirm / toast
  terminal.py              _TermShell + TerminalWindow(Toplevel) + TerminalPane(Frame)
  tabs.py                  顶部页签栏（可拉出/关闭）
  md.py                    极简 Markdown 渲染（标题/列表/行内码/代码块）
  logpanel.py / statusbar.py / tray.py / icon.py

pages/                     页面（都是 Mixin，多继承拼进 ToolboxApp）
  hosts_page.py            主机管理（清单 + 连接与设备两段）
  cred_page.py / proxy_page.py / script_page.py / log_page.py
  ai_dock.py               右侧智能运维助手（会话流 / 工具卡 / 选模型 / AI 活动流）
  model_page.py            模型管理清单（一条配置一个模型）
  web_page.py / db_page.py / security_page.py / cron_page.py / files_page.py …
  settings_center.py       设置中心壳

data/                      运行时（**绝不入库**）
  profiles.dat creds.dat proxies.dat   密文档案
  scripts.json             自定义脚本（明文，正文本就不含凭据）
  ai.json agent.json       模型与助手配置（Key 为密文）
  logs/audit-YYYYMMDD.jsonl            操作审计
  logs/*.log                           命令日志
```

---

## 3. 启动与打包

```bat
:: 本地跑（解释器需带 tkinter + cryptography）
run.local.cmd

:: 打包（输出 TerminalToolbox.exe 到项目根）
build.local.cmd

:: 自测（全部带 --no-probe，避开受限环境的 schtasks/reg）
_selftest  _redacttest  _inlinetest  _sectest  _sectest2  _dbtest  _crontest
_sitetest  _newpages  _restest  _restest2  _tabtest  _docktest
_aitracetest  _aimodeltest  _tsmoke2
```

解释器：`C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`

打包注意：`toolbox.spec` 的 `hiddenimports` 必须补齐 `core.*` / `pages.*` / `ui.*`
（这些是运行时 import，静态分析会漏）。漏了的表现是 exe 启动即崩或缺页。

---

## 4. 本版本（v1.0.4）新增

| 模块 | 内容 |
|---|---|
| 主机资源 | 主机管理 / 代理池 / 凭证管理 / 脚本库 / 日志管理 五页；导航 12 → 16 页 |
| 外壳 | 顶部页签栏 + 终端「嵌入页签 ↔ 拉成独立窗口」双向搬上下文（正文 + 命令历史） |
| AI 活动流 | 助手调用模型的请求/响应、落设备上的命令与输出、rc，全部实时写进终端；可关 |
| 多模型 | 一条配置一个模型：清单式管理，可切当前、试连、删除；底部「⌄ 模型」浮层可搜 |
| 助手界面 | Markdown 渲染（标题/列表/行内码/代码块带复制/链接）、动作条、引导页首条即收 |
| 布局 | 侧栏常驻窗口最右、宽度可拖并记忆；终端在左、助手在右可同时看 |

---

## 5. 已知取舍与坑（接手必读）

- **不要再往远端装东西**：所有功能必须能用纯命令实现，做不到就降级为「连上才采」。
- **布局别自作主张**：页签位置、面板层级由用户定。改前先问。
- **终端与助手的容器层级**：`paned` 必须挂在窗口层（`app._body`），挂在页面里会导致
  终端一占满整页就把助手侧栏盖住。
- **`Nav` 的 pack 顺序**：`self.nav.pack(..., before=self.workspace)`，否则导航会被
  排到终端与助手中间。
- **Text 高度自适应**：`ui/md.py` 里必须先有真实宽度再算 `displaylines`，
  `width=1` 时算出来是「每个字一行」，会撑出一大块空白。
- **AI 执行命令去重**：`app._ai_owns_cmd()` + `_ai_mark_cmd()`，否则 tracer 会再喂一遍。
- **模型配置双写**：`ai.json` 的扁平字段（provider/base/model/key_enc…）永远是「当前模型」
  的镜像；切模型 = 把档案写回扁平字段，引擎不用改。
- **老配置迁移**：只有扁平字段的老 `ai.json` 会静默升级成一条「默认模型」，不弹窗、不丢 Key。
- **最后一条模型删不掉**：删空助手就没模型可用。
- `_fsmoke` 偶发「远程栏自动刷新」时序失败，重跑即过，属 flaky，不是回归。

---

## 6. 交付物说明（同级目录）

| 文件 | 内容 |
|---|---|
| `Terminal-GUI-Toolbox-1.0.4-windows-x64-portable.zip` | 便携运行包：exe + 使用说明 + LICENSE，解压即用 |
| `terminal-gui-toolbox工具包.zip` | 源码 + exe + 构建脚本 + 全部自测，接手可直接改 |
| `terminal-gui-toolbox(github).zip` | GitHub 仓库的 `git clone` 快照（含 `.git`，可续提交） |
| `terminal-gui-toolbox项目知识库.zip` | 文档：README、使用说明、改造说明、交接说明、deb 构建说明 |
| `terminal-gui-toolbox-deb构建说明-1.0.4.md` | Linux 下打 `.deb` 的完整步骤与文件模板 |

---

## 7. 还没做的（下一版候选）

1. 计划任务页的可视化编辑（现在只能看 + 简单增删）。
2. 代理链的连通性验证（现在只做静态成环检测）。
3. 脚本库的「按主机批量执行 + 结果对比」。
4. 助手多会话（现在单会话，清空即重来）。
