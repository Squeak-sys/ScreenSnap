# -*- coding: utf-8 -*-
"""ScreenSnap Qt 版 UI 包（P1 骨架、P2 选区、P3 编辑器）。

目录分工（迁移期与旧 tkinter 版并行）::

    ui_qt/theme.py      设计 Token（SHUTTER）+ theme.qss 加载与拼装
    ui_qt/theme.qss     QSS 模板（${TOKEN} 占位由 theme.qss() 替换）
    ui_qt/geometry.py   DPI / 多屏坐标层：内部统一物理像素
    ui_qt/imageutil.py  PIL <-> QImage / QPixmap 转换
    ui_qt/clipboard.py  剪贴板兼容层（沿用 copy_image_to_clipboard(img) 签名）
    ui_qt/dialogs.py    弹窗/文件对话框（按鼠标所在屏定位，替代 dialogs.patch）
    ui_qt/app.py        QApplication 初始化、单实例、托盘、热键桥接
    ui_qt/capture_overlay.py  截图选区层（全屏遮罩 + 框选/窗口/全屏）
    ui_qt/editor.py     图像编辑器（选区调整 + 7 种标注 + 撤销 + 完成）

约定：本包不 import 任何旧 UI 模块（capture / editor / gallery / settings /
sticker / recorder），只复用无 UI 的 config / storage / hotkey。
"""

__all__ = [
    "theme",
    "geometry",
    "imageutil",
    "clipboard",
    "dialogs",
    "app",
    "capture_overlay",
    "editor",
]
