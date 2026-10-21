# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台窗口。

构建：pyinstaller --noconfirm --clean vpncert.spec
产物：dist/VPNCertConsole.exe

说明：onefile 模式下运行时会把依赖解压到 %TEMP%\\_MEIxxxx，
而 vpncert_gui.py 里的 DATA_DIR 已按 sys.frozen 锚定到 exe 同级目录，
所以证书数据不会随临时目录一起消失。
"""

a = Analysis(
    ['vpncert_gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'paramiko',
        'paramiko.sftp',
        'cryptography.hazmat.backends.openssl',
        'cryptography.hazmat.bindings._rust',
        'cryptography.hazmat.primitives.asymmetric.ed25519',
        'cryptography.hazmat.primitives.asymmetric.rsa',
        'cryptography.hazmat.primitives.serialization.ssh',
        'cryptography.hazmat.primitives.ciphers.aead',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 主程序不用这些，排掉可显著缩小体积
    excludes=[
        'numpy', 'pandas', 'matplotlib', 'scipy', 'PIL',
        'IPython', 'pytest', 'notebook', 'pydoc_data',
        'tkinter.test', 'lib2to3', 'distutils',
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
    name='VPNCertConsole',
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
