# -*- coding: utf-8 -*-
"""图像工具层：PIL Image <-> QImage / QPixmap / QIcon 双向转换。

迁移原则（见方案 §3.3-7）：**图像内核保持 Pillow**，Qt 只负责显示与交互。
所有转换都做了内存深拷贝，避免 PIL 缓冲区被回收后 QImage 变成花屏。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QImage, QPainter,
                           QPainterPath, QPen, QPixmap)
from PIL import Image

# 兼容不同 PIL 版本取 alpha 通道
try:  # pragma: no cover - 取决于 Pillow 版本
    from PIL import Image as _Image

    _HAS_ALPHA = hasattr(_Image, "has_alpha")  # Pillow 12 已移除
except Exception:  # pragma: no cover
    _HAS_ALPHA = False


def _pil_has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return True
    return "transparency" in img.info


# ---------------- PIL -> Qt ----------------
def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL Image -> QImage（RGB 走 RGB888，其余统一转 RGBA8888，深拷贝）。"""
    if img is None:
        return QImage()
    src = img if isinstance(img, Image.Image) else Image.frombytes(img)

    if src.mode == "RGB":
        w, h = src.size
        data = src.tobytes("raw", "RGB")
        qimg = QImage(data, w, h, w * 3, QImage.Format.Format_RGB888)
    else:
        if src.mode != "RGBA":
            src = src.convert("RGBA")
        w, h = src.size
        data = src.tobytes("raw", "RGBA")
        qimg = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888)

    # 深拷贝：data 是临时 bytes，必须 copy 才能脱离 Python 缓冲区
    return qimg.copy()


def pil_to_qpixmap(img: Image.Image, dpr: float = 1.0) -> QPixmap:
    """PIL Image -> QPixmap，可指定 devicePixelRatio（高分屏 1:1 显示）。"""
    pm = QPixmap.fromImage(pil_to_qimage(img))
    if dpr and abs(dpr - 1.0) > 1e-6:
        pm.setDevicePixelRatio(float(dpr))
    return pm


def pil_to_qicon(img: Image.Image) -> QIcon:
    """PIL Image -> QIcon（托盘/工具按钮图标）。"""
    return QIcon(pil_to_qpixmap(img))


# ---------------- Qt -> PIL ----------------
def qimage_to_pil(qimg: QImage) -> Image.Image | None:
    """QImage -> PIL Image（统一 RGBA8888 取出，保留 alpha）。"""
    if qimg is None or qimg.isNull():
        return None
    converted = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = converted.width(), converted.height()
    bpl = converted.bytesPerLine()
    try:
        raw = bytes(converted.constBits())
    except TypeError:  # 兼容返回 sip.voidptr 的版本
        raw = converted.constBits().asstring(bpl * h)
    return Image.frombytes("RGBA", (w, h), raw, "raw", "RGBA", bpl, 1)


def qpixmap_to_pil(pm: QPixmap) -> Image.Image | None:
    """QPixmap -> PIL Image。"""
    if pm is None or pm.isNull():
        return None
    return qimage_to_pil(pm.toImage())


# ---------------- 文件 / 兜底图标 ----------------
def file_to_qicon(path: str, size: QSize | None = None) -> QIcon | None:
    """从图片文件加载 QIcon（路径不存在返回 None）。"""
    if not path or not os.path.isfile(path):
        return None
    pm = QPixmap(path)
    if pm.isNull():
        return None
    if size is not None and not size.isEmpty():
        pm = pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return QIcon(pm)


def letter_icon(text: str, bg: str | QColor = "#2F6DF6",
                fg: str | QColor = "#FFFFFF", size: int = 64) -> QIcon:
    """生成带圆角底色 + 单字的兜底图标（资源缺失时托盘用）。"""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    rect = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    p.fillPath(path, QBrush(QColor(bg) if not isinstance(bg, QColor) else bg))
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Segoe UI", "Arial"])
    f.setPixelSize(int(size * 0.56))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(QPen(QColor(fg) if not isinstance(fg, QColor) else fg))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, (text or "S")[:1].upper())
    p.end()
    return QIcon(px)


# ---------------- 圆角 / 缩放 ----------------
def rounded_pixmap(pm: QPixmap, radius: float,
                   border: QColor | None = None, border_width: float = 1.0) -> QPixmap:
    """把 QPixmap 裁成圆角矩形（保留透明边角，用于贴图窗 / 缩略图）。"""
    if pm is None or pm.isNull():
        return QPixmap()
    w, h = pm.width(), pm.height()
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    if border is not None and border_width > 0:
        p.setClipping(False)
        p.setPen(QPen(border, border_width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.end()
    return out


def scaled_pixmap(pm: QPixmap, size: QSize,
                  mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio) -> QPixmap:
    """平滑缩放 QPixmap。"""
    if pm is None or pm.isNull():
        return QPixmap()
    return pm.scaled(size, mode, Qt.TransformationMode.SmoothTransformation)


def thumbnail(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    """PIL 缩略图（保持比例，不放大）。"""
    out = img.copy()
    out.thumbnail(box, Image.Resampling.LANCZOS)
    return out


def crop_physical(img: Image.Image, rect) -> Image.Image:
    """按物理像素 QRect 裁剪 PIL 图（rect 为屏幕绝对坐标时先减去 offset）。"""
    x, y, w, h = rect.left(), rect.top(), rect.width(), rect.height()
    return img.crop((x, y, x + w, y + h))


def offset_rect_to_image(rect, offset_x: int, offset_y: int):
    """屏幕绝对坐标矩形 -> 图片内坐标 (x, y, w, h)。"""
    return (rect.left() - offset_x, rect.top() - offset_y,
            rect.width(), rect.height())


def point_to_image(point: QPoint, offset_x: int, offset_y: int) -> QPoint:
    """屏幕绝对坐标点 -> 图片内坐标点（等价旧版 capture._to_img）。"""
    return QPoint(point.x() - offset_x, point.y() - offset_y)
