# 待办清单 v1.0.7（2026-10-23 22:55 落盘，23:35 增补 T8/T9）

> 用途：上下文被压缩后，照着这份直接干。每条都写清「改哪个文件、判据是什么」。
> 源码根 `N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`
> 解释器 `C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
> 跑测试：`$PY _winstest.py` / `_tabtest.py` / `_tsmoke.py` / `_uismoke.py` …

---

## 〇、进度快照（00:5x 更新：T3–T9 全绿，只剩打包）

| 项 | 状态 | 证据 |
|---|---|---|
| T1 日志底栏 | ✅ | `_logdocktest.py` 16/16 |
| T2 ＋菜单收起 | ✅ | `_attest.py` 132/132 |
| T4 模型管理（文本 + token 三件套） | ✅ | `_modeltest.py` 94/94、`_aimodeltest.py` 39/39 |
| T6 发送/停止合一 | ✅ | `_attest.py::test_send_stop` |
| T3 助手四件 | ✅ | `_uidocktest.py` 37/37 |
| T5 本机协议瘦身 | ✅ | `_hostformtest.py` 41/41 |
| T7 中途插话 | ✅ | `_injecttest.py` 25/25 |
| T8 防火墙/路由表图形化编辑 + 自动探测 | ✅ | `_fwroutetest.py` 115/115、`_newpages.py` 73/73 |
| T9 去掉重复的「连接」 | ✅ | `_vpndedupetest.py` 29/29、`_vpnsmoke.py` 27/27 |
| 打包发版 | ⏳ 最后（下一步） | |

**T8/T9 落地后的对外接口（别再造一套）：**
- `core/firewall.py`：新增 `UfwBackend` / `FirewalldBackend`；
  `BACKEND_CLASSES` / `PROBE_ORDER` / `rank()` / `detect_backend(results)` /
  `probe_plan(device)` / `detect()`（逐个试、命中即在用档就停）；
  `supports(backend, op)` / `plan()` / `edit_cmds` / `toggle_cmds` / `undo_cmds` /
  `run_cmds`；备份用 `save_backup(root, key, text, tag="fw")`、`list_backups(root, tag)`，
  落在 `data/fw_backup/`（路由用 `tag="route"`）。
- `core/routes.py`：`edit_cmds` / `default_cmds`（Linux 用 `ip route replace`）/
  `plan(action∈add|del|edit|default)` / `undo_cmds`；`apply()` 多了 `old=` 参数。
- `core/aiops.py`：`classify_risk` 收紧成「真的在改才算 write」，只读查询
  （`ufw status` / `iptables -S` / `firewall-cmd --list-all` / `netsh ... show`）
  不再被误判；默认路由删/换、`iptables -F/-X/-P`、`netsh advfirewall reset` 判 danger。
- `pages/firewall_page.py`：进页面自动探测；按钮 新增/编辑/删除/启用停用/上移下移/撤销上一步/备份；
  双击=编辑；写前备份 + `fw.change` 审计；改完高亮受影响行。
- `pages/routes_page.py`：新增/编辑/删除/设为默认/撤销；`route.change` 审计。
- `nasops.py::MainSessionClient`：把主程序当前会话包成 NasSession（`probe/backup/
  deploy_material/patch_conf/restart/read_text/write_text` 直接复用），
  `has_sudo` 看主会话的 `can_admin()`，`close()` 不关主程序会话。
- `vpncert_gui.py`：`embedded=True` 时页签从「证书」开始（无 conn）；
  头部 `lbl_dev` + 「主机管理」跳转；部署页多了「探测当前设备」与「服务端现状」块。

**T4 已经落地的接口（T3 直接用，别再造一套）：**
- `core/modelsync.py`：`is_text_model` / `filter_text_models` / `to_int` /
  `parse_limits` / `fetch_models` / `sync_text_models` / `guess_caps`（级别 off/low/high/max）
- `core/aiops.py`：`THINKING_LEVELS`、`MODALITY_KEYS`、`clean_caps`、`clean_modalities`、
  `caps_label`、`fmt_tokens`；模型档案多了
  `kind="text"` / `caps.thinking.levels·default` / `caps.tools` / `modalities` /
  `context` / `max_input` / `max_output`（0 = 未知）
- `pages/model_page.py`：`ModelDialog`（列：ctx / max_in / max_out / caps）、
  `_ModelForm`（胶囊 + 手填、勾思考才出档位）、`_SyncDialog`（同步选择）

---

## 一、本轮已完成（不要再改，除非有回归）

| 项 | 产物 | 判据 |
|---|---|---|
| 启动高度自适应 | `core/winstate.py`、`app.py::_apply_win_geometry / _win_fit_sync` | `_winstest.py` A/C 段 |
| 记住退出时窗口状态 | `data/window.json`（含 x/y/w/h/nx/ny/nw/nh/zoomed/scale） | `_winstest.py` C 段 |
| 导航装不下时出滚动条 | `wbtui.py::Nav`（Canvas + 动态滚动条 + 滚轮） | `_winstest.py` B 段 |
| 缩放卡顿 | 根治：16 个页面全部 `grid_remove`，只留当前页在布局（1167→177 个 mapped 控件） | 1500ms/步 → 250ms/步 |
| 滚轮 `event.num='??'` 崩溃 | `wbtui.py::Nav._wheel` 先按 delta 判 | `_tabtest/_tsmoke` 全绿 |

**关键数字**：改前 1497ms/步，改后 250ms/步（同一进程三次取最小）。
瓶颈已确认在 Tk 自身重绘（窗口 withdraw 时 4.8ms），不是我们的回调。
剩下 250ms 分布：导航 ~85ms、页面栈 ~75ms、其余（头部/状态栏/paned）~90ms。

---

## 二、待做（按建议顺序）

### T1 · 日志管理：状态栏按钮 + IDE 式底边栏
- **现状**：日志是左侧导航最后一项（`app.py` NAV_ITEMS 里 `("group","审计",[("log","日志管理")])`）。
- **要做的**：
  1. 导航里删掉「日志管理」条目（`log` 键保留，`NAV_ALIAS` 里映射到别处或直接留空）。
  2. `ui/statusbar.py::StatusBar` 最右侧加一个「日志」按钮，回调注入（`on_log=...`），
     不要在里面直接引用 app。
  3. 底边栏：`pages/log_dock.py`（新建），容器挂在 `self.workspace` 下方，
     与主区用 `tk.PanedWindow(orient="vertical")` 分隔，可拖高度、可关。
     标题行：`日志` + 分类下拉（全部/终端/安全/数据库/AI/API…）+ 搜索框 + 清空 + `×`。
     内容直接复用 `pages/log_page.py` 的表格构建（把 `build_log(page)` 抽成可复用的
     `build_log_into(parent)`，页面与底栏共用一份）。
  4. 状态记忆：`data/window.json` 里加 `log_open` / `log_h`（或另开 `data/ui.json`），
     下次启动恢复。
  5. 状态栏按钮要能显示「面板开着/关着」两种状态。
- **判据**：新测试 `_logdocktest.py`——点状态栏按钮面板出现/消失；拖到 200px 再重启仍在 200px；
  日志面板里能看到 `terminal.command` 事件（命令 + rc + stdout/stderr + 口令已打码）。

### T2 · 助手「＋」菜单点别处/再点不收
- **现象**：点 ＋ 开菜单后，点外面或再点 ＋ 都不收。
- **查**：`pages/ai_dock.py::_popup / _close_if_unfocused / _ai_plus_menu`。
  `_close_if_unfocused` 现在只在 `<FocusOut>` 触发，而 Tk 的 Frame 不抢焦点 → 永远收不到。
  改成 `<Button-1>` 全局绑定（`bind_all`，且用 `add="+"`），点非菜单区域就关；
  ＋ 按钮自己再点一次要 toggle（`_popup` 里记 `_menu_for`，相同则 destroy）。
- **判据**：`_attest.py` 加三条：点画布空白 → 菜单没了；连点两次 ＋ → 第二次是收起；
  点二级浮层再点外面 → 两级一起收。

### T3 · 助手缺的四件（参考 Cherry Studio Video 与 DeepSeek Harness GUI）
- **参考源码**（先解压到 `N:\AI-workspace\ref\`，别解到项目里）：
  - `\\192.168.3.12\Public\软件\AI生成的应用\Cherry Studio\Cherry-Studio-Video\cherry-studio-video(GitHub).zip`（290MB）
  - `\\192.168.3.12\Public\软件\AI生成的应用\DeepSeek-Harness-GUI\DeepSeek-Harness-GUI(github).zip`（2MB）
- **要做的**：
  1. **权限档位**：输入框上方下拉（自动 / 只读 / 每步确认），接已有 `agent_cfg.mode`
     （`auto/confirm/readonly`），文案对齐 CodeBuddy。
  2. **上下文用量提醒**：输入框右下角显示 `已用 / 上限`（tokens），超过 80% 变黄、95% 变红并提示
     「建议新建会话」；数据来自 `agent.tokens` 与模型 `context` 配置。
  3. **思考模式档位**：模型配置里可设 `thinking_levels`（例如 DeepSeek 的 关/低/中/高），
     输入框旁一个下拉选当前档位，发请求时带进 payload（`reasoning_effort` 或厂商字段）。
  4. **会话页签样式对齐 CodeBuddy**：可关闭的页签 + 末尾 `＋`；「历史会话」入口现在在
     `⋯ → 所有会话`，改成独立按钮（历史图标），列表按时间倒序、能搜。
- **判据**：`_attest.py` 或新建 `_uidocktest.py`：三档切换能改变 `agent_cfg` 并落盘；
  token 计数在发一轮后 > 0 且超阈值变红；思考档位写进请求 body（用假 client 断言）。

### T4 · 模型管理改成「文本模型」专用（参考 Cherry Studio 模型服务）
- **现状**：`pages/ai_page.py` 里的模型设置（`ai_model_settings`）是旧式表单。
- **要做的**：
  1. 列表只显示**文本**模型；「添加模型」也只加文本，**不要**图片/视频/嵌入/重排。
  2. 模型类型：固定「文本」，不给选（或只读徽章）。
  3. 能力：两个复选框 —— `思考`（勾上后展开「可用档位」，可多选 + 指定默认档位）、`工具`。
     档位参考 DeepSeek Harness 的模型管理（去源码里找 `thinking` / `reasoning_effort` 字段名）。
  4. 输入模态：只保留 `视觉`、`音频` 两个复选框。
  5. 数据落在 `data/ai.json` 的模型条目上，字段建议：
     `{"kind":"text","caps":{"thinking":{"levels":["off","low","high"],"default":"high"},"tools":true},
       "modalities":["vision","audio"]}`。
  6. 同步（从服务商拉模型列表）也只拉文本模型，过滤掉 embedding/rerank/image/video/tts 名字。
  7. **Token 限制三件套**（用户 22:52 补的要求）：每个模型要有
     `context` / `max_input` / `max_output` 三个值，界面上是「预设胶囊 + 手填」：
     - 上下文窗口预设：128K / 200K / 256K / 400K / 512K / 1M
     - 最大输入 Token 预设：128K / 200K / 256K / 512K / 1M
     - 最大输出 Token 预设：16K / 32K / 64K / 128K / 256K
     - 每个下面一个输入框（占位符写「例如 128000」），胶囊点了就把值填进输入框，
       手填的值优先。
  8. **同步时尽力抓这三个值**：服务商 `/models` 一般不给，但不少兼容网关给
     （`context_length` / `max_context_length` / `max_input_tokens` /
     `max_output_tokens` / `top_provider.context_length` 等字段名都可能出现）。
     规则：**能抓到就填、抓不到留空**，绝不用默认值瞎猜；界面上空值显示「未知」。
     实现放 `core/aiops.py` 或新建 `core/modelsync.py`，解析函数单独可测。
- **判据**：新建 `_modeltest.py`——添加模型只能选文本；勾「思考」才出现档位；默认档位必须属于
  可用档位；输入模态只有两项；同步时 embedding 模型被过滤；
  **解析器对 6 种常见字段名各给一份假 JSON，能取到值；字段缺失时返回 None 而不是 0**。

### T5 · 添加主机：本机协议瘦身 + sudo/管理员
- **位置**：`pages/hosts_page.py::_host_form`（或 `ui/forms.py` 的表单描述）。
- **要做的**：
  1. 协议选「本机」时，隐藏：地址、端口、认证、私钥路径、登录密码、凭证、代理。
     只留：名称、分组、标签。
  2. 自动检测本机系统（`platform.system()` / `core/detect.py`）：
     - Linux：显示「使用 sudo」复选框；勾上后出现「sudo 密码」（`kind="secret"`）。
     - Windows：显示「以管理员权限运行」复选框；勾上后**启动会话时**走 UAC 提权
       （项目里已有提权路径 `_elevate` 相关代码，复用；不要自己再写一套）。
       另外显示「Shell」下拉：`cmd` / `powershell`。
  3. 字段联动要用 `forms` 的显示/隐藏能力；没有就加 `on_change` 回调按字段值重建表单。
  4. 落盘：`profiles` 条目加 `sudo` / `sudo_pass_enc` / `elevate` / `shell` 字段。
- **判据**：`_newpages.py` 加：切到「本机」后不可见字段数为 0；Linux 分支有 sudo 勾选；
  Windows 分支有管理员勾选 + cmd/powershell 下拉；值能存能读。

---

## 三、T6 / T7：22:55–22:56 新提的两条

### T6 · 发送与停止合成一个按钮（WorkBuddy 形态）
- **现状**：`pages/ai_dock.py` 底部并排两个按钮 —— `btn_ai_send`（发送）、`btn_ai_stop`（停止）。
- **要做的**：只留**一个**圆形按钮，同一位置切换：
  - 空闲：实心圆 + 上箭头（↑ / ➤），点击 = 发送；
  - 忙碌：同一位置的圆变成方块图标（■），点击 = 停止；
  - 切换点：`_ai_refresh_dock_state()` 里按 `self.ai_busy` 决定 `set_text(...)`；
    `btn_ai_stop` 从布局里去掉（对象可留作兼容，或直接删引用）。
  - 顺手：`Ctrl+Enter` 发送、`Enter` 换行（若现在不是这样）。
- **判据**：`_attest.py` 加：空闲时按钮文案是发送图标；`app.ai_busy=True` 后刷新变停止图标；
  点一下停止真的调 `ai_dock_stop`。

### T7 · 中途插话（参考 Cherry Studio）
- **要做的**：一轮任务正在跑的时候，用户还能在输入框打字并按发送，
  这条消息作为**补充指令**插进当前轮，而不是排队等下一轮、更不是被忽略：
  1. UI：`ai_busy` 时不锁输入框（现在如果 set state=disabled 要去掉），
     发送按钮在忙碌时是「停止」，所以插话用 `Ctrl+Enter` 或输入框右侧的小箭头。
  2. 数据：`self._ai_pending` 已有雏形 —— 把它改成队列 `self._ai_inject: list[str]`。
  3. Agent 层：`core/agent.py::run_task` 每步开始前检查注入队列，
     有内容就作为一条 `user` 消息追加到 history（注明「用户在你执行期间补充」），
     然后继续循环。**绝不能并发跑第二个 run_task。**
  4. 消息流里要能看到插话的那条气泡（和普通提问区分开，标一个「插话」徽章）。
- **判据**：新测试 `_injecttest.py`：假 executor 跑 8 步，在第 2 步注入一句话，
  断言 history 里出现该条、总步数仍受限、没有第二个线程/第二次 run_task。

---

## 三·补、T8 / T9：23:30 新提的两条

### T8 · 防火墙列表与路由表要能「点着改」，防火墙要先探测是哪一套
- **用户原话**：「防火墙列表，路由表都是可以直接通过图形界面修改的，防火墙管理，
  因为不同的设备用的防火墙不一样，所以你得终端还得探测」。
- **现状**：
  - `pages/routes_page.py` 只读（读 `ip route` / `route print` 列表）。
  - `pages/firewall_page.py` 是「安全中心」里的防火墙块：有 `探测后端`（`self.fw_backend`），
    但列表只能看，不能加/删/改；`core/` 里有 `firewall` 相关后端函数（先 `grep -n
    "def fw_\|def firewall" core/*.py` 找现有能力，别重写）。
- **要做的**：
  1. **探测**：把后端探测做成进页面就自动跑一次（已有 `fw_backend` 字段与
     `_after_switch` 里的复位），结果写进徽章：`ufw` / `firewalld` / `nftables` /
     `iptables` / `Windows 高级防火墙(netsh)` / `未知`。探测命令必须是只读的，
     且**零安装**：`command -v ufw && ufw status`、`systemctl is-active firewalld`、
     `nft list ruleset`、`iptables -S`、`netsh advfirewall firewall show rule name=all`。
     顺序按「先探测到哪个用哪个」，别一股脑全跑（网络设备上全跑会卡）。
  2. **增删改**：列表右键/按钮给「新增规则」「编辑」「删除」「启用/停用」「上移/下移」，
     每个动作翻译成该后端的原生命令（ufw allow/delete、firewall-cmd --add-port/--remove-port、
     nft add/delete rule、iptables -A/-D、netsh advfirewall firewall add/delete rule）。
     所有写操作**必须**：先 `forms.confirm(..., danger=True)`；进审计（`audit`）；
     输入用 `forms.FormDialog` 描述字段，不要让用户手打命令行。
  3. **路由表**：同样给「新增路由」「删除路由」「设为默认」；Linux 用
     `ip route add/del/replace`，Windows 用 `route add/delete -p`（-p 持久可选）。
     改完立刻重读列表，并把新行高亮。
  4. 风险：`ip route del default`、`iptables -F`、`netsh advfirewall reset` 这类
     必须标 danger 并二次确认；改之前把当前规则备份成字符串存 `data/fw_backup/*.txt`，
     界面上给「回滚上一次改动」。
- **判据**：新建 `_fwroutetest.py`（或并入 `_newpages.py`）：
  - 6 种后端的探测输出各给一份假 stdout，`detect_backend(stdout_map)` 能认对；
  - 每个写动作生成**命令字符串**的函数是纯函数，能对 5 种后端各断言一条
    （如 ufw → `ufw allow 8080/tcp`、firewalld → `firewall-cmd --permanent --add-port=8080/tcp`）；
  - 路由 add/del 同理；危险命令的 `aiops.classify_risk()` 必须是 `danger`；
  - 界面上：伪造一个 session，点「新增规则」→ 表单出现 → 保存后列表多一行。

### T9 · 去掉重复的「连接」（截图里那个 VPN 控制台的第一个页签）
- **用户原话**：「这个连接功能重复了，因为前面已经可以进行连接了」。
- **现状**：`vpncert_gui.py` 第 367 行的页签是
  `[("conn","连接"),("cert","证书"),("pack","打包"),("deploy","部署"),("cons","命令日志")]`，
  `连接` 页里是一整套「地址/端口/用户/私钥/口令/sudo/连接并探测」——这套东西
  主机管理里已经有了，用户要在两处填同样的参数。
- **要做的**：
  1. 内嵌模式（`embedded=True`，`app.py::_build_vpn` 用的就是这个）**去掉 `conn` 页签**，
     页签从 `证书` 开始。
  2. 内嵌时把「服务端现状」改成跟着**主程序当前会话**走：`self.app.session`
     （`core/session.py`），在进入本页 / 切换设备时自动探测一次，头部显示
     「当前设备：root@192.168.3.12」；没有会话就显示「先在主机管理里连接一台设备」
     + 一个跳到 `hosts` 页的按钮。
  3. 保留一个适配层 `_MainSessionClient`，方法签名对齐原来的 `self.nas`
     （至少 `run(cmd)` / `close()`），这样 `do_probe / do_deploy` 一行都不用改。
     独立运行（`python vpncert_gui.py`）时仍可退回原连接表单 —— 判据是 `embedded`。
  4. 顺手：`连接并探测` 文案改成「探测本机隧道服务」之类，别再出现「连接」字样。
- **判据**：`_tabtest.py` / 新 `_vpndedupetest.py`：
  - 内嵌时页签集合不含 `conn`；
  - 没有会话时出现「先在主机管理里连接」的提示且不崩；
  - 有假会话时能拿到 `run()` 的返回并显示到「服务端现状」；
  - 独立模式下 `conn` 页签仍在（不破坏 VPNCertConsole.exe）。

---

## 四、顺序与依赖

1. T1（底边栏）与 T2（菜单）都只碰外壳，先做，风险低。→ 已完成
2. T3 的思考档位依赖 T4 的模型字段 → **先 T4 定数据模型，再做 T3-3**。→ T4 已完成
3. **顺序（用户 22:56「T1–T7 不调顺序」+ 23:30 加的两条排在后面）**：
   T3 ✅ → T5 ✅ → T7 ✅ → T8 ✅ → T9 ✅ → **只剩打包**。
   打包前全绿记录：`_fwroutetest 115/115`、`_vpndedupetest 29/29`、`_newpages 73/73`、
   `_uismoke 110/110`、`_attest 132/132`、`_tsmoke 70/70`、`_tsmoke2 72/72`、
   `_fsmoke 49/49`、`_sectest 52/52`、`_termsmoke 32/32`、`_conntest 106/106`、
   `_websmoke 98/98`、`_inlinetest 40/40`、`_modeltest 94/94`、`_logdocktest 16/16`、
   `_winstest 44/44`、`_tabtest 56/56`、`_redacttest 30/30`、`_selftest`、
   `_vpnsmoke 27/27`。
4. 全部做完 → 版本号 1.0.7 → 重打 exe → `_texe` 验收 → 推共享目录与本机 `D:\Tools`
   （共享盘不允许覆盖已存在文件，用 `_deploy_share.py`）。
5. 打包前必须全绿：`_selftest / _tsmoke / _tsmoke2 / _fsmoke / _sectest / _redacttest /
   _termsmoke / _inlinetest / _vpnsmoke / _uismoke / _conntest / _websmoke / _texe` +
   本轮新增的 `_modeltest / _logdocktest / _attest / _winstest / _tabtest`。

## 五、已知坑（别重踩）

- 页面必须只留一个在 `grid` 里，否则缩放 1.5 秒一步（这是 v1.0.7 修的）。
- `winfo_containing` 在 Windows 上不可靠（窗口被遮返回 None），判断 z 序读 `app._ws_top`。
- 共享盘 `\\192.168.3.12\Public` 不允许 unlink / 覆盖，只能新建或改名到不冲突的名字。
- `audit.STATUS` 只有 `ok/fail/warn/info`。
- 提交要 GPG 签名。`pinentry-timeout` 已从 600 提到 **7200**、口令缓存
  `default-cache-ttl` / `max-cache-ttl` 都设 28800（2026-10-24 凌晨，因为
  10 分钟窗口在夜里连超三轮）。改完必须用 **Windows 版**
  `"C:\Program Files (x86)\GnuPG\bin\gpgconf.exe" --kill gpg-agent` 才生效
  （MSYS 那个 gpg 不是同一个 agent）。
- 桌面快捷方式叫「终端工具箱」，指向 `D:\Tools\Terminal-GUI-Toolbox\TerminalToolbox.exe`。
- PyInstaller onefile 运行时天然是**两个进程**（引导器 + 真程序，父子同 exe）。
  看到两个同名 PID 不等于「进程残留」，别乱杀父进程。
- 项目虚拟环境是 `C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole`
  （3.14.7，带 cryptography / paramiko / tkinter）。`C:\Python314` 没装
  cryptography，`C:\Program Files\python` 是 3.8.6 太老（`(?>` 原子组不支持）。

---

## 六、T10（2026-10-24 加做）：退出机制与托盘

用户报「关掉窗口进程还在、任务栏右下角也找不到托盘图标」。

- **根因**：`close_to_tray` 默认 `True` → 点 × 只 `withdraw()`；而 Win11 默认把
  新图标塞进「︿」溢出区，`Shell_NotifyIcon` 的气泡又常被专注助手压掉 →
  用户既看不到图标也不知道要右键退出，只能去任务管理器强杀。
- **改法**：
  1. 默认值改 `False`（`app.py` 两处 + `pages/settings_center.py` 一处），
     点 × 就是 `_quit_app()`。托盘改成显式开关。
  2. `_tray_hint()` / `_hide_tray_hint()`：真收进托盘时自绘一个右下角小窗
     （`overrideredirect` + `-topmost`），写明「图标在 ︿ 里」「右键 → 退出」。
     9 秒自动消失，点一下也消失；`_show_from_tray` 与 `_quit_app` 都会先收掉它。
  3. 托盘起不来时 `_hide_to_tray` 走 `iconify()` + 状态栏 toast，不再变幽灵。
  4. 设置页文案同步：「默认关闭：点 × 直接退出」。
- **验收**：`_traytest.py` **18/18**（托盘底层 3 项、退出语义 6 项、提示窗 9 项）。

