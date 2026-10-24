# Terminal GUI Toolbox · Linux 下构建 .deb 说明（{{VER_V}}）

> 目标：在 Debian/Ubuntu 上产出一个可 `apt install` 的包。
> 产物名：`terminal-gui-toolbox_{{VER}}_all.deb`（版本号与 tag 一致）。
>
> 现状说明：这个程序是 **Python + Tkinter** 桌面应用，Windows 侧用 PyInstaller 打成 exe。
> Linux 侧不打单文件，直接**打成 deb 装进 /opt**，依赖系统 python3-tk —— 体积小、能跟随系统
> 升级 Python，也便于排查（堆栈里有真实文件名）。
>
> 注意：**Linux 侧没有做过实机验收**。下面这套流程是在 Windows 侧核对过源码依赖后写的，
> 第一次在 Linux 上跑时请按 §4 的必查三项逐条过一遍，有偏差回来改这份文档。
>
> 这份文件是**模板**（`docs/模板-deb构建说明.md`），由 `_pack_release.py` 的
> `render_docs()` 代入版本号生成 `_release/v{{VER}}/terminal-gui-toolbox-deb构建说明-{{VER}}.md`。
> **改内容改模板**，不要去改生成出来的那份。

---

## 0. 一条命令的版本（推荐先跑通这条）

在**干净**的 Debian 12 / Ubuntu 22.04+ 虚拟机上：

```bash
sudo apt update
sudo apt install -y build-essential debhelper devscripts python3 python3-tk \
                    python3-cryptography python3-paramiko dh-python
sudo apt install -y fonts-noto-cjk

git clone https://github.com/thomachq/terminal-gui-toolbox.git
cd terminal-gui-toolbox
git checkout {{VER_V}}

# 用下面的第 3 节文件铺好 debian/，然后：
dpkg-buildpackage -us -uc -b
sudo apt install ../terminal-gui-toolbox_{{VER}}_all.deb
terminal-gui-toolbox
```

---

## 1. 目录约定（装到哪、读哪）

| 用途 | 路径 |
|---|---|
| 程序代码 | `/opt/terminal-gui-toolbox/` |
| 可执行入口 | `/usr/bin/terminal-gui-toolbox`（shell wrapper） |
| 桌面菜单 | `/usr/share/applications/terminal-gui-toolbox.desktop` |
| 图标 | `/usr/share/icons/hicolor/256x256/apps/terminal-gui-toolbox.png` |
| 用户数据 | `${XDG_DATA_HOME:-~/.local/share}/terminal-gui-toolbox/`（**不要**写进 /opt） |
| 文档 | `/usr/share/doc/terminal-gui-toolbox/` |

关键点：程序默认在**自身目录**下读写 `data/`（`app.py` 里 `DATA_DIR = APP_DIR / "data"`）。
装到 /opt 后 /opt 对普通用户是只读的，所以 wrapper 必须用 `TB_DATA_DIR` 把数据目录
指到用户家目录 —— 这个环境变量 `app.py` 已经支持（第 91 行），不用改代码：

```bash
# /usr/bin/terminal-gui-toolbox
#!/bin/sh
set -e
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/terminal-gui-toolbox"
mkdir -p "$DATA_HOME"
export TB_DATA_DIR="$DATA_HOME"
exec python3 /opt/terminal-gui-toolbox/app.py "$@"
```

不要用 `cd /opt/...` 再启动：工作目录与数据目录是两件事，`TB_DATA_DIR`
才是唯一该管的那个。

---

## 2. 依赖（control 里写死，别让用户自己猜）

运行时：`python3 (>=3.10)`, `python3-tk`, `python3-cryptography`, `python3-paramiko`
建议：`fonts-noto-cjk`（中文不缺字）；`python3-xlib` 仅在你打算加全局快捷键时才需要

装完可用 `python3 -c "import tkinter, cryptography, paramiko"` 自检。

---

## 3. debian/ 目录文件模板

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

> 只拷代码目录。**绝不能**拷 `data/` —— 里面是 CA 私钥与密文档案，属于使用者本机数据，
> 仓库里也没有（`.gitignore` 挡着，`_pack_release.py` 的 `audit()` 也会拦）。

### `debian/terminal-gui-toolbox.wrapper`

见第 1 节那段 shell，存成这个文件名，`chmod +x`。

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

### `debian/changelog`（版本号必须与 tag 一致）

每次发版在最上面**追加**一条，日期用发包当天：

```
terminal-gui-toolbox ({{VER}}) unstable; urgency=medium

  * Delivery docs are now rendered from templates in docs/ by the release
    script, so the handover and .deb guides can no longer silently go stale.
  * deploy_share.py now refuses to deploy when any of the six artifacts is
    missing, instead of skipping it quietly.
  * Fixed: editing an existing host always failed with a duplicate-name error
    because the uniqueness check compared against itself using object identity,
    while host records are keyed by name and validated against a merged copy.
  * Single-instance guard on Windows: a second launch raises the existing window.
  * Operations pages gained an explicit "operation target" selector.
  * Host list shows live connection status; clicking a device opens its terminal.
  * Firewall and routing tables can be edited graphically.
  * Model management grouped by provider, plus an MCP library.
  * Assistant: multi-session tabs, skills, connectors, file references.
  * Fixed table cursor sticking on hover and auto-probe never firing.

 -- thomachq <thomachq@users.noreply.github.com>  {{DATE_RFC2822}}
```

> 更早的条目不用在这里累积 —— `git log` 和 tag 消息就是历史。

### `debian/copyright`

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: terminal-gui-toolbox
Source: https://github.com/thomachq/terminal-gui-toolbox

Files: *
Copyright: 2026 thomachq
License: MIT
```

> 许可证按仓库实际 `LICENSE` 填写（当前是 MIT），第三方依赖见仓库
> `THIRD-PARTY-NOTICES.md`，一并放进 `/usr/share/doc/terminal-gui-toolbox/`。

---

## 4. 构建与验收

```bash
# 构建（在源码根，debian/ 已就位）
dpkg-buildpackage -us -uc -b

# 看包内容对不对
dpkg-deb -c ../terminal-gui-toolbox_{{VER}}_all.deb | less
dpkg-deb -I ../terminal-gui-toolbox_{{VER}}_all.deb

# 装、跑、卸
sudo apt install ../terminal-gui-toolbox_{{VER}}_all.deb
terminal-gui-toolbox            # 能起窗口、能连一台 SSH 即算通过
sudo apt remove terminal-gui-toolbox
```

**必查三项**

1. `python3 -c "import tkinter, cryptography, paramiko"` 全通，无私有库依赖。
2. 装完 `/usr/bin/terminal-gui-toolbox` 存在且可执行，菜单里有图标。
3. 首次运行后 `${XDG_DATA_HOME:-~/.local/share}/terminal-gui-toolbox/` 里出现 `*.dat`，
   而不是往 `/opt` 里写（写不进去会崩）。

---

## 5. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 启动报 `No module named tkinter` | 漏了 `python3-tk` 依赖 |
| 中文全是方块 | 漏 `fonts-noto-cjk`，或系统 locale 不是 UTF-8 |
| `PermissionError: /opt/.../data` | wrapper 没设 `TB_DATA_DIR`，代码在往安装目录写 |
| 打包时把 `data/` 带进去了 | `rules` 里只拷代码目录；`data/` 含 CA 私钥，绝不能进包 |
| 想把 exe 一起塞进 deb | 不必要，也不要：Linux 侧直接跑 python3 更省事 |
| Linux 上能开多个实例 | 正常。单实例用命名互斥量（`core/singleinstance.py`），在非 Windows 平台 `acquire()` 直接放行；要限制就自己加 `flock` 保护 |
| 托盘图标不显示 | Linux 托盘要 StatusNotifier/AppIndicator 支持，`ui/tray.py` 目前只走 Windows 路径，缺了不影响主窗口 |

---

## 6. 版本号规则

- `APP_VER`（`app.py`）、git tag、`debian/changelog`、deb 文件名，**四处必须一致**。
- 每次发版递增：功能 → 次版本号（1.0.6 → 1.1.0）；修 bug → 修订号（1.0.12 → 1.0.13）。
- 不要复用旧版本号重发。
- 仓库**只打签名 tag，不建 Release、不挂任何二进制附件**（v1.0.6 之前的旧 Release
  已全部删除，tag 保留）。要 exe 或 deb 的人自己从源码构建。
