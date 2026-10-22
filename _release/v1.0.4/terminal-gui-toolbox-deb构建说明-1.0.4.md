# Terminal GUI Toolbox · Linux 下构建 .deb 说明（v1.0.4）

> 目标：在 Debian/Ubuntu 上产出一个可 `apt install` 的包。
> 产物名：`terminal-gui-toolbox_1.0.4_amd64.deb`（版本号与 tag 一致）。
>
> 现状说明：这个程序是 **Python + Tkinter** 桌面应用，Windows 侧用 PyInstaller 打成 exe。
> Linux 侧不打单文件，直接**打成 deb 装进 /opt**，依赖系统 python3-tk —— 体积小、能跟随系统
> 升级 Python，也便于排查（堆栈里有真实文件名）。

---

## 0. 一条命令的版本（推荐先跑通这条）

在**干净**的 Debian 12 / Ubuntu 22.04+ 虚拟机上：

```bash
sudo apt update
sudo apt install -y build-essential debhelper devscripts python3 python3-tk \
                    python3-cryptography python3-paramiko qt6-base-dev-tools 2>/dev/null || true
sudo apt install -y python3-tk python3-cryptography python3-paramiko dh-python

git clone https://github.com/thomachq/terminal-gui-toolbox.git
cd terminal-gui-toolbox
git checkout v1.0.4

# 用下面的第 3 节文件铺好 debian/，然后：
dpkg-buildpackage -us -uc -b
sudo apt install ../terminal-gui-toolbox_1.0.4_amd64.deb
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
| 用户数据 | `~/.local/share/terminal-gui-toolbox/`（**不要**写进 /opt） |
| 文档 | `/usr/share/doc/terminal-gui-toolbox/` |

关键点：程序默认在**自身目录**下读写 `data/`。装到 /opt 后 /opt 是只读的，
所以 wrapper 必须把数据目录指到用户家目录：

```bash
# /usr/bin/terminal-gui-toolbox
#!/bin/sh
export TERMTOOLBOX_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/terminal-gui-toolbox"
mkdir -p "$TERMTOOLBOX_HOME"
cd /opt/terminal-gui-toolbox || exit 1
exec python3 /opt/terminal-gui-toolbox/app.py "$@"
```

> 代码里 `DATA_DIR` 需要支持环境变量覆盖（若尚未支持，在 `app.py` 顶部按
> `os.environ.get("TERMTOOLBOX_HOME")` 优先取值；Windows 下不受影响）。

---

## 2. 依赖（control 里写死，别让用户自己猜）

运行时：`python3 (>=3.10)`, `python3-tk`, `python3-cryptography`, `python3-paramiko`
建议：`python3-xlib`（如需全局快捷键润色）、`fonts-noto-cjk`（中文不缺字）

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

```
terminal-gui-toolbox (1.0.4) unstable; urgency=medium

  * Host resources: hosts / proxies / credentials / scripts / audit log.
  * Tabbed shell with embeddable or detachable terminal.
  * AI activity stream: model requests, commands and output mirrored to terminal.
  * Multi-model management with per-model probe and switch.
  * Markdown rendering and model picker in the assistant dock.

 -- thomachq <thomachq@users.noreply.github.com>  Thu, 23 Oct 2026 01:00:00 +0800
```

### `debian/copyright`

```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: terminal-gui-toolbox
Source: https://github.com/thomachq/terminal-gui-toolbox

Files: *
Copyright: 2026 thomachq
License: MIT
```

> 许可证按仓库实际 LICENSE 填写；若为 MIT，这里写 MIT 并把全文放到
> `/usr/share/doc/terminal-gui-toolbox/copyright`。

---

## 4. 构建与验收

```bash
# 构建（在源码根，debian/ 已就位）
dpkg-buildpackage -us -uc -b

# 看包内容对不对
dpkg-deb -c ../terminal-gui-toolbox_1.0.4_amd64.deb | less
dpkg-deb -I ../terminal-gui-toolbox_1.0.4_amd64.deb

# 装、跑、卸
sudo apt install ../terminal-gui-toolbox_1.0.4_amd64.deb
terminal-gui-toolbox            # 能起窗口、能连一台 SSH 即算通过
sudo apt remove terminal-gui-toolbox
```

**必查三项**

1. `ldd` 不涉及私有库；`python3 -c "import tkinter, cryptography, paramiko"` 全通。
2. 装完 `/usr/bin/terminal-gui-toolbox` 存在且可执行，菜单里有图标。
3. 首次运行后 `~/.local/share/terminal-gui-toolbox/` 里出现 `*.dat`，
   而不是往 `/opt` 里写（写不进去会崩）。

---

## 5. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 启动报 `No module named tkinter` | 漏了 `python3-tk` 依赖 |
| 中文全是方块 | 漏 `fonts-noto-cjk`，或系统 locale 不是 UTF-8 |
| `PermissionError: /opt/.../data` | wrapper 没设 `TERMTOOLBOX_HOME`，代码在往安装目录写 |
| 打包时把 `data/` 带进去了 | `rules` 里只拷代码目录，**绝不能**拷 `data/`（含 CA 私钥与密文档案） |
| 想把 exe 一起塞进 deb | 不必要，也不要：Linux 侧直接跑 python3 更省事 |

---

## 6. 版本号规则

- `APP_VER`（`app.py`）、git tag、`debian/changelog`、deb 文件名，**四处必须一致**。
- 每次发版递增：功能 → 次版本号（1.0.4 → 1.1.0）；修 bug → 修订号（1.0.4 → 1.0.5）。
- 不要复用旧版本号重发；旧 Release 的附件也不覆盖，让 tag 与附件始终对得上。
