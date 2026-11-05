# -*- mode: python ; coding: utf-8 -*-
"""Qt 版打包配置（P7）。

与旧 tk 版 spec 并存：本文件入口为 main_qt.py，产物名 ScreenSnap-Qt，
输出到 dist_qt / build_qt，不覆盖仍在用的 dist\\ScreenSnap（tk 版）。
"""

BASE = 'E:/PythonProjects/Python/ScreenSnap'

# ---- P9：OCR 依赖 ----
# 主程序内置「Windows 系统 OCR」后端（winrt 投影，零模型、约 +5MB）；
# RapidOCR（onnxruntime + 模型，约 +40~60MB）不进主包，改为运行时组件包安装。
#
# winrt（PyWinRT 4.x）是 namespace package：site-packages/winrt 下没有
# __init__.py，collect_submodules('winrt') 只能看到 runtime / system 等少数
# 顶层模块，windows.* 投影子包全部不可见；而这些投影包各自的目录里还带着
# 一个 _table.pywinrt（import 时由 winrt._winrt 运行时读取的二进制表），
# PyInstaller 只收 .py 不收表，missing 表会让 frozen 版 import 直接失败。
# winrt-runtime 自带的 pyinstaller40 hook 干的正是这件事，但它在 namespace
# 包情形下入口点发现不可靠（实测 _internal 里一个 winrt 文件都没有），
# 因此在 spec 里显式复刻同等收集：表文件进 datas、投影包名进 hiddenimports、
# 运行库扩展与随包 dll 进 binaries。
import os                     # noqa: E402
import sys                    # noqa: E402
from importlib.metadata import distributions  # noqa: E402

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

WINRT_TABLE = '_table.pywinrt'

winrt_datas, winrt_binaries, winrt_hidden = [], [], []


def _collect_winrt():
    """展开已安装 PyWinRT 投影包：返回 (datas, binaries, hiddenimports)。"""
    entries = []                     # (文件名, 包目录, 源绝对路径)
    winrt_dir = None
    for dist in distributions():
        accessor = dist.locate_file('')
        for f in dist.files or []:
            pkg = str(f).replace('\\', '/').rpartition('/')[0]
            if not pkg:
                continue
            src = str(dist.locate_file(f))
            entries.append((f.name, pkg, src))
            if f.name == WINRT_TABLE and pkg == 'winrt':
                winrt_dir = os.path.dirname(src)
            if f.name.startswith('_winrt.') and f.name.endswith('.pyd'):
                winrt_dir = os.path.dirname(src)

    roots = {pkg.partition('/')[0] for name, pkg, _ in entries if name == WINRT_TABLE}
    datas, binaries, hidden = [], [], []
    for name, pkg, src in entries:
        if pkg.partition('/')[0] not in roots:
            continue
        if name == WINRT_TABLE:
            datas.append((src, pkg))
            hidden.append(pkg.replace('/', '.'))
        elif name.lower().endswith('.dll'):
            binaries.append((src, pkg))

    if winrt_dir and os.path.isdir(winrt_dir):
        for name in os.listdir(winrt_dir):
            src = os.path.join(winrt_dir, name)
            if not os.path.isfile(src):
                continue
            low = name.lower()
            if low.endswith('.pyd') or low.endswith('.dll'):
                binaries.append((src, 'winrt'))

    # 补齐 import 图看不到的部分：namespace 顶层模块、运行库扩展、投影子模块
    hidden += ['winrt', 'winrt._winrt', 'winrt.runtime', 'winrt.system']
    hidden += [m for m in collect_submodules('winrt') if m and '.__pyinstaller' not in m]
    return datas, binaries, hidden


winrt_datas, winrt_binaries, winrt_hidden = _collect_winrt()

if not winrt_hidden:
    print('!! [SPEC] 当前解释器未安装 winrt，Windows 系统 OCR 无法打进包：',
          sys.executable)
else:
    print('!! [SPEC] winrt 收集：datas=%d binaries=%d hidden=%d（解释器 %s）'
          % (len(winrt_datas), len(winrt_binaries), len(winrt_hidden), sys.executable))

a = Analysis(
    [BASE + '/main_qt.py'],
    pathex=[BASE],
    binaries=list(dict.fromkeys(winrt_binaries)),
    datas=[(BASE + '/assets', 'assets')] + winrt_datas,
    hiddenimports=[
        'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'PIL.Image', 'PIL.ImageFilter', 'PIL.ImageGrab',
        'winrt', 'winrt.system', 'winrt.windows.foundation',
        'winrt.windows.foundation.collections',
        'winrt.windows.globalization',
        'winrt.windows.graphics.imaging',
        'winrt.windows.media.ocr',
        'winrt.windows.storage',
        'winrt.windows.storage.streams',
    ] + list(dict.fromkeys(winrt_hidden)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pandas', 'numpy', 'pyarrow', 'jedi', 'lxml', 'zmq', 'IPython',
        'notebook', 'scipy', 'matplotlib',
        # ---- P9：OCR 组件包不进主包（运行时安装到组件目录）----
        'rapidocr', 'onnxruntime', 'cv2', 'shapely', 'pyclipper',
        'yaml', 'omegaconf', 'tokenizers',
        # 旧 tk 版专属依赖：Qt 版不再需要
        'tkinter', '_tkinter', 'pystray', 'PIL._tkinter_finder',
        # ---- P8：体积裁剪（Qt 侧未使用模块）----
        # QML / Quick / WebEngine / Sql / Test / Multimedia / 打印 / 网络 / SVG 均未使用
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtQuickControls2', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtWebChannel', 'PySide6.QtSql', 'PySide6.QtTest',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtPrintSupport', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
        'PySide6.QtNetwork', 'PySide6.QtXml', 'PySide6.QtDBus',
        'PySide6.QtPositioning', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
        'PySide6.QtSerialPort', 'PySide6.QtCharts', 'PySide6.QtPdf',
        'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        # PIL：AVIF 编码器（约 7.5MB）与 Qt 版功能无关
        'PIL._avif',
        # 标准库里明显用不到的重家伙
        'unittest', 'doctest', 'pydoc', 'pydoc_data', 'test',
        'xmlrpc', 'ftplib', 'smtplib', 'imaplib', 'poplib', 'telnetlib',
        'distutils', 'setuptools', 'pip', 'wheel', 'pkg_resources',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ScreenSnap-Qt',
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
    icon=[BASE + '/assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ScreenSnap-Qt',
)
