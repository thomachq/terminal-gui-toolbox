# 待办：v1.0.23（2026-10-25 21:30 落盘，上下文紧张时照此接上）

## 已完成（本轮）

- **日志底栏变矮 + 行高缩小**（用户 21:01 要求「高度太高、每行缩小、能展示 3 条」）
  - `ui/table.py`：新增 `ROW_H_NORMAL=30` / `ROW_H_DENSE=26` / `_ensure_tree_style(row_h, name)`，
    `DataTable(..., row_h=26)` 用**独立样式名**（`TableR<N>.Treeview`），只有日志变密，
    主机列表等仍 30。样式名在构造时定一次，`_paint()` 只重配不新建（否则每帧泄漏一个）。
  - `pages/log_page.py`：`DataTable(..., row_h=ROW_H_DENSE)`。
  - `pages/log_dock.py`：`DEFAULT_H 280 → 252`（实测 251~252 = 表头 51 + 整 3 行、第 4 条不露；
    250 → 2 条、253 → 半行）、`USABLE_H 240→210`、删掉按窗口 0.30 收的 `AUTO_RATIO`
    （它把 1080 高的窗口压成 216 逻辑，只露 1 条，方向反了），改成 `FALLBACK_RATIO=0.45` 只做兜底。
  - 新增「高度是谁定的」标记 `log_h_auto`：`app._win_state_save()` 落盘；`_log_drag_done()`
    置 False。老存档没这个字段 → 一律按新默认重置（用户那份 320 就是旧默认值，
    不重置的话改 DEFAULT_H 等于白改）。
  - `_logdocktest.py`：25 项；新增「紧凑行高」「默认露 3 条」「没有半截行」「log_h_auto 落盘」
    「手动设过之后标记为用户自己的」。
  - `_navaudit.py`：判据改「滚到顶/底首尾两项完整可见」（逐项移动在真实窗口里坐标刷新滞后，会误报）。
- 版本号已改：`app.py APP_VER = "1.0.23"`、`_pack_release.py VER = "1.0.23"`。

## 未完成（按顺序）

### 1. 页面级滚动条（用户 21:22 / 21:25 两次强调：「很多界面都缺少外层滚动条」）

- 症状：安全中心页底部内容与右侧「撤销上一步」被裁；用户要求**页面级外层滚动条**。
- 现状：`app.py` 每个页面是 `U.wframe(self.stack, "page")`，直接 `grid` 内容，没有可滚容器。
- 计划：
  1. `wbtui.py` 新增 `ScrollPage`：Canvas + 内 Frame + 竖向（必要时横向）滚动条，
     `inner 高 = max(内容 reqheight, 可视高)`，这样**表格页仍能 expand 填满**，
     只有内容真超高时才滚。滚动条必须 `pack(before=canvas)`（v1.0.22 踩过的坑）。
  2. `app.py` 建页时 `page = U.ScrollPage(self.stack)`，`builder(page.body)`；
     `self._pages[key]` 存外层，`_show()` 里的 mapped 判断要用 body。
  3. 滚轮路由：canvas 内滚动要能接管 `<MouseWheel>`，但**别抢走表格自己的滚动**
     （指针在 DataTable 上时由表格处理）。
  4. 新测试 `_pagerolltest.py`：所有页 × 多档窗口，断言「无控件被裁 + 需要时滚动条出现
     + 滚到底能看见最后一页内容」。
- 判据：安全中心页在 950×760 下能滚到「撤销上一步」与最底部。

### 2. 群晖防火墙：能设目标 IP + 能持久化（用户 21:22）

- 用户原话：「访问群晖的时候，设置不了持久化的防火墙，我的目标是能改这个
  （只是群晖 GUI 界面的防火墙设置不了目标 IP，而我希望能设置目标 IP）」
- 截图状态：后端 `iptables`、规则 0 条、持久化「否」、提示
  「iptables 规则重启丢失。持久化建议 `iptables-save > /etc/iptables/rules.v4`」。
- 要做：
  1. 先真机调研（群晖 DSM 7）：防火墙规则到底存在哪（`/etc/firewall.d/`? `synowebapi`?）、
     开机由谁恢复、`iptables-save` 是否可用、`iptables` 是否需 root。
  2. 解析器要能**写**：新增「按目标 IP / 端口建规则」，且知道群晖的持久化路径，
     不再给通用建议（对群晖是错的）。
  3. 持久化：群晖可用「任务计划 -> 触发的任务 -> 开机」脚本，或 DSM 自带防火墙 API。
     **零安装铁律**：不往远端装任何东西；用现成的 `iptables-save` / 计划任务。
- 铁律提醒：改远端防火墙等于可能把自己锁在外面，必须**先加放行 SSH 的规则再加拒绝**，
  并且要能在超时/失败时自动回滚（`iptables -F` 或恢复备份）。

### 3. 收尾流水线（每次发版固定）

1. 全量回归（58+ 脚本，两批串行）。
2. 三份文档：`docs/模板-项目交接说明.md`（版本表 + 测试清单）、
   `docs/模板-deb构建说明.md`（changelog 顶部追加）、`使用说明.txt`（BOM+CRLF 保持）。
3. `python -m PyInstaller --noconfirm --clean --distpath . toolbox.spec`
4. `_texe.py`（24 项）+ 跑完核对无残留进程 + 看一眼 screenshots 是否被写成碎片。
5. `git commit -F <msg文件>`（GPG 签名）→ `git tag -s v1.0.23` → 四端 `push main --tags`。
6. `_pack_release.py` → `_deploy_share.py 1.0.23`（六件，归档顺延）。
7. `_deploy_tools.py` 覆盖 `D:\Tools\Terminal-GUI-Toolbox`（用户必须先关掉实例，
   脚本只等不杀，最多 15 分钟）。
8. 记忆：`MEMORY.md` 追加本轮坑；`2026-10-25.md` 追加收尾记录。

## 已知环境

- venv：`C:\Users\Thomas\.workbuddy\binaries\python\envs\vpnconsole\Scripts\python.exe`
- 源码：`N:\AI-workspace\WorkBuddy\Terminal GUI Toolbox`
- 安装：`D:\Tools\Terminal-GUI-Toolbox`
- 屏幕 2560×1440，U.S=1.5；日志表行高 39 物理、表头 51 物理。
