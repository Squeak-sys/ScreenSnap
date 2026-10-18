# -*- coding: utf-8 -*-
"""剪贴板：图片复制到系统剪贴板（ctypes 实现，无额外依赖）。"""
import ctypes
import io
from ctypes import wintypes

CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def copy_image_to_clipboard(img):
    """把 PIL Image 复制到剪贴板（DIB 格式）。返回 True/False。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # 声明指针/句柄类型，避免 64 位句柄被截断
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "BMP")
        data = buf.getvalue()[14:]  # 去掉 BMP 文件头，保留 DIB
        buf.close()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(h)
            return False
        user32.EmptyClipboard()
        ok = bool(user32.SetClipboardData(CF_DIB, h))
        user32.CloseClipboard()
        return ok
    except Exception:
        return False
