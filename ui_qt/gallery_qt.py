# -*- coding: utf-8 -*-
"""P4 历史库（Qt 版）。

对应 tk 版 ``gallery.py``（GalleryWindow / _LargeViewer），用 PySide6 的
``QGraphicsView`` 重写，功能对齐：

* **按日期分组的无限画布**：日期分组横向依次排开（旧日期在左、新日期在右），
  同一日期内从上到下填满一列再往右开新列，每列行数随视口高度自适应；
* **视口裁剪增量渲染**：只为「当前视口内」的卡片创建 QGraphicsItem，
  缩放/平移时增量增删，避免上千张图一次性建 item 卡死；
* **滚轮缩放**（5% ~ 1200%，以鼠标位置为锚点）、**空白拖拽平移**；
* **单击选中 / Shift 多选 / Ctrl+A 全选 / 空白单击取消**；
* **按住卡片拖动 = 调整顺序**（仅允许同日期内换位，与 tk 版一致）；
* **Delete = 移入回收站**（``save_root/.trash``，保留相对路径，``trash_days``
  天后自动清除，可在文件管理器里找回）；
* **双击 = 大图查看**（LargeViewer：适应窗口/100%、滚轮缩放、拖拽平移、
  复制、另存为、在文件夹中显示、删除、Esc 关闭）；
* **双击录屏条目 = 内置播放窗**（P6 接入 ``video_player_qt``：打开即播、暂停/
  继续、重新播放、关闭；解码器不可用时回退系统播放器）；
* 右键菜单、F5 刷新、状态栏（总数 / 已选数 / 缩放比例 / 存档目录）；
* 打开时自动执行 ``cleanup_trash`` + ``scan_videos`` + 收录「未登记的图片」，
  所以 P3 直接落盘的 ``ScreenSnap_*.png`` 也能出现在历史库里。

设计约束（与 P1~P3 一致）：
* 只复用无 UI 的 ``config`` / ``storage``，不 import 旧 UI 模块
  （``capture`` / ``editor`` / ``gallery`` / ``settings`` / ``recorder``）；
* 不改动 tk 版任何源码，Qt 版与 tk 版可同时运行（单实例互斥名不同）。

入口：``open_gallery(parent, cfg)``，由 ``main_qt.py`` 的托盘菜单 / 面板按钮调用。
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from datetime import datetime

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QIcon, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF, QShortcut,
                           QTransform)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QPushButton, QToolButton, QVBoxLayout,
                               QWidget)

from . import clipboard, dialogs, geometry, theme
from .imageutil import qpixmap_to_pil

# 保证以任意 cwd 运行都能 import 根目录下的 config / storage
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config as appconfig          # noqa: E402
import storage as appstorage        # noqa: E402

# ---------------- 画布尺寸常量（对齐 tk 版 gallery.py） ----------------
THUMB_W = 180               # 缩略图卡片宽
THUMB_H = 130               # 缩略图卡片高
CELL_W = 200                # 单元格宽（含间距）
CELL_H = 170                # 单元格高（含间距）
MARGIN = 26                 # 画布四周留白
GROUP_H = 44                # 日期标题占位高度
GROUP_GAP = 36              # 日期分组之间的水平间隔
PAD = 26                    # 内容到 scene 边界的固定偏移

MIN_SCALE = 0.05
MAX_SCALE = 12.0
ZOOM_STEP = 1.1
DRAG_THRESHOLD = 8          # 超过该像素才判定为「拖动排序」
PAN_PAD = 260               # scene 比视口多出的可平移余量
FREE = 900                  # 越界留白：内容四周可自由移出的空白，取消顶部/左边界硬限制

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv")

TITLE = "ScreenSnap · 截图历史库"
TIP_TEXT = ("滚轮=缩放   空白拖动=平移画布   单击=选中    Shift=多选    "
            "按住卡片拖动=调整顺序    Delete=删除(回收站)    双击=查看大图")
EMPTY_HINT = "还没有任何截图\n按热键截一张，或把图片放进存档目录后按 F5 刷新"

_WEEK = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


# ======================================================================
# 一、数据侧：入库同步（不改 storage.py，只调它的公开接口）
# ======================================================================
CAPACITY_CHOICES = (5, 7, 10)


def _norm_capacity(value, default: int = 7) -> int:
    """把配置里的列容量归一到 5 / 7 / 10 中最接近的一档。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n in CAPACITY_CHOICES else default


def _open_storage(save_root: str | None = None, trash_days: int = 3):
    """构造 storage.Storage（会顺带执行 cleanup_trash）。"""
    root = save_root or appconfig.load_config().get("save_root") \
        or appconfig.DEFAULT_SAVE_ROOT
    try:
        days = int(trash_days or 3)
    except Exception:
        days = 3
    return appstorage.Storage(root, days)


def _thumb_path_for(path: str) -> str:
    suffix = getattr(appstorage, "THUMB_SUFFIX", "_thumb")
    stem, _ext = os.path.splitext(path)
    return f"{stem}{suffix}.png"


def _guess_date(path: str) -> str:
    """优先用所在目录名（YYYY-MM-DD），否则用文件修改时间。"""
    parent = os.path.basename(os.path.dirname(path))
    if len(parent) == 10 and parent[4] == "-" and parent[7] == "-":
        try:
            datetime.strptime(parent, "%Y-%m-%d")
            return parent
        except Exception:
            pass
    try:
        ts = os.path.getmtime(path)
    except Exception:
        ts = time.time()
    return appstorage.date_dir_name(ts)


def register_capture(path: str, storage_obj=None, save_root: str | None = None) -> bool:
    """把一张已落盘的截图登记进 ``gallery.json``（含缩略图）。

    供 ``main_qt.py`` 在截图保存成功后调用；P3 的落盘命名（``ScreenSnap_*.png``）
    与目录结构（存档根目录）保持不变，只在背后补一条历史记录。
    """
    try:
        if not path or not os.path.isfile(path):
            return False
        st = storage_obj or _open_storage(save_root)
        items = st.load_gallery() or []
        target = os.path.normcase(os.path.abspath(path))
        for it in items:
            if os.path.normcase(os.path.abspath(str(it.get("path", "")))) == target:
                return False                      # 已登记
        thumb = _thumb_path_for(path)
        if not os.path.exists(thumb):
            with Image.open(path) as im:
                st.make_thumb(im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im,
                              thumb)
        ts = int(os.path.getmtime(path))
        items.append({
            "name": os.path.basename(path),
            "date": _guess_date(path),
            "path": os.path.abspath(path),
            "thumb": thumb,
            "ts": ts,
            "order": len(items),
        })
        st.save_gallery(items)
        return True
    except Exception:
        return False


def _iter_candidate_files(save_root: str):
    """遍历存档目录下可能未登记的图片（根目录散图 + YYYY-MM-DD 子目录）。"""
    try:
        entries = list(os.scandir(save_root))
    except Exception:
        return
    for e in entries:
        name = e.name
        if name.startswith(".") or name == getattr(appstorage, "RECORD_DIR", "录屏"):
            continue
        if e.is_file():
            if name.lower().endswith(IMAGE_EXTS) and "_thumb" not in name.lower():
                yield e.path
        elif e.is_dir() and len(name) == 10 and name[4] == "-" and name[7] == "-":
            try:
                for f in os.scandir(e.path):
                    ln = f.name.lower()
                    if f.is_file() and ln.endswith(IMAGE_EXTS) and "_thumb" not in ln:
                        yield f.path
            except Exception:
                continue


def sync_library(storage_obj) -> int:
    """收录「文件在、但 gallery.json 里没有」的图片，返回新增条数。"""
    added = 0
    try:
        for path in _iter_candidate_files(storage_obj.save_root):
            if register_capture(path, storage_obj=storage_obj):
                added += 1
    except Exception:
        pass
    return added


# ======================================================================
# 二、小工具
# ======================================================================
def _date_label(date_str: str) -> str:
    """``2026-10-30`` -> ``今天 · 2026-10-30 星期五``。"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return date_str or "未知日期"
    today = datetime.now().date()
    delta = (today - d).days
    week = _WEEK[d.weekday()]
    if delta == 0:
        return f"今天 · {date_str} {week}"
    if delta == 1:
        return f"昨天 · {date_str} {week}"
    return f"{date_str} {week}"


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _reveal_in_explorer(path: str) -> None:
    """在资源管理器中定位文件。"""
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except Exception:
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass


def _open_path(path: str) -> None:
    try:
        os.startfile(path)          # noqa: S606 - 交给系统默认程序
    except Exception as exc:
        dialogs.msg_error("打开失败", str(exc))


# ======================================================================
# 三、缩略图卡片
# ======================================================================
class _ThumbItem(QGraphicsPixmapItem):
    """单张缩略图卡片：圆角裁切 + 选中/悬停描边 + 视频角标。"""

    def __init__(self, path: str, w: int, h: int, kind: str = "image"):
        super().__init__()
        self.path = path
        self.card_w = max(4, int(w))
        self.card_h = max(3, int(h))
        self.kind = kind
        self.selected = False
        self.hovered = False
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)   # 交互统一交给 View
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setZValue(1)

    # --- 几何 ---
    def boundingRect(self) -> QRectF:      # noqa: N802 (Qt 命名)
        return QRectF(-2.0, -2.0, self.card_w + 4.0, self.card_h + 4.0)

    def shape(self):
        p = QPainterPath()
        p.addRoundedRect(QRectF(0, 0, self.card_w, self.card_h),
                         self._radius(), self._radius())
        return p

    def _radius(self) -> float:
        return _clamp(self.card_w * 0.035, 2.0, 8.0)

    # --- 绘制 ---
    def paint(self, p: QPainter, option, widget=None) -> None:  # noqa: N802
        w, h = float(self.card_w), float(self.card_h)
        r = self._radius()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), r, r)

        # 底板
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE))
        p.drawPath(card)

        # 图片（等比居中）
        p.save()
        p.setClipPath(card)
        pm = self.pixmap()
        if pm.isNull():
            p.fillPath(card, QColor(theme.SURFACE_3))
        else:
            x = (w - pm.width()) / 2.0
            y = (h - pm.height()) / 2.0
            p.drawPixmap(int(round(x)), int(round(y)), pm)
        p.restore()

        # 视频角标
        if self.kind == "video":
            cx, cy = w / 2.0, h / 2.0
            rad = _clamp(self.card_w * 0.09, 9.0, 20.0)
            p.setBrush(QColor(16, 22, 32, 150))
            p.setPen(QPen(QColor(255, 255, 255, 220), max(1.0, rad * 0.09)))
            p.drawEllipse(QPointF(cx, cy), rad, rad)
            tri = QPainterPath()
            tri.moveTo(cx - rad * 0.28, cy - rad * 0.42)
            tri.lineTo(cx + rad * 0.5, cy)
            tri.lineTo(cx - rad * 0.28, cy + rad * 0.42)
            tri.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawPath(tri)

        # 描边：选中 > 悬停 > 普通
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.selected:
            p.setPen(QPen(QColor(theme.ACCENT), 2.4))
        elif self.hovered:
            p.setPen(QPen(QColor(theme.INK_3), 1.4))
        else:
            p.setPen(QPen(QColor(theme.LINE), 1.0))
        p.drawPath(card)


# ======================================================================
# 四、画布视图
# ======================================================================
class GalleryView(QGraphicsView):
    """画布：把鼠标/滚轮/键盘事件转给 GalleryWindow 处理。"""

    def __init__(self, win: "GalleryWindow"):
        super().__init__(win)
        self.win = win
        self.setObjectName("GalleryView")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform
                            | QPainter.RenderHint.TextAntialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setBackgroundBrush(QColor(theme.CANVAS_DARK))
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

        self._panning = False
        self._pan_last = QPoint()
        self._blank_moved = False
        self._press: dict | None = None

    # ---------------- 鼠标 ----------------
    def mousePressEvent(self, event) -> None:      # noqa: N802
        pos = event.position().toPoint()
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            thumb = self.win.thumb_at(pos)
            if thumb is not None and btn == Qt.MouseButton.LeftButton:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self._press = {
                    "item": thumb,
                    "pos": pos,
                    "started": False,
                    "shift": bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
                }
                return
            # 空白 / 中键 -> 平移
            self._panning = True
            self._pan_last = pos
            self._blank_moved = False
            if btn == Qt.MouseButton.LeftButton:
                self._press = {"item": None}
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:       # noqa: N802
        pos = event.position().toPoint()
        if self._panning:
            d = pos - self._pan_last
            if d.x() or d.y():
                self._blank_moved = True
                self.win.pan_by(-d.x(), -d.y())
                self._pan_last = pos
            return
        press = self._press
        if press and press.get("item") is not None:
            if not press["started"] and (pos - press["pos"]).manhattanLength() >= DRAG_THRESHOLD:
                self.win.begin_drag(press["item"], pos)
                press["started"] = True
            if press["started"]:
                self.win.drag_to(pos)
            return
        # 悬停高亮
        thumb = self.win.thumb_at(pos)
        self.win.set_hover(thumb.path if thumb is not None else None)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:    # noqa: N802
        pos = event.position().toPoint()
        press = self._press
        self._press = None
        if press is not None:
            if press.get("item") is not None:
                thumb = press["item"]
                if press.get("started"):
                    self.win.end_drag(pos)
                else:
                    self.win.click_item(thumb.path, press.get("shift", False))
                return
            # 空白：没拖动 = 取消选择
            if self._panning and not self._blank_moved:
                self.win.clear_selection()
        if self._panning:
            self._panning = False
            self.unsetCursor()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:    # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            thumb = self.win.thumb_at(event.position().toPoint())
            if thumb is not None:
                self.win.open_viewer(thumb.path)
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:           # noqa: N802
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return
        self.win.zoom_at(event.position().toPoint(), 1 if delta > 0 else -1)
        event.accept()

    # ---------------- 键盘 ----------------
    def keyPressEvent(self, event) -> None:        # noqa: N802
        if self.win.handle_key(event):
            return
        super().keyPressEvent(event)

    # ---------------- 右键 ----------------
    def contextMenuEvent(self, event) -> None:     # noqa: N802
        thumb = self.win.thumb_at(event.pos())
        if thumb is not None and thumb.path not in self.win.selected:
            self.win.click_item(thumb.path, False)
        self.win.build_context_menu(thumb is not None).exec(event.globalPos())
        event.accept()


# ======================================================================
# 五、大图查看
# ======================================================================
def _ed():
    """惰性导入 editor：复用其图标 / 弹层 / 文字绘制，避免模块级循环依赖。"""
    from . import editor as _m
    return _m


def _arrow_head(x0: float, y0: float, x1: float, y1: float,
                w: float) -> list[tuple[float, float]] | None:
    """箭头头部三角（与截图页 editor 的算法一致）。"""
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 8:
        return None
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    hl = max(10.0, w * 3)
    return [(x1, y1),
            (x1 - ux * hl + px * hl * 0.55, y1 - uy * hl + py * hl * 0.55),
            (x1 - ux * hl - px * hl * 0.55, y1 - uy * hl - py * hl * 0.55)]


def _paint_shape(p: QPainter, s: dict, base_pm: QPixmap | None = None) -> None:
    """绘制单个标注对象（坐标为图像像素，视图缩放由 QGraphicsView 变换负责）。"""
    kind = s.get("kind")
    color = tuple(s.get("color") or (255, 59, 48))
    col = QColor(int(color[0]), int(color[1]), int(color[2]))
    w = max(1.0, float(s.get("width") or 4))

    if kind == "text":
        _ed()._paint_text_obj(
            p, {"x": s["x"], "y": s["y"], "txt": s["txt"],
                "color": color, "font_size": s["font_size"]}, 1.0)
        return

    if kind == "mosaic":
        if base_pm is None or base_pm.isNull():
            return
        r = QRectF(QPointF(s["x0"], s["y0"]), QPointF(s["x1"], s["y1"])).normalized()
        r = r.intersected(QRectF(base_pm.rect()))
        if r.width() < 2 or r.height() < 2:
            return
        src = base_pm.copy(r.toRect())
        bw = max(1, int(r.width()) // 8)
        bh = max(1, int(r.height()) // 8)
        small = src.scaled(bw, bh, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        block = small.scaled(max(1, int(r.width())), max(1, int(r.height())),
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        p.drawPixmap(r.topLeft(), block)
        return

    p.setPen(QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "pen":
        pts = list(s.get("pts") or [])
        if len(pts) == 1:
            p.setBrush(col)
            p.drawEllipse(QPointF(pts[0][0], pts[0][1]), w / 2, w / 2)
            return
        if len(pts) < 2:
            return
        path = QPainterPath(QPointF(pts[0][0], pts[0][1]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x, y))
        p.drawPath(path)
        return

    x0, y0, x1, y1 = s["x0"], s["y0"], s["x1"], s["y1"]
    if kind == "rect":
        p.drawRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
    elif kind == "ellipse":
        p.drawEllipse(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
    elif kind == "line":
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
    elif kind == "arrow":
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        head = _arrow_head(x0, y0, x1, y1, w)
        if head:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(a, b) for a, b in head]))


class _OverlayItem(QGraphicsItem):
    """标注覆盖层。``rect`` 覆盖整张图，但不接收鼠标（事件由 viewer 的 viewport 过滤器接管）。"""

    def __init__(self, viewer: "LargeViewer"):
        super().__init__()
        self._viewer = viewer
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(10)

    def boundingRect(self) -> QRectF:          # noqa: N802
        return QRectF(self._viewer._pm.rect()).adjusted(-8, -8, 8, 8)

    def paint(self, p: QPainter, option, widget=None) -> None:   # noqa: N802
        self._viewer._paint_overlay(p)


class LargeViewer(QWidget):
    """单张大图查看：滚轮缩放、拖拽平移，底部为与截图页同款的单排居中标注工具栏。"""

    def __init__(self, item: dict, storage_obj, parent=None, on_changed=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.item = dict(item or {})
        self.storage = storage_obj
        self.on_changed = on_changed
        path = str(self.item.get("path") or "")
        self.setWindowTitle(f"ScreenSnap · 查看大图 - {os.path.basename(path)}")
        self.setMinimumSize(660, 420)
        self.setStyleSheet(f"QWidget {{ background: {theme.CANVAS}; }}")

        self._path = path
        self._pm = QPixmap(path)
        self._fit_mode = True
        self._z = 1.0

        ed = _ed()
        # 标注状态：与截图页共享色板 / 线宽档位
        self.color = tuple(ed.PALETTE[0])
        self.line_width = 4
        self._tool: str | None = None
        self._shapes: list[dict] = []
        self._draft: dict | None = None
        self._tool_btns: dict[str, QToolButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame(self)
        bar.setObjectName("ViewerBar")
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            f"QFrame#ViewerBar {{ background: {theme.SURFACE};"
            f" border-bottom: 1px solid {theme.LINE}; }}")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(10)
        self._name = QLabel(os.path.basename(path), bar)
        self._name.setFont(theme.font(theme.FONT_BODY_SIZE, bold=True))
        self._name.setStyleSheet(f"color: {theme.INK};")
        hl.addWidget(self._name)
        self._meta = QLabel(self._meta_text(), bar)
        self._meta.setStyleSheet(f"color: {theme.INK_3}; font-size: 9pt;")
        hl.addWidget(self._meta)
        hl.addStretch(1)
        self._hint = QLabel("滚轮=缩放   拖拽=平移   双击=适应/100%   Ctrl+P=钉在原位   Esc=关闭", bar)
        self._hint.setStyleSheet(f"color: {theme.INK_3}; font-size: 9pt;")
        hl.addWidget(self._hint)
        hl.addSpacing(4)
        self._zoom_label = QLabel("", bar)
        self._zoom_label.setStyleSheet(f"color: {theme.INK_2}; font-size: 9pt;")
        hl.addWidget(self._zoom_label)
        root.addWidget(bar)

        self._scene = QGraphicsScene(self)
        self._view = QGraphicsView(self._scene, self)
        self._view.setObjectName("ViewerView")
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform
                                  | QPainter.RenderHint.Antialiasing)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._view.setBackgroundBrush(QColor(theme.CANVAS_DARK))
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.wheelEvent = self._on_view_wheel          # 借用实例级替换，简化子类
        self._view.mouseDoubleClickEvent = lambda e: self._on_view_double()
        self._view.viewport().installEventFilter(self)       # 标注绘制：接管 viewport 鼠标
        root.addWidget(self._view, 1)

        self._pix_item = QGraphicsPixmapItem(self._pm)
        self._pix_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._pix_item.setZValue(0)
        self._scene.addItem(self._pix_item)
        self._overlay = _OverlayItem(self)
        self._scene.addItem(self._overlay)
        if not self._pm.isNull():
            self._scene.setSceneRect(QRectF(self._pm.rect()))

        # ---------------- 底部：单排居中图标工具栏（与截图页同款） ----------------
        foot = QFrame(self)
        foot.setObjectName("ViewerFoot")
        foot.setFixedHeight(48)
        foot.setStyleSheet(
            f"QFrame#ViewerFoot {{ background: {theme.SURFACE};"
            f" border-top: 1px solid {theme.LINE}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 8, 10, 8)
        fl.setSpacing(0)
        fl.addStretch(1)

        strip = QWidget(foot)
        strip.setObjectName("ViewerStrip")
        strip.setFixedHeight(ed.TOOLBAR_H)
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)

        for i, tool in enumerate(ed.TOOLS, 1):
            b = self._tbtn(ed.tool_icon(tool), f"{ed.TOOL_NAMES[tool]}（快捷键 {i}）",
                           strip, checkable=True)
            b.setObjectName(f"ViewerTool_{tool}")
            b.clicked.connect(lambda _=False, t=tool:
                              self.set_tool(None if self._tool == t else t))
            self._tool_btns[tool] = b
            sl.addWidget(b)

        sl.addSpacing(8)
        sl.addWidget(self._sep(strip))
        sl.addSpacing(8)

        self._color_btn = self._tbtn(QIcon(ed._color_dot_pixmap(self.color)),
                                     "颜色（点击展开选择）", strip)
        self._color_btn.setObjectName("ViewerAct_color")
        self._color_btn.clicked.connect(self._open_color)
        sl.addWidget(self._color_btn)

        self._width_btn = self._tbtn(ed.tool_icon("width"), "线条粗细", strip)
        self._width_btn.setObjectName("ViewerAct_width")
        self._width_btn.clicked.connect(self._open_width)
        sl.addWidget(self._width_btn)

        sl.addSpacing(8)
        sl.addWidget(self._sep(strip))
        sl.addSpacing(8)

        self._undo_btn = self._tbtn(ed.tool_icon("undo"), "撤销上一步（Ctrl+Z）", strip)
        self._undo_btn.setObjectName("ViewerAct_undo")
        self._undo_btn.clicked.connect(lambda: self.undo())
        sl.addWidget(self._undo_btn)

        sl.addSpacing(8)
        sl.addWidget(self._sep(strip))
        sl.addSpacing(8)

        self._btn_copy = self._tbtn(ed.tool_icon("copy"), "复制图片（Ctrl+C）", strip)
        self._btn_copy.setObjectName("ViewerAct_copy")
        self._btn_copy.clicked.connect(self.copy_image)
        sl.addWidget(self._btn_copy)

        self._btn_pin = self._tbtn(ed.tool_icon("pin"), "贴图：钉在原位（Ctrl+P，不进历史库）", strip)
        self._btn_pin.setObjectName("ViewerAct_pin")
        self._btn_pin.clicked.connect(self.pin_image)
        sl.addWidget(self._btn_pin)

        self._btn_save = self._tbtn(ed.tool_icon("save"), "另存为…", strip)
        self._btn_save.setObjectName("ViewerAct_save")
        self._btn_save.clicked.connect(self.save_as)
        sl.addWidget(self._btn_save)

        self._btn_reveal = self._tbtn(ed.tool_icon("folder"), "打开所在文件夹", strip)
        self._btn_reveal.setObjectName("ViewerAct_reveal")
        self._btn_reveal.clicked.connect(lambda: _reveal_in_explorer(self._path))
        sl.addWidget(self._btn_reveal)

        self._btn_del = self._tbtn(ed.tool_icon("delete"), "移入回收站", strip)
        self._btn_del.setObjectName("ViewerAct_delete")
        self._btn_del.clicked.connect(self.delete_item)
        sl.addWidget(self._btn_del)

        self._btn_ocr = self._tbtn(ed.tool_icon("ocr"),
                                   "识别文字：提取标注后的整图文字（Ctrl+T）", strip)
        self._btn_ocr.setObjectName("ViewerAct_ocr")
        self._btn_ocr.clicked.connect(self.ocr_text)
        sl.addWidget(self._btn_ocr)

        self._btn_close = self._tbtn(ed.tool_icon("close"), "关闭（Esc）", strip)
        self._btn_close.setObjectName("ViewerAct_close")
        self._btn_close.clicked.connect(self.close)
        sl.addWidget(self._btn_close)

        fl.addWidget(strip, 0, Qt.AlignmentFlag.AlignCenter)
        fl.addStretch(1)
        root.addWidget(foot)

        QShortcut(QKeySequence("Ctrl+C"), self, activated=self.copy_image)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_as)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.pin_image)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.ocr_text)
        QShortcut(QKeySequence("Esc"), self, activated=self.close)

        size = self._screen_size()
        self.resize(size)
        QTimer.singleShot(0, self._fit)

    # ---------------- 工具栏辅助 ----------------
    def _tbtn(self, icon: QIcon, tip: str, parent: QWidget,
              checkable: bool = False) -> QToolButton:
        ed = _ed()
        b = QToolButton(parent)
        b.setFixedSize(ed.TOOL_BTN, ed.TOOL_BTN)
        b.setIcon(icon)
        b.setIconSize(QSize(20, 20))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCheckable(checkable)
        b.setStyleSheet(
            "QToolButton{border:none;background:transparent;border-radius:12px;}"
            "QToolButton:hover{background:%s;}"
            "QToolButton:checked{background:%s;}" % (theme.SURFACE_3, theme.ACCENT_SOFT))
        ed.Tooltip(b, tip)
        b.setToolTip(tip)          # 原生 tooltip 兜底（无障碍/自动化可读）
        return b

    def _sep(self, parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setFixedSize(1, 18)
        line.setStyleSheet(f"background:{theme.LINE};border:none;")
        return line

    def _open_color(self):
        ed = _ed()
        pop = ed.ColorPopup(self)
        pop.move(self._popup_pos(self._color_btn, pop))
        pop.show()

    def _open_width(self):
        ed = _ed()
        pop = ed.WidthPopup(self)
        pop.move(self._popup_pos(self._width_btn, pop))
        pop.show()

    def _popup_pos(self, btn: QWidget | None, pop: QWidget) -> QPoint:
        area = geometry.virtual_rect_logical()
        if btn is None:
            return self.mapToGlobal(QPoint(0, -pop.height() - 6))
        anchor = btn.mapToGlobal(QPoint(btn.width() // 2 - pop.width() // 2, 0))
        gp = QPoint(anchor.x(), anchor.y() - pop.height() - 6)   # 工具栏在底部，弹层向上开
        gp.setX(max(area.left() + 4, min(gp.x(), area.right() - pop.width() - 4)))
        gp.setY(max(area.top() + 4, gp.y()))
        return gp

    # ---------------- 标注状态 ----------------
    def set_tool(self, tool: str | None) -> None:
        if tool and self._pm.isNull():
            dialogs.msg_error("无法标注", "图片无法读取。", parent=self)
            return
        self._tool = tool
        for t, b in self._tool_btns.items():
            b.setChecked(t == tool)
        self._draft = None
        if tool is None:
            self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self._view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._overlay.update()

    def set_color(self, rgb, *args) -> None:
        self.color = tuple(int(v) for v in tuple(rgb)[:3])
        self._color_btn.setIcon(QIcon(_ed()._color_dot_pixmap(self.color)))

    def set_width(self, w: int, name: str | None = None) -> None:
        self.line_width = int(w)

    def undo(self) -> None:
        if self._draft is not None:
            self._draft = None
        elif self._shapes:
            self._shapes.pop()
        else:
            return
        self._overlay.update()

    def has_marks(self) -> bool:
        return bool(self._shapes)

    # ---------------- 绘制 ----------------
    def _paint_overlay(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for s in self._shapes:
            _paint_shape(p, s, self._pm)
        if self._draft is not None:
            _paint_shape(p, self._draft, self._pm)

    def _composite_pixmap(self) -> QPixmap:
        """原图副本 + 标注合成（不改动 self._pm，重复导出不会重影）。"""
        if self._pm.isNull():
            return self._pm
        pm = QPixmap(self._pm.size())
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._pm)
        for s in self._shapes:
            _paint_shape(p, s, self._pm)
        p.end()
        return pm

    # ---------------- 鼠标：标注绘制 ----------------
    def eventFilter(self, obj, event):        # noqa: N802
        if obj is self._view.viewport():
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and \
                    event.button() == Qt.MouseButton.LeftButton:
                return self._on_press(event)
            if et == QEvent.Type.MouseMove:
                return self._on_move(event)
            if et == QEvent.Type.MouseButtonRelease and \
                    event.button() == Qt.MouseButton.LeftButton:
                return self._on_release(event)
        return super().eventFilter(obj, event)

    def _img_pos(self, event) -> QPointF:
        sp = self._view.mapToScene(event.position().toPoint())
        if self._pm.isNull():
            return sp
        return QPointF(min(max(sp.x(), 0.0), float(self._pm.width())),
                       min(max(sp.y(), 0.0), float(self._pm.height())))

    def _on_press(self, event) -> bool:
        if self._tool is None:
            return False                      # 未选工具：保留原拖拽平移
        pt = self._img_pos(event)
        kind = self._tool
        if kind == "text":
            QTimer.singleShot(0, lambda p=QPointF(pt): self._ask_text(p))
            return True
        if kind == "pen":
            self._draft = {"kind": "pen", "pts": [(pt.x(), pt.y())],
                           "color": self.color, "width": self.line_width}
        else:
            self._draft = {"kind": kind, "x0": pt.x(), "y0": pt.y(),
                           "x1": pt.x(), "y1": pt.y(),
                           "color": self.color, "width": self.line_width}
        self._overlay.update()
        return True

    def _on_move(self, event) -> bool:
        if self._draft is None:
            return False
        pt = self._img_pos(event)
        if self._draft["kind"] == "pen":
            pts = self._draft["pts"]
            lx, ly = pts[-1]
            if abs(pt.x() - lx) >= 1.5 or abs(pt.y() - ly) >= 1.5:
                pts.append((pt.x(), pt.y()))
        else:
            self._draft["x1"] = pt.x()
            self._draft["y1"] = pt.y()
        self._overlay.update()
        return True

    def _on_release(self, event) -> bool:
        d = self._draft
        if d is None:
            return False
        self._draft = None
        keep = True
        if d["kind"] == "pen":
            keep = len(d["pts"]) >= 2
        else:
            keep = (abs(d["x1"] - d["x0"]) >= 3 or abs(d["y1"] - d["y0"]) >= 3)
        if keep:
            self._shapes.append(d)
        self._overlay.update()
        return True

    def _ask_text(self, pt: QPointF) -> None:
        txt, ok = QInputDialog.getText(self, "添加文字", "文字内容：")
        if not ok or not str(txt).strip():
            return
        size = max(12, int(self.line_width) * 4)
        y = min(pt.y(), max(0.0, self._pm.height() - size * 1.4))
        self._shapes.append({"kind": "text", "x": pt.x(), "y": y,
                             "txt": str(txt), "color": self.color,
                             "font_size": size})
        self._overlay.update()

    # ---------------- 视图辅助 ----------------
    def _meta_text(self) -> str:
        if self.item.get("kind") == "video":
            return f"录屏 · {self.item.get('date', '')}"
        if self._pm.isNull():
            return "图片无法加载"
        return f"{self._pm.width()} × {self._pm.height()} · {self.item.get('date', '')}"

    def _screen_size(self) -> QSize:
        scr = QApplication.primaryScreen()
        area = scr.availableGeometry() if scr else None
        w = self._pm.width() or 900
        h = self._pm.height() or 600
        max_w = int((area.width() if area else 1440) * 0.86)
        max_h = int((area.height() if area else 900) * 0.84)
        return QSize(min(max(720, w + 40), max(720, max_w)),
                     min(max(520, h + 130), max(520, max_h)))

    def _fit(self) -> None:
        self._fit_mode = True
        if self._pm.isNull():
            return
        vp = self._view.viewport().size()
        if vp.width() < 20 or vp.height() < 20:
            QTimer.singleShot(30, self._fit)
            return
        z = min(vp.width() / self._pm.width(), vp.height() / self._pm.height())
        z = _clamp(z, 0.05, 4.0)
        self._apply_zoom(z)
        self._view.centerOn(self._pix_item)

    def _apply_zoom(self, z: float) -> None:
        self._z = _clamp(z, 0.05, 8.0)
        self._view.setTransform(QTransform.fromScale(self._z, self._z))
        self._zoom_label.setText(f"{int(round(self._z * 100))}%")

    def _toggle_fit(self) -> None:
        if self._fit_mode:
            self._fit_mode = False
            self._apply_zoom(1.0)
            self._view.centerOn(self._pix_item)
        else:
            self._fit()

    def _on_view_double(self) -> None:
        if self._tool is not None:
            return                            # 标注中双击不改变缩放，避免误触
        self._toggle_fit()

    def _on_view_wheel(self, event) -> None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return
        self._fit_mode = False
        anchor = self._view.transformationAnchor()
        self._view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self._apply_zoom(self._z * factor)
        self._view.setTransformationAnchor(anchor)
        event.accept()

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            QTimer.singleShot(0, self._fit)

    # ---------------- 动作 ----------------
    def copy_image(self) -> None:
        try:
            if self._pm.isNull():
                raise RuntimeError("图片无法读取")
            pm = self._composite_pixmap()
            ok = clipboard.copy_pixmap(pm)
        except Exception as exc:
            dialogs.msg_error("复制失败", str(exc), parent=self)
            return
        if not ok:
            dialogs.msg_error("复制失败", "剪贴板不可用。", parent=self)

    def _pin_anchor(self) -> tuple[QPoint, tuple[int, int] | None]:
        """大图「钉住」的落点：图片在大图里的屏幕左上角（逻辑坐标）
        与当前显示尺寸（物理像素）。取不到时退回窗口左上角。
        """
        try:
            vp = self._view.mapFromScene(self._pix_item.scenePos())
            gp = self._view.viewport().mapToGlobal(vp)
            dpr = float(self._view.viewport().devicePixelRatioF() or 1.0)
            z = float(self._z or 1.0)
            w = max(1, int(round(self._pm.width() * z * dpr)))
            h = max(1, int(round(self._pm.height() * z * dpr)))
            # 兜底：图像被平移到屏幕外时，至少保证钉图落在可见区域边缘
            try:
                area = geometry.virtual_rect_logical()
                gx = max(area.left(), min(gp.x(), area.right() - 80))
                gy = max(area.top(), min(gp.y(), area.bottom() - 80))
                gp = QPoint(gx, gy)
            except Exception:
                pass
            return QPoint(gp.x(), gp.y()), (w, h)
        except Exception:
            return self.mapToGlobal(QPoint(0, 0)), None

    def pin_image(self) -> None:
        """钉住当前（含标注）画面：贴回**大图里图片所在的位置**，不进历史库。"""
        if self._pm.isNull():
            dialogs.msg_error("无法贴图", "图片无法读取。", parent=self)
            return
        img = qpixmap_to_pil(self._composite_pixmap())
        if img is None:
            dialogs.msg_error("无法贴图", "图片转换失败。", parent=self)
            return
        # 落点与尺寸都取"大图当前呈现的样子"：位置不动、大小不变，
        # 钉住就像把大图里那张图原地摘下来贴在屏幕上。
        anchor, init_size = self._pin_anchor()
        try:
            from . import sticker_qt
            sticker_qt.open_sticker(img, anchor.x(), anchor.y(),
                                    title=os.path.basename(self._path) or "大图标注",
                                    align_tl=True, init_size=init_size)
        except Exception as exc:
            dialogs.msg_error("无法贴图", str(exc), parent=self)
            return
        # 贴图（盯住）成功后再收起当前大图窗口，避免大图压住刚钉上的贴图
        self.close()

    def ocr_text(self) -> None:
        """P9：对当前大图（含标注）做文字识别。"""
        from . import ocr_result_qt
        if self.item.get("kind") == "video":
            dialogs.msg_error("无法识别", "视频不支持文字识别。", parent=self)
            return
        # 点击瞬间先做引擎守门：缺引擎立刻弹提示并中止，不等识别窗建起来才报
        if not ocr_result_qt.gate_entry(None, owner=self)[0]:
            return
        img = qpixmap_to_pil(self._composite_pixmap())
        if img is None:
            try:
                with Image.open(self._path) as im:
                    img = im.convert("RGB")
            except Exception as exc:
                dialogs.msg_error("无法识别", "图片无法读取：%s" % exc, parent=self)
                return
        ocr_result_qt.open_ocr(img, item=self.item, storage=self.storage)

    def save_as(self) -> None:
        if self._pm.isNull():
            dialogs.msg_error("另存为失败", "图片无法读取。", parent=self)
            return
        ext = os.path.splitext(self._path)[1].lstrip(".").lower() or "png"
        out = dialogs.ask_save_file(
            "另存为", initialdir=os.path.dirname(self._path),
            initialfile=os.path.basename(self._path),
            filetypes=[("图片", f"*.{ext}")], default_ext=ext, parent=self)
        if not out:
            return
        try:
            if not self._shapes:
                import shutil
                shutil.copy2(self._path, out)
                return
            # 有标注：导出合成图（原图副本 + 标注），源文件保持不变
            if not self._composite_pixmap().save(out):
                dialogs.msg_error("另存为失败", "无法写入目标文件。", parent=self)
        except Exception as exc:
            dialogs.msg_error("另存为失败", str(exc), parent=self)

    def delete_item(self) -> None:
        if self.item.get("kind") == "video":
            tip = "将从历史库移除该录屏记录（文件也会移入回收站）"
        else:
            tip = "将移入回收站，可在存档目录 .trash 内找回"
        days = getattr(self.storage, "trash_days", 3)
        if not dialogs.msg_confirm(
                "删除到回收站", f"确定删除「{os.path.basename(self._path)}」？",
                parent=self,
                detail=f"{tip}；{days} 天后自动清除。",
                ok_text="移入回收站", cancel_text="取消", danger=True):
            return
        try:
            n = self.storage.delete_to_trash([self.item])
        except Exception as exc:
            dialogs.msg_error("删除失败", str(exc), parent=self)
            return
        if callable(self.on_changed):
            self.on_changed()
        if n:
            self.close()
        else:
            dialogs.msg_warn("未删除", "文件可能已被移动或删除。", parent=self)

    def keyPressEvent(self, event) -> None:        # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._apply_zoom(self._z * ZOOM_STEP)
            return
        if event.key() == Qt.Key.Key_Minus:
            self._apply_zoom(self._z / ZOOM_STEP)
            return
        if event.key() == Qt.Key.Key_0:
            self._fit()
            return
        super().keyPressEvent(event)


# ======================================================================
# 六、历史库主窗口
# ======================================================================
class GalleryWindow(QWidget):
    """历史库窗口（单例）。"""

    _active: "GalleryWindow | None" = None

    def __init__(self, parent=None, cfg: dict | None = None,
                 storage_obj=None, save_root: str | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self._cfg = dict(cfg or appconfig.load_config() or {})
        self._save_root = save_root or str(self._cfg.get("save_root")
                                          or appconfig.DEFAULT_SAVE_ROOT)
        self._trash_days = int(self._cfg.get("trash_days") or 3)
        self.storage = storage_obj or _open_storage(self._save_root, self._trash_days)
        self._save_root = self.storage.save_root
        self.capacity = _norm_capacity(self._cfg.get("column_capacity"))

        # 数据
        self.items: list[dict] = []
        self._item_map: dict[str, dict] = {}
        self._by_date: dict[str, list[dict]] = {}
        self._groups: list[dict] = []
        self._meta: dict[str, dict] = {}      # path -> {wx, wy, gi}
        self.selected: set[str] = set()
        self._last_clicked: str | None = None
        self._viewer = None                  # 大图查看窗
        self._player = None                  # 录屏内置播放窗（P6）

        # 渲染状态
        self._scale = 1.0
        self._tw = THUMB_W
        self._th = THUMB_H
        self._pool: dict[str, _ThumbItem] = {}
        self._header_pool: dict[int, QGraphicsSimpleTextItem] = {}
        self._pix_cache: dict[tuple, QPixmap] = {}
        self._content_w = MARGIN + CELL_W
        self._content_h = MARGIN + CELL_H
        self._rows_used = 0                  # 当前「每列行数」（随视口高度自适应）
        self._syncing = False

        # 拖拽排序
        self._drag: dict | None = None
        self._floating: QGraphicsPixmapItem | None = None
        self._marker: QGraphicsRectItem | None = None
        self._drag_target: str | None = None
        self._drag_src_item: _ThumbItem | None = None

        self.setWindowTitle(TITLE)
        self.setStyleSheet(f"QWidget#GalleryRoot {{ background: {theme.CANVAS}; }}")
        self.resize(1140, 760)
        self.setMinimumSize(780, 540)
        self._build_ui()

        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(16)
        self._sync_timer.timeout.connect(self._sync_visible)

        GalleryWindow._active = self
        self.reload(focus_today=True)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame(self)
        bar.setObjectName("GalleryBar")
        bar.setFixedHeight(42)
        bar.setStyleSheet(f"QFrame#GalleryBar {{ background: {theme.SURFACE};"
                          f" border-bottom: 1px solid {theme.LINE}; }}")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(14, 0, 12, 0)
        hl.setSpacing(8)

        tip = QLabel(TIP_TEXT, bar)
        tip.setStyleSheet(f"color: {theme.INK_3}; font-size: 9pt;")
        hl.addWidget(tip)
        hl.addStretch(1)

        self._status = QLabel("", bar)
        self._status.setStyleSheet(f"color: {theme.INK_2}; font-size: 9pt;")
        hl.addWidget(self._status)

        for text, slot, name in (
                ("刷新", lambda: self.reload(keep_view=True), "Ghost"),
                ("打开存档目录", self.open_save_dir, "Ghost"),
                ("回收站", self.open_trash, "Ghost")):
            b = QPushButton(text, bar)
            b.setObjectName(name)
            b.setFixedHeight(26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            hl.addWidget(b)
        root.addWidget(bar)

        self._scene = QGraphicsScene(self)
        self.view = GalleryView(self)
        self.view.setScene(self._scene)
        self.view.horizontalScrollBar().valueChanged.connect(self.schedule_sync)
        self.view.verticalScrollBar().valueChanged.connect(self.schedule_sync)
        root.addWidget(self.view, 1)

        self._empty = QGraphicsSimpleTextItem(EMPTY_HINT)
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(14)
        self._empty.setFont(f)
        self._empty.setBrush(QColor(theme.INK_3))
        self._scene.addItem(self._empty)
        self._empty.setVisible(False)

    # ---------------- 生命周期 ----------------
    def closeEvent(self, event) -> None:           # noqa: N802
        if GalleryWindow._active is self:
            GalleryWindow._active = None
        super().closeEvent(event)

    def showEvent(self, event) -> None:            # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._first_show_sync)

    def _first_show_sync(self) -> None:
        self._relayout_for_view()
        self._apply_geometry()
        if not getattr(self, "_focused_once", False):
            self._focused_once = True
            self.focus_date(self._latest_date_key())

    def _relayout_for_view(self) -> None:
        """视口尺寸变化后重算「每列行数」，行数变了就重排列布局。"""
        if self._meta and self._rows_per_col() != self._rows_used:
            self._build_index()

    def _relayout_after_layout(self) -> None:
        """控件完成布局后再校验一次：首次 show 时视口高度可能尚未生效。"""
        if not self._meta:
            return
        if self._rows_per_col() != self._rows_used:
            self._build_index()
            self._apply_geometry()
            self.schedule_sync()

    def resizeEvent(self, event) -> None:          # noqa: N802
        super().resizeEvent(event)
        self._relayout_for_view()
        self._scene.setSceneRect(self._scene_rect())
        self.schedule_sync()
        QTimer.singleShot(0, self._relayout_after_layout)

    # ---------------- 数据加载 ----------------
    def reload(self, focus_today: bool = False, keep_view: bool = False) -> None:
        anchor = None
        if keep_view:
            anchor = self._world_anchor()
        try:
            self.storage.trash_days = self._trash_days
        except Exception:
            pass
        try:
            self.storage.cleanup_trash()
            self.storage.scan_videos()
        except Exception:
            pass
        sync_library(self.storage)

        items: list[dict] = []
        try:
            raw = self.storage.load_gallery() or []
        except Exception:
            raw = []
        for it in raw:
            path = str(it.get("path") or "")
            if path and os.path.exists(path):
                items.append(it)
        items.sort(key=lambda d: (str(d.get("date") or ""), int(d.get("order") or 0)),
                   reverse=False)
        # 日期升序：旧的一天在左，新的一天往右，符合从左到右的阅读习惯
        items.sort(key=lambda d: str(d.get("date") or ""), reverse=False)
        self.items = items
        self._item_map = {str(it.get("path")): it for it in items}
        self.selected = {p for p in self.selected if p in self._item_map}
        self._build_groups()
        self._build_index()
        self._apply_geometry()
        if anchor is not None:
            self._restore_world_anchor(anchor)
        if focus_today:
            self.focus_date(self._latest_date_key())
        self._update_status()

    def _build_groups(self) -> None:
        """按日期聚合（保持首次出现顺序，同日期只出一个分组）。"""
        self._by_date = {}
        for it in self.items:
            key = str(it.get("date") or _guess_date(str(it.get("path") or "")))
            it["date"] = key
            self._by_date.setdefault(key, []).append(it)

    def _rows_per_col(self) -> int:
        """每列最多放几张：按视口高度自适应，让一天的分组尽量一屏看全。

        整体排布约定为「日期分组从左往右排；同一天内从上到下填满一列，
        再往右开新列」，因此纵向行数压到一屏能容纳的数量，浏览时主要左右平移。
        """
        try:
            vh = self.view.viewport().height()
        except Exception:
            vh = 0
        # 竖列张数严格按设置档位（5 / 7 / 10），不再随视口高度缩水；
        # 窗口矮于一列高度时整体上下滚动，保证分组内行数恒定。
        return max(1, int(self.capacity))

    def _build_index(self) -> None:
        """计算每张卡片、每个日期标题在「世界坐标」里的位置（未乘缩放）。"""
        self._meta = {}
        self._groups = []
        rows = self._rows_per_col()
        self._rows_used = rows
        x = MARGIN
        max_y = MARGIN
        for date_key, group_items in self._by_date.items():
            n = len(group_items)
            cols = max(1, math.ceil(n / rows))
            gx, gy = x, MARGIN
            self._groups.append({"date": date_key, "x": gx, "y": gy,
                                 "cols": cols, "rows": rows, "count": n})
            gi = len(self._groups) - 1
            for i, it in enumerate(group_items):
                c, r = divmod(i, rows)      # 先上下填满一列，再往右一列
                wx = gx + c * CELL_W
                wy = gy + GROUP_H + r * CELL_H
                self._meta[str(it.get("path"))] = {"wx": wx, "wy": wy, "gi": gi}
                max_y = max(max_y, wy + THUMB_H)
            x += cols * CELL_W + GROUP_GAP
        self._content_w = max(x - GROUP_GAP + MARGIN, MARGIN + CELL_W)
        self._content_h = max(max_y + MARGIN, MARGIN + CELL_H)

    # ---------------- 几何 / 渲染 ----------------
    def _so(self) -> float:
        """内容世界原点在场景中的偏移（PAD + 越界留白）。"""
        return PAD + FREE

    def _scene_rect(self) -> QRectF:
        vp = self.view.viewport().size()
        # 四周留白对称：内容右/下侧补上与左/上侧相同的 PAD + FREE，避免右侧被 26px 卡住
        w = max(self._content_w * self._scale + PAD * 2 + FREE * 2, vp.width() + PAN_PAD * 2)
        h = max(self._content_h * self._scale + PAD * 2 + FREE * 2, vp.height() + PAN_PAD * 2)
        return QRectF(-FREE, -FREE, w + FREE * 2, h + FREE * 2)

    def _scene_pos(self, path: str) -> tuple[float, float] | None:
        m = self._meta.get(path)
        if not m:
            return None
        return (m["wx"] * self._scale + self._so(), m["wy"] * self._scale + self._so())

    def _apply_geometry(self) -> None:
        """缩放到「世界坐标 -> 场景坐标」的换算，并重建可见内容。"""
        self._tw = max(4, int(round(THUMB_W * self._scale)))
        self._th = max(3, int(round(THUMB_H * self._scale)))
        self._pix_cache.clear()
        for it in self._pool.values():
            self._scene.removeItem(it)
        self._pool.clear()
        for t in self._header_pool.values():
            self._scene.removeItem(t)
        self._header_pool.clear()
        self._scene.setSceneRect(self._scene_rect())
        self._sync_visible()
        self._update_status()

    def _pixmap_for(self, item: dict) -> QPixmap:
        key = (str(item.get("path")), self._tw)
        cached = self._pix_cache.get(key)
        if cached is not None:
            return cached
        pm = QPixmap()
        try:
            tp = self.storage.thumb_for(item)
            if tp:
                pm = QPixmap(tp)
        except Exception:
            pm = QPixmap()
        if pm.isNull():
            try:
                pm = QPixmap(str(item.get("path") or ""))
            except Exception:
                pm = QPixmap()
        if not pm.isNull():
            pm = pm.scaled(self._tw, self._th,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        else:
            pm = QPixmap(self._tw, self._th)
            pm.fill(QColor(theme.SURFACE_3))
        self._pix_cache[key] = pm
        return pm

    def _sync_visible(self) -> None:
        """只创建视口内（外扩 200px）的卡片与标题。"""
        if self._syncing or not self._meta:
            if not self._meta:
                self._show_empty(bool(self._empty))
            return
        self._syncing = True
        try:
            vp = self.view.viewport().rect()
            vis = self.view.mapToScene(vp).boundingRect().adjusted(-200, -200, 200, 200)

            alive: set[str] = set()
            for path, m in self._meta.items():
                x = m["wx"] * self._scale + self._so()
                y = m["wy"] * self._scale + self._so()
                if QRectF(x, y, self._tw, self._th).intersects(vis):
                    alive.add(path)

            for path in [p for p in self._pool if p not in alive]:
                self._scene.removeItem(self._pool.pop(path))

            for path in alive:
                if path in self._pool:
                    continue
                pos = self._scene_pos(path)
                if pos is None:
                    continue
                data = self._item_map.get(path, {})
                thumb = _ThumbItem(path, self._tw, self._th,
                                   str(data.get("kind") or "image").lower())
                thumb.setPixmap(self._pixmap_for(data))
                thumb.setPos(QPointF(pos[0], pos[1]))
                thumb.selected = path in self.selected
                self._scene.addItem(thumb)
                self._pool[path] = thumb

            # 日期标题
            alive_g: set[int] = set()
            for gi, g in enumerate(self._groups):
                gx = g["x"] * self._scale + self._so()
                gy = g["y"] * self._scale + self._so()
                gh = GROUP_H * self._scale
                gw = g["cols"] * CELL_W * self._scale
                if QRectF(gx, gy, gw, gh).intersects(vis):
                    alive_g.add(gi)

            for gi in [i for i in self._header_pool if i not in alive_g]:
                self._scene.removeItem(self._header_pool.pop(gi))

            for gi in alive_g:
                if gi in self._header_pool:
                    continue
                g = self._groups[gi]
                text = f"{_date_label(g['date'])}    {g['count']} 张"
                t = QGraphicsSimpleTextItem(text)
                f = QFont(theme.FONT_FAMILY)
                f.setPixelSize(int(_clamp(13 * self._scale, 10, 26)))
                f.setBold(True)
                t.setFont(f)
                t.setBrush(QColor(theme.INK_3))
                t.setPos(QPointF(g["x"] * self._scale + self._so(),
                                 g["y"] * self._scale + self._so() + 6 * self._scale))
                t.setZValue(0)
                self._scene.addItem(t)
                self._header_pool[gi] = t

            self._show_empty(len(self.items) == 0)
        finally:
            self._syncing = False

    def _show_empty(self, visible: bool) -> None:
        self._empty.setVisible(visible)
        if not visible:
            return
        vp = self.view.viewport().rect()
        center = self.view.mapToScene(vp.center())
        br = self._empty.boundingRect()
        self._empty.setPos(center.x() - br.width() / 2, center.y() - br.height() / 2)

    def schedule_sync(self, *_args) -> None:
        self._sync_timer.start()

    # ---------------- 选中 / 悬停 ----------------
    def thumb_at(self, view_pos: QPoint) -> _ThumbItem | None:
        item = self.view.itemAt(view_pos)
        return item if isinstance(item, _ThumbItem) else None

    def click_item(self, path: str, shift: bool = False) -> None:
        if shift:
            if path in self.selected:
                self.selected.discard(path)
            else:
                self.selected.add(path)
        else:
            self.selected = {path}
        self._last_clicked = path
        self._refresh_selection()

    def clear_selection(self) -> None:
        if not self.selected:
            return
        self.selected.clear()
        self._refresh_selection()

    def select_all(self) -> None:
        self.selected = set(self._meta.keys())
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for path, thumb in self._pool.items():
            sel = path in self.selected
            if thumb.selected != sel:
                thumb.selected = sel
                thumb.update()
        self._update_status()

    def set_hover(self, path: str | None) -> None:
        for p, thumb in self._pool.items():
            hovered = (p == path)
            if thumb.hovered != hovered:
                thumb.hovered = hovered
                thumb.update()

    # ---------------- 缩放 / 平移 ----------------
    def _world_anchor(self) -> tuple[float, float, QPoint] | None:
        vp = self.view.viewport()
        if vp.width() < 10 or vp.height() < 10:
            return None
        pos = QPoint(vp.width() // 2, vp.height() // 2)
        sp = self.view.mapToScene(pos)
        return ((sp.x() - self._so()) / self._scale, (sp.y() - self._so()) / self._scale, pos)

    def _restore_world_anchor(self, anchor) -> None:
        wx, wy, pos = anchor
        self._scroll_to_world(wx, wy, pos)

    def _scroll_to_world(self, wx: float, wy: float, view_pos: QPoint) -> None:
        target = QPointF(wx * self._scale + self._so(), wy * self._scale + self._so())
        cur = self.view.mapToScene(view_pos)
        dx = int(round(target.x() - cur.x()))
        dy = int(round(target.y() - cur.y()))
        hb = self.view.horizontalScrollBar()
        vb = self.view.verticalScrollBar()
        if dx:
            hb.setValue(hb.value() + dx)
        if dy:
            vb.setValue(vb.value() + dy)

    def zoom_at(self, view_pos: QPoint, direction: int) -> None:
        old = self._scale
        factor = ZOOM_STEP if direction > 0 else 1.0 / ZOOM_STEP
        new = _clamp(old * factor, MIN_SCALE, MAX_SCALE)
        if abs(new - old) < 1e-6:
            return
        sp = self.view.mapToScene(view_pos)
        wx = (sp.x() - self._so()) / old
        wy = (sp.y() - self._so()) / old
        self._scale = new
        self._apply_geometry()
        # 缩放后把同一世界坐标重新钉回鼠标位置：以鼠标点为缩放中心，且不受滚动条取整影响
        target = QPointF(wx * new + self._so(), wy * new + self._so())
        self.view.horizontalScrollBar().setValue(int(round(target.x() - view_pos.x())))
        self.view.verticalScrollBar().setValue(int(round(target.y() - view_pos.y())))
        self._sync_visible()
        self._update_status()

    def zoom_reset(self) -> None:
        if abs(self._scale - 1.0) < 1e-6:
            return
        anchor = self._world_anchor()
        self._scale = 1.0
        self._apply_geometry()
        if anchor:
            self._restore_world_anchor(anchor)
        self._sync_visible()
        self._update_status()

    def pan_by(self, dx: int, dy: int) -> None:
        hb = self.view.horizontalScrollBar()
        vb = self.view.verticalScrollBar()
        if dx:
            hb.setValue(hb.value() + dx)
        if dy:
            vb.setValue(vb.value() + dy)
        self.schedule_sync()

    def _latest_date_key(self) -> str | None:
        # 日期升序排列，最新的一天在最右侧（分组末尾）
        return self._groups[-1]["date"] if self._groups else None

    def _today_key(self) -> str | None:
        today = datetime.now().strftime("%Y-%m-%d")
        return today if today in self._by_date else self._latest_date_key()

    def focus_date(self, date_key: str | None) -> None:
        if not date_key:
            return
        for g in self._groups:
            if g["date"] == date_key:
                x = g["x"] * self._scale + self._so() - 14
                y = g["y"] * self._scale + self._so() - 14
                # 最右侧分组放得下整屏时右对齐，避免滚动到右端后出现大片空白
                gw = g["cols"] * CELL_W * self._scale
                vp_w = self.view.viewport().width()
                if gw + 26 <= vp_w:
                    x = (g["x"] + g["cols"] * CELL_W) * self._scale + self._so() + 26 - vp_w
                self.view.horizontalScrollBar().setValue(int(x))
                self.view.verticalScrollBar().setValue(int(y))
                self.schedule_sync()
                return

    # ---------------- 拖拽排序 ----------------
    def begin_drag(self, thumb: _ThumbItem, view_pos: QPoint) -> None:
        self._drag = {"path": thumb.path, "start": view_pos}
        self._drag_src_item = thumb
        pm = thumb.pixmap()
        self._floating = QGraphicsPixmapItem(pm)
        self._floating.setZValue(1000)
        self._floating.setOpacity(0.85)
        self._floating.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._floating)
        pen = QPen(QColor(theme.ACCENT), 1.6, Qt.PenStyle.DashLine)
        self._marker = QGraphicsRectItem()
        self._marker.setPen(pen)
        self._marker.setBrush(QColor(47, 109, 246, 28))
        self._marker.setZValue(999)
        self._marker.setVisible(False)
        self._scene.addItem(self._marker)
        thumb.setVisible(False)
        self.view.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.drag_to(view_pos)

    def drag_to(self, view_pos: QPoint) -> None:
        if self._floating is None:
            return
        sp = self.view.mapToScene(view_pos)
        self._floating.setPos(sp.x() - self._tw / 2, sp.y() - self._th / 2)
        target = self.thumb_at(view_pos)
        path = target.path if target is not None else None
        if path and path == self._drag["path"]:
            path = None
        self._drag_target = path
        if target is not None and path is None:
            target = None
        if target is None or self._marker is None:
            if self._marker is not None:
                self._marker.setVisible(False)
            return
        src_item = self._item_map.get(self._drag["path"], {})
        dst_item = self._item_map.get(path, {})
        same_day = str(src_item.get("date")) == str(dst_item.get("date"))
        r = target.sceneBoundingRect()
        self._marker.setRect(r)
        self._marker.setPen(QPen(QColor(theme.ACCENT if same_day else theme.DANGER),
                                 1.6, Qt.PenStyle.DashLine))
        self._marker.setVisible(True)

    def end_drag(self, view_pos: QPoint) -> None:
        target = self._drag_target
        src = self._drag["path"] if self._drag else None
        self._cleanup_drag()
        if not src or not target or src == target:
            return
        if self._reorder(src, target):
            self.reload(keep_view=True)
            self.click_item(src, False)
            self.set_status("顺序已更新")
        else:
            self.set_status("只能在同一日期分组内调整顺序")

    def _cleanup_drag(self) -> None:
        if self._floating is not None:
            self._scene.removeItem(self._floating)
            self._floating = None
        if self._marker is not None:
            self._scene.removeItem(self._marker)
            self._marker = None
        if self._drag_src_item is not None:
            self._drag_src_item.setVisible(True)
            self._drag_src_item = None
        self._drag = None
        self._drag_target = None
        self.view.unsetCursor()

    def _reorder(self, src_path: str, dst_path: str) -> bool:
        a = self._item_map.get(src_path)
        b = self._item_map.get(dst_path)
        if a is None or b is None:
            return False
        if str(a.get("date")) != str(b.get("date")):
            return False
        try:
            items = list(self.storage.load_gallery() or [])
            day = str(a.get("date"))
            same = [it for it in items if str(it.get("date")) == day]
            same.sort(key=lambda d: int(d.get("order") or 0))
            src = next((it for it in same if it.get("path") == src_path), None)
            dst = next((it for it in same if it.get("path") == dst_path), None)
            if src is None or dst is None:
                return False
            i, j = same.index(src), same.index(dst)
            same.insert(j, same.pop(i))
            for k, it in enumerate(same):
                it["order"] = k
            self.storage.save_gallery(items)
            return True
        except Exception:
            return False

    # ---------------- 动作 ----------------
    def open_viewer(self, path: str | None = None) -> None:
        target = path or (sorted(self.selected)[0] if self.selected else self._last_clicked)
        if not target:
            self.set_status("请先选中一张")
            return
        item = self._item_map.get(target)
        if item is None:
            return
        if str(item.get("kind") or "").lower() == "video" or \
                str(item.get("path", "")).lower().endswith(VIDEO_EXTS):
            self.play_video(str(item.get("path")))
            return
        viewer = LargeViewer(item, self.storage, parent=self,
                             on_changed=lambda: self.reload(keep_view=True))
        viewer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()
        self._viewer = viewer

    def play_video(self, path: str | None) -> None:
        """用内置播放窗播放录屏；不可用时回退到系统默认播放器。"""
        path = str(path or "")
        if not path:
            return
        from . import video_player_qt
        win = video_player_qt.open_video(path, parent=self,
                                         on_close=self._on_player_closed)
        if win is None:
            _open_path(path)            # 解码器不可用 → 交系统播放器
            return
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._player = win
        self.set_status(f"正在播放：{os.path.basename(path)}")

    def _on_player_closed(self) -> None:
        self._player = None

    def ocr_selected(self) -> None:
        """P9：识别选中图片中的文字（历史库入口）。"""
        paths = [p for p in (self.selected or {self._last_clicked}) if p]
        paths = [p for p in paths if p in self._item_map]
        if not paths:
            self.set_status("请先选中一张图片")
            return
        if len(paths) > 1:
            self.set_status("一次只能识别一张（已选 %d 项）" % len(paths))
            return
        self.ocr_path(paths[0])

    def ocr_path(self, path: str) -> None:
        from . import ocr_result_qt
        if os.path.splitext(path)[1].lower() in VIDEO_EXTS:
            self.set_status("视频不支持文字识别")
            return
        # 点击瞬间先做引擎守门：缺引擎立刻弹提示并中止，不再等图片读完/识别线程返回
        if not ocr_result_qt.gate_entry(None, owner=self)[0]:
            self.set_status("缺少文字识别组件，已弹出安装引导")
            return
        try:
            with Image.open(path) as im:
                img = im.convert("RGB")
        except Exception as exc:
            self.set_status("打开图片失败：%s" % exc)
            return
        item = self._item_map.get(path) if isinstance(self._item_map, dict) else None
        ocr_result_qt.open_ocr(img, item=item, storage=self.storage)
        self.set_status("正在识别文字…")

    def copy_selected(self) -> None:
        paths = [p for p in (self.selected or {self._last_clicked}) if p]
        paths = [p for p in paths if p in self._item_map]
        if not paths:
            self.set_status("请先选中一张图片")
            return
        if len(paths) > 1:
            self.set_status("一次只能复制一张（已选 %d 项）" % len(paths))
            return
        path = paths[0]
        try:
            with Image.open(path) as im:
                ok = clipboard.copy_image_to_clipboard(im.convert("RGB")
                                                       if im.mode not in ("RGB", "RGBA")
                                                       else im)
        except Exception as exc:
            self.set_status(f"复制失败：{exc}")
            return
        self.set_status("已复制图片到剪贴板" if ok else "复制失败：剪贴板不可用")

    def save_selected_as(self) -> None:
        paths = [p for p in sorted(self.selected) if p in self._item_map]
        if not paths:
            self.set_status("请先选中一张图片")
            return
        path = paths[0]
        ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
        out = dialogs.ask_save_file("另存为", initialdir=os.path.dirname(path),
                                    initialfile=os.path.basename(path),
                                    filetypes=[("图片", f"*.{ext}")],
                                    default_ext=ext, parent=self)
        if not out:
            return
        try:
            import shutil
            shutil.copy2(path, out)
            self.set_status(f"已另存为 {os.path.basename(out)}")
        except Exception as exc:
            dialogs.msg_error("另存为失败", str(exc), parent=self)

    def delete_selected(self) -> None:
        items = [self._item_map[p] for p in sorted(self.selected) if p in self._item_map]
        if not items:
            self.set_status("未选中任何项目（Delete 前先单击选中）")
            return
        n = len(items)
        trash_dir = os.path.join(self._save_root, getattr(appstorage, "TRASH_DIR", ".trash"))
        if not dialogs.msg_confirm(
                "删除到回收站",
                f"确定把 {n} 项移入回收站？", parent=self,
                detail=(f"回收站位置：{trash_dir}\n"
                        f"{self._trash_days} 天内可在该目录找回，之后自动清除。"),
                ok_text="移入回收站", cancel_text="取消", danger=True):
            return
        try:
            moved = self.storage.delete_to_trash(items)
        except Exception as exc:
            dialogs.msg_error("删除失败", str(exc), parent=self)
            return
        self.selected.clear()
        self.reload(keep_view=True)
        self.set_status(f"已移入回收站 {moved} 项（{self._trash_days} 天后自动清除）")

    def open_save_dir(self) -> None:
        try:
            os.startfile(self._save_root)
        except Exception as exc:
            dialogs.msg_error("打开目录失败", str(exc), parent=self)

    def open_trash(self) -> None:
        trash = os.path.join(self._save_root, getattr(appstorage, "TRASH_DIR", ".trash"))
        if not os.path.isdir(trash):
            self.set_status("回收站为空")
            return
        try:
            os.startfile(trash)
        except Exception as exc:
            dialogs.msg_error("打开回收站失败", str(exc), parent=self)

    # ---------------- 右键菜单 ----------------
    def build_context_menu(self, has_item: bool) -> QMenu:
        menu = QMenu(self)
        n = len(self.selected)
        if has_item:
            act_view = menu.addAction("查看大图")
            act_view.triggered.connect(lambda: self.open_viewer())
            act_copy = menu.addAction("复制图片")
            act_copy.triggered.connect(self.copy_selected)
            if len(self.selected) == 1:
                _p = sorted(self.selected)[0]
                if os.path.splitext(_p)[1].lower() not in VIDEO_EXTS:
                    act_ocr = menu.addAction("识别文字")
                    act_ocr.triggered.connect(self.ocr_selected)
            act_save = menu.addAction("另存为…")
            act_save.triggered.connect(self.save_selected_as)
            act_rev = menu.addAction("在文件夹中显示")
            act_rev.triggered.connect(
                lambda: _reveal_in_explorer(sorted(self.selected)[0])
                if self.selected else None)
            menu.addSeparator()
            act_del = menu.addAction(f"移入回收站{f'（{n} 项）' if n > 1 else ''}")
            act_del.triggered.connect(self.delete_selected)
        else:
            act_all = menu.addAction("全选")
            act_all.triggered.connect(self.select_all)
            act_ref = menu.addAction("刷新")
            act_ref.triggered.connect(lambda: self.reload(keep_view=True))
            menu.addSeparator()
            act_dir = menu.addAction("打开存档目录")
            act_dir.triggered.connect(self.open_save_dir)
            act_trash = menu.addAction("打开回收站")
            act_trash.triggered.connect(self.open_trash)
        return menu

    # ---------------- 键盘 ----------------
    def handle_key(self, event) -> bool:
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
        if key == Qt.Key.Key_Escape:
            self.close()
            return True
        if key == Qt.Key.Key_F5:
            self.reload(keep_view=True)
            self.set_status("已刷新")
            return True
        if key == Qt.Key.Key_A and ctrl:
            self.select_all()
            return True
        if key == Qt.Key.Key_C and ctrl:
            self.copy_selected()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_viewer()
            return True
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_at(self.view.viewport().rect().center(), 1)
            return True
        if key == Qt.Key.Key_Minus:
            self.zoom_at(self.view.viewport().rect().center(), -1)
            return True
        if key == Qt.Key.Key_0:
            self.zoom_reset()
            return True
        return False

    # ---------------- 状态 ----------------
    def set_status(self, text: str) -> None:
        self._update_status(extra=text)

    def _update_status(self, extra: str | None = None) -> None:
        total = len(self.items)
        videos = sum(1 for it in self.items
                     if str(it.get("kind") or "").lower() == "video")
        parts = [f"共 {total} 项"]
        if videos:
            parts.append(f"截图 {total - videos} · 录屏 {videos}")
        parts.append(f"缩放 {int(round(self._scale * 100))}%")
        if self.selected:
            parts.append(f"已选 {len(self.selected)} 项")
        if extra:
            parts.append(extra)
        else:
            parts.append(f"存档 {self._save_root}")
        try:
            self._status.setText("  ·  ".join(parts))
        except RuntimeError:
            pass


# ======================================================================
# 七、对外入口
# ======================================================================
def open_gallery(parent=None, cfg: dict | None = None,
                 save_root: str | None = None) -> GalleryWindow:
    """打开（或唤醒）历史库单例窗口。"""
    win = GalleryWindow._active
    if win is not None:
        try:
            win.reload(keep_view=True)
            if not win.isVisible():
                win.show()
            win.raise_()
            win.activateWindow()
            return win
        except RuntimeError:
            GalleryWindow._active = None
    win = GalleryWindow(parent=parent, cfg=cfg, save_root=save_root)
    win.show()
    win.raise_()
    win.activateWindow()
    return win


__all__ = ["GalleryWindow", "GalleryView", "LargeViewer", "open_gallery",
           "register_capture", "sync_library"]
