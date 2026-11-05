# -*- coding: utf-8 -*-
"""P2 截图选区层 —— 全屏遮罩 / 拖拽选区 / 尺寸徽标 / 框外提示条 / 多屏支持。

设计要点
--------
1. **抓屏仍走 Pillow**：``QScreen.grabWindow`` 在部分驱动/远程桌面下返回黑屏，
   P1 已验证过，因此整块虚拟桌面用 ``ImageGrab.grab(bbox=..., all_screens=True)``
   一次性抓成一张 PIL 图（物理像素）。
2. **每屏一个覆盖窗**：混排 DPI 下单个 Qt 窗口无法同时正确铺满不同 DPR 的屏幕，
   用 ``geometry.overlay_geometries_logical()`` 为每个屏幕建一个无边框置顶窗，
   各自只绘制自己那一块截图切片（``QPixmap`` 带 devicePixelRatio，物理像素 1:1）。
3. **物理像素为唯一坐标系**：拖拽全程用物理像素全局坐标记录选区，
   窗口内绘制时再换算为「本地逻辑像素」，与旧版 ``capture._to_img`` 的语义一致。
4. **交互对齐旧版**：单击=截窗口、拖拽=截区域、双击=全屏、Esc=取消（新增右键取消）。
   单击延时 :data:`CLICK_DELAY_MS` 执行，才能让双击全屏真正可用。

对外接口（供 main_qt 接入）::

    from ui_qt import capture_overlay

    ses = capture_overlay.CaptureSession()
    ses.finished.connect(on_result)      # (PIL img, offset_x, offset_y, full_img, bbox)
    ses.cancelled.connect(on_cancel)
    ses.start()

``on_result`` 的参数与旧版 ``capture.CaptureWindow`` 完全一致，方便 P3 编辑器直接接管。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PIL import Image, ImageGrab
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFontMetricsF, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

from ui_qt import geometry as G
from ui_qt import imageutil, theme

# ---------------- 视觉参数（对齐 tk 版 capture.py） ----------------
CORNER_LEN = 14.0        # 取景角标边长
CORNER_W = 2.5           # 取景角标线宽
BORDER_W = 2.0           # 选区描边
BADGE_H = 22.0           # 尺寸徽标高
BADGE_R = 6.0            # 尺寸徽标圆角
BADGE_PAD_X = 12.0       # 尺寸徽标左右内边距
TIP_H = 28.0             # 提示条高
TIP_R = 14.0             # 提示条圆角（胶囊）
TIP_PAD_X = 12.0         # 提示条左右内边距
TIP_GAP = 62.0           # 提示条到选区上沿的距离（让开徽标）

TIP_TEXT = "单击=截窗口   拖拽=截区域   双击=全屏   Esc=取消"
TIP_TEXT_DRAG = "松开=截取区域   双击=全屏   Esc=取消"

CLICK_DELAY_MS = 180     # 单击延时，用于让双击全屏可被识别
HOVER_FILL_ALPHA = 26    # 悬停窗口高亮填充透明度（0-255，遮罩挖洞后再叠一层淡蓝）

# 不属于「可截窗口」的系统壳窗口类名（否则悬停桌面会画一个满屏高亮框）
_SKIP_CLASSES = {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd",
                 "windows.ui.core.corewindow"}


# ---------------- Win32 窗口枚举（不依赖 tkinter） ----------------
class _WRECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_user32 = ctypes.windll.user32
_dwmapi = getattr(ctypes.windll, "dwmapi", None)
_DWMWA_CLOAKED = 14


def _is_cloaked(hwnd: int) -> bool:
    """UWP 应用被挂起后窗口仍「可见」，需要用 DWM 属性过滤掉。"""
    if _dwmapi is None:
        return False
    try:
        val = ctypes.c_int(0)
        res = _dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), ctypes.c_uint(_DWMWA_CLOAKED),
                                            ctypes.byref(val), ctypes.sizeof(val))
        return res == 0 and val.value != 0
    except Exception:
        return False


def _class_name(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return (buf.value or "").lower()
    except Exception:
        return ""


def enumerate_windows(exclude=()) -> list[QRect]:
    """枚举可见顶层窗口的物理像素矩形，按 z-order 从顶到底。"""
    rects: list[QRect] = []
    excl = {int(h) for h in exclude}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        try:
            h = int(hwnd) if hwnd else 0
            if h in excl:
                return True
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            if _is_cloaked(h):
                return True
            if _class_name(h) in _SKIP_CLASSES:
                return True
            r = _WRECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            w, hh = r.right - r.left, r.bottom - r.top
            if w < 40 or hh < 30:      # 过滤过小窗口（与 tk 版一致）
                return True
            rects.append(QRect(r.left, r.top, w, hh))
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_cb, 0)
    except Exception:
        pass
    return rects


def window_at(point: QPoint, exclude=()) -> QRect | None:
    """返回包含该物理点的最上层窗口矩形；没有则 None。"""
    for rect in enumerate_windows(exclude):
        if rect.contains(point):
            return QRect(rect)
    return None


def grab_virtual_desktop() -> tuple[Image.Image, int, int]:
    """抓取整个虚拟桌面，返回 (PIL Image, 原点 x, 原点 y)（物理像素）。"""
    x1, y1, x2, y2 = G.grab_bbox()
    img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
    if img.mode != "RGB":
        img = img.convert("RGB")
    expect = (max(1, x2 - x1), max(1, y2 - y1))
    if (img.width, img.height) != expect:
        # 极少数驱动在混合 DPI 下返回逻辑尺寸，这里按物理尺寸拉伸兜底
        img = img.resize(expect, Image.Resampling.LANCZOS)
    return img, x1, y1


def _make_rect(a: QPoint, b: QPoint) -> QRect:
    """两点 -> 规范化矩形（右下开区间，保证宽高为像素差）。"""
    x1, x2 = sorted((a.x(), b.x()))
    y1, y2 = sorted((a.y(), b.y()))
    return QRect(x1, y1, x2 - x1, y2 - y1)


# ---------------- 单屏覆盖窗 ----------------
class _ScreenView(QWidget):
    """一块屏幕的全屏覆盖窗，绘制委托给 :class:`CaptureSession`。"""

    def __init__(self, session: "CaptureSession", screen: G.ScreenInfo):
        super().__init__(None,
                         Qt.WindowType.Window
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.session = session
        self.screen = screen
        self.base_pixmap: QPixmap | None = None
        self.base_offset = QPointF(0.0, 0.0)

        self.setWindowTitle("ScreenSnap 选区")
        self.setGeometry(screen.logical)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

    # ---- 绘制 ----
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        try:
            self.session.paint_view(self, painter)
        finally:
            painter.end()

    # ---- 鼠标 / 键盘 ----
    def _phys(self, event) -> QPoint:
        return G.logical_to_physical(event.globalPosition().toPoint())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self.session.cancel()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.session.press(self._phys(event))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self.session.move(self._phys(event))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.session.release(self._phys(event))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.session.double_click()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.session.cancel()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------- 会话控制器 ----------------
class CaptureSession(QObject):
    """一次截图会话：管遮罩窗、拖拽状态、选区绘制与结果输出。

    ``finished(img, offset_x, offset_y, full_img, bbox)``
        img       —— 裁好的 PIL 图（选区内容）
        offset_x/y—— 选区左上角在虚拟桌面中的物理坐标
        full_img  —— 整块虚拟桌面的原图（供 P3 编辑器「调整选区」用）
        bbox      —— 选区在 full_img 内的像素 bbox ``(l, t, r, b)``，全屏截图时为整图
    ``cancelled()`` —— Esc / 右键 / 取消
    """

    finished = Signal(object, int, int, object, object)
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.img: Image.Image | None = None
        self.vrect = QRect()
        self.hover_rect: QRect | None = None

        self._views: list[_ScreenView] = []
        self._drag_start: QPoint | None = None
        self._drag_cur: QPoint | None = None
        self._moved = False
        self._cursor_phys = QPoint(0, 0)
        self._finished = False
        self._hover_at = 0.0

        self._click_point: QPoint | None = None
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(CLICK_DELAY_MS)
        self._click_timer.timeout.connect(self._on_single_click)

    # ---------------- 生命周期 ----------------
    def start(self, image: tuple[Image.Image, int, int] | None = None) -> None:
        """抓屏并铺满所有屏幕。

        ``image`` 仅供自检脚本注入假数据，正常调用不传。
        """
        if self._views or self._finished:
            return
        if image is None:
            self.img, ox, oy = grab_virtual_desktop()
        else:
            self.img, ox, oy = image
        self.vrect = QRect(ox, oy, self.img.width, self.img.height)

        screens = G.screens()
        if not screens:                       # 极端兜底：无 Qt 屏幕信息
            self._finish_session(cancel=True)
            return

        for screen in screens:
            view = _ScreenView(self, screen)
            self._prepare_view(view)
            self._views.append(view)

        self._cursor_phys = G.logical_to_physical(QCursor.pos())
        for view in self._views:
            view.show()
        QApplication.processEvents()

        target = self._view_at_physical(self._cursor_phys)
        for view in self._views:
            view.raise_()
        if target is not None:
            target.activateWindow()
            target.setFocus(Qt.FocusReason.OtherFocusReason)

        self._update_hover(self._cursor_phys, force=True)
        self._repaint_all()

    def _prepare_view(self, view: _ScreenView) -> None:
        """把整图里属于该屏的部分裁成 QPixmap（带 dpr，物理像素 1:1）。"""
        screen = view.screen
        phys = screen.physical.intersected(self.vrect) if not screen.physical.isEmpty() else QRect()
        if phys.isEmpty():
            view.base_pixmap = None
            return
        x = phys.left() - self.vrect.left()
        y = phys.top() - self.vrect.top()
        slice_img = self.img.crop((x, y, x + phys.width(), y + phys.height()))
        scale = screen.dpr or 1.0
        view.base_pixmap = imageutil.pil_to_qpixmap(slice_img, dpr=scale)
        view.base_offset = QPointF((phys.left() - screen.physical.left()) / scale,
                                   (phys.top() - screen.physical.top()) / scale)

    def close_views(self) -> None:
        for view in self._views:
            try:
                view.hide()
                view.close()
                view.deleteLater()
            except Exception:
                pass
        self._views.clear()

    def _finish_session(self, cancel: bool = False) -> None:
        if self._finished:
            return
        self._finished = True
        self._cancel_click_timer()
        self.close_views()
        if cancel:
            self.cancelled.emit()

    def cancel(self) -> None:
        self._finish_session(cancel=True)

    # ---------------- 状态查询 ----------------
    @property
    def drag_rect(self) -> QRect | None:
        """当前拖拽选区（物理像素全局坐标），未开始或未移动时为 None。"""
        if self._drag_start is None or self._drag_cur is None or not self._moved:
            return None
        return _make_rect(self._drag_start, self._drag_cur)

    def _excludes(self) -> list[int]:
        out = []
        for view in self._views:
            try:
                out.append(int(view.winId()))
            except Exception:
                pass
        return out

    def _view_at_physical(self, point: QPoint) -> _ScreenView | None:
        if not self._views:
            return None
        best: _ScreenView | None = None
        best_area = -1
        for view in self._views:
            inter = view.screen.physical.intersected(QRect(point.x(), point.y(), 1, 1))
            area = inter.width() * inter.height()
            if area > best_area:
                best_area = area
                best = view
        return best

    # ---------------- 交互状态机 ----------------
    def press(self, phys: QPoint) -> None:
        if self._finished:
            return
        self._cancel_click_timer()
        self._cursor_phys = QPoint(phys)
        self._drag_start = QPoint(phys)
        self._drag_cur = QPoint(phys)
        self._moved = False
        self.hover_rect = None
        self._repaint_all()

    def move(self, phys: QPoint) -> None:
        if self._finished:
            return
        self._cursor_phys = QPoint(phys)
        if self._drag_start is None:
            self._update_hover(phys)
            return
        self._drag_cur = QPoint(phys)
        if not self._moved:
            threshold = max(4, QApplication.startDragDistance())
            dx = abs(phys.x() - self._drag_start.x())
            dy = abs(phys.y() - self._drag_start.y())
            if max(dx, dy) > threshold:
                self._moved = True
        self._repaint_all()

    def release(self, phys: QPoint) -> None:
        if self._finished:
            return
        start = self._drag_start
        self._drag_start = None
        self._drag_cur = None
        if start is None:
            return
        cur = QPoint(phys)
        if self._moved:
            self._moved = False
            rect = _make_rect(start, cur).intersected(self.vrect)
            if rect.width() >= 2 and rect.height() >= 2:
                self._finish_with(rect)
            else:
                self._repaint_all()
            return
        # 单击：延时执行，让双击全屏有机会覆盖
        self._click_point = cur
        self._click_timer.start()

    def double_click(self) -> None:
        if self._finished:
            return
        self._cancel_click_timer()
        self._drag_start = None
        self._drag_cur = None
        self._moved = False
        self._finish_with(QRect(self.vrect))

    def _on_single_click(self) -> None:
        if self._finished or self._click_point is None:
            return
        point = self._click_point
        self._click_point = None
        hit = window_at(point, self._excludes())
        rect = QRect(self.vrect) if hit is None else hit.intersected(self.vrect)
        if rect.isEmpty():
            rect = QRect(self.vrect)
        self._finish_with(rect)

    def _cancel_click_timer(self) -> None:
        if self._click_timer.isActive():
            self._click_timer.stop()
        self._click_point = None

    def _finish_with(self, rect: QRect) -> None:
        if self._finished or self.img is None:
            return
        rect = rect.intersected(self.vrect)
        if rect.isEmpty():
            rect = QRect(self.vrect)
        x1 = rect.left() - self.vrect.left()
        y1 = rect.top() - self.vrect.top()
        bbox = (x1, y1, x1 + rect.width(), y1 + rect.height())
        crop = self.img.crop(bbox)
        full = self.img.copy()
        offset_x, offset_y = rect.left(), rect.top()
        self._finish_session(cancel=False)
        self.finished.emit(crop, offset_x, offset_y, full, bbox)

    # ---------------- 悬停高亮（窗口捕捉） ----------------
    def _update_hover(self, phys: QPoint, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._hover_at < 0.07:
            return
        self._hover_at = now
        rect = window_at(phys, self._excludes())
        if rect is not None:
            rect = rect.intersected(self.vrect)
            if rect.isEmpty():
                rect = None
        if (rect is None and self.hover_rect is None) or rect == self.hover_rect:
            return
        self.hover_rect = rect
        self._repaint_all()

    # ---------------- 绘制 ----------------
    def _repaint_all(self) -> None:
        for view in self._views:
            view.update()

    def _view_logical_rect(self, view: _ScreenView, phys_rect: QRect) -> QRectF:
        """物理矩形 -> 该窗口内的逻辑矩形。"""
        screen = view.screen
        scale = screen.dpr or 1.0
        left = (phys_rect.left() - screen.physical.left()) / scale
        top = (phys_rect.top() - screen.physical.top()) / scale
        return QRectF(left, top, phys_rect.width() / scale, phys_rect.height() / scale)

    def tip_text(self) -> str:
        return TIP_TEXT_DRAG if self._drag_start is not None else TIP_TEXT

    def paint_view(self, view: _ScreenView, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        viewport = QRectF(0.0, 0.0, float(view.width()), float(view.height()))

        # 1) 截图底图（未遮罩的原貌）
        painter.fillRect(viewport, QColor(0, 0, 0))
        if view.base_pixmap is not None and not view.base_pixmap.isNull():
            painter.drawPixmap(view.base_offset, view.base_pixmap)

        mask = theme.mask_color()
        sel = self.drag_rect

        if sel is None:
            # 2a) 未拖拽：整屏遮罩 + 悬停窗口挖洞高亮 + 左上提示条
            hint = None
            if self.hover_rect is not None:
                hint = self._view_logical_rect(view, self.hover_rect).intersected(viewport)
                if hint.isEmpty():
                    hint = None
            if hint is None:
                painter.fillRect(viewport, mask)
            else:
                # 遮罩按奇偶填充规则挖掉悬停窗口，使被捕捉窗口保持原亮度，
                # 再叠一层淡蓝让「已捕捉」有明确颜色反馈，最后描边
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.OddEvenFill)
                path.addRect(viewport)
                path.addRect(hint)
                painter.fillPath(path, mask)
                fill = QColor(theme.ACCENT)
                fill.setAlpha(HOVER_FILL_ALPHA)
                painter.fillRect(hint, fill)
                self._paint_window_hint(view, painter, self.hover_rect)
            if view is self._view_at_physical(self._cursor_phys):
                self._paint_tip(painter, viewport, None, TIP_TEXT)
            return

        # 2b) 拖拽中：选区外遮罩，选区内保持明亮（odd-even 挖洞）
        sel_local = self._view_logical_rect(view, sel)
        hole = sel_local.intersected(viewport)
        if hole.isEmpty():
            painter.fillRect(viewport, mask)
        else:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            path.addRect(viewport)
            path.addRect(hole)
            painter.fillPath(path, mask)

        # 3) 选区描边 + 四角取景角标
        self._paint_selection(painter, sel_local, hole)

        # 4) 尺寸徽标 + 提示条（只由选区「上沿中点」所在屏绘制，避免多屏重复）
        anchor = QPoint(sel.center().x(), sel.top())
        if view is self._view_at_physical(anchor):
            self._paint_badge(painter, viewport, sel_local, f"{sel.width()} × {sel.height()}")
            self._paint_tip(painter, viewport, sel_local, TIP_TEXT_DRAG)

    def _paint_selection(self, painter: QPainter, sel_local: QRectF, hole: QRectF) -> None:
        if hole.isEmpty():
            return
        rect = QRectF(hole).adjusted(BORDER_W / 2.0, BORDER_W / 2.0,
                                     -BORDER_W / 2.0, -BORDER_W / 2.0)
        pen = QPen(QColor(theme.ACCENT), BORDER_W)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # 角标：贴着真实选区边界画（可能被屏幕边缘裁掉，正好提示「还有一段在别屏」）
        pen = QPen(QColor(theme.ACCENT), CORNER_W)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        ln = max(4.0, min(CORNER_LEN, sel_local.width() / 2.0, sel_local.height() / 2.0))
        corners = (
            (sel_local.left(), sel_local.top(), ln, ln),
            (sel_local.right(), sel_local.top(), -ln, ln),
            (sel_local.left(), sel_local.bottom(), ln, -ln),
            (sel_local.right(), sel_local.bottom(), -ln, -ln),
        )
        for x, y, dx, dy in corners:
            painter.drawLine(QPointF(x, y), QPointF(x + dx, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy))

    def _paint_window_hint(self, view: _ScreenView, painter: QPainter, phys_rect: QRect) -> None:
        rect = self._view_logical_rect(view, phys_rect).adjusted(
            BORDER_W / 2.0, BORDER_W / 2.0, -BORDER_W / 2.0, -BORDER_W / 2.0)
        pen = QPen(QColor(theme.ACCENT), BORDER_W)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _paint_badge(self, painter: QPainter, viewport: QRectF, sel_local: QRectF,
                     text: str) -> None:
        font = theme.font(theme.FONT_BADGE_SIZE, bold=True, digit=True)
        fm = QFontMetricsF(font)
        width = max(56.0, fm.horizontalAdvance(text) + BADGE_PAD_X * 2)
        x1 = sel_local.center().x() - width / 2.0
        y1 = sel_local.top() - BADGE_H - 4.0
        if y1 < 2.0:                                   # 贴顶放不下 -> 选区下方
            y1 = sel_local.bottom() + 6.0
        if y1 + BADGE_H > viewport.height() - 2.0:
            y1 = max(2.0, viewport.height() - BADGE_H - 2.0)
        x1 = min(max(2.0, x1), max(2.0, viewport.width() - width - 2.0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(QRectF(x1, y1, width, BADGE_H), BADGE_R, BADGE_R)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.setFont(font)
        painter.drawText(QRectF(x1, y1, width, BADGE_H), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_tip(self, painter: QPainter, viewport: QRectF, sel_local: QRectF | None,
                   text: str) -> None:
        font = theme.font(theme.FONT_BODY_SIZE)
        fm = QFontMetricsF(font)
        width = min(max(120.0, fm.horizontalAdvance(text) + TIP_PAD_X * 2),
                    max(60.0, viewport.width() - 8.0))

        if sel_local is None:
            # 初始态：左上角 14,12（与 tk 版一致）
            x1, y1 = 14.0, 12.0
        else:
            x1 = sel_local.center().x() - width / 2.0
            y1 = sel_local.top() - TIP_GAP
            if y1 < 2.0:                                # 上方不足 -> 选区下方
                y1 = sel_local.bottom() + 8.0
                if y1 + TIP_H > viewport.height() - 2.0:
                    # 上下都不够（选区近全屏）-> 右上角
                    x1 = viewport.width() - width - 14.0
                    y1 = 12.0
        x1 = min(max(2.0, x1), max(2.0, viewport.width() - width - 2.0))
        y1 = min(max(2.0, y1), max(2.0, viewport.height() - TIP_H - 2.0))

        rect = QRectF(x1, y1, width, TIP_H)
        painter.setPen(QPen(QColor(theme.LINE), 1.0))
        painter.setBrush(QColor(theme.SURFACE))
        painter.drawRoundedRect(rect, TIP_R, TIP_R)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.INK_2)))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
