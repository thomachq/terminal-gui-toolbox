# v1.0.6 待办清单（给「什么都不记得的下一轮」）

> 项目根：`N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`
> 解释器：`C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
> 上级版本：v1.0.5（commit `b3bc7ce`，tag `v1.0.5` 已推四端）

## 一、已完成（本轮）

| # | 内容 | 关键产物 |
|---|---|---|
| 48 | 免 PIL 读剪贴板（CF_HDROP / CF_DIB → 手工 PNG） | `core/clipfile.py` |
| 49 | 输入区附件：添加文件 / 引用对话中的文件 / Ctrl+V | `pages/ai_dock.py` |
| 50 | ＋ 菜单一级 + 二级浮层（技能/连接器/模式/引用文件） | `_ai_plus_menu` / `_ai_open_sub` / `_submenu` |
| 51 | 模型浮层按 WorkBuddy 重做（>7 个才出搜索框） | `_ai_model_menu` |
| 52 | 附件注入上下文 + spec hiddenimports | `_ai_attach_text`、`toolbox.spec` |
| 53 | `_attest.py`（106 项）与全量回归全绿 | `_attest.py` |
| 55 | 左侧导航改为「主机资源」页签私有 | `app.py`（`U.Nav(self.ws_hosts, ...)`） |
| 56 | 删掉右侧命令日志面板，并入日志管理 | `app.py`（`_trace_cmd → _audit_command`） |
| 57 | 日志管理导航项置底 | `app.py` NAV 三组结构 |
| 58 | 日志管理收录运维工具命令与输出 + 口令脱敏 | `core/redact.py` 11 类规则 |
| 59 | 私钥/证书路径「浏览…」 | `ui/forms.py` kind=file/secret_file |
| 60 | 侧栏宽度默认 372、夹在 340~560 且 ≤窗口 42%，记忆存逻辑像素 | `DOCK_W_*`、`_ai_dock_sash/_remember/_clamp` |
| 61 | 多会话页签（新建/切换/关闭）+ 所有会话列表 + 落盘 | `core/aisession.py`、`_ai_sess_*`、`_ai_tab_*` |

## 二、待完成

### 1. 全量回归复跑（进行中）
一条命令：
```bash
cd "/n/AI-workspace/WorkBuddy/Terminal GUI Toolbox"
PY=/c/Users/Thomas/.workbuddy/binaries/python/envs/vpnconsole/Scripts/python.exe
for t in _selftest _tsmoke _tsmoke2 _fsmoke _sectest _redacttest _termsmoke \
         _inlinetest _vpnsmoke _uismoke _conntest _websmoke _newpages _tabtest \
         _attest _aismoke _aimodeltest _aiskilltest _aitracetest _docktest; do
  printf "%-14s " "$t"; out=$("$PY" $t.py 2>&1); echo "$out" | tail -1
done
```
判据：**每一项 0 失败**。

### 2. #58 显示侧验证（欠）
日志管理页要能读出「命令原文 + 输出 + rc」。
事件形如 `terminal.command` / status `ok|fail` / detail `$ cmd\nrc=0 · 42 ms\n[stdout]...`。
检查 `pages/log_page.py` 的详情渲染是否把 detail 整块显示（含换行），
必要时补一个「运维工具」筛选 chip。

### 3. v1.0.6 发版（#54）
顺序**不能颠倒**：
1. `app.py` 的 `APP_VER = "1.0.6"`（当前还是 1.0.5，第 106 行）
2. 截图（`_shots.py`，必须在打包前，否则交付件里是旧图）
3. 打包：
   ```bash
   "$PY" -m PyInstaller --noconfirm --clean --distpath . toolbox.spec
   "$PY" _texe.py            # 含 --page X 逐页起窗验收
   ```
4. 四个 zip：`"$PY" _pack_release.py`（产出 `_release/v1.0.6/`；
   `(github).zip` 必须推送后重新 clone，不能是源码拷贝）
5. 文档升版：`使用说明.txt`、交接说明、deb 构建说明
6. 分发：共享目录（旧文件先 `os.replace` 进 `old/`，需提权）
   + 本机 `D:\Tools\Terminal GUI-Toolbox`
7. GPG 提交（受管后台任务，60 秒内输口令；提交信息用文件 + `-F`）
8. tag `v1.0.6` 推四端：origin / gitee / gitcode / forgejo，main 同一 commit

### 4. 收尾
- 桌面快捷方式指向新 exe（`_mkshortcut.py`）
- `data/` 与 `data/agent_sessions/` 绝不入库

## 三、本轮踩过的坑（别重犯）

| 坑 | 绕法 |
|---|---|
| `winfo_containing` 在 Windows 走 `WindowFromPoint`，窗口被别的程序盖住就返回 None | 别用它做 z 序断言。`app._ws_raise()` 记账，测试读 `_ws_top` |
| `_fsmoke` 断言「远程栏已自动刷新」早于异步 reload 完成 | 用 `wait_for(...)` 等，不要拿 `_fl_busy` 当完成信号 |
| `_ai_attach_clear` 里 `self._ai_attach = []` 会让会话状态脱钩 | 一律原地改：`[:] = []` / `[:] = [...]` |
| 拖分隔条时 `_ai_dock_reflow` 也记宽度 → 记忆越跑越偏 | 只在 `_ai_dock_remember()` 记，且存**逻辑**像素（÷`U.S`） |
| `_ai_note` 默认进 transcript 会让重放刷一屏过程提示 | `record=False` 默认 |
