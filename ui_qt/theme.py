# -*- coding: utf-8 -*-
"""ScreenSnap Qt 主题层 —— SHUTTER（取景器）设计语言。

数值与根目录 ``ui_theme.py`` 保持一致（tk 版继续使用那份 Token，本模块不 import
它，避免反向依赖）。本模块只做三件事：

1. 提供 Python 侧可直接使用的 Token 常量 / QColor / QFont 工厂；
2. 加载 ``theme.qss`` 并把 ``${TOKEN}`` 占位替换为实际色值（单一数据源）；
3. 提供胶囊按钮等常用控件的内联样式生成函数。

用法::

    from ui_qt import theme
    theme.apply(app)                    # 全局 QSS + 默认字体
    btn.setStyleSheet(theme.pill_style(theme.ACCENT, theme.ACCENT_HOVER))
"""

from __future__ import annotations

import os
import re
import sys

from PySide6.QtGui import QColor, QFont

# ---------------- 色彩系统 ----------------
ACCENT = "#2F6DF6"            # 主色 · 快门蓝
ACCENT_HOVER = "#2559D6"      # 主按钮悬停
ACCENT_SOFT = "#E8EFFE"       # 选中底色 / 聚焦光环
SUCCESS = "#0FA958"           # 完成 / 绿色确认
SUCCESS_HOVER = "#0D9550"     # 完成按钮悬停
DANGER = "#E5484D"            # 删除 / 录制红点 / 关闭钮
DANGER_HOVER = "#D93B40"      # 危险悬停
WARN = "#F5A623"              # 冲突提示 / 即将过期
INK = "#1C2430"               # 一级文本
INK_2 = "#5B6472"             # 二级文本
INK_3 = "#9AA2AD"             # 三级文本 / 占位 / 快捷键角标
LINE = "#E3E6EA"              # 分割线 / 边框
SURFACE = "#FFFFFF"           # 窗口面板
SURFACE_2 = "#F7F8FA"         # 侧栏 / 输入底
SURFACE_3 = "#EEF0F3"         # 悬停底
CANVAS = "#F0F1F4"            # 页面 / 工作区底
CANVAS_DARK = "#15171B"       # 深色画布底（历史库缩略图画布 / 大图查看）
TOOLBAR = "#DEDFE1"           # 悬浮工具栏底
MASK_RGB = (16, 22, 32)       # 截图遮罩 RGB（绘制时叠加 alpha 0.46）
MASK_ALPHA = 0.46

# ---------------- 圆角 ----------------
R_WIN = 10                    # 窗口 / 大卡片
R_CARD = 10                   # 卡片
R_BTN = 8                     # 按钮 / 输入框
R_INPUT = 8
R_PILL = 999                  # 胶囊 / 开关 / 徽标

# ---------------- 字体 ----------------
FONT_FAMILY = "Microsoft YaHei UI"
FONT_DIGIT = "Segoe UI"       # 尺寸徽标 / 数字
FONT_FALLBACKS = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Arial")

FONT_MAIN_SIZE = 9            # 通用控件（pt）
FONT_SM_SIZE = 8              # 辅助 caption
FONT_BODY_SIZE = 10           # 表单正文 / 设置行标题
FONT_SECTION_SIZE = 12        # 分区标题（bold）
FONT_TITLE_SIZE = 14          # 窗口标题（bold）
FONT_BADGE_SIZE = 9           # 尺寸徽标（bold）

# ---------------- 尺寸 ----------------
TOOLBAR_H = 32                # 悬浮工具条高度
TOOL_BTN = 24                 # 工具按钮直径
TOOL_ICON = 24                # 图标显示尺寸
SWITCH_W = 40                 # Switch 宽
SWITCH_H = 22                 # Switch 高
SHADOW_PAD = 18               # 无边框窗口为投影预留的透明边距

# ---------------- 编辑器色板（10 常用色 + 自定义） ----------------
PALETTE = [
    (229, 72, 77),    # danger 红
    (245, 166, 35),   # warn 橙
    (242, 201, 76),   # 金黄
    (15, 169, 88),    # success 绿
    (47, 109, 246),   # accent 蓝
    (123, 97, 255),   # 紫
    (232, 121, 185),  # 粉
    (28, 36, 48),     # ink 黑
    (91, 100, 114),   # ink-2 灰
    (255, 255, 255),  # 白
]
PALETTE_NAMES = ["红", "橙", "黄", "绿", "蓝", "紫", "粉", "黑", "灰", "白"]

# ---------------- 线宽预设（1 / 2 / 4 / 6 / 8） ----------------
WIDTH_PRESETS = [1, 2, 4, 6, 8]
WIDTH_NAMES = {1: "极细", 2: "细", 4: "常规", 6: "粗", 8: "特粗"}

# 供 QSS 模板替换的 Token 白名单（必须全部是字符串值）
TOKENS = {
    "ACCENT": ACCENT,
    "ACCENT_HOVER": ACCENT_HOVER,
    "ACCENT_SOFT": ACCENT_SOFT,
    "SUCCESS": SUCCESS,
    "SUCCESS_HOVER": SUCCESS_HOVER,
    "DANGER": DANGER,
    "DANGER_HOVER": DANGER_HOVER,
    "WARN": WARN,
    "INK": INK,
    "INK_2": INK_2,
    "INK_3": INK_3,
    "LINE": LINE,
    "SURFACE": SURFACE,
    "SURFACE_2": SURFACE_2,
    "SURFACE_3": SURFACE_3,
    "CANVAS": CANVAS,
    "CANVAS_DARK": CANVAS_DARK,
    "TOOLBAR": TOOLBAR,
    "R_WIN": str(R_WIN),
    "R_CARD": str(R_CARD),
    "R_BTN": str(R_BTN),
    "R_INPUT": str(R_INPUT),
    "R_PILL": str(R_PILL),
    "FONT_FAMILY": FONT_FAMILY,
}

_TOKEN_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_qss_cache: str | None = None


# ---------------- 路径 ----------------
def resource_path(name: str) -> str:
    """返回包内资源绝对路径，兼容 PyInstaller 单文件解包目录（sys._MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "ui_qt", name) if getattr(sys, "_MEIPASS", None) else os.path.join(base, name)


def qss_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme.qss")


# ---------------- 颜色 / 字体 ----------------
def color(value, alpha: int | None = None) -> QColor:
    """字符串 / RGB 元组 -> QColor，可附加 0-255 alpha。"""
    if isinstance(value, QColor):
        c = QColor(value)
    elif isinstance(value, (tuple, list)):
        r, g, b = (list(value) + [0, 0, 0])[:3]
        c = QColor(int(r), int(g), int(b))
    else:
        c = QColor(str(value))
    if alpha is not None:
        c.setAlpha(int(alpha))
    return c


def mask_color() -> QColor:
    """截图遮罩色（RGB + 0.46 alpha）。"""
    return color(MASK_RGB, int(round(MASK_ALPHA * 255)))


def font(size: int = FONT_MAIN_SIZE, bold: bool = False,
         family: str | None = None, digit: bool = False) -> QFont:
    """构造带字体回退链的 QFont（size 为 pt，与 tk 版字号语义一致）。"""
    f = QFont()
    fams = [family] if family else ([FONT_DIGIT] if digit else list(FONT_FALLBACKS))
    if family is None and not digit:
        fams = list(FONT_FALLBACKS)
    f.setFamilies(fams)
    f.setPointSize(int(size))
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def ui_font() -> QFont:
    return font(FONT_MAIN_SIZE)


# ---------------- QSS ----------------
def qss() -> str:
    """读取 theme.qss 并替换 ${TOKEN} 占位，结果缓存。"""
    global _qss_cache
    if _qss_cache is not None:
        return _qss_cache
    try:
        with open(qss_path(), "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        raw = ""

    def _sub(m):
        key = m.group(1)
        return TOKENS.get(key, m.group(0))

    _qss_cache = _TOKEN_RE.sub(_sub, raw)
    return _qss_cache


def apply(app=None) -> None:
    """给 QApplication 应用全局 QSS 与默认字体（幂等）。"""
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication 尚未创建，无法应用主题")
    app.setFont(ui_font())
    app.setStyleSheet(qss())


def reload() -> str:
    """清空缓存并重新读取 QSS（开发期热更新用）。"""
    global _qss_cache
    _qss_cache = None
    return qss()


# ---------------- 控件内联样式 ----------------
def pill_style(bg: str, hover: str, fg: str = "#FFFFFF", radius=None,
               padding: str = "5px 12px", bold: bool = True) -> str:
    """实底胶囊/圆角按钮样式（替代 ui_theme.make_pill_text 的两态 PNG）。

    QSS 的 :hover 直接承接悬停态，不再需要预渲染悬停图。
    """
    r = R_BTN if radius is None else radius
    weight = "font-weight: 600;" if bold else ""
    return (
        "QPushButton {"
        f" background: {bg}; color: {fg}; border: none;"
        f" border-radius: {r}px; padding: {padding}; {weight}"
        " }"
        f"QPushButton:hover {{ background: {hover}; }}"
        f"QPushButton:pressed {{ background: {hover}; }}"
        f"QPushButton:disabled {{ background: {LINE}; color: {INK_3}; }}"
    )


def ghost_style(fg: str = INK_2, hover_bg: str = SURFACE_3, fg_hover: str = INK) -> str:
    """无边框幽灵按钮（次要操作）。"""
    return (
        "QPushButton {"
        f" background: transparent; color: {fg}; border: none;"
        f" border-radius: {R_BTN}px; padding: 4px 10px;"
        " }"
        f"QPushButton:hover {{ background: {hover_bg}; color: {fg_hover}; }}"
    )


def chip_style(fg: str = ACCENT, bg: str = ACCENT_SOFT) -> str:
    """小徽标/角标（胶囊）。"""
    return (
        "QLabel {"
        f" color: {fg}; background: {bg}; border-radius: {R_PILL}px;"
        " padding: 2px 8px; font-size: 8pt; font-weight: 600;"
        " }"
    )
