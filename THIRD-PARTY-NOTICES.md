# 第三方组件声明

本项目以 **MIT** 许可发布（见 `LICENSE`）。MIT 只覆盖本仓库作者的原创代码，
下列第三方组件仍归各自作者所有，并按各自许可证分发。

## 运行时依赖

| 组件 | 版本（开发时） | 许可证 | 在本项目中的用途 |
|---|---|---|---|
| [paramiko](https://github.com/paramiko/paramiko) | 5.1.0 | **LGPL-2.1** | SSH / SFTP 传输层、密钥解析与指纹 |
| [cryptography](https://github.com/pyca/cryptography) | 50.0.2 | Apache-2.0 OR BSD-3-Clause | AES-256-GCM、PBKDF2、X.509 证书与 CRL |

paramiko 的间接依赖（bcrypt、PyNaCl、cffi）分别遵循 Apache-2.0 与 MIT。

### 关于 LGPL-2.1（paramiko）

本项目**只是调用** paramiko 的公开接口，没有修改其源码，也没有把它静态链接进自研代码。
分发 `TerminalToolbox.exe` 时，paramiko 以未修改的原始形态被 PyInstaller 一并收集进包内，
使用者可以自行用 `pip install paramiko==5.1.0` 取得同样的库，或从
<https://github.com/paramiko/paramiko> 获取其完整源码并替换实验。
按 LGPL-2.1 第 6 条，这属于「使用库的作品」，本项目的 MIT 许可不覆盖该库本身。

## 随包分发的调试工具（`bin/`）

这两个不是 Python 库，而是**独立可执行文件**，由程序以子进程方式调用；
不修改、不链接，许可证与源码见各自上游。

| 组件 | 版本（随包） | 许可证 | 用途 |
|---|---|---|---|
| [Android SDK Platform-Tools（adb）](https://developer.android.com/tools/releases/platform-tools) | 37.0.1（adb 1.0.41） | Apache-2.0 | ADB 连接（USB / 网络） |
| [OpenHarmony hdc](https://gitee.com/openharmony/developtools_hdc) | 3.2.0f | Apache-2.0 | HDC 连接（鸿蒙设备） |

`bin/NOTICE-android-platform-tools.txt` 是 platform-tools 原样附带的 NOTICE。
仓库**不跟踪**这些二进制（`.gitignore` 已排除）：clone 后跑 `python _fetch_bins.py`
即可从本机已装的 SDK 补齐；发布用的工具包 / portable 包里则由 `_pack_release.py`
显式带上，开箱可用。

## 打包工具

| 组件 | 版本 | 许可证 | 说明 |
|---|---|---|---|
| [PyInstaller](https://pyinstaller.org/) | 6.22.3 | GPL-2.0 **含 bootloader 例外条款** | 用于生成单文件 exe |

PyInstaller 的许可证带有明确的例外：**用它打包出的可执行文件不受 GPL 传染**，
可以按任意许可（本项目为 MIT）分发。这是 PyInstaller 许可中的原文例外，
不是本项目的额外声明。

## 开发期依赖（不随程序分发）

| 组件 | 许可证 | 用途 |
|---|---|---|
| [Pillow](https://python-pillow.org/) | MIT-CMU（HPND） | 截图、图标生成的开发脚本 |

## 界面风格

界面配色与观感参考了 WorkBuddy 的浅色蓝调风格，**代码与资源均为独立实现**，
未复制任何第三方源码、图标或字体文件。界面中不含任何第三方商标素材；
设备图标（Linux / Windows / macOS / FreeBSD / 网络设备）均由 `ui/statusbar.py`
用 Canvas 自绘，运行时不需要外部图片。

如发现上述归属有遗漏或错误，请开 issue，会尽快更正。
