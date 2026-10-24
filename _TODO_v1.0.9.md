# v1.0.9 待完成清单（2026-10-24 15:2x 落盘）

> 用户可以随时打断，所以这份清单要能让「什么都不记得的下一轮」直接接着干。
> 顺序有依赖：A→B 可以并行，C 依赖 B 的 Panel 模式，D 依赖 C。

## A. 交互小毛病（先做，改动小）

### A1 表格/分隔线光标卡在双箭头
- 现象：拖动 ttk Treeview 列分隔线后，鼠标离开也一直是 `sb_h_double_arrow`。
- 原因：ttk 在 `<ButtonRelease-1>` 里恢复 cursor，但松手时指针已不在该控件上、
  或拖完立刻 rebuild 表格，release 没落到原来的 tree 上。
- 改法（`ui/table.py`）：`<ButtonRelease-1>` / `<Motion>` / `<Leave>` 上补一个
  `_reset_cursor`，条件是「没有按下左键（`event.state & 0x100 == 0`）」时把
  `cursor=""`；再在 toplevel 上挂一次兜底。
- 判据：`_cursorreset.py`（新建）伪造 拖→离开→移动 序列，断言 cursor 回到空。

### A2 拖窗口大小卡顿
- 已定位的可疑点：`pages/ai_dock.py::_ai_dock_reflow` 在 `<Configure>` 里
  **同步遍历所有消息气泡** 改 wraplength（N 个 Label × 每像素一次）。
- 改法：debounce 60~80ms（沿用 table.py 里 `_fit_job` 的写法），
  并且只在宽度真的变了（`abs(new-old) >= 2`）才跑。
- 判据：`_resizelag.py`（新建）灌 200 条消息，量 30 次 configure 的总耗时
  与单次最坏耗时，写进输出，不设死阈值但要求「比改前小」。

## B. 单模型表单：去掉「服务商」与「API Key」（进行中）

- `pages/model_page.py::_ModelForm`
  - 删 `_row_provider` 与 `_pick_provider`（已删），`_ok()` 用 `self._prov`；
  - 删 API Key 那一行（已删），`_ok()` 里 `out["key_enc"]` 不再从表单取；
  - `_open_sync()` 的 key 改成 `cfg.provider_key(self._prov)`；
  - 标题加服务商 Pill（已完成）；
  - 新模型接口地址默认取 `cfg.provider_base(prov)`（已完成）。
- 待办：`_ok()` 里 `prov = self._pick_provider()` 还没改；`_open_sync` 还没改。
- 判据：`_modeltest.py` 110 项仍全绿。

## C. 设置中心「助手」区：4 个内嵌选项卡（替代 3 个弹窗按钮）

- 位置：`pages/settings_center.py::_sec_agent`，现在是三个按钮
  「智能体设置… / 模型设置… / 技能库…」。
- 目标：换成 `U` 风格的页签条 + 4 个内嵌面板：
  1. 智能体设置（原 `AgentDialog`，`pages/ai_dock.py:3168`）
  2. 模型设置（原 `ModelDialog`，`pages/model_page.py`）
  3. 技能库（原 `SkillDialog`，`pages/ai_page.py:472`）
  4. **MCP 库**（全新，见 D）
- 模式（已定）：每个弹窗拆成 `XxxPanel(tk.Frame)` + `XxxDialog(tk.Toplevel)`
  薄壳。Panel 里不再调 `title/transient/geometry/grab_set/destroy`，
  改 `self._close()`（由壳注入 `on_close`）。**老弹窗入口全部保留**，
  因为侧栏「⋯」菜单和 settings 其它地方还在调。
- 判据：`_tabhost.py`（新建）建出面板，切 4 个页签各一次不抛异常，
  且不再出现新 `Toplevel`（用 `winfo_children()` 里 Toplevel 数量断言）。

## D. MCP 库（用户选了「完整可用」）

### D1 `core/mcp.py`
- 存储：`data/mcp.json`，`{"servers": [{sid, name, transport, command, args,
  env, cwd, url, headers, enabled, timeout, note}]}`，`enabled` 默认 True。
- 传输：
  - `stdio`：`subprocess.Popen`，按行 JSON-RPC 2.0；stderr 单独线程收（只留尾部）；
    `initialize`（protocolVersion 2025-06-18，退了再试 2024-11-05）→
    `notifications/initialized` → `tools/list`；`tools/call` 返回 content 数组拍平文本。
  - `http`：POST JSON-RPC，`Accept: application/json, text/event-stream`，
    记 `Mcp-Session-Id`，响应可能是 SSE（逐行 `data:` 解析）。
- 进程池：按 sid 懒启动，空闲 5 分钟回收；`stop_all()` 供退出时调用。
- 工具 id：`mcp__<sid>__<tool>`，slug 化（非 `[A-Za-z0-9_]` 换成 `_`）。
- 判据：`_mcptest.py`（新建）内置一个假 MCP server（同目录 `_mcpstub.py`，
  python 起，stdio 回 JSON-RPC），跑完 初始化/列工具/调工具/超时/坏 JSON 五段。

### D2 助手接入
- `core/agent.py`：`tool_catalog()` 末尾追加 MCP 工具清单（从 `core.mcp` 取，
  失败就静默跳过，别让 MCP 拖垮助手）；`TOOLS` 注册表不塞动态项，
  用 `mcp_tool_spec()` 单独返回 `ToolSpec(risk="write")`。
- 执行侧：`pages/ai_dock.py` 的 executor 里加分支 `tid.startswith("mcp__")`
  → `core.mcp.call_tool()`，结果转文本回灌。
- 判据：`_mcpagenttest.py`（新建）用假 server 让助手「调用一次 mcp 工具」，
  断言真的走了子进程且有输出回填。

### D3 `pages/mcp_panel.py`
- 左：服务器清单（状态点 / 名字 / 传输 / 工具数）；右：详情表单
  （名字、传输、命令+参数、环境变量、URL+头、超时、启用）。
- 按钮：保存 / 试连（initialize + tools/list，把工具列出来）/ 启动 / 停止 /
  删除 / 从 mcp.json 导入（兼容 `{"mcpServers": {...}}` 与 WorkBuddy 的写法）。
- 判据：并入 `_tabhost.py` 与 `_mcptest.py`。

## E. 收尾
- `APP_VER` / `_pack_release.VER` → `1.0.9`；全套回归；两个 exe 重打；
  四包；提交 + 签名 tag；推四端；`_deploy_share.py 1.0.9`；覆盖 `D:\Tools`；
  重建桌面「终端工具箱.lnk」。
- 记忆：追加到 `.workbuddy/memory/2026-10-24.md`。

## 已知环境事实（别重复踩）
- 项目 venv：`C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
- 源码根：`N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`
- 共享目录只许新建/改名，不许删改已有文件（`_deploy_share.py` 已处理）。
- Bash 里 heredoc 写中文文件偶尔被拦，优先用 Write/Edit。

## F. 模型管理对齐 Cherry Studio（用户 16:03 要求，复用其操作逻辑）

参考截图三张，要照抄的交互：
1. 左栏：**搜索框** + 供应商清单（图标/名字/状态点/「N 个模型」）+ 底部「+ 添加服务商」。
2. 右栏头：供应商名 + 齿轮；右上**启用开关**（关掉=这家所有模型都不参与）。
3. **API 密钥**行：掩码显示 + 眼睛 + ✎ + 「检测」按钮。
4. **API 地址**行：可加多个端点（「添加端点」）。
5. **模型**区：`同步模型` / `+` / 搜索 / 过滤；模型**按「分组名称」折叠**，
   每行右侧能力徽章（视觉/工具/思考）+ 删除。
6. 右侧「编辑模型」抽屉：模型 ID / 模型名称 / 分组名称 / 模型用途（对话，其余置灰）。
- 服务商不再是固定 5 个枚举：可增删、可改名、可含多个同类型实例
  （截图里就有两个「Llama网关」、三个「ComfyUI网关」）。
- 存储：`ai.json` 的 `providers[pid] = {name, kind, base, key_enc, enabled, endpoints[]}`，
  内置 5 个只作为「类型预设」提供默认地址/提示。
- 模型新增字段 `group`（分组名称，默认取模型名前缀或「默认」）。

状态：**F 段全部做完（2026-10-24 16:4x）**，`_grouptest.py 70/70`、
`_modeltest.py 114/114`、`_aimodeltest.py 42/42`，其余 31 个测试文件全绿。

落地情况（逐条对截图）：

| 截图要的 | 落在哪 | 怎么做的 |
|---|---|---|
| 左栏搜索 + 供应商清单 + 「+ 添加服务商」 | `pages/model_page.py::_build_provider_card` 上方 | `_ProviderList` 重画，`v_search` 过滤；`provider_add()` 弹名字输入 |
| 右上启用开关 | 供应商卡第 0 行右侧 | `U.Check(self.v_enabled)` + `_toggle_enabled()` |
| API 密钥 + 眼睛 + 检测 | 供应商卡第 2 行 | `btn_eye` 切 `show=""`/`"•"`；`btn_prov_probe` 就是「检测」 |
| API 地址可加端点 | 供应商卡第 1 行 | `btn_eps` 弹 `tk.Menu`：切/加/删；后端 `provider_endpoints/add_endpoint/remove_endpoint/use_endpoint` |
| 模型按分组折叠 + 能力徽章 + 删除 | `DataTable.set_group()` + `_render_grouped()` | 组头是 Treeview 父节点，Tk 自带三角；`_open` 记住收起状态；`caps` 列 + 行尾 `✕` 列（`column_at`/`row_at` 定位） |
| 右侧「编辑模型」抽屉 | `_ModelDrawer` | `place` 浮层（不挤窄表格），4 字段 + 「更多参数…」转完整表单 |

顺带修的：
- `_ModelForm._open_sync()` 还在读已删掉的 `self.ent_base` → 改成 `cfg.provider_base(self._prov)`。
- `model_view()` 对自定义 pid 默认「不需要 Key」→ 改成默认需要（网关九成要鉴权）。
- `upsert_model()` 只给内置 pid 继承供应商地址/Key → 自定义供应商也继承。
- `provider_meta()` 丢掉 `endpoints` / `set_provider()` 整体覆盖写会丢端点 → 都补上了。

没照抄的两处（有意）：
- 截图里密钥行还有 ✎。这里的输入框本身就是可编辑的，再加个笔等于两个入口干一件事，没加。
- Cherry Studio 的抽屉是挤窄列表；这里是浮层。挤窄会让表格每次开关都重排列宽、横向滚动条忽隐忽现。

## G. 发版（**下一步就干这个**）

顺序不能乱：先改版本号 → 打包 → 跑 exe 冒烟 → 提交+签名 tag → 推四端 → 分发 → 快捷方式。

1. 版本号：`app.py` 的 `APP_VER`、`_pack_release.py` 的 `VER` → `1.0.9`
   （`grep -n "1.0.8" app.py _pack_release.py` 确认没有漏的）。
2. 打包：`python -m PyInstaller --noconfirm --clean --distpath . toolbox.spec`
   （VPNCertConsole 用 `vpncert.spec`，同一条命令换 spec）。
3. 冒烟：`python _texe.py`（含 `--page X` 逐页起窗）。
4. 提交：`git commit -F _commitmsg.txt`（**必须 GPG 签名**，信息用文件避免中文引号被吃）。
5. tag：`git tag -s v1.0.9 -m "v1.0.9"`。
6. 推：`git push origin main` + `git push origin v1.0.9`，四个远端都要对齐到同一 commit
   （origin=GitHub / gitcode / gitee / forgejo）。**不建 Release、不挂二进制**。
7. 分发：`python _deploy_share.py 1.0.9`（共享目录只许新建/改名，脚本已处理顺延命名）。
8. 覆盖 `D:\Tools\Terminal-GUI-Toolbox`，再跑 `python _mkshortcut.py` 重建桌面「终端工具箱.lnk」。
9. 记忆：追加到 `.workbuddy/memory/2026-10-24.md`。
