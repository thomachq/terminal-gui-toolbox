# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台窗口。

构建：  build.cmd          （等价于 pyinstaller --noconfirm --clean --distpath . toolbox.spec）
产物：  TerminalToolbox.exe  —— 和 data\\ 同一个目录

两点必须留意：

1. onefile 模式运行时把依赖解压到 %TEMP%\\_MEIxxxx，退出即删。
   所以程序里的 DATA_DIR 按 sys.frozen 锚到 exe 同级目录，否则用户
   建好的 CA、档案、密钥全都会在下次启动时"消失"。

2. 证书控制台已经内嵌进主窗口（vpncert_gui.App(embedded=True)，
   直接当 Frame 用），不再靠 `--vpncert` 起子进程；但它仍是运行时
   import，vpncert_gui 这条链上的模块要显式列进 hiddenimports，
   不能指望静态分析一定扫得到。ui.terminal 同理（点按钮才 import）。
"""

hidden = [
    # 证书控制台（app.py 里是运行时 import）
    'vpncert_gui', 'pkilib', 'nasops',
    # 本地存储与加密
    'crypto_store', 'lockmgr', 'profiles',
    # SSH
    'paramiko', 'paramiko.sftp',
    'cryptography.hazmat.backends.openssl',
    'cryptography.hazmat.bindings._rust',
    'cryptography.hazmat.primitives.asymmetric.ed25519',
    'cryptography.hazmat.primitives.asymmetric.rsa',
    'cryptography.hazmat.primitives.serialization.ssh',
    'cryptography.hazmat.primitives.ciphers.aead',
    # 本工具箱自身的分层（页面是 Mixin，靠多继承拼进 ToolboxApp，
    # 静态分析有时扫不到，全部显式列出）
    'core.detect', 'core.session', 'core.telnet_session', 'core.metrics',
    'core.cli_channel', 'core.ssh_shell', 'core.sessionmgr',
    'core.parsers', 'core.routes', 'core.firewall', 'core.docker',
    'core.files', 'core.redact', 'core.webstack', 'core.aiops',
    'core.secscan', 'core.dbscan', 'core.cronjob',
    'core.ipaddr', 'core.iplib',
    # 主机资源四件套 + 操作审计 + AI 活动流（v1.0.4）
    'core.hosts', 'core.creds', 'core.proxies', 'core.scripts',
    'core.audit', 'core.aitrace',
    # 剪贴板读文件/图片（v1.0.6）：纯 ctypes，不进 hiddenimports
    # 打包后会因为「只在运行时 import」被漏掉
    'core.clipfile',
    'core.aisession',
    'core.ai_skills', 'core.ai_skills_builtin', 'core.agent',
    'ui.statusbar', 'ui.logpanel', 'ui.table', 'ui.forms',
    'ui.icon', 'ui.tray', 'ui.terminal', 'ui.tabs', 'ui.md',
    'pages.routes_page', 'pages.firewall_page',
    'pages.docker_page', 'pages.files_page', 'pages.api_page',
    'pages.web_page', 'pages.ai_page', 'pages.ai_dock',
    'pages.settings_center',
    'pages.security_page', 'pages.db_page', 'pages.cron_page',
    'pages.hosts_page', 'pages.cred_page', 'pages.proxy_page',
    'pages.script_page', 'pages.log_page', 'pages.model_page',
    # 给 AI 智能体的本地 HTTP API
    'api.pool', 'api.server',
    'wbtui',
]

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    # 图标打进 exe 里：用户只拷一个 exe 也能有托盘/窗口图标。
    # 程序里会先找 exe 同级的 assets\，找不到才回落 sys._MEIPASS。
    # 地址库（常用网段/省级/国家）也在这里：~3.8 MB，离线可用，
    # 用户点「更新地址库」时写的是 data/iplib（覆盖内置这份）。
    datas=[('assets/app.ico', 'assets'), ('assets/app.png', 'assets'),
           ('assets/iplib', 'assets/iplib')],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 主程序不用这些，排掉可显著缩小体积
    excludes=[
        'numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
        'IPython', 'pytest', 'notebook', 'pydoc_data',
        'tkinter.test', 'lib2to3', 'distutils',
        'setuptools', 'pip', 'wheel',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TerminalToolbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
)
