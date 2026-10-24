# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['E:/PythonProjects/Python/ScreenSnap/main.py'],
    pathex=[],
    binaries=[],
    datas=[('E:/PythonProjects/Python/ScreenSnap/assets/icons', 'assets/icons'),
           ('E:/PythonProjects/Python/ScreenSnap/assets/icon.png', 'assets/icon.png')],
    hiddenimports=['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'numpy', 'pyarrow', 'jedi', 'lxml', 'zmq', 'IPython', 'notebook', 'scipy', 'matplotlib'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenSnap',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['E:/PythonProjects/Python/ScreenSnap/assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScreenSnap',
)
