# 待办清单 v1.0.7（2026-10-23 22:55 落盘）

> 用途：上下文被压缩后，照着这份直接干。每条都写清「改哪个文件、判据是什么」。
> 源码根 `N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`
> 解释器 `C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
> 跑测试：`$PY _winstest.py` / `_tabtest.py` / `_tsmoke.py` / `_uismoke.py` …

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

## 四、顺序与依赖

1. T1（底边栏）与 T2（菜单）都只碰外壳，先做，风险低。
2. T3 的思考档位依赖 T4 的模型字段 → **先 T4 定数据模型，再做 T3-3**。
3. T5 独立，随时可做。
4. 全部做完 → 版本号 1.0.7 → 重打 exe → `_texe` 验收 → 推共享目录与本机 `D:\Tools`
   （共享盘不允许覆盖已存在文件，用 `_deploy_share.py`）。

## 四、已知坑（别重踩）

- 页面必须只留一个在 `grid` 里，否则缩放 1.5 秒一步（这是 v1.0.7 修的）。
- `winfo_containing` 在 Windows 上不可靠（窗口被遮返回 None），判断 z 序读 `app._ws_top`。
- 共享盘 `\\192.168.3.12\Public` 不允许 unlink / 覆盖，只能新建或改名到不冲突的名字。
- `audit.STATUS` 只有 `ok/fail/warn/info`。
- 提交要 GPG 签名，pinentry 已设 600 秒（`%APPDATA%\gnupg\gpg-agent.conf`）。
- 桌面快捷方式叫「终端工具箱」，指向 `D:\Tools\Terminal-GUI-Toolbox\TerminalToolbox.exe`。
