# -*- coding: utf-8 -*-
"""P3：Qt 版图像编辑器（对齐 tk 版 editor.py 的功能与交互）。

结构：
  * ToolbarWindow   独立悬浮工具条（32px 高，圆角灰白底）
  * EditorCanvas    图像画布（1:1 显示 + 标注/选区 overlay）
  * EditorWindow    编辑主窗（frameless 顶置，物理坐标=截图位置）

行为对齐 tk 版：
  * 两种模式：MODE_ADJUST（调整选区）/ MODE_DRAW（标注）
  * 工具 7 个：框选/圆选/划线/画笔/马赛克/文字/箭头，数字键 1-7 切换
  * 颜色 10 色 + 自定义，线宽 5 档
  * 撤销 10 步（Ctrl+Z）、完成（Enter）、取消（Esc）
  * 文字：单击输入、双击重编辑、可拖动
  * 调整模式：8 手柄缩放 + 内部拖动移动 + 尺寸徽标 + 提示条

与 tk 版差异（受阶段约束，已在 P3 文档说明）：
  * 录制按钮仅在提供 on_record 回调时显示（录屏为后续阶段）
  * 贴图（钉住）未接入时回落到 on_done（与 tk 版 on_pin 缺省行为一致）
"""
from __future__ import annotations

import io
import math
import os
import sys
import time
import weakref

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import (QEvent, QObject, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import geometry, theme
from .imageutil import pil_to_qimage, pil_to_qicon

# ---------------- 工具与样式常量（与 tk 版一致） ----------------
TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_LINE = "line"
TOOL_PEN = "pen"
TOOL_MOSAIC = "mosaic"
TOOL_TEXT = "text"
TOOL_ARROW = "arrow"

TOOL_NAMES = {
    TOOL_RECT: "框选",
    TOOL_ELLIPSE: "圆选",
    TOOL_LINE: "划线",
    TOOL_PEN: "画笔",
    TOOL_MOSAIC: "马赛克",
    TOOL_TEXT: "文字",
    TOOL_ARROW: "箭头",
}
TOOLS = [TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_PEN, TOOL_MOSAIC, TOOL_TEXT,
         TOOL_ARROW]

MODE_ADJUST = "adjust"
MODE_DRAW = "draw"

# 编辑器色板与线宽：直接复用 P1 主题 Token（与 tk 版 ui_theme 同源）
PALETTE = list(theme.PALETTE)
PALETTE_NAMES = list(theme.PALETTE_NAMES)
COLORS = {n: c for n, c in zip(PALETTE_NAMES, PALETTE)}

WIDTH_PRESETS = list(theme.WIDTH_PRESETS)
WIDTH_NAMES = dict(theme.WIDTH_NAMES)
WIDTHS = {WIDTH_NAMES[w]: w for w in WIDTH_PRESETS}

TOOLBAR_H = theme.TOOLBAR_H
TOOL_BTN = theme.TOOL_BTN
DONE_W = 76
HANDLE_HIT = 12
MIN_SEL = 8

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FONT_FALLBACK = r"C:\Windows\Fonts\msyhbd.ttc"

_ICON_CACHE: dict[str, QIcon] = {}


def _load_pil_font(size: int) -> ImageFont.FreeTypeFont:
    for fp in (FONT_PATH, FONT_FALLBACK):
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _rgb(rgb) -> QColor:
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def _hex(rgb) -> str:
    return f"#{int(rgb[0]):02x}{int(rgb[1]):02x}{int(rgb[2]):02x}"


def _ui_font(size: int, bold: bool = False) -> QFont:
    f = QFont(theme.FONT_FAMILY)
    f.setPixelSize(int(size))
    f.setBold(bool(bold))
    return f


def _icon_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", "icons")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "icons")


def _fallback_icons() -> dict[str, QIcon]:
    """PNG 缺失时的保底图标：PIL 绘制 40x40（2 倍图），与 tk 版形态一致。"""
    size = 40
    k = 2  # 坐标放大倍数
    ink = "#4a4a4a"
    out: dict[str, QIcon] = {}

    def _new():
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    def _s(*vals):
        return [v * k for v in vals]

    # 24 基准坐标（与 assets/icons/svg/*.svg 的 viewBox 一致，换算到 40px 画布）
    _k24 = size / 24.0

    def _v(*vals):
        return [v * _k24 for v in vals]

    img, d = _new()
    d.rounded_rectangle(_s(2.5, 2.5, 17.5, 17.5), radius=2.5 * k, outline=ink, width=2 * k)
    for cx, cy in ((4, 4), (16, 4), (4, 16), (16, 16)):
        d.rectangle(_s(cx - 1.2, cy - 1.2, cx + 1.2, cy + 1.2), fill=ink)
    out[TOOL_RECT] = pil_to_qicon(img)

    img, d = _new()
    d.ellipse(_s(2.5, 6, 17.5, 14), outline=ink, width=2 * k)
    out[TOOL_ELLIPSE] = pil_to_qicon(img)

    img, d = _new()
    d.line(_s(4, 17.5, 16, 4.5), fill=ink, width=2 * k)
    d.ellipse(_s(2.8, 16.3, 5.2, 18.7), fill=ink)
    d.ellipse(_s(14.8, 3.3, 17.2, 5.7), fill=ink)
    out[TOOL_LINE] = pil_to_qicon(img)

    img, d = _new()
    d.polygon(_s(2.75, 16.25, 6.25, 19.75, 14.25, 12.25, 10.75, 8.75), fill=ink)
    d.polygon(_s(17.5, 5.5, 13.75, 11.5, 11.25, 9.5), fill=ink)
    out[TOOL_PEN] = pil_to_qicon(img)

    img, d = _new()
    for y in (3, 8.7, 14.4):
        for x in (3, 8.7, 14.4):
            d.rounded_rectangle(_s(x, y, x + 4.6, y + 4.6), radius=0.8 * k, fill=ink)
    out[TOOL_MOSAIC] = pil_to_qicon(img)

    img, d = _new()
    d.line(_s(5, 4.5, 15, 4.5), fill=ink, width=2 * k)
    d.line(_s(10, 4.5, 10, 15.5), fill=ink, width=2 * k)
    d.line(_s(7, 17.5, 13, 17.5), fill=ink, width=1 * k)
    out[TOOL_TEXT] = pil_to_qicon(img)

    img, d = _new()
    d.line(_s(3.3, 16.3, 12.5, 7.1), fill=ink, width=2 * k)
    d.polygon(_s(16.25, 3.33, 13.75, 8.33, 11.25, 5.83), fill=ink)
    out[TOOL_ARROW] = pil_to_qicon(img)

    img, d = _new()
    d.arc(_s(4, 8, 16, 20), start=180, end=330, fill=ink, width=2 * k)
    d.line(_s(4, 14, 7, 10.5), fill=ink, width=2 * k)
    d.line(_s(4, 14, 7, 17.5), fill=ink, width=2 * k)
    out["undo"] = pil_to_qicon(img)

    img, d = _new()
    d.ellipse(_s(6, 2, 14, 10), outline=ink, width=2 * k)
    d.line(_s(10, 10, 10, 15), fill=ink, width=2 * k)
    d.line(_s(8, 15, 12, 15), fill=ink, width=2 * k)
    out["pin"] = pil_to_qicon(img)

    img, d = _new()
    d.rounded_rectangle(_s(2, 7, 18, 17), radius=3 * k, outline=ink, width=2 * k)
    d.ellipse(_s(7, 10, 13, 16), outline=ink, width=2 * k)
    d.polygon(_s(9, 4, 13, 4, 11, 8), fill=ink)
    out["record"] = pil_to_qicon(img)

    img, d = _new()
    d.line(_s(3, 10, 13, 10), fill=ink, width=2 * k)
    d.line(_s(8, 5, 8, 15), fill=ink, width=2 * k)
    out["width"] = pil_to_qicon(img)

    # 图片翻译：相框 + 两行文字 + 底部双向箭头（与 tool_pic_translate.png 同形）
    img, d = _new()
    _w = max(2, int(round(1.8 * _k24)))
    d.rounded_rectangle(_v(2, 3.5, 15.5, 14.5), radius=2.5 * _k24, outline=ink, width=_w)
    d.line(_v(5, 7, 12.5, 7), fill=ink, width=_w)
    d.line(_v(5, 10.2, 9.5, 10.2), fill=ink, width=_w)
    d.line(_v(6.5, 19, 18, 19), fill=ink, width=_w)
    d.line(_v(6.5, 19, 8.9, 16.6), fill=ink, width=_w)
    d.line(_v(6.5, 19, 8.9, 21.4), fill=ink, width=_w)
    d.line(_v(18, 19, 15.6, 16.6), fill=ink, width=_w)
    d.line(_v(18, 19, 15.6, 21.4), fill=ink, width=_w)
    out["pic_translate"] = pil_to_qicon(img)

    # 打开所在文件夹：文件夹线框（与 tool_folder.png 同形），供大图页复用
    img, d = _new()
    _w = max(2, int(round(1.9 * _k24)))
    d.rounded_rectangle(_v(2.6, 8.4, 21.4, 20.2), radius=2.4 * _k24, outline=ink, width=_w)
    d.line(_v(2.6, 8.4, 2.6, 5.6), fill=ink, width=_w)
    d.line(_v(2.6, 5.6, 9.2, 5.6), fill=ink, width=_w)
    d.line(_v(9.2, 5.6, 11.4, 8.4), fill=ink, width=_w)
    out["folder"] = pil_to_qicon(img)
    return out


def tool_icon(key: str) -> QIcon:
    """读取 assets/icons/tool_*.png，缺失时回退到 PIL 绘制图标。"""
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    name = f"tool_{key}.png"
    icon: QIcon | None = None
    path = os.path.join(_icon_dir(), name)
    try:
        if os.path.exists(path):
            pm = QPixmap(path)
            if not pm.isNull():
                if pm.width() >= 40:
                    # 2 倍图（40x40）：声明 DPR，逻辑尺寸 20x20，高分屏下不模糊
                    pm.setDevicePixelRatio(2.0)
                    icon = QIcon(pm)
                else:
                    icon = QIcon(pm.scaled(20, 20, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))
    except Exception:
        icon = None
    if icon is None:
        icon = _fallback_icons().get(key, QIcon())
    _ICON_CACHE[key] = icon
    return icon


def _pic_tr_pixmap(size: int) -> QPixmap:
    """在 size 像素画布上绘制「图片翻译」图案（按 20px 基准等比缩放）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    k = size / 20.0
    p.scale(k, k)
    pen = QPen(QColor(theme.INK_2))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(1.6, 2.6, 12.0, 10.4), 2.6, 2.6)   # 相框
    p.drawLine(QPointF(3.8, 5.9), QPointF(11.4, 5.9))           # 文字线 1
    p.drawLine(QPointF(3.8, 8.9), QPointF(8.6, 8.9))            # 文字线 2
    p.drawLine(QPointF(5.6, 16.2), QPointF(18.4, 16.2))         # 双向箭头主轴
    p.drawLine(QPointF(5.6, 16.2), QPointF(7.6, 14.2))          # 左箭头
    p.drawLine(QPointF(5.6, 16.2), QPointF(7.6, 18.2))
    p.drawLine(QPointF(18.4, 16.2), QPointF(16.4, 14.2))        # 右箭头
    p.drawLine(QPointF(18.4, 16.2), QPointF(16.4, 18.2))
    p.end()
    return pm


def pic_translate_icon(size: int = 20) -> QIcon:
    """（兼容保留）QPainter 绘制的「图片翻译」图标：圆角相框 + 两条文字线 + 双向箭头。

    工具条已改走 tool_icon("pic_translate")（读 tool_pic_translate.png，
    缺失时由 _fallback_icons 的同形 PIL 兜底），此函数仅留作备用绘图实现。
    默认按 20px 绘制——正好等于工具按钮 iconSize，1:1 贴图不发虚；另附 2x 位图供高分屏。
    """
    icon = QIcon()
    icon.addPixmap(_pic_tr_pixmap(size))
    icon.addPixmap(_pic_tr_pixmap(size * 2))
    return icon


def _color_dot_pixmap(rgb, diameter: int = 16) -> QPixmap:
    """颜色圆点：实心圆 + 1px 浅描边。"""
    pm = QPixmap(diameter + 2, diameter + 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor("#D9DDE3"), 1))
    p.setBrush(_rgb(rgb))
    p.drawEllipse(1, 1, diameter - 1, diameter - 1)
    p.end()
    return pm


class Tooltip(QObject):
    """悬浮提示（与 tk 版一致的纯文字小气泡）。"""

    def __init__(self, widget: QWidget, text: str):
        super().__init__(widget)
        self.widget = widget
        self.text = text
        self._tip: QWidget | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(420)
        self._timer.timeout.connect(self._show)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.Enter:
            self._timer.start()
        elif et in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
            self._timer.stop()
            self._hide()
        return False

    def _show(self):
        if self._tip is not None:
            return
        tip = QWidget(None, Qt.ToolTip | Qt.FramelessWindowHint)
        tip.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QVBoxLayout(tip)
        lay.setContentsMargins(0, 0, 0, 0)
        card = QLabel(self.text, tip)
        card.setStyleSheet(
            "background:#FFFFFF;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:3px 6px;font-size:12px;"
            % (theme.INK_2, theme.LINE))
        card.setFont(_ui_font(12))
        lay.addWidget(card)
        tip.adjustSize()
        gp = self.widget.mapToGlobal(QPoint(0, self.widget.height() + 4))
        tip.move(gp)
        tip.show()
        self._tip = tip

    def _hide(self):
        if self._tip is not None:
            self._tip.close()
            self._tip.deleteLater()
            self._tip = None


class _TextEntry(QLineEdit):
    """画布内文字输入框：Return 提交 / Esc 取消 / 失焦提交。"""

    def __init__(self, parent, on_commit, on_cancel):
        super().__init__(parent)
        self._on_commit = on_commit
        self._on_cancel = on_cancel

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_commit()
            return
        if event.key() == Qt.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._on_commit()


class WidthPopup(QWidget):
    """线宽选择弹层：每行「线样 + 名称（Npx）」。"""

    def __init__(self, editor: "EditorWindow"):
        super().__init__(editor, Qt.Popup | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint)
        self.editor = editor
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(
            "#card{background:#FFFFFF;border:1px solid %s;border-radius:8px;}"
            % theme.LINE)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)
        self._rows: list[QWidget] = []
        for w in WIDTH_PRESETS:
            row = _WidthRow(editor, w, WIDTH_NAMES[w], self._pick)
            lay.addWidget(row)
            self._rows.append(row)
        self.adjustSize()

    def _pick(self, w: int, name: str):
        self.editor.set_width(w, name)
        self.close()


class _WidthRow(QWidget):
    def __init__(self, editor: "EditorWindow", w: int, name: str, on_pick):
        super().__init__()
        self._w = w
        self._name = name
        self._on_pick = on_pick
        self._active = editor.line_width == w
        self.setFixedHeight(22)
        self.setCursor(Qt.PointingHandCursor)
        self._font = _ui_font(11)
        self.setMouseTracking(True)

    def _text(self) -> str:
        mark = "● " if self._active else "   "
        return f"{mark}{self._name}（{self._w}px）"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.ACCENT_SOFT if self._active else "#FFFFFF"))
        p.setPen(QPen(QColor(theme.INK_2), self._w))
        y = self.height() // 2
        p.drawLine(6, y, 62, y)
        p.setPen(QColor(theme.INK_2))
        p.setFont(self._font)
        p.drawText(QRect(68, 0, self.width() - 68, self.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, self._text())
        p.end()

    def sizeHint(self) -> QSize:
        fm = QFontMetricsF(self._font)
        return QSize(int(78 + fm.horizontalAdvance(self._text()) + 10), 22)

    def mousePressEvent(self, event):
        self._on_pick(self._w, self._name)


class ColorPopup(QWidget):
    """色板弹层：5 列圆点 + 自定义颜色。"""

    def __init__(self, editor: "EditorWindow"):
        super().__init__(editor, Qt.Popup | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint)
        self.editor = editor
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(
            "#card{background:#FFFFFF;border:1px solid %s;border-radius:8px;}"
            % theme.LINE)
        outer.addWidget(card)
        outer_lay = QVBoxLayout(card)
        outer_lay.setContentsMargins(3, 3, 3, 3)
        outer_lay.setSpacing(2)
        for row in range(2):
            line = QHBoxLayout()
            line.setSpacing(2)
            for col in range(5):
                name = PALETTE_NAMES[row * 5 + col]
                rgb = COLORS[name]
                btn = QToolButton()
                btn.setFixedSize(26, 26)
                btn.setIcon(QIcon(_color_dot_pixmap(rgb, 12)))
                btn.setIconSize(QSize(14, 14))
                btn.setCursor(Qt.PointingHandCursor)
                btn.setStyleSheet(
                    "QToolButton{border:none;border-radius:5px;background:#F7F8FA;}"
                    "QToolButton:hover{background:%s;}" % theme.ACCENT_SOFT)
                btn.clicked.connect(lambda _=False, c=rgb: self._pick(c))
                Tooltip(btn, name)
                line.addWidget(btn)
            outer_lay.addLayout(line)
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        custom = QPushButton("…")
        custom.setFixedSize(26, 22)
        custom.setCursor(Qt.PointingHandCursor)
        custom.setFont(_ui_font(14, True))
        custom.setStyleSheet(
            "QPushButton{border:none;border-radius:5px;background:#FFFFFF;color:%s;}"
            "QPushButton:hover{background:%s;}" % (theme.INK_2, theme.SURFACE_3))
        custom.clicked.connect(lambda _=False: self._pick_custom())
        bottom.addWidget(custom)
        bottom.addStretch(1)
        outer_lay.addLayout(bottom)
        self.adjustSize()

    def _pick(self, rgb):
        self.editor.set_color(rgb)
        self.close()

    def _pick_custom(self):
        self.close()
        cur = _rgb(self.editor.color)
        col = QColorDialog.getColor(cur, self.editor, "自定义颜色")
        if col.isValid():
            self.editor.set_color((col.red(), col.green(), col.blue()))


class TipBubble(QWidget):
    """轻量单行气泡：贴在工具条上方显示进度/结果，不抢焦点、不阻塞。

    图片翻译要等接口返回，工具条按钮上放不下中文长提示，用独立小窗既能
    看清又不改动截图区域（覆盖层、选区框、工具条全部保持原样）。
    """

    def __init__(self, editor: "EditorWindow", text: str, msec: int = 0):
        super().__init__(editor, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.editor = editor
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet(
            "#card{background:#FFFFFF;border:1px solid %s;border-radius:8px;}" % theme.LINE)
        outer.addWidget(card)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(12, 6, 12, 6)
        self.lbl = QLabel(text, card)
        self.lbl.setFont(_ui_font(12, False))
        self.lbl.setStyleSheet("color:%s;" % theme.INK)
        cl.addWidget(self.lbl)
        self.adjustSize()
        if msec > 0:
            QTimer.singleShot(msec, self.close)

    def set_text(self, text: str) -> None:
        self.lbl.setText(text)
        self.adjustSize()

    def show_near_toolbar(self) -> None:
        """浮在工具条正上方；空间不够就压到工具条下沿外侧。"""
        tb = getattr(self.editor, "toolbar", None)
        if tb is None:
            self.show()
            return
        x = tb.x() + (tb.width() - self.width()) // 2
        y = tb.y() - self.height() - 6
        if y < 0:
            y = tb.y() + tb.height() + 6
        self.move(max(0, x), y)
        self.show()
        self.raise_()


class ToolbarWindow(QWidget):
    """独立悬浮工具条：32px 高，圆角 10，灰白实心底 #DEDFE1。"""

    def __init__(self, editor: "EditorWindow", has_record: bool = True):
        # 父窗口必须挂到编辑器：Windows 下 owned window 恒在 owner 之上。
        # 若 parent=None，两个同级 Qt.Tool 会抢 z-order，编辑窗 activateWindow() 后
        # 会把工具条压到下面，表现为「刚选完区看不到按钮」。
        super().__init__(editor, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.editor = editor
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedHeight(TOOLBAR_H)
        self.setFocusPolicy(Qt.NoFocus)
        self._tool_btns: dict[str, QToolButton] = {}
        self._color_btn: QToolButton | None = None
        self._width_btn: QToolButton | None = None
        self._record_btn: QToolButton | None = None   # P6：录制入口（无 on_record 时为 None）

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(0)

        for i, tool in enumerate(TOOLS, 1):
            b = self._tbtn(tool_icon(tool), f"{TOOL_NAMES[tool]}（快捷键 {i}）")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, t=tool: self.editor.set_tool(t))
            self._tool_btns[tool] = b
            lay.addWidget(b)

        lay.addSpacing(8)
        lay.addWidget(self._sep())
        lay.addSpacing(8)

        self._color_btn = self._tbtn(QIcon(_color_dot_pixmap(editor.color)),
                                     "颜色（点击展开选择）")
        self._color_btn.clicked.connect(self._open_color)
        lay.addWidget(self._color_btn)

        self._width_btn = self._tbtn(tool_icon("width"), "线条粗细")
        self._width_btn.clicked.connect(self._open_width)
        lay.addWidget(self._width_btn)

        lay.addSpacing(8)
        lay.addWidget(self._sep())
        lay.addSpacing(8)

        undo = self._tbtn(tool_icon("undo"), "撤销上一步（Ctrl+Z）")
        undo.clicked.connect(lambda _=False: self.editor.undo())
        lay.addWidget(undo)

        if has_record:
            rec = self._tbtn(tool_icon("record"), "屏幕录制（开始录屏）")
            rec.clicked.connect(lambda _=False: self.editor.request_record())
            self._record_btn = rec
            lay.addWidget(rec)

        pin = self._tbtn(tool_icon("pin"), "贴图：钉在屏幕（不进历史库）")
        pin.clicked.connect(lambda _=False: self.editor.pin())
        lay.addWidget(pin)

        # P9：识别文字（选区/整图 → 独立结果窗，不进历史库）
        ocr = self._tbtn(tool_icon("ocr"), "识别文字：提取选区/整图中的文字（Ctrl+T）")
        ocr.clicked.connect(lambda _=False: self.editor.request_ocr())
        self._ocr_btn = ocr
        lay.addWidget(ocr)

        # 百度图片翻译：选区原图整块交给接口，译文贴回原位并钉图
        # 图标走 assets/icons/tool_pic_translate.png（缺失时 _fallback_icons 手绘兜底）
        pic_tr = self._tbtn(tool_icon("pic_translate"), "图片翻译（百度）")
        pic_tr.clicked.connect(lambda _=False: self.editor.request_picture_translate())
        self._pic_tr_btn = pic_tr
        lay.addWidget(pic_tr)

        lay.addSpacing(2)

        self._done = QPushButton("✓ 完成")
        self._done.setFixedSize(DONE_W, 28)
        self._done.setCursor(Qt.PointingHandCursor)
        self._done.setFont(_ui_font(12, True))
        self._done.setFocusPolicy(Qt.NoFocus)
        self._done.setStyleSheet(
            "QPushButton{background:%s;color:#FFFFFF;border:none;border-radius:12px;}"
            "QPushButton:hover{background:%s;}" % (theme.SUCCESS, theme.SUCCESS_HOVER))
        self._done.clicked.connect(lambda _=False: self.editor.done())
        lay.addWidget(self._done)

        self.adjustSize()
        self._highlight_tool()

    def _tbtn(self, icon: QIcon, tip: str) -> QToolButton:
        b = QToolButton(self)
        b.setFixedSize(TOOL_BTN, TOOL_BTN)
        b.setIcon(icon)
        b.setIconSize(QSize(20, 20))
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.setStyleSheet(
            "QToolButton{border:none;background:transparent;border-radius:12px;}"
            "QToolButton:hover{background:%s;}"
            "QToolButton:checked{background:%s;}" % (theme.SURFACE_3, theme.ACCENT_SOFT))
        Tooltip(b, tip)
        return b

    def _sep(self) -> QFrame:
        line = QFrame(self)
        line.setFixedSize(1, 24)
        line.setStyleSheet("background:%s;border:none;" % theme.LINE)
        return line

    # ---------- 弹层 ----------
    def _open_color(self):
        pop = ColorPopup(self.editor)
        pop.move(self._popup_pos(self._color_btn, pop))
        pop.show()

    def _open_width(self):
        pop = WidthPopup(self.editor)
        pop.move(self._popup_pos(self._width_btn, pop))
        pop.show()

    def _popup_pos(self, btn: QWidget | None, pop: QWidget) -> QPoint:
        if btn is None:
            return self.mapToGlobal(QPoint(0, self.height() + 6))
        gp = btn.mapToGlobal(QPoint(btn.width() // 2 - pop.width() // 2,
                                    btn.height() + 6))
        area = geometry.virtual_rect_logical()
        if gp.y() + pop.height() > area.bottom():
            gp.setY(btn.mapToGlobal(QPoint(0, 0)).y() - pop.height())
        gp.setX(max(area.left() + 4, gp.x()))
        return gp

    # ---------- 状态同步 ----------
    def _highlight_tool(self):
        for t, b in self._tool_btns.items():
            b.setChecked(self.editor.mode == MODE_DRAW and t == self.editor.tool)

    def set_color(self, rgb):
        if self._color_btn is not None:
            self._color_btn.setIcon(QIcon(_color_dot_pixmap(rgb)))

    def set_width(self, w: int, name: str | None = None):
        pass  # 线宽无持续显示，弹层内高亮即可

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 10, 10)
        p.fillPath(path, QColor(theme.TOOLBAR))
        p.end()


class EditorCanvas(QWidget):
    """图像画布：按 1:1 逻辑像素绘制工作图 + 文字层 + 预览 + 选区 overlay。"""

    def __init__(self, editor: "EditorWindow"):
        super().__init__(editor)
        self.editor = editor
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.CrossCursor)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # 事件转发：画布铺满窗口，所有鼠标事件交由 EditorWindow 统一处理
    def mousePressEvent(self, event):
        self.editor.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.editor.mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.editor.mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.editor.mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        ed = self.editor
        if ed.mode == MODE_DRAW and ed._sb.isVisible():
            delta = event.angleDelta().y()
            ed._sb.setValue(ed._sb.value() - int(delta / 120) * 40)
            event.accept()
            return
        super().wheelEvent(event)

    # 坐标换算：逻辑视口 <-> 图片物理像素
    def img_pos(self, pos: QPointF) -> tuple[int, int]:
        ed = self.editor
        y = pos.y() + (ed.scroll if ed.mode == MODE_DRAW else 0)
        return int(round(pos.x() * ed.scale)), int(round(y * ed.scale))

    def paintEvent(self, event):
        ed = self.editor
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        if ed.qimg is None:
            p.end()
            return
        p.save()
        p.translate(0, -(ed.scroll if ed.mode == MODE_DRAW else 0))
        p.drawImage(QRectF(0, 0, ed.view_w, ed.view_h_img), ed.qimg)

        # 文字层（未合成，可拖动/重编辑）
        for obj in ed.text_objs:
            _paint_text_obj(p, obj, ed.scale)

        ed.paint_preview(p)

        if ed.mode == MODE_ADJUST:
            ed.paint_adjust_overlay(p)
        else:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(1, 1, ed.view_w - 2, ed.view_h_img - 2))
        p.restore()
        p.end()


def _text_bbox(obj: dict, scale: float) -> QRectF:
    """文字对象在逻辑视口中的包围盒（与 _paint_text_obj 的基线定位一致）。

    注意：不用 QFontMetricsF.boundingRect(str)，部分环境下其返回的 x 偏移异常
    （实测会出现 100000 级偏移），改用 advance + ascent/descent 自行拼装。
    """
    size = max(1.0, obj["font_size"] / scale)
    f = _ui_font(round(size))
    fm = QFontMetricsF(f)
    txt = obj["txt"] or " "
    w = max(2.0, fm.horizontalAdvance(txt))
    h = max(2.0, fm.ascent() + fm.descent())
    return QRectF(obj["x"] / scale, obj["y"] / scale, w, h)


def _paint_text_obj(p: QPainter, obj: dict, scale: float):
    size = max(1.0, obj["font_size"] / scale)
    f = _ui_font(round(size))
    fm = QFontMetricsF(f)
    x, y = obj["x"] / scale, obj["y"] / scale
    path = QPainterPath()
    path.addText(QPointF(x, y + fm.ascent()), f, obj["txt"])
    p.setPen(QPen(QColor(255, 255, 255), max(1.0, 2.0), Qt.SolidLine,
                  Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(_rgb(obj["color"]))
    p.drawPath(path)


def open_editor_count() -> int:
    """当前仍处于显示状态的编辑窗数量（供主窗做并发上限判断）。

    注意：_live 是 WeakSet，编辑窗关闭后其 Python 包装对象可能仍短暂存活，
    而底层 C++ 对象已被 shiboken 释放，此时 isVisible() 会抛 RuntimeError，
    必须逐个兜底，否则会让整条 start_capture 链路中断（表现为按热键无反应）。
    """
    count = 0
    for w in list(EditorWindow._live):
        try:
            if w.isVisible():
                count += 1
        except RuntimeError:  # 底层 C++ 对象已销毁，视为已关闭
            continue
    return count


class EditorWindow(QWidget):
    """截图编辑主窗。

    参数与 tk 版 EditorWindow 对齐：
        parent, image, on_done(img, x, y), on_cancel(),
        offset_x, offset_y, on_pin, full_image, sel_box, on_record
    """

    _live: "weakref.WeakSet[EditorWindow]" = weakref.WeakSet()

    def __init__(self, parent, image: Image.Image, on_done, on_cancel,
                 offset_x: int = 0, offset_y: int = 0, on_pin=None,
                 full_image: Image.Image | None = None, sel_box=None,
                 on_record=None, on_picture_pin=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool |
                         Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        EditorWindow._live.add(self)
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_pin = on_pin
        self.on_record = on_record
        # 图片翻译成功后的收尾回调：on_picture_pin(译文图, x, y, 原截图)
        # 由 main_qt 负责钉图 + 恢复主窗；为 None 时编辑器自行钉图。
        self.on_picture_pin = on_picture_pin
        self.offset_x = int(offset_x)
        self.offset_y = int(offset_y)
        self.undo_stack: list[tuple[Image.Image, list[dict]]] = []

        self.tool = TOOL_RECT
        self.color = COLORS["红"]
        self.line_width = WIDTHS["常规"]
        self.text_objs: list[dict] = []
        self._start: tuple[int, int] | None = None
        self._pen_points: list[tuple[int, int]] = []
        self._drag_text: dict | None = None
        self._drag_off = (0, 0)
        self._entry: _TextEntry | None = None
        self._committing_entry = False
        self._editing_obj: dict | None = None
        self._text_pos = (0, 0)
        self._adjust_handle: str | None = None
        self._adjust_move = False
        self._adjust_off = (0, 0)
        self._cur: tuple[int, int] | None = None
        self._closing = False
        self.scroll = 0

        # ---------- 模式与工作图（与 tk 一致） ----------
        if full_image is not None:
            self.full_img = full_image.convert("RGB")
            self.mode = MODE_ADJUST
            if sel_box:
                x1, y1, x2, y2 = sel_box
                self.sel = [max(0, int(x1)), max(0, int(y1)),
                            min(int(x2), self.full_img.width),
                            min(int(y2), self.full_img.height)]
            else:
                self.sel = [0, 0, self.full_img.width, self.full_img.height]
            self.img = self.full_img.copy()
        else:
            self.full_img = image.convert("RGB")
            self.mode = MODE_DRAW
            self.sel = [0, 0, self.full_img.width, self.full_img.height]
            self.img = self.full_img.copy()

        self.vx, self.vy, self.vw, self.vh = geometry.virtual_screen()
        self.qimg = pil_to_qimage(self.img)

        # ---------- 几何：视口逻辑尺寸 ----------
        self._layout_geometry()

        self.canvas = EditorCanvas(self)
        self.canvas.move(0, 0)

        self._sb = QScrollBar(Qt.Vertical, self)
        self._sb.setFocusPolicy(Qt.NoFocus)
        self._sb.setStyleSheet(
            "QScrollBar:vertical{background:%s;width:12px;margin:0;border:none;}"
            "QScrollBar::handle:vertical{background:#C9CED6;border-radius:6px;"
            "min-height:24px;margin:2px;}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}"
            "QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}"
            % theme.SURFACE_2)
        self._sb.valueChanged.connect(self._on_scroll)
        self._sync_scrollbar()

        # ---------- 工具栏 ----------
        self.toolbar = ToolbarWindow(self, has_record=on_record is not None)
        self.position_toolbar()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.ArrowCursor if self.mode == MODE_ADJUST else Qt.CrossCursor)
        self._refresh()

    # ---------- 几何 ----------
    def _layout_geometry(self):
        """计算视口逻辑尺寸与缩放比，并按模式设置窗口几何。"""
        area = geometry.virtual_rect_logical()
        if self.mode == MODE_ADJUST:
            rect = area
            self.view_w = max(1, rect.width())
            self.view_h_img = max(1, rect.height())
            self.view_h = self.view_h_img
            self._win_rect = rect
            self.scale = self.full_img.width / float(self.view_w)
        else:
            w_phys = self.img.width
            h_phys = self.img.height
            src = QRect(self.offset_x, self.offset_y, w_phys, h_phys)
            rect = geometry.logical_geometry_for_physical_rect(src)
            self.view_w = max(1, rect.width())
            self.view_h_img = max(1, rect.height())
            avail_h = max(120, area.height())
            self.view_h = min(self.view_h_img, avail_h)
            # 窗口宽度在需要滚动条时补 12px
            self._win_rect = QRect(rect.left(), rect.top(),
                                   self.view_w, min(self.view_h_img, avail_h))
            self.scale = self.img.width / float(self.view_w)

    def _need_scroll(self) -> bool:
        return self.mode == MODE_DRAW and self.view_h_img > self.view_h + 1

    def _sync_scrollbar(self):
        need = self._need_scroll()
        self._sb.setVisible(need)
        if need:
            self._sb.setGeometry(self.view_w, 0, 12, self.view_h)
            self._sb.setRange(0, max(0, self.view_h_img - self.view_h))
            self._sb.setPageStep(max(1, self.view_h))
            self.setFixedSize(self.view_w + 12, self.view_h)
        else:
            self.scroll = 0
            self.setFixedSize(self.view_w, self.view_h)
        self.canvas.setFixedSize(self.view_w, self.view_h)
        self.setGeometry(self._win_rect.left(), self._win_rect.top(),
                         self.width(), self.height())

    def _on_scroll(self, value: int):
        self.scroll = int(value)
        self.canvas.update()

    # ---------- 工具栏定位 ----------
    def position_toolbar(self):
        """优先选区/窗口下方外侧，其次上方外侧，最后取遮挡最小的一侧。"""
        if self.mode == MODE_ADJUST:
            x1, y1, x2, y2 = self.sel
            phys = QRect(self.vx + x1, self.vy + y1, x2 - x1, y2 - y1)
            reg = geometry.physical_rect_to_logical(phys)
        else:
            reg = QRect(self.x(), self.y(), self.width(), self.height())
        area = geometry.virtual_rect_logical()
        scr = QGuiApplication.screenAt(reg.center()) or QGuiApplication.primaryScreen()
        max_bottom = area.bottom()
        if scr is not None:
            # 避让任务栏：clamp 下界取该屏 availableGeometry，别让工具条落进任务栏
            max_bottom = min(max_bottom, scr.availableGeometry().bottom())
        bar_w = self.toolbar.width()
        bar_h = self.toolbar.height()
        tx = reg.left()
        if tx + bar_w > area.right():
            tx = max(area.left(), area.right() - bar_w)
        gap = 5
        ty = reg.bottom() + gap
        if ty + bar_h > max_bottom:
            up = reg.top() - bar_h - gap
            if up >= area.top():
                ty = up
            else:
                over_down = ty + bar_h - max_bottom
                over_up = gap + bar_h - (reg.top() - area.top())
                ty = up if over_up <= over_down else (max_bottom - bar_h)
        ty = max(area.top(), min(ty, max_bottom - bar_h))
        self.toolbar.move(tx, ty)
        self.toolbar.raise_()

    # ---------- 显示 ----------
    def showEvent(self, event):
        super().showEvent(event)
        # 工具栏是独立顶层窗口（Qt.Tool），主窗显示后必须显式 show
        self.position_toolbar()
        self.toolbar.show()
        self.toolbar.raise_()
        # 调用方 show() 之后还会 activateWindow()/setFocus() 改写 z-order，
        # 下一轮事件循环再提一次，避免首帧工具条被编辑窗压住
        QTimer.singleShot(0, self._raise_toolbar)

    def _raise_toolbar(self) -> None:
        if self.isVisible():
            self.position_toolbar()
            self.toolbar.show()
            self.toolbar.raise_()

    def hideEvent(self, event):
        self.toolbar.hide()
        super().hideEvent(event)

    def _refresh(self):
        self.qimg = pil_to_qimage(self.img)
        self.canvas.update()
        self.toolbar._highlight_tool()

    def set_tool(self, tool: str):
        if self.mode == MODE_ADJUST:
            if not self._commit_selection():
                return
        self.tool = tool
        self.toolbar._highlight_tool()
        self.canvas.update()

    def set_color(self, rgb):
        self.color = tuple(rgb)
        self.toolbar.set_color(self.color)

    def set_width(self, w: int, name: str | None = None):
        self.line_width = int(w)

    # ---------- 撤销 ----------
    def _push_undo(self):
        self.undo_stack.append((self.img.copy(), [dict(o) for o in self.text_objs]))
        if len(self.undo_stack) > 10:
            self.undo_stack.pop(0)

    def undo(self):
        if self.mode == MODE_ADJUST:
            return
        if self.undo_stack:
            self.img, self.text_objs = self.undo_stack.pop()
            self._start = None
            self._cur = None
            self._pen_points = []
            self._refresh()

    # ---------- 完成 / 贴图 / 录制 / 取消 ----------
    def done(self):
        if self.mode == MODE_ADJUST:
            self._finish_adjust()
            return
        self._finish_and_close(self.on_done)

    def pin(self):
        if self.mode == MODE_ADJUST:
            self._finish_adjust(self.on_pin or self.on_done)
            return
        self._finish_and_close(self.on_pin or self.on_done)

    def request_ocr(self, prefer: str | None = None):
        """P9：识别当前选区（框选阶段）或整图（标注阶段）中的文字。

        结果在独立的识别结果窗中呈现，不写入历史库、不改变当前编辑内容。
        """
        from . import ocr_result_qt
        # 点击瞬间先做引擎守门：缺引擎立刻弹安装/引导提示并中止，
        # 不再等识别线程返回、也不拖到钉图之后才把提示甩给用户。
        ok, prefer = ocr_result_qt.gate_entry(prefer, owner=self)
        if not ok:
            return
        if ocr_result_qt.recently_closed():
            # 识别窗刚被关掉：此刻的点击多半是关闭动作的余波（release / 双击第二次按下）
            # 落到工具条「识别」按钮上，直接忽略，避免弹窗刚关又弹回来。
            return
        if self.mode == MODE_ADJUST:
            x1, y1, x2, y2 = self.sel
            x1, y1 = max(0, x1), max(0, y1)
            x2 = min(self.full_img.width, x2)
            y2 = min(self.full_img.height, y2)
            if x2 - x1 < 4 or y2 - y1 < 4:
                return
            img = self.full_img.crop((x1, y1, x2, y2))
            # 不隐藏截图区域：覆盖层、选区框、工具条全部保持原样；
            # 识别文本以可自由拖动的置顶浮窗呈现在选区旁，便于边看截图边核对。
            anchor = QRect(self.vx + x1, self.vy + y1, x2 - x1, y2 - y1)
            ok, prefer = self._ensure_ocr_engine(prefer)
            if not ok:
                return
            ocr_result_qt.open_ocr(img, prefer=prefer, anchor=anchor, owner=self)
            return
        ok, prefer = self._ensure_ocr_engine(prefer)
        if not ok:
            return
        self._commit_text()
        self._flatten_texts()
        ocr_result_qt.open_ocr(self.img.copy(), prefer=prefer, owner=self)

    def _ensure_ocr_engine(self, prefer: str | None) -> tuple[bool, str | None]:
        """识别引擎守门，缺包时就地弹引导。

        注意：全程不动截图覆盖层——选区框、工具条、图层都保持原样，用户随时知道自己
        截的哪块。引导框由 ocr_result_qt 负责用 SetWindowPos 顶到 topmost 组最上层，
        所以不会再被全屏覆盖层压住。
        """
        from . import ocr_result_qt
        if ocr_result_qt.engine_ready(prefer):
            return True, prefer
        return ocr_result_qt.ensure_ocr_ready(prefer)

    def yield_for_dialog(self):
        """缺引擎弹提示期间暂时退出置顶，提示关掉后按需恢复。

        截图覆盖层是「全屏 + WindowStaysOnTopHint」，缺包提示框若与它挤在同一层，
        用户看到的就是“点了没反应”，直到钉图/退出覆盖层才突然冒出提示。
        这里在弹框前把覆盖层降顶，返回一个恢复回调交给 gate_entry 在提示关闭后调用。
        """
        from . import ocr_result_qt
        was_top = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
        if was_top:
            try:
                self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
                self.show()
            except Exception:
                was_top = False

        def _restore() -> None:
            if not was_top:
                return
            try:
                # 引擎仍不可用（用户去了系统语言设置）：保持让位，别挡住系统窗口
                if not ocr_result_qt.engine_ready(None):
                    return
                self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
                self.show()
                ocr_result_qt._force_topmost(self)
            except Exception:
                pass

        return _restore

    # ---------- 图片翻译（百度） ----------
    def _pic_btn(self) -> QToolButton | None:
        return getattr(getattr(self, "toolbar", None), "_pic_tr_btn", None)

    def _pic_busy(self, busy: bool) -> None:
        btn = self._pic_btn()
        if btn is not None:
            btn.setEnabled(not busy)

    def _pic_close_bubble(self) -> None:
        tip = getattr(self, "_pic_bubble", None)
        self._pic_bubble = None
        if tip is not None:
            try:
                tip.close()
            except Exception:
                pass

    def _pic_notice(self, text: str, msec: int = 0) -> None:
        """工具条正上方的单行提示；msec>0 时自动消失。"""
        self._pic_close_bubble()
        tip = TipBubble(self, text, msec=msec)
        self._pic_bubble = tip
        tip.show_near_toolbar()

    def request_picture_translate(self) -> None:
        """把当前选区的原始截图交给百度图片翻译。

        只从 full_img（未叠加任何标注的原图）按 sel 裁剪，用户画的框线/箭头/
        马赛克都不会进接口；翻译期间不关闭截图窗——覆盖层、选区框、工具条
        全部保持原样。翻译成功后把译文按 rect 贴回原位，再以钉图方式钉在选区
        处（1:1 原尺寸，图下带「原文 / 译文」切换按钮），同时关闭截图覆盖层。
        """
        from . import translate_qt          # 局部导入：ocr_result_qt 反向依赖本模块

        task = getattr(self, "_pic_task", None)
        if task is not None and task.isRunning():
            return

        x1, y1, x2, y2 = self.sel          # 调整/标注阶段 sel 均为 full_img 坐标
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2 = min(self.full_img.width, int(x2))
        y2 = min(self.full_img.height, int(y2))
        if x2 - x1 < 4 or y2 - y1 < 4:
            self._pic_notice("选区太小，先框出一块要翻译的区域", 3200)
            return
        img = self.full_img.crop((x1, y1, x2, y2))

        try:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            raw = buf.getvalue()
        except Exception as e:
            self._pic_notice("截图转换失败：%s" % e, 5200)
            return

        try:
            import config as app_cfg
            cfg = dict(app_cfg.load_config() or {})
        except Exception:
            cfg = {}
        to_lang = str(cfg.get("translate_target") or "auto").strip()
        if not to_lang or to_lang.lower() == "auto":
            to_lang = "zh"          # 百度图片翻译的 to 不支持 auto，回落中文

        self._pic_image = img
        self._pic_started = time.monotonic()
        self._pic_busy(True)
        self._pic_notice("图片翻译中…（识别文字并贴回原位）")

        task = translate_qt.PictureTranslateTask(
            raw, cfg, from_lang="auto", to_lang=to_lang, paste=2, view_type=1)
        self._pic_task = task
        ref = weakref.ref(self)

        def _done(result, ref=ref):
            w = ref()
            if w is not None:
                w._on_pic_done(result)

        def _failed(msg, ref=ref):
            w = ref()
            if w is not None:
                w._on_pic_failed(msg)

        task.done.connect(_done)
        task.failed.connect(_failed)
        task.start()

    def _on_pic_done(self, result: dict) -> None:
        """图片翻译成功：把译文按 rect 贴回原截图，再以钉图方式钉在选区原位。

        与旧版「弹预览窗」的区别：
            * 不再打开 ``PictureTransWindow``，直接出译文图；
            * 钉图尺寸与原截图 **1:1**（sticker 收到 alt_image 时不做 900×600 缩略）；
            * 钉图下方带「原文 / 译文」切换按钮；
            * 钉图前关闭截图覆盖层，且**不复制剪贴板、不写历史库**。
        """
        from . import ocr_result_qt, sticker_qt

        self._pic_busy(False)
        items = (result or {}).get("contents") or []
        if not items:
            self._pic_notice("没有从这块截图里找到可翻译的文字", 4200)
            return
        src = getattr(self, "_pic_image", None)        # 原截图（full_img 按 sel 裁剪）
        if src is None:
            self._pic_notice("原截图已丢失，无法贴回译文", 4200)
            return
        try:
            trans = ocr_result_qt.render_picture_paste(src, result)
        except Exception as e:
            self._pic_notice("译文贴回失败：%s" % e, 5200)
            return

        # 钉图落点 = 原选区在屏幕上的左上角（物理像素）
        if self.mode == MODE_DRAW:
            out_x, out_y = int(self.offset_x), int(self.offset_y)
        else:
            x1, y1, _x2, _y2 = self.sel
            out_x = int(self.vx + max(0, int(x1)))
            out_y = int(self.vy + max(0, int(y1)))

        self._pic_close_bubble()
        cb = getattr(self, "on_picture_pin", None)
        self._teardown()                               # 关闭覆盖层 + 工具条，不触发存盘/剪贴板
        if cb is not None:
            try:
                cb(trans, out_x, out_y, src)
                return
            except Exception:
                pass
        try:                                           # 无回调（独立使用编辑器）时自行钉图
            sticker_qt.open_sticker(trans, out_x, out_y, title="图片翻译",
                                    alt_image=src, alt_title="原图")
        except Exception:
            pass

    def _on_pic_failed(self, msg: str) -> None:
        self._pic_busy(False)
        text = str(msg or "未知错误")
        need_setup = any(k in text for k in
                         ("API Key", "APPID", "未配置", "尚未配置", "密钥", "Key"))
        if not need_setup:
            self._pic_notice("图片翻译失败：%s" % text, 6000)
            return
        self._pic_close_bubble()
        box = QMessageBox(self)
        box.setWindowTitle("图片翻译未配置")
        box.setIcon(QMessageBox.Warning)
        box.setText(text)
        box.setInformativeText("请在「设置 → 翻译 → 百度」里填好 APPID 与 API Key，保存后即可直接翻译。")
        go = box.addButton("去设置", QMessageBox.AcceptRole)
        box.addButton("稍后", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self._pic_open_settings()

    def _pic_open_settings(self) -> None:
        """从截图窗直接进设置页补 Key，保存后立刻可用。"""
        from . import settings_qt, translate_qt

        new_cfg = settings_qt.open_settings(self)
        if new_cfg is None:
            return
        fields = (translate_qt.credentials("baidu_llm", new_cfg) or {}).get("fields") or {}
        if str(fields.get("appid") or "").strip() and str(fields.get("apikey") or "").strip():
            self._pic_notice("图片翻译已配置好，再点一次按钮即可", 4000)
        else:
            self._pic_notice("配置仍不完整：图片翻译需要百度 APPID 与 API Key", 4800)

    def request_record(self):
        if self.on_record is None:
            return
        if self.mode == MODE_ADJUST:
            x1, y1, x2, y2 = self.sel
            rect = (self.vx + x1, self.vy + y1, x2 - x1, y2 - y1)
        else:
            rect = (self.offset_x, self.offset_y, self.img.width, self.img.height)
        cb = self.on_record
        self._teardown()
        cb(rect)

    def _finish_and_close(self, callback):
        self._commit_text()
        self._flatten_texts()
        img = self.img
        x, y = self._win_rect.left(), self._win_rect.top()
        if self.mode == MODE_DRAW:
            x, y = self.offset_x, self.offset_y
        self._teardown()
        callback(img, x, y)

    def _finish_adjust(self, callback=None):
        cb = callback or self.on_done
        x1, y1, x2, y2 = self.sel
        x1, y1 = max(0, x1), max(0, y1)
        x2 = min(self.full_img.width, x2)
        y2 = min(self.full_img.height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        img = self.full_img.crop((x1, y1, x2, y2))
        out_x, out_y = self.vx + x1, self.vy + y1
        self._teardown()
        cb(img, out_x, out_y)

    def _commit_selection(self) -> bool:
        """调整模式 -> 标注模式：按当前选区裁剪工作图并重定位窗口。"""
        x1, y1, x2, y2 = self.sel
        x1, y1 = max(0, x1), max(0, y1)
        x2 = min(self.full_img.width, x2)
        y2 = min(self.full_img.height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return False
        self.mode = MODE_DRAW
        self.sel = [x1, y1, x2, y2]
        self.img = self.full_img.crop((x1, y1, x2, y2))
        self.offset_x = self.vx + x1
        self.offset_y = self.vy + y1
        self.scroll = 0
        self._layout_geometry()
        self._sync_scrollbar()
        self.setCursor(Qt.CrossCursor)
        self._refresh()
        self.move(self._win_rect.left(), self._win_rect.top())
        self.position_toolbar()
        return True

    def cancel(self):
        self._teardown()
        self.on_cancel()

    def _teardown(self):
        if self._closing:
            return
        self._closing = True
        self._cancel_entry()
        try:
            self.toolbar.close()
            self.toolbar.deleteLater()
        except Exception:
            pass
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        """用户直接关闭窗口（Alt+F4 / 关闭按钮）等同取消，避免主窗无响应。"""
        try:
            self.toolbar.close()
        except Exception:
            pass
        if not self._closing:
            self._closing = True
            self._cancel_entry()
            try:
                self.on_cancel()
            except Exception:
                pass
        super().closeEvent(event)

    # ---------- 鼠标 ----------
    def _img(self, pos: QPointF) -> tuple[int, int]:
        return self.canvas.img_pos(pos)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        if self._entry is not None:
            self._commit_text()
            return
        x, y = self._img(pos)
        if self.mode == MODE_ADJUST:
            self._adjust_press(x, y)
            return
        hit = self._text_at(x, y)
        if hit is not None:
            self._drag_text = hit
            bb = _text_bbox(hit, self.scale)
            self._drag_off = (x - int(bb.left() * self.scale),
                              y - int(bb.top() * self.scale))
            self.setCursor(Qt.SizeAllCursor)
            return
        self._push_undo()
        if self.tool == TOOL_TEXT:
            self._open_text_entry(x, y, pos)
            return
        self._start = (x, y)
        self._cur = (x, y)
        self._pen_points = [(x, y)]
        self.canvas.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.mode == MODE_ADJUST:
            if self._adjust_handle or self._adjust_move:
                x, y = self._img(pos)
                self._adjust_drag(x, y)
            else:
                x, y = self._img(pos)
                self.setCursor(self._adjust_cursor(x, y))
            return
        if self._drag_text is not None:
            x, y = self._img(pos)
            obj = self._drag_text
            obj["x"] = x - self._drag_off[0]
            obj["y"] = y - self._drag_off[1]
            self.canvas.update()
            return
        if self._start is None:
            return
        x, y = self._img(pos)
        self._cur = (x, y)
        if self.tool == TOOL_PEN:
            self._pen_points.append((x, y))
        self.canvas.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        if self.mode == MODE_ADJUST:
            self._adjust_handle = None
            self._adjust_move = False
            x, y = self._img(pos)
            self.setCursor(self._adjust_cursor(x, y))
            return
        if self._drag_text is not None:
            self._drag_text = None
            self.setCursor(Qt.CrossCursor)
            return
        if self._start is None:
            return
        x, y = self._img(pos)
        x0, y0 = self._start
        self._start = None
        self._cur = None
        tool = self.tool
        if tool == TOOL_MOSAIC:
            self._apply_mosaic(min(x0, x), min(y0, y), max(x0, x), max(y0, y))
        else:
            draw = ImageDraw.Draw(self.img)
            w = self.line_width
            if tool == TOOL_RECT:
                draw.rectangle([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                               outline=self.color, width=w)
            elif tool == TOOL_ELLIPSE:
                draw.ellipse([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                             outline=self.color, width=w)
            elif tool == TOOL_LINE:
                draw.line([(x0, y0), (x, y)], fill=self.color, width=w)
            elif tool == TOOL_ARROW:
                self._draw_arrow(draw, x0, y0, x, y)
            elif tool == TOOL_PEN:
                if len(self._pen_points) >= 2:
                    draw.line(self._pen_points, fill=self.color, width=w,
                              joint="curve")
        self._pen_points = []
        self._refresh()

    def mouseDoubleClickEvent(self, event):
        if self.mode != MODE_DRAW or self._entry is not None:
            return
        x, y = self._img(event.position())
        hit = self._text_at(x, y)
        if hit is not None:
            self._edit_text_obj(hit, event.position())

    def keyPressEvent(self, event):
        if self._entry is not None:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.done()
            return
        if key == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undo()
            return
        if key == Qt.Key_T and event.modifiers() & Qt.ControlModifier:
            self.request_ocr()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            idx = key - Qt.Key_1
            if idx < len(TOOLS):
                self.set_tool(TOOLS[idx])
                return
        if event.text() in [str(i) for i in range(1, len(TOOLS) + 1)]:
            idx = int(event.text()) - 1
            self.set_tool(TOOLS[idx])
            return
        super().keyPressEvent(event)

    # ---------- 预览绘制 ----------
    def paint_preview(self, p: QPainter):
        if self._start is None or self._cur is None:
            return
        s = self.scale
        x0, y0 = self._start
        x1, y1 = self._cur
        col = _rgb(self.color)
        pen = QPen(col, max(1.0, self.line_width / s))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.tool == TOOL_PEN:
            path = QPainterPath()
            pts = self._pen_points or [self._start, self._cur]
            path.moveTo(pts[0][0] / s, pts[0][1] / s)
            for px, py in pts[1:]:
                path.lineTo(px / s, py / s)
            p.drawPath(path)
            return
        r = QRectF(min(x0, x1) / s, min(y0, y1) / s,
                   abs(x1 - x0) / s, abs(y1 - y0) / s)
        if self.tool in (TOOL_RECT, TOOL_MOSAIC):
            p.drawRect(r)
        elif self.tool == TOOL_ELLIPSE:
            p.drawEllipse(r)
        elif self.tool == TOOL_LINE:
            p.drawLine(QPointF(x0 / s, y0 / s), QPointF(x1 / s, y1 / s))
        elif self.tool == TOOL_ARROW:
            p.drawLine(QPointF(x0 / s, y0 / s), QPointF(x1 / s, y1 / s))
            head = self._arrow_head(x0, y0, x1, y1)
            if head:
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                poly = QPainterPath()
                poly.moveTo(head[0][0] / s, head[0][1] / s)
                poly.lineTo(head[1][0] / s, head[1][1] / s)
                poly.lineTo(head[2][0] / s, head[2][1] / s)
                poly.closeSubpath()
                p.drawPath(poly)

    # ---------- 箭头 ----------
    def _arrow_head(self, x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 8:
            return None
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        hl = max(10, self.line_width * 3)
        return [(x1, y1),
                (x1 - ux * hl + px * hl * 0.55, y1 - uy * hl + py * hl * 0.55),
                (x1 - ux * hl - px * hl * 0.55, y1 - uy * hl - py * hl * 0.55)]

    def _draw_arrow(self, draw, x0, y0, x1, y1):
        draw.line([(x0, y0), (x1, y1)], fill=self.color, width=self.line_width)
        head = self._arrow_head(x0, y0, x1, y1)
        if head:
            draw.polygon(head, fill=self.color)

    # ---------- 马赛克 ----------
    def _apply_mosaic(self, x1, y1, x2, y2):
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.img.width, x2), min(self.img.height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        region = self.img.crop((x1, y1, x2, y2))
        w2, h2 = region.size
        small = region.resize((max(1, w2 // 8), max(1, h2 // 8)), Image.NEAREST)
        block = small.resize((w2, h2), Image.NEAREST)
        self.img.paste(block, (x1, y1))

    # ---------- 调整选区 ----------
    def _handle_points(self):
        x1, y1, x2, y2 = self.sel
        return {
            "nw": (x1, y1), "n": ((x1 + x2) // 2, y1), "ne": (x2, y1),
            "w": (x1, (y1 + y2) // 2), "e": (x2, (y1 + y2) // 2),
            "sw": (x1, y2), "s": ((x1 + x2) // 2, y2), "se": (x2, y2),
        }

    def _hit_handle(self, x, y):
        for name, (hx, hy) in self._handle_points().items():
            if abs(x - hx) <= HANDLE_HIT and abs(y - hy) <= HANDLE_HIT:
                return name
        return None

    def _adjust_cursor(self, x, y):
        h = self._hit_handle(x, y)
        if h:
            return {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
                    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
                    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
                    "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor}[h]
        x1, y1, x2, y2 = self.sel
        if x1 <= x <= x2 and y1 <= y <= y2:
            return Qt.SizeAllCursor
        return Qt.ArrowCursor

    def _adjust_press(self, x, y):
        h = self._hit_handle(x, y)
        if h:
            self._adjust_handle = h
            self.setCursor(self._adjust_cursor(x, y))
            return
        x1, y1, x2, y2 = self.sel
        if x1 <= x <= x2 and y1 <= y <= y2:
            self._adjust_move = True
            self._adjust_off = (x - x1, y - y1)
            self.setCursor(Qt.SizeAllCursor)
        else:
            self._adjust_handle = None
            self._adjust_move = False
            self.setCursor(Qt.ArrowCursor)

    def _adjust_drag(self, x, y):
        x1, y1, x2, y2 = self.sel
        MIN = MIN_SEL
        if self._adjust_handle:
            h = self._adjust_handle
            if h == "nw":
                x1, y1 = x, y
            elif h == "ne":
                x2, y1 = x, y
            elif h == "sw":
                x1, y2 = x, y
            elif h == "se":
                x2, y2 = x, y
            elif h == "n":
                y1 = y
            elif h == "s":
                y2 = y
            elif h == "w":
                x1 = x
            elif h == "e":
                x2 = x
            x1 = max(0, min(x1, self.full_img.width - MIN))
            y1 = max(0, min(y1, self.full_img.height - MIN))
            x2 = max(MIN, min(x2, self.full_img.width))
            y2 = max(MIN, min(y2, self.full_img.height))
            if x2 - x1 < MIN:
                if h in ("nw", "w", "sw"):
                    x1 = x2 - MIN
                else:
                    x2 = x1 + MIN
            if y2 - y1 < MIN:
                if h in ("nw", "n", "ne"):
                    y1 = y2 - MIN
                else:
                    y2 = y1 + MIN
            self.sel = [x1, y1, x2, y2]
            self.canvas.update()
            self.position_toolbar()
        elif self._adjust_move:
            dx = x - self._adjust_off[0]
            dy = y - self._adjust_off[1]
            w = x2 - x1
            hh = y2 - y1
            nx1 = max(0, min(dx, self.full_img.width - w))
            ny1 = max(0, min(dy, self.full_img.height - hh))
            self.sel = [nx1, ny1, nx1 + w, ny1 + hh]
            self.canvas.update()
            self.position_toolbar()

    def paint_adjust_overlay(self, p: QPainter):
        """选区 overlay：accent 2px 描边 + 四角 L 角标 + 8 手柄 + 尺寸徽标 + 提示条。"""
        s = self.scale
        x1, y1, x2, y2 = [v / s for v in self.sel]
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))

        # 四角 L 形取景角标
        L = 14
        pen = QPen(QColor(theme.ACCENT), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1),
                               (x1, y2, 1, -1), (x2, y2, -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * L, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * L))

        # 8 手柄：8px 白底 + accent 1.5px 边
        p.setPen(QPen(QColor(theme.ACCENT), 1.5))
        p.setBrush(QColor("#FFFFFF"))
        for hx, hy in self._handle_points().values():
            p.drawRect(QRectF(hx / s - 4, hy / s - 4, 8, 8))

        self._paint_badge(p, x1, y1, x2, y2)
        self._paint_hint(p, x1, y1, x2, y2)

    def _paint_badge(self, p: QPainter, x1, y1, x2, y2):
        text = f"{self.sel[2] - self.sel[0]} × {self.sel[3] - self.sel[1]}"
        f = _ui_font(12, True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        bw = fm.horizontalAdvance(text) + 22
        bh = 22
        cx = (x1 + x2) / 2
        by = y1 + 10
        if by + bh > y2:
            by = max(0, y1 - bh - 6)
        rect = QRectF(cx - bw / 2, by, bw, bh)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawPath(path)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(rect, Qt.AlignCenter, text)

    def _paint_hint(self, p: QPainter, x1, y1, x2, y2):
        text = "调整选区 · 拖动移动 · 拉角缩放 · Enter 确认 · Esc 取消"
        f = _ui_font(11)
        p.setFont(f)
        fm = QFontMetricsF(f)
        bw = fm.horizontalAdvance(text) + 28
        bh = 26
        vw, vh = self.view_w, self.view_h
        hx = (x1 + x2 - bw) / 2
        hx = max(4, min(hx, vw - bw - 4))
        hy = y1 - bh - 8
        if hy < 0:
            hy = y2 + 8
            if hy + bh > vh - 4:
                hy = max(4, vh - bh - 4)
        rect = QRectF(hx, hy, bw, bh)
        path = QPainterPath()
        path.addRoundedRect(rect, 13, 13)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.setBrush(QColor("#F4F6F9"))
        p.drawPath(path)
        p.setPen(QColor(theme.INK_2))
        p.drawText(rect, Qt.AlignCenter, text)

    # ---------- 文字 ----------
    def _open_text_entry(self, ix: int, iy: int, pos: QPointF):
        self._text_pos = (ix, iy)
        self._editing_obj = None
        self._show_entry(pos, "")

    def _edit_text_obj(self, obj: dict, pos: QPointF):
        self._text_pos = (obj["x"], obj["y"])
        self._editing_obj = obj
        self._show_entry(pos, obj["txt"])

    def _show_entry(self, pos: QPointF, text: str):
        self._close_entry()
        entry = _TextEntry(self, self._commit_text, self._cancel_entry)
        entry.setStyleSheet(
            "QLineEdit{background:#FFFFFF;color:#000000;border:1px solid %s;"
            "border-radius:3px;padding:1px 3px;font-size:14px;}" % theme.ACCENT)
        entry.setText(text)
        entry.setFixedWidth(200)
        entry.adjustSize()
        entry.setFixedWidth(200)
        entry.setFixedHeight(26)
        y = int(pos.y()) - 6
        y = max(0, min(y, self.view_h - 26))
        x = max(0, min(int(pos.x()), max(0, self.view_w - 200)))
        entry.move(x, y)
        entry.show()
        entry.setFocus()
        entry.selectAll()
        self._entry = entry

    def _commit_text(self):
        """提交文字（Enter / 失焦 / 完成）：防 hide() 触发 focusOut 重入。"""
        if self._entry is None or self._committing_entry:
            return
        self._committing_entry = True
        entry = self._entry
        self._entry = None
        try:
            txt = entry.text().strip()
            entry.hide()
            entry.deleteLater()
        except Exception:
            txt = ""
        try:
            if not txt:
                self._editing_obj = None
                return
            if self._editing_obj is not None:
                obj = self._editing_obj
                obj["txt"] = txt
                obj["font_size"] = max(12, self.line_width * 4)
                obj["color"] = tuple(self.color)
                self._editing_obj = None
            else:
                self.text_objs.append({
                    "x": self._text_pos[0], "y": self._text_pos[1], "txt": txt,
                    "font_size": max(12, self.line_width * 4),
                    "color": tuple(self.color),
                })
            self.canvas.update()
        finally:
            self._committing_entry = False
            self.setFocus()

    def _close_entry(self):
        """只关闭输入框，不动 _editing_obj（编辑已有文字时用）。"""
        if self._entry is not None:
            self._entry.hide()
            self._entry.deleteLater()
            self._entry = None

    def _cancel_entry(self):
        self._close_entry()
        self._editing_obj = None

    def _text_at(self, x: int, y: int):
        """按图片物理坐标 (x, y) 命中文字对象（内部换算到逻辑坐标比较）。"""
        if self.scale <= 0:
            return None
        pt = QPointF(x / self.scale, y / self.scale)
        for obj in reversed(self.text_objs):
            bb = _text_bbox(obj, self.scale).adjusted(-2, -2, 2, 2)
            if bb.contains(pt):
                return obj
        return None

    def _flatten_texts(self):
        if not self.text_objs:
            return
        base = self.img.convert("RGBA")
        for obj in self.text_objs:
            font = _load_pil_font(int(obj["font_size"]))
            tmp = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            bbox = ImageDraw.Draw(tmp).textbbox((0, 0), obj["txt"], font=font,
                                                stroke_width=2)
            tw = max(4, bbox[2] - bbox[0] + 6)
            th = max(4, bbox[3] - bbox[1] + 6)
            layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text(
                (3 - bbox[0], 3 - bbox[1]), obj["txt"], font=font,
                fill=tuple(obj["color"]) + (255,),
                stroke_width=2, stroke_fill=(255, 255, 255, 255))
            base.alpha_composite(layer, (int(obj["x"] - 3), int(obj["y"] - 3)))
        self.img = base.convert("RGB")
