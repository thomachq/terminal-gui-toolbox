# bin — 内置调试组件

主机管理里的 **ADB**（安卓）与 **HDC**（鸿蒙）连接，默认先找环境变量里的
`adb` / `hdc`；**找不到时就用这个目录里的那一份**。档案和设置中心都能切
「自动 / 环境变量 / 内置 bin」。

COM（console 串口）不需要外部程序：串口读写是用系统 API 直接做的，所以这里没有。

## 内容

| 文件 | 来源 | 版本 | 许可 |
|---|---|---|---|
| `adb.exe` + `AdbWinApi.dll` + `AdbWinUsbApi.dll` + `libwinpthread-1.dll` | Android SDK Platform-Tools | 37.0.1（adb 1.0.41） | Apache-2.0，见 `NOTICE-android-platform-tools.txt` |
| `hdc.exe` + `libusb_shared.dll` | OpenHarmony SDK toolchains | 3.2.0f | Apache-2.0（OpenHarmony） |

> 这两个程序都是**第三方工具**，本仓库只做随包分发，不修改它们。
> 仓库里不含二进制（`.gitignore` 已排除）：clone 下来只有本说明，
> 打包脚本会把本目录整个塞进工具包 / portable 包。

## 为什么放在应用目录而不是系统 PATH

- 便携：换台机器、拷个文件夹就能用，不需要装 Android SDK 或 DevEco Studio；
- 可预期：用的是随包的那一版，不会被别的软件装的旧 adb 顶掉；
- 可回退：真出问题，删掉 `bin/` 里对应文件，切回「环境变量」即可。

## 补齐 / 升级

```bat
:: 自动从本机已装的 SDK 复制（推荐）
python _fetch_bins.py

:: 或手动：把下面两处的文件拷进本目录
::   %LOCALAPPDATA%\Android\Sdk\platform-tools\  → adb.exe / AdbWin*.dll
::   <OpenHarmony SDK>\toolchains\               → hdc.exe / libusb_shared.dll
```

`_fetch_bins.py` 会顺带打印找到的路径与版本；找不到时给出官方下载地址。

## 端口

- adb server：`5037`
- hdc server：`8710`

两个端口都可能被 Android Studio / DevEco Studio / 手机助手 / 投屏工具抢占。
本程序在连接前会检查，被占用时把「是谁占的、结束它会断什么」摆出来，
由用户决定「由程序结束」还是「自己关掉再重试」。
