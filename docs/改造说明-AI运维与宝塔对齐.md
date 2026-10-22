# 改造说明 · AI 运维与宝塔功能对齐

> 这份文档是**给下一次会话看的**。上下文会丢，这里不丢。
> 最后更新：2026-10-22 22:45
> 仓库：`N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`（GitHub `thomachq/terminal-gui-toolbox`，当前 `v1.0.2`）

---

## 〇、一句话定位（用户自己的说法，以此为准）

> **「功能其实挺接近宝塔面板和宝塔终端的整合版，外加其他 AI 智能体可以调用的 API。」**

拆开就是三块，缺一不可：

| 块 | 对标 | 我们的形态 | 差别 |
|---|---|---|---|
| 远端设备管理 | 宝塔面板（Linux + Windows） | 本程序里的若干页：连接 / 诊断 / 文件 / 路由 / 防火墙 / Docker / Web 服务与站点 / 隧道证书 / 数据库 / 安全中心 / 计划任务 | **对方不用装任何东西**，全靠 SSH/Telnet + 对方已有的命令与配置文件 |
| 多会话终端 + AI 运维 | 宝塔终端（BT-Xterm） | 现有连接即会话；AI 页在同一界面对话、逐条生成命令、远端执行 | **不绑账号**，模型可以用自家 Key / 本地 Harness |
| 对外接口 | （宝塔没有等价物） | 「智能体接入」页：本地 HTTP API + 会话池 | 这是**我们独有的**，别的智能体可以调我们的连接 |

**边界**：凡是「断网就废」（在线申请证书、官方规则库、账号绑定）或「要往远端装东西」（agent、常驻采集器、软件商店）
的功能都不做 —— 这两条是硬约束，不是偏好。

---

## 一、目标（用户原话，别改味）

1. **「我的目标就是用这个GUI工具箱连接就能用，不要像宝塔一样需要在各台设备安装」**
   —— 零安装：全部走 SSH / Telnet + 对方已有的命令与配置文件，不往远端写 agent、脚本、常驻采集器。
2. **「是依托官网在线服务不做，不是不连互联网」**
   —— 判断标准是「**断网还能不能用**」，不是「联不联网」。调模型 API 属于正常联网，可以做；
   凡是要注册/登录宝塔账号、要官方云端下发规则或授权的，一律不做。
3. 「也不是所有功能都要，比如他的 FTP 选项卡」—— **FTP 不做**。
4. 「宝塔面板软件商店，你也不要做」—— **软件商店不做**。
5. 「网站那一块，并入我的 Web 服务，里面可以涉及域名绑定的内容」
   —— **不新开「网站」页**，扩展现有「Web 服务」页，加站点与域名绑定。
6. 「安全中心、数据库要做」—— **两项都要做**（不是只读壳子，但写操作要备份+回滚）。
7. 「AI 的话也做……加个可以 AI 操作的 Terminal，AI 依托 DeepSeek Harness，
   可以参考 Cherry Studio 里的 DeepSeek Harness 运行时」—— **要做 AI 运维页**。
8. 「然后允许添加自己的 AI API」—— **服务商可自定义**（base / model / key 自填），不止 DeepSeek。

---

## 二、宝塔解析结果（两份都摸清了）

### 2.1 Windows 版（就在本机，源码可读）

| 项 | 值 |
|---|---|
| 进程 | `btPanel.exe` 监听 `::8888` |
| 安装目录 | `D:\BtSoft`（panel / php / mysql / tomcat_site / springboot / wwwlogs / wwwroot / backup / cron） |
| 安全入口 | `D:\BtSoft\panel\data\admin_path.pl` = `/LBjfQcf0` |
| 真实地址 | `http://192.168.3.107:8888/LBjfQcf0`（直接访问 `/home` 必然 404，这是入口校验，不是服务没起） |
| 登录 | `class/userlogin.py`：客户端发 RSA(1024) 加密的 md5(密码)，服务端 `md5(密码+salt)` 比对；RSA 密钥**按 session 现生成、不落盘**，首次失败后要验证码 → 命令行登录不划算，**直接读源码更清楚** |
| 语言 | Python（Flask + gevent websocket），前端 Vue SPA |

功能面（`class/*.py`）：`panelSite` 网站、`panelSSL`/`acme_v2` 证书、`panelMysql`/`panelMssql` 数据库、
`ftp`、`files` 文件、`crontab` 计划任务、`firewalls` 防火墙、`monitor` 监控、`log_analysis` 日志、
`ssh_terminal` 终端、`webshell_check`/`san_baseline`/`panelDefense` 安全、`iis`/`apache`/`panelPHP` 运行环境、
`panelPush`/`panelMessage` 消息推送、`panelDnsapi` DNS API。

### 2.2 Linux 版（`192.168.3.7:39598`，已打通只读）

- 安全入口 `/a8f9d60d/`；缺它任何路径都跳「安全入口校验失败」（`/home` 会 302 到 `/login` 再报错）。
- **登录链路**（与 Windows 版不同，别混）：
  1. GET 入口页（浏览器 UA）拿 cookie；
  2. 从 HTML 取 `window.vite_public_login_token` 与 `window.vite_public_encryption`（RSA 公钥，含 `\n` 转义）；
  3. **POST 到 origin 根的 `/login`**（`/<入口>/login` 是 404），`application/x-www-form-urlencoded`；
     发 JSON 会被当空表单（回「用户名或密码不能为空」）；
  4. `username = rsa(md5(md5(username + login_token)))`、`password = rsa(md5(md5(password) + "_bt.cn"))`（PKCS#1 v1.5 + base64）；
  5. 成功返回 `{"status": true, "msg": "登录成功,正在跳转..."}`。
- 业务接口要签名头（`权限认证失败`），`x-http-token` / `X-Request-Token` 试过不通；**但不用接口**：
  登录后 GET 入口页，HTML 里 `window.vite_public_menu` 就是完整菜单树。
- **23 个菜单**：首页 / 网站（PHP、java、Node.js、Go、Python、.NET、反向代理、HTML、其他）/ FTP /
  数据库 / Docker / 监控 / 系统 / 安全中心 / WAF / 文件 / 日志 / WP Tools / 域名 / SSL / 多用户 /
  邮局 / 终端 / AI / 节点管理 / 计划任务 / 软件商店 / 设置。

> ⚠️ 该面板的账号口令使用过一次后**已全部删除**，没有留任何文件。要再进就重新弹窗。

---

## 三、功能矩阵（宝塔 23 项 → 我们的取舍）

| 宝塔 | 决定 | 理由 / 做法 |
|---|---|---|
| 网站 | **并入 Web 服务页** | 站点清单：域名 → 根目录 → 反代 → 运行环境 → 证书 → 日志；补**域名绑定**的查看与新增 |
| 日志 | **做**（并入 Web 服务页或独立块） | 纯读文件，零安装 |
| 数据库 | **做** | MySQL / SQL Server / Redis：库表大小、连接数、慢查询；写操作先备份 |
| 安全中心 | **做** | 登录审计（成功/失败）、SSH 配置体检、暴露端口、防火墙状态；只读为主，改配置要备份 |
| 计划任务 | **做**（第二批） | `crontab -l` / `schtasks`，改动前给 diff + 备份 |
| 监控 / 系统 | **做** | CPU/内存/磁盘/负载/进程/服务状态，连上才采，不留常驻 |
| SSL | **做一半** | 本地证书查看/校验/部署（已有）；**在线申请（Let's Encrypt）不做** |
| Docker / 文件 / 终端 / 防火墙 / 路由 / 证书签发 | 已有 | 不重复造 |
| FTP | **不做** | 用户点名 |
| 软件商店 | **不做** | 用户点名，且要往远端装包 |
| WAF / WP Tools / 邮局 / 多用户 / 域名 / 设置 / 节点管理 | **不做** | 要么官方云端下发规则，要么面板自身账号体系，要么要装服务端组件 |
| AI | **做** | 见第五节；断网时降级为不可用，属正常 |

---

## 四、施工顺序

| 批次 | 内容 | 状态 |
|---|---|---|
| 1 | **AI 运维页**（`core/aiops.py` + `pages/ai_page.py`） | `core/aiops.py` 已写完，页面与接线**未做** |
| 2 | **Web 服务页扩展**：站点清单 + 域名绑定 + 站点日志 | 未开始 |
| 3 | **安全中心页**（`core/secops.py` + `pages/sec_page.py`） | 未开始 |
| 4 | **数据库页**（`core/dbops.py` + `pages/db_page.py`） | 未开始 |
| 5 | 计划任务页（带 diff / 备份） | 未开始 |

每批都要：跑全量自测（`_selftest` `_tsmoke` `_tsmoke2` `_fsmoke` `_sectest` `_redacttest` `_termsmoke`
`_inlinetest` `_vpnsmoke` `_uismoke` `_conntest` `_websmoke`）→ 新增对应 `_*smoke.py` → 重打包 exe
（`ToolboxSpec` 的 hiddenimports 要加新模块）→ 更新 `README.md` 与 `使用说明.txt` → 提交（GPG 签名，需用户输口令）→ 推四端。

---

## 五、AI 运维页设计（核心设计，别推翻）

### 5.1 一条铁律

**AI 自己驱动一个「生成命令 → 远端执行 → 读回输出 → 再生成」的循环，但执行通道必须是我们的 session 层。**

原因：这个工具箱连的是**远端设备**，而模型的工具默认跑在**本机**，直接放权等于在错误的机器上执行命令。
所以模型不给工具，只给「下一步要跑什么」；由我们用 SSH/Telnet/本机会话替它跑，再把输出喂回去。

工作方式参照助手自身用 ssh 技能连设备排障的样子：**边跑边看、根据真实输出决定下一条**，
不是一次列一堆命令让人点。区别只在于——危险动作要人点头。

Cherry Studio 的 `packages/dsh-bridge` 是一套 Unix socket + dsh 插件 + 审批桥的重型方案
（`protocol.ts` 里的 `guard/check`、`approval/ask` 就是审批通道），**我们不抄它的架构，只借鉴它的审批思路**。

### 5.2 两个引擎（`core/aiops.py` 已实现）

| 引擎 | 用法 | 说明 |
|---|---|---|
| `OpenAIEngine` | `POST {base}/chat/completions` | 覆盖 DeepSeek 官方 / Ollama / LM Studio / 任意自建网关；Key 用 `crypto_store` 加密存 `data/ai.json` |
| `DshEngine` | `dsh --profile headless --json <prompt>` | 用本机 DeepSeek Harness 已配好的模型；解析 NDJSON，只取 `{"type":"final","text":...}`；**明确要求它不用工具** |

`find_dsh()` 查找顺序：`DSH_CLI` 环境变量 → `PATH` 上的 `dsh` →
`%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd`。

已核实：`dsh` 存在、headless 能跑通、事件流是 NDJSON；但本机 **`DEEPSEEK_API_KEY` 未写入 dsh 的凭据服务**，
所以直接用 dsh 会报 `MISSING_CREDENTIAL`。要么在 Harness 的 Models 页写入 Key，要么在我们的页里配 Key 走 OpenAI 通道。

### 5.3 服务商预设（`PROVIDERS`）

`deepseek`（官方，需 Key）/ `dsh`（本机 Harness，无需 Key）/ `ollama`（11434）/ `lmstudio`（1234）/
`custom`（**用户自填 base + model + key**，即「添加自己的 AI API」）。

### 5.4 协议：模型只回 JSON

系统提示词要求模型**只输出一个 JSON 对象**：

```json
{"analysis": "不超过 120 字的判断", "done": false,
 "commands": [{"cmd": "单行命令", "why": "为什么", "risk": "read|write|danger"}]}
```

`parse_plan()` 容错：先剥 ``` 围栏，再按括号配对取第一个 JSON 对象；解不出来就把整段当 `analysis`、
命令列表留空——**绝不猜命令**。模型自报的 `risk` 与本地规则冲突时，**取更危险的那个**。

### 5.5 风险分级（`classify_risk`）

- `danger`：`rm -rf`、`mkfs`、`dd of=/dev/`、`shutdown/reboot`、`passwd/userdel`、`iptables -F`、
  `>/etc/`、`curl|sh`、`diskpart/bcdedit`、`Remove-Item -Recurse -Force` 等
- `write`：包管理、`systemctl start/stop`、`docker rm`、`sed -i`、`cp/mv/rm`、`chmod/chown`、`crontab`、`reg add` 等
- 其余 `read`

界面表现：`read` 可「全部批准」，`write`/`danger` 必须逐条点，`danger` 再弹一次确认。

### 5.6 执行循环（关键，别做成「一次列一堆命令」）

用户原话：「AI 的操作，可以参考你使用 ssh-device-connect 连接技能的做法，就是生成命令，
**自动在远端实时执行**」。所以是一个**自驱循环**，不是批量审批清单。

```
用户一句话
   ↓
┌─ 组装上下文（设备信息 + 已有对话 + 本轮已跑过的 [命令, rc, 输出尾部]）─┐
│                                                                      │
│  模型 → {"analysis": "...", "commands": [...], "done": false}          │
│      ↓                                                               │
│  逐条过风险门（见 5.5）                                                │
│      ↓                                                               │
│  执行：session.run(cmd) / run_admin(cmd) / CLI 通道                    │
│      ↓                                                               │
│  把 (cmd, rc, out_tail≤1200字) 追加进本轮记录，回到顶部                 │
│                                                                      │
│  退出条件：done=true / 达到 max_steps / 用户点停止 / 危险命令被拒绝      │
└──────────────────────────────────────────────────────────────────────┘
   ↓
收尾：模型给一句总结（也可由最后一步的 analysis 兼任）
```

要点：

- **`done` 字段**：模型认为查清了就置 `true` 并停止；没给 `done` 时，命令全跑完且命令列表为空 → 视为完成。
- **`max_steps`**（默认 8）：防跑飞。到顶就停，提示「已达步数上限，可继续」。
- **输出截断**：每次回填只带尾部 1200 字符（`tail -n` 已在命令里，回填再截一次），防上下文爆炸。
- **失败即停还是继续**：`rc != 0` 时把 stderr 回填给模型，让它自己判断要不要换路子；连续 3 次失败强制停。
- **同一条命令不重复跑**：本轮已执行过的命令记指纹，模型重复给就跳过并提示它。
- **可中断**：一个 `threading.Event` 做取消位，页面「停止」按钮置位，循环在下一步检查处退出。

### 5.7 三种执行模式（配置项 `exec_mode`）

| 模式 | 只读命令 | 会改动 | 高风险 |
|---|---|---|---|
| `auto`（默认） | 自动跑 | 自动跑 | **弹窗确认**（可勾「本次会话不再问」） |
| `readonly_auto` | 自动跑 | 弹窗确认 | 弹窗确认（红色，二次确认） |
| `confirm_all` | 弹窗确认 | 弹窗确认 | 弹窗确认（红色，二次确认） |

`auto` 是用户要的手感（像 assistant 自己连上去查）；`confirm_all` 给不放心的时候用。
不管哪种模式，`danger` 都**不允许**「本次会话不再问」。

### 5.8 页面骨架（待写 `pages/ai_page.py`）

```
[服务商] DeepSeek 官方 · deepseek-chat   [模式 auto▾] [设置…] [停止] [清空]
[对话区] 只读文本框，流式追加：
         你 → AI 分析 → ▶ 执行的命令（灰）→ 输出摘录（等宽）→ …→ AI 总结
         每条危险命令处留一个 [允许/拒绝] 卡片，超时视为拒绝
[输入框] 多行，Ctrl+Enter 发送；空态提示「描述你要做的事，例如：找出 80 端口的服务并看它日志」
[状态栏] 步数 3/8 · Token 1.2k · 引擎 dsh-headless
```

接线点：`app.py` 的导航表加 `("ai", "AI 运维")`、`_build_*` 注册、`_pages` 计数断言（现为 10，将变 11）、
`toolbox.spec` 的 hiddenimports 加 `core.aiops`、`pages.ai_page`。

执行命令时用 `self.bg(...)` 起后台线程，结果用 `self.post(...)` 回主线程；`host_kind == "network"` 走 CLI 通道；
需要提权时靠 `NeedElevation` 自动弹窗（`bg(..., allow_elev=True)` 已有）。
输出同时写 `_log_raw`（进全局命令日志）与对话区。

> `core/aiops.py` 目前只实现了「一问一答」，需要在 `Assistant` 上加
> `run_task(question, executor, on_event, mode, max_steps, cancel: Event)`，
> 循环与退出条件按 5.6。`executor` 由页面注入（闭包持有 session 与提权逻辑），
> 这样 `core/aiops.py` 依然可以单测（注入假 executor，不碰真设备）。

---

## 六、后续三页的抓手

### 6.1 Web 服务页扩展（站点 + 域名）

- 复用 `core/webstack.py` 已有的服务探测与配置路径解析，新增：
  - `sites_of(kind, config_path)` → `[{server_name, root, proxy_pass, php, ssl_cert, log_access, log_error}]`
  - Nginx：`server {}` 块（`server_name` / `root` / `location` 里 `proxy_pass` / `ssl_certificate` / `access_log`）
  - Apache：`<VirtualHost>`（`ServerName` / `DocumentRoot` / `ProxyPass` / `SSLCertificateFile` / `ErrorLog`）
  - Caddy：站点块（`tls` / `reverse_proxy` / `root`）
  - **域名绑定**：读取 + 追加 `server_name`（改前备份，`nginx -t` 失败自动还原）
- 站点日志：`tail -n 200` 读 `access_log`/`error_log`，支持按站点过滤与关键字高亮。

### 6.2 安全中心（`core/secops.py`）

| 检查 | 数据来源 |
|---|---|
| 成功登录 | `last -n 30`、`/var/log/secure`、`journalctl -u ssh` |
| 失败爆破 | `/var/log/auth.log`、`/var/log/secure` 里 `Failed password` 聚合 IP |
| SSH 配置体检 | `/etc/ssh/sshd_config`：`PermitRootLogin`、`PasswordAuthentication`、`Port`、`MaxAuthTries` |
| 暴露端口 | `ss -tulnp` 与防火墙规则对照，标出「对外且无规则」的端口 |
| 防火墙 | 复用 `core/firewall.py` |
| 账户 | `/etc/passwd` 里的 UID 0 账户、空口令账户（`passwd -S`） |

Windows 侧用 `Get-WinEvent` 读 4624/4625 事件。**只读优先**，改 sshd 要备份 + `sshd -t` 校验。

### 6.3 数据库（`core/dbops.py`）

- MySQL/MariaDB：`mysql` 客户端可用则 `SHOW DATABASES` / `information_schema` 取表大小与行数、
  `SHOW PROCESSLIST` 看连接；只读用 `--batch --skip-column-names`。
- SQL Server：`sqlcmd -Q`。
- Redis：`redis-cli INFO` / `DBSIZE`。
- 口令来源：已保存的凭据（`crypto_store`）+ 连接页那套「保存口令」逻辑，**不要新增明文路径**。

---

## 七、宝塔终端（BT-Xterm）解析 —— 第三份参考

安装位置：`C:\Users\Thomas\AppData\Local\Programs\BT-Xterm`（Electron，497 MB，`resources/app.asar` 107 MB）。
它自带的 Agent 文档目录 `resources/agent-docs/` 有三份原文，直接读比翻 asar 快：

| 文件 | 里面有什么 |
|---|---|
| `宝塔AI运维助手自我介绍与设计边界.md` | 运行循环、工具面、动手三条纪律、**审批三档**、记忆规则 |
| `Agent配置MCP指南.md` | MCP 的 stdio/http/sse 配置字段、探针自证、错误码、痕迹卫生 |
| `技能安装与使用指南.md` | 技能 = Markdown + frontmatter，按 `@slug` 引用 |

### 7.1 它的界面（三屏）

1. **主机管理**：左栏 `主机管理 / 代理池 / 凭证管理 / 脚本库 / 日志管理`，右栏主机卡片
   （添加主机 / 本地终端 / 导入主机，视图可切列表·宫格·树，按分组筛选 + 搜索）。
2. **设置 → AI Agent**：`模型提供商 / Agent CLI / MCP 服务 / 工具 / 技能 / 安全控制`；
   模型提供商列表里能直接「添加提供商」「模型排序与默认模型」。
   注意：它的「宝塔 AI」是绑宝塔账号才能用的云端服务 —— **这一条我们不抄**（断网即废）。
3. **终端主界面**：左侧可见终端（`top` 实时刷新），右侧 AI 助手侧栏：
   AI 分析 → **执行命令卡片（标「待确认」，带「拒绝 / 批准」和「原始参数」）** → 底部输入框（可 `@` 引用上文、`/` 调用工具）。

### 7.2 它写明的行为约束（比我原来设计的更严，值得照搬）

| 条 | 原文要点 | 我们怎么落 |
|---|---|---|
| 执行可见优先 | 机器操作默认走**可见终端**那条通道，后台静默执行是例外，且要说明原因 | AI 的命令一律回显在对话区的命令卡片 + 状态栏，不静默 |
| 会话唯一 | `session.open` 是唯一创建入口，一台主机只留一条；`session.run` 只复用绝不新建 | 已有 `open_session` + 会话池，AI 只拿现成会话 |
| 先定机器 | 会落到具体机器的动作（**含只读检查**）先确认是哪台；能唯一确定就直接做，真有歧义才反问 | 上下文里带 `host_kind / os / vendor`，AI 首个动作常是 `hostname` 与 `uname` |
| 先查后报 | 只读查证不必等确认，先查清再下结论；查不到就说查不到 | 只读命令 `auto` 模式直接跑 |
| 写前等应允 | 改状态的动作要先报「查到什么 + 拟做什么 + 影响面 + 回滚办法」，等明确应允 | 与我们 `write` 档一致 |
| 档位不免纪律 | 全权档只是免了审批弹窗，「先报告等应允」照旧 | 我们 `auto` 模式同样保留 danger 必问 |

### 7.3 它的审批三档 → 我们的映射

| BT-Xterm | 语义 | 我们（`exec_mode`） |
|---|---|---|
| `request_approval` | 只读直接跑；写/高风险逐次批准 | `confirm_all` |
| `assist_approval` | 只读直接跑；中高风险多由自动判断放行 | `auto`（默认） |
| `full_access` | 写不再逐次审批，仍审计留痕 | 不提供 —— 我们连的是别人的生产机，没有审计后端，不做 |

**所以我们的三档不是照抄，而是取它中间那档做默认，并额外保留一个「只读也不自动」的极保守档。**

### 7.4 不抄的部分（明确记下来，免得以后反复讨论）

| 它的设计 | 为什么不抄 |
|---|---|
| 保险库（Vault）+ `******` 哨兵回写 | 我们用系统凭据加密（`crypto_store`）就够，多一套哨兵规则只会多一种错法 |
| MCP 服务的 增/改 与「落库恒未启用、必须人工开开关」 | 我们不做 MCP 服务器管理；AI 通过我们自己的一套动作跑，动作集合是封闭的 |
| 「宝塔 AI」绑定账号的云端模型 | 断网即废，且要账号 |

> 技能包（frontmatter + `@slug`）原先记的是「不抄」，后来用户点名要那 8 个内置技能，
> 于是**只抄数据、不抄插件体系**：技能就是 Markdown 文本，由我们自己的加载器读，
> 不做 `@slug` 语法、不做技能市场、不执行技能目录里的脚本。详见 7.6。

### 7.6 8 个内置技能（用户点名要的，已落地）

宝塔终端把技能定义**硬编码在主进程**里（`app.asar.unpacked/out/main/index.js`
的 `DEFAULT_SKILLS` 数组，8 份 Markdown 字面量），首次启动时落到 `{dataDir}/skills/<slug>/SKILL.md`。
我们按同样结构搬了过来：

| slug | 名称 | 什么时候用 |
|---|---|---|
| `server-performance-diagnosis` | 服务器资源与性能检查 | 磁盘满、CPU/内存高、负载高、iowait |
| `security-diagnosis` | 安全诊断与加固 | 挖矿/反弹 shell/Webshell、SSH 配置、权限 |
| `service-cron-diagnosis` | 服务与计划任务诊断 | systemd/init.d 起不来、端口冲突、cron 没跑 |
| `network-firewall-diagnosis` | 网络与防火墙诊断 | firewalld/ufw/iptables、端口、DNS 解析 |
| `website-ssl-diagnosis` | 网站与 SSL 诊断 | 502/504、响应慢、证书过期、证书链 |
| `database-diagnosis` | 数据库诊断 | MySQL/MariaDB 连接数、慢查询、缓冲池、锁等待 |
| `log-analysis` | 日志分析 | 错误日志按级别分级 + 时间线定位 |
| `file-ftp-diagnosis` | 文件与 FTP 诊断 | 目录权限、磁盘占用、大文件、FTP 被动模式/chroot |

每份正文都是同一套骨架：**目标 → 1 探测（Probe）→ 2 检查（Check）→ 3 诊断（Diagnose）
→ 4 修复（Fix）→ 5 注意事项**，且都写着「修改配置/删除文件/重启服务必须先获授权」。

落地方式（`core/ai_skills.py` + `core/ai_skills_builtin.py`）：

- 正文从 asar 里按 JS 字符串转义规则还原（`\uXXXX` 直接转字符，**不要**经 latin-1 中转，
  否则中文全变 `æ°æ®åº`），生成 `core/ai_skills_builtin.py` 常量表，随包一起打进去。
- 首次运行落到 `data/skills/<slug>/SKILL.md`，写 `.seeded` 标记；**用户删掉某个技能就不再塞回来**
  （与宝塔一致，尊重删除）。
- frontmatter 只支持宝塔那个子集：`key: value`、`description: >` 折叠块、`tags: [a, b]`、
  缩进 `- a` 列表、逗号串；嵌套映射 / 多文档 / 缺 `name` 一律判坏跳过（事件名照抄宝塔：
  `skill.missing_entry` / `skill.bad_frontmatter` / `skill.invalid_slug` / `skill.file_too_large`）。
- 旧扁平布局 `skills/<slug>.md` 仍能读，目录式优先。
- 选用方式：AI 页输入区左边多一个「技能」下拉（`自动判断` + 8 个 + 自装的），
  落盘到 `data/ai.json` 的 `skill` 字段；`自动判断` 按 slug / 名称 / tags / 描述里的词打分，
  命中理由显示在对话区（`· 技能：服务器资源与性能检查（命中 磁盘、磁盘满）`）。
- 注入方式：`ai_skills.render(skill)` 拼成一段塞进 `SYSTEM_PROMPT_TASK` 的 `{skill}` 槽，
  外面包 `----- SOP 开始/结束 -----`，并明确写「SOP 只是排查顺序，**不改变输出格式**」——
  模型仍然只回那一个 JSON，命令照样经会话通道执行、照样按风险分级。
- 「技能库」窗口能看正文、装第三方技能（`.md` / `.zip` / 含 `SKILL.md` 的目录）、
  一键打开 `data/skills/`。**不执行技能目录里的任何脚本**（技能只是文本）。

| 代理池 / 脚本库 | 不是这套工具箱的诉求 |

### 7.5 可以借的一点：AI 侧栏与终端并排

它的布局是**左终端、右 AI**，命令卡片就在 AI 侧栏里。我们的 AI 页更简单些：
对话区 + 命令卡片 + 输入框，执行结果直接引用会话通道的回显，不再单开终端窗口。

---

## 八、参考资料位置

| 东西 | 路径 |
|---|---|
| Cherry Studio 源码压缩包 | `\\192.168.3.12\Public\软件\AI生成的应用\Cherry Studio\Cherry-Studio-Video\cherry-studio-video(GitHub).zip`（287 MB，用 `zipfile` 按需读，别全解） |
| 其中值得读的 | `cherry-studio-video/packages/dsh-bridge/src/{protocol,runtime,policy,destructiveCommand,plugin}.ts` |
| DeepSeek Harness GUI | `C:\DeepSeek-Harness-GUI`（`launcher.ini` 里 `port=3080`；`home/` 是它的 `DSH_HOME`） |
| dsh 命令行 | `C:\Users\Thomas\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd` |
| 宝塔 Windows 源码 | `D:\BtSoft\panel`（`class/` 看功能，`data/admin_path.pl` 看入口） |
| 宝塔 Linux 版本体 | `http://192.168.3.7:39598/a8f9d60d/` |
| 宝塔终端（桌面端） | `C://Users//Thomas//AppData//Local//Programs//BT-Xterm`；Agent 文档在 `resources/agent-docs/`（三份纯 Markdown，优先读这个，不必解 asar） |

---

## 九、已完成 / 未完成

**已完成**
- 两份宝塔面板解析（上表）；用户口令已清理，无残留。
- `docs/改造说明-AI运维与宝塔对齐.md`（本文），需求与解析都在这里。
- `core/aiops.py`：服务商预设、Key 加密存档、OpenAI 兼容引擎、dsh 引擎、JSON 方案解析、
  风险分级、上下文拼装、`Assistant` 一问一答门面。**尚未接线。**
  已过离线冒烟（不联网、不碰设备）：风险分级九例全对（模型把 `rm -rf /` 标成 read 时被规则抬到 danger）、
  围栏 JSON 能剥、无 JSON 时不猜命令、上下文拼装正常、`make_engine` 在 custom/dsh 间切换正常、
  `find_dsh()` 能找到 `dsh.CMD`。

**未完成**
- Web 服务页的站点与域名绑定
- 安全中心页、数据库页、计划任务页
- README / 使用说明 增补；重打包 exe；`v1.0.3` Release

**2026-10-22 深夜 · 8 个内置技能（宝塔终端那 8 份 SOP）**

- `core/ai_skills_builtin.py`：8 份 SKILL.md 原文（从 BT-Xterm 的 `DEFAULT_SKILLS` 里逐字还原）。
- `core/ai_skills.py`：`SkillStore`（落地 / 扫描 / 安装 / 坏文件护栏）、frontmatter 子集解析、
  `pick()` 关键词打分选技能、`render()` 拼提示词、`preferred_slug()` 手动优先。
- `core/aiops.py`：`SYSTEM_PROMPT_TASK` 加 `{skill}` 槽，`run_task(..., skill_text=...)`。
- `pages/ai_page.py`：输入区加「技能」下拉（选完存盘）+「技能库」窗口
  （看正文 / 导入 .md·.zip / 打开目录 / 用它）；对话区标出本轮命中哪个技能。
- `toolbox.spec` 加两条 hiddenimports；`_aiskilltest.py` 新增 54 项（解析 / 挑选 / 安装 / 注入 / 旧布局）。
- `_aismoke.py` 加第 D 组 14 项（45 → 59）。全量自测见 §九 末尾。

**2026-10-22 深夜 · AI 运维页与多设备已落地**

- `core/aiops.py` 增加 `Assistant.run_task()` 自驱循环（生成 → 远端执行 → 读回 → 再生成），
  `parse_plan` 支持 `done`，新增 `SYSTEM_PROMPT_TASK`。三档模式：
  `auto`（只读与一般写直接跑，danger 必问）/ `confirm`（写也问）/ `readonly`（只跑只读）。
  停止用 `should_stop`，步数上限 `max_steps`，单条输出按 3000 字裁剪后再喂回模型。
- `pages/ai_page.py`：模型设置（服务商可自定义）、模式下拉（存 `data/ai.json` 的 `exec_mode`）、
  对话区（命令按风险着色）、Ctrl+Enter 发送、停止、清空、复制对话。
  **命令必须显示在界面上**（照宝塔终端的「执行可见优先」），不做静默执行。
  确认弹窗从工作线程经 `post` 回流到主线程，用 `threading.Event` 阻塞等结果（超时当拒绝）。
- `core/sessionmgr.py` + 顶部设备标签：多台设备同时连着，点标签切换。
  `app.session / device / dialect / sampler` 变成指向「当前那台」的 property（`session` 带 setter
  兼容旧写法），页面代码一行没改。`_disconnect` 只断当前那台，关完自动回退到剩下那台或本机。
  标签栏**只在有设备时才显示**，否则白占一行高度、小屏下会把连接页挤到看不全。
- 自测：`_aismoke.py` 45 项、`_mdevsmoke.py` 34 项，全量 14 套 0 失败。

**2026-10-22 夜 · 顺手修掉的（已过全量自测）**
- 「给智能体的说明」按钮点了像没反应：原来只往剪贴板塞内容、状态栏闪一行字。
  现在改成**先弹窗**（可读、可选中）+ 全文同时进剪贴板，窗内加「复制全文」按钮，
  按钮文字会变「已复制 N 字」。`InfoDialog` 新增 `copyable` 参数。
- 新增 `App.copy_text(text, label)` 统一剪贴板写入（`update_idletasks` 强制提交），
  `api_copy_token` / `api_copy_sid` 一并改走它。
- `_uismoke.py` 加第 5 组用例（7 项），**103 → 110 项**。全量 12 套 0 失败：
  71 / 65 / 49 / 52 / 30 / 32 / 40 / 27 / 110 / 106 / 98 + `_selftest`。
- 新增第七节：BT-Xterm（宝塔终端）解析与三档审批映射。
