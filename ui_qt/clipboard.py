# -*- coding: utf-8 -*-
"""剪贴板兼容层。

旧版 ``clipboard.py`` 用 ctypes 写 CF_DIB；Qt 版直接走 ``QGuiApplication
.clipboard()``，但**保留同名同签名的 ``copy_image_to_clipboard(img)``**，
让 main / gallery / sticker 的调用点可以零改动切换。

注意：必须在主线程调用（Qt 剪贴板要求 GUI 线程）。
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PIL import Image

from ui_qt import imageutil


def _clipboard():
    """取系统剪贴板；未创建 QApplication 时返回 None。"""
    if QGuiApplication.instance() is None:
        return None
    return QGuiApplication.clipboard()


def copy_image_to_clipboard(img: Image.Image) -> bool:
    """把 PIL Image 写入系统剪贴板（与旧 clipboard.py 同签名）。"""
    if img is None:
        return False
    cb = _clipboard()
    if cb is None:
        return False
    try:
        cb.setImage(imageutil.pil_to_qimage(img))
        return True
    except Exception:
        return False


def copy_qimage(qimg: QImage) -> bool:
    """把 QImage 写入剪贴板。"""
    cb = _clipboard()
    if cb is None or qimg is None or qimg.isNull():
        return False
    cb.setImage(qimg)
    return True


def copy_pixmap(pm: QPixmap) -> bool:
    """把 QPixmap 写入剪贴板。"""
    if pm is None or pm.isNull():
        return False
    return copy_image_to_clipboard(imageutil.qpixmap_to_pil(pm))


def copy_text(text: str) -> bool:
    """写入纯文本（错误信息 / 路径复制）。"""
    cb = _clipboard()
    if cb is None:
        return False
    cb.setText(text or "")
    return True


def get_pil_image() -> Image.Image | None:
    """读取剪贴板图片为 PIL Image（无图返回 None）。"""
    qimg = get_qimage()
    return imageutil.qimage_to_pil(qimg) if qimg is not None else None


def get_qimage() -> QImage | None:
    """读取剪贴板图片为 QImage（无图返回 None）。"""
    cb = _clipboard()
    if cb is None:
        return None
    qimg = cb.image()
    return None if qimg.isNull() else qimg


def get_text() -> str:
    """读取剪贴板文本。"""
    cb = _clipboard()
    return cb.text() if cb is not None else ""


def has_image() -> bool:
    cb = _clipboard()
    return bool(cb is not None and cb.mimeData() is not None and cb.mimeData().hasImage())


def has_text() -> bool:
    cb = _clipboard()
    return bool(cb is not None and cb.mimeData() is not None and cb.mimeData().hasText())


def clear() -> None:
    cb = _clipboard()
    if cb is not None:
        cb.clear()
