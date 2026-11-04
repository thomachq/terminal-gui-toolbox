# Terminal GUI Toolbox · Linux 下构建 .deb（通用版）

> **这份文档不绑定任何版本。** 无论仓库当前是哪个版本，照着做都能出包 ——
> 版本号一律**从源码读**，不要手抄，也不要在这份文档里写死。
>
> 目标：在 Debian/Ubuntu 上产出一个可 `apt install` 的包
> `terminal-gui-toolbox_<版本>_all.deb`。
>
> 程序是 **Python + Tkinter** 桌面应用：Windows 侧用 PyInstaller 打 exe，
> Linux 侧**不打单文件**，直接装进 `/opt`、依赖系统 `python3-tk` —— 体积小、
> 能跟随系统升级 Python，堆栈里也有真实文件名，便于排障。
>
> ⚠️ **Linux 侧没有做过实机验收**。下面的流程是在 Windows 侧核对源码依赖后写的。
> 第一次在 Linux 上跑，必须按 §6 的「必查三项」逐条过一遍；有偏差回来改这份文档。

---

## 0. 给执行者（人或 AI 智能体）的硬约束

按顺序做完这五件事，任何一步不符就停下来说明，不要"大概能跑"就交付。

| # | 要做的事 | 判定标准 |
|---|---|---|
| 1 | 从**源码**确定版本号 | `VER` 与 `app.py` 的 `APP_VER`、`git tag` 三者一致 |
| 2 | 确认工作区是**该版本的干净源码** | `git describe --tags` 指向该版本；`data/` 不存在 |
| 3 | 铺 `debian/` 六个文件 | §4 的清单一个不少，且 `rules` 只拷代码目录 |
| 4 | 构建 + 验收 | `dpkg-deb -I` 的 Version 等于 `VER`；§6 必查三项全过 |
| 5 | 回报产物 | 给出 `.deb` 的路径、大小、sha256、`dpkg-deb -I` 的 Version 行 |

> **不要**把 `data/` 打进包。里面是 CA 私钥与加密档案，属于使用者本机数据，
> 仓库里也没有（`.gitignore` 挡着，`_pack_release.py` 的 `audit()` 也会拦）。

---

## 1. 先从源码取版本号（唯一来源）

```bash
# 在仓库根执行
VER=$(grep -m1 '^APP_VER' app.py | sed 's/.*"\(.*\)".*/\1/')
echo "源码版本 = $VER"
git describe --tags --always          # 应当等于 v$VER，或在其之后
```

- `APP_VER`（`app.py`）是**唯一来源**。`_pack_release.py` / `_deploy_share.py`
  都从这里读，所以 deb 版本号也必须从这里取。
- 如果 `git describe` 比 `APP_VER` 新（开发中），**先确认要不要发版**；
  发版就按发布链打 tag，不发版就别出 deb（否则包名和 tag 对不上）。
- 以下四处必须一致：`APP_VER`、git tag、`debian/changelog` 首行、
  `.deb` 文件名。**不要复用旧版本号重发。**

---

## 2. 一条命令的版本（推荐先跑通这条）

在**干净**的 Debian 12 / Ubuntu 22.04+ 虚拟机上：

```bash
sudo apt update
sudo apt install -y build-essential debhelper devscripts python3 python3-tk \
                    python3-cryptography python3-paramiko dh-python fonts-noto-cjk

git clone https://github.com/thomachq/terminal-gui-toolbox.git
cd terminal-gui-toolbox
VER=$(grep -m1 '^APP_VER' app.py | sed 's/.*"\(.*\)".*/\1/')
git checkout "v$VER"        # 或用具体提交

# 按 §4 铺好 debian/ 之后：
dpkg-buildpackage -us -uc -b
sudo apt install "../terminal-gui-toolbox_${VER}_all.deb"
terminal-gui-toolbox
```

---

## 3. 目录约定（装到哪、读哪）

| 用途 | 路径 |
|---|---|
| 程序代码 | `/opt/terminal-gui-toolbox/` |
| 可执行入口 | `/usr/bin/terminal-gui-toolbox`（shell wrapper） |
| 桌面菜单 | `/usr/share/applications/terminal-gui-toolbox.desktop` |
| 图标 | `/usr/share/icons/hicolor/256x256/apps/terminal-gui-toolbox.png` |
| 用户数据 | `${XDG_DATA_HOME:-$HOME/.local/share}/terminal-gui-toolbox/`（**不要**写进 /opt） |
| 文档 | `/usr/share/doc/terminal-gui-toolbox/` |

关键点：程序默认在**自身目录**下读写 `data/`（`app.py` 里 `DATA_DIR = APP_DIR / "data"`，
可用环境变量 `TB_DATA_DIR` 覆盖）。装到 `/opt` 后 `/opt` 对普通用户只读，
所以 wrapper **必须**用 `TB_DATA_DIR` 把数据目录指到用户家目录：

```bash
# /usr/bin/terminal-gui-toolbox
#!/bin/sh
set -e
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/terminal-gui-toolbox"
mkdir -p "$DATA_HOME"
export TB_DATA_DIR="$DATA_HOME"
exec python3 /opt/terminal-gui-toolbox/app.py "$@"
```

不要用 `cd /opt/...` 再启动：工作目录与数据目录是两件事，`TB_DATA_DIR` 才是该管的那个。

---

## 4. `debian/` 目录文件模板

六个文件：`control`、`rules`、`changelog`、`copyright`、`terminal-gui-toolbox.wrapper`、
`terminal-gui-toolbox.desktop`。

### `debian/control`

```
Source: terminal-gui-toolbox
Section: utils
Priority: optional
Maintainer: thomachq <thomachq@users.noreply.github.com>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.2
Rules-Requires-Root: no
Homepage: https://github.com/thomachq/terminal-gui-toolbox

Package: terminal-gui-toolbox
Architecture: all
Depends: python3 (>= 3.10), python3-tk, python3-cryptography, python3-paramiko,
         fonts-noto-cjk
Description: Multi-protocol terminal toolbox with an AI operations assistant
 SSH/Telnet/local terminal with host inventory, credential vault, proxy chains,
 script library, audit log, and an AI assistant that runs commands over the same
 session. All capabilities rely on tools already present on the target host.
```

> 依赖要写死，别让用户自己猜。运行时只需 `python3 / python3-tk /
> python3-cryptography / python3-paramiko`；`fonts-noto-cjk` 是为了中文不缺字；
> `python3-xlib` 仅在打算加全局快捷键时才需要。

### `debian/rules`

```make
#!/usr/bin/make -f
%:
	dh $@

override_dh_auto_build:
	@echo "pure python, nothing to build"

override_dh_auto_install:
	install -d debian/terminal-gui-toolbox/opt/terminal-gui-toolbox
	cp -r app.py wbtui.py crypto_store.py lockmgr.py profiles.py \
	      core ui pages api assets \
	      debian/terminal-gui-toolbox/opt/terminal-gui-toolbox/
	find debian/terminal-gui-toolbox/opt -name '__pycache__' -type d -prune -exec rm -rf {} +
	install -d debian/terminal-gui-toolbox/usr/bin
	install -m 755 debian/terminal-gui-toolbox.wrapper \
	        debian/terminal-gui-toolbox/usr/bin/terminal-gui-toolbox
	install -d debian/terminal-gui-toolbox/usr/share/applications
	install -m 644 debian/terminal-gui-toolbox.desktop \
	        debian/terminal-gui-toolbox/usr/share/applications/
	install -d debian/terminal-gui-toolbox/usr/share/icons/hicolor/256x256/apps
	install -m 644 assets/icon.png \
	        debian/terminal-gui-toolbox/usr/share/icons/hicolor/256x256/apps/terminal-gui-toolbox.png
```

> `rules` 里只列**代码目录**。新增顶层模块时要同步加进来，否则装完运行会
> `ModuleNotFoundError`（这条最容易漏）。

### `debian/terminal-gui-toolbox.wrapper`

见 §3 那段 shell，存成这个文件名，`chmod +x`。

### `debian/terminal-gui-toolbox.desktop`

```
[Desktop Entry]
Type=Application
Name=Terminal GUI Toolbox
Name[zh_CN]=终端工具箱
Comment=Multi-protocol terminal with AI operations assistant
Comment[zh_CN]=多协议终端 + 智能运维助手
Exec=terminal-gui-toolbox
Icon=terminal-gui-toolbox
Terminal=false
Categories=System;TerminalEmulator;Network;
Keywords=ssh;telnet;terminal;ops;
StartupWMClass=TerminalToolbox
```

### `debian/changelog`（版本号必须与 `APP_VER` / tag 一致）

格式固定，**从源码生成**，不要凭记忆抄：

```bash
VER=$(grep -m1 '^APP_VER' app.py | sed 's/.*"\(.*\)".*/\1/')
PREV=$(git describe --tags --abbrev=0 "v$VER^" 2>/dev/null || echo "")

{
  printf 'terminal-gui-toolbox (%s) unstable; urgency=medium\n\n' "$VER"
  # 上一个 tag 到当前版本的提交，每条前面加两个空格 + * 
  if [ -n "$PREV" ]; then
    git log --no-merges --pretty='  * %s' "$PREV..HEAD"
  else
    git log --no-merges --pretty='  * %s' -n 20
  fi
  printf '\n -- thomachq <thomachq@users.noreply.github.com>  %s\n' "$(date -R)"
} > debian/changelog
```

生成的形状：

```
terminal-gui-toolbox (1.0.35) unstable; urgency=medium

  * COM 串口改成下拉（检测到的口直接点，仍可手输）
  * 统一原子写（WinError 5），12 处落盘点全部接上 core/atomicio

 -- thomachq <thomachq@users.noreply.github.com>  Wed, 04 Nov 2026 15:40:00 +0800
```

> 更早的条目不用累积 —— `git log` 和 tag 消息就是历史。
> 首行括号里的版本号如果和 `.deb` 文件名对不上，`dpkg-buildpackage` 会按
> changelog 生成包名，结果就是「代码是新的、包名是旧的」。

### `debian/copyright`

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: terminal-gui-toolbox
Source: https://github.com/thomachq/terminal-gui-toolbox

Files: *
Copyright: 2026 thomachq
License: MIT
```

> 许可证按仓库实际 `LICENSE` 填写（当前 MIT）；第三方依赖见
> `THIRD-PARTY-NOTICES.md`，一并放进 `/usr/share/doc/terminal-gui-toolbox/`。

---

## 5. 构建

```bash
# 在源码根，debian/ 已就位
dpkg-buildpackage -us -uc -b

# 产物在上一级目录
ls -l "../terminal-gui-toolbox_${VER}_all.deb"
```

---

## 6. 验收（必查三项 + 打包含量）

```bash
# 1) 包名与版本
dpkg-deb -I "../terminal-gui-toolbox_${VER}_all.deb" | head -12
#    Version: 必须等于 $VER

# 2) 看包内容
dpkg-deb -c "../terminal-gui-toolbox_${VER}_all.deb" | less
#    必须有 /opt/terminal-gui-toolbox/app.py
#    绝不能有 /opt/terminal-gui-toolbox/data/

# 3) 装、跑、卸
sudo apt install "../terminal-gui-toolbox_${VER}_all.deb"
terminal-gui-toolbox          # 能起窗口、能连一台 SSH 即算通过
sudo apt remove terminal-gui-toolbox
```

**必查三项**

1. `python3 -c "import tkinter, cryptography, paramiko"` 全通，无私有库依赖。
2. 装完 `/usr/bin/terminal-gui-toolbox` 存在且可执行，应用菜单里有图标。
3. 首次运行后 `${XDG_DATA_HOME:-~/.local/share}/terminal-gui-toolbox/` 里出现
   `*.dat`，而不是往 `/opt` 里写（写不进去会直接崩）。

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 启动报 `No module named tkinter` | 漏了 `python3-tk` 依赖 |
| 中文全是方块 | 漏 `fonts-noto-cjk`，或系统 locale 不是 UTF-8 |
| `PermissionError: /opt/.../data` | wrapper 没设 `TB_DATA_DIR`，代码在往安装目录写 |
| `ModuleNotFoundError: xxx`（装完才报） | `debian/rules` 的 `cp -r` 漏了新增的顶层模块 |
| 打包时把 `data/` 带进去了 | `rules` 只拷代码目录；`data/` 含 CA 私钥，绝不能进包 |
| 包名版本与源码对不上 | `debian/changelog` 首行没改，或手工抄错 |
| 想把 exe 一起塞进 deb | 不必要也不要：Linux 侧直接跑 `python3` 更省事 |
| Linux 上能开多个实例 | 正常。单实例用命名互斥量（`core/singleinstance.py`），非 Windows 平台 `acquire()` 直接放行；要限制就自己加 `flock` |
| 托盘图标不显示 | Linux 托盘要 StatusNotifier/AppIndicator 支持，`ui/tray.py` 目前只走 Windows 路径，缺了不影响主窗口 |

---

## 8. 版本号规则

- `APP_VER`（`app.py`）、git tag、`debian/changelog`、deb 文件名，**四处必须一致**。
- 每次发版递增：功能 → 次版本号（1.0.6 → 1.1.0）；修 bug → 修订号（1.0.12 → 1.0.13）。
- 不要复用旧版本号重发。
- 仓库**只打签名 tag，不建 Release、不挂任何二进制附件**。要 exe 或 deb 的人
  自己从源码构建。

---

## 9. 执行完成后回报什么

给发起人这四行就够，别贴整段构建日志：

```
版本      : <VER>（来自 app.py APP_VER）
提交      : <git rev-parse --short HEAD>（tag <vVER>）
产物      : <绝对路径>/terminal-gui-toolbox_<VER>_all.deb
大小/sha  : <字节数> / <sha256 前 16 位>
验收      : dpkg-deb -I 的 Version=<VER>；必查三项 1/2/3 全过
```
