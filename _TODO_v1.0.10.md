# v1.0.10 待完成清单（2026-10-24 18:45 落盘）

上一轮 v1.0.9 已发版（commit `9d25eb9`，tag `v1.0.9`）。本轮改的是**主机 / 连接页**，
版本号已升到 `1.0.10`，但**尚未打包、未提交、未打 tag、未分发**。

## 本轮做了什么（已完成，有测试背书）

用户四条反馈 + 一条追加：

1. 主机清单没有连接状态 → 新增「状态」列（可达 / 本机 / 未检测 / 不可达），
   `probe_host()` 只做一次 TCP 握手，不登录不落任何东西；`PROBE_TTL=90` 节流。
2. 右键「编辑 / 删除」点了没反应 + 要头部编辑按钮 → 根因是 `DataTable._popup`
   一律按 `cb(row)` 调，而项目里有 `lambda: ...` 的 0 参写法，TypeError 被 Tk 吞掉。
   修法 `_takes_row(cb)` 用 `inspect.signature` 分派；右键先用 `identify_row` 选行。
   顶部加「编辑」按钮，行尾加 ✎（`_host_row_click`）。
3. 选了私钥就不要登录密码 → 凭证二选一：私钥 → 私钥密码 `key_passphrase`；
   登录密码 → 登录密码。`login_pw` 谓词 + `watch` 列表。
4. 保存 sudo 密码的勾没了 + 要探测目标类型 → 每个口令框各带「保存」勾
   （结果键 `<key>__save`）；提权区跟**目标系统**走（`H.assume_os`）。
5. **追加（用户 18:16）**：目标系统允许自己选 Mac OS / FreeBSD 等 →
   `core/hosts.py::OS_PICK` 成为下拉项唯一来源（`app.OS_UI` 与
   `hosts_page.host_fields` 都从这里取）；新增 `UNIX_OS` / `is_unix()`；
   `assume_os` 返回规范化的精确系统（linux/macos/freebsd/windows/network），
   别名 darwin→macos、bsd→freebsd；`_remember_detected_os` 不再把 darwin /
   freebsd 一律糊成 linux。

顺手修的三个真 bug：

- `_remember_detected_os` 会按「档案」下拉回写 → 「档案选着 chromas、
  协议切成**本机**连一下」就把本机 Windows 写进 chromas（Synology）的档案里，
  还把连接页目标系统钉成 Windows，之后切回 SSH 时 sudo 密码框整个不出现。
  现在：本机连接直接 return；远端要求 `v_host == rec["host"]` 才写。
  回归测试在 `_conntest.py` 第 [8] 段（5 项）。
- 表单高度到上限后 `_refit` 只恢复旧坐标 → 勾「连接后自动提权」多一行，
  窗口长到 1238 而 y 还停在 +220，底部「确定」被屏幕下沿吃掉。
  现在 `_refit` / `_center` 都重新夹 x、y（留 px(64) 给标题栏/任务栏）。
- `pages/log_page.py::audit_reload` 在日志 dock 还没建时直接引用
  `cb_audit_group` → AttributeError。现在先判 `tbl_audit` 是否存在。

## 数据事故（已修，必须记住）

- `_shot_model.py` / `_shot_host.py` / `_shot_ai.py` / `_shot_aistream.py` /
  另 7 个 `_shot_*.py` + `_termapp.py` **全都在写真实 `data/`**。
  10-24 16:32 `_shot_model.py` 先删光 ollama 模型再灌 demo，
  17:50 `_shot_host.py` upsert 了 6 条假档案 → 用户的档案与模型表被覆盖。
  **没有更早备份可恢复**（`ai.json.bak-20261024` 是污染之后才拷的）。
- 已恢复：`ai.json` 5 个模型（按 `created=1792830749` 这个批次戳摘掉 6 条 demo）；
  `profiles.dat` 6 条（`chromas.synology.me` / `DESKTOP-HBM6KCB` / `gitcode.com` /
  `gitee.com` / `github.com` / `Synology`）。备份留在
  `data/ai.json.before-clean` / `data/ai.json.bak-20261024` / `data/profiles.dat.bak-20261024`。
- 根因治理（本轮已落地，别回退）：
  - `_shots.isolate()`：造 demo 的截图脚本先把 data **复制**到临时目录再改，
    `atexit` 删临时目录；`ca/` 私钥不复制。
  - `_lockoff.unlock()`：关锁屏一律备份 + 退出还原（原来 11 个脚本直接清真实
    `settings.json` 的 `lock_hash`，跑一次就把用户的锁屏密码删了）。
  - `app.py` 支持 `TB_DATA_DIR=<临时目录>` 环境变量重定向数据目录（模块级生效）。
  - 跑测器 `/tmp/runall.py` 给每个测试单独发一个 `TB_DATA_DIR`，
    全 44 个测试互不污染。

## 还差哪几步（顺序不能乱）

1. **等全套回归跑完**（后台任务 `/tmp/runall.py`，日志 `/tmp/runall.log`）。
   注意：那一轮是在我改 `_remember_detected_os` **之前**启动的，
   `_conntest` / `_tsmoke` 的结果要重跑一次确认。
   判定：44 个全 OK；`_conntest 111/0`、`_hostformtest 126/126`、
   `_hostlisttest 37/37`、`_settest 69/0`、`_docktest 74/0`、`_restest2 72/0`。
2. 截图核对：`python _shot_host.py`（现已隔离）→
   `screenshots/hosts_status.png` / `host_form.png` / `host_form_win.png`。
   已确认：底部「确定 / 取消」不再被裁；「私钥密码」说明一行放得下。
3. 打包：`python -m PyInstaller --noconfirm --clean --distpath . toolbox.spec`
   （VPNCertConsole 用 `vpncert.spec`）。
4. 冒烟：`python _texe.py`（含 `--page X` 逐页起窗）。
5. 提交：`git commit -F _commitmsg.txt`（**必须 GPG 签名**；提交信息写文件里，
   避免中文引号被 shell 吃掉）。
6. tag：`git tag -s v1.0.10 -m "v1.0.10"`。
7. 推四端：origin(GitHub) / gitcode / gitee / forgejo，main 与 tag 都要对齐同一
   commit。**不建 Release、不挂二进制附件**。
8. 分发：`python _deploy_share.py 1.0.10`（共享目录只许新建 / 改名，脚本已做顺延
   命名：`name` → `name.prev` → `name.r2` …）。
9. 覆盖 `D:\Tools\Terminal-GUI-Toolbox`，再跑 `python _mkshortcut.py`
   重建桌面「终端工具箱.lnk」（指向安装目录，不是源码目录）。
10. 记忆：追加到 `.workbuddy/memory/2026-10-24.md`。

## 已知环境坑

- `reg.exe` 被安全中心列进程序黑名单 → `_tsmoke` 的本机探测段会被拦，
  相关断言失败。**不要绕过**（提示里明说不能 retry / 换 shell）。
  隔离数据后重跑一次确认是否仍是同一组失败；若只差这一组，属环境限制。
- `_conntest` / `_tsmoke` 等曾因读真实 data 而时绿时红 —— 现在统一用
  `TB_DATA_DIR` 隔离，别再把它们设回真实目录。
- 共享目录 `\\192.168.3.12\Public\...` 不允许删除/覆盖，`cp -f` 会失败。
- 「桌面」在 `D:\Documents\Desktop`，不要写 `C:\Users\Thomas\Desktop`。
