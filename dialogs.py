"""弹窗定位补丁：messagebox / filedialog 未传 parent 时，
自动创建一个位于鼠标所在屏幕中心的透明定位窗口作为 parent，
使对话框在点击的屏幕内弹出，避免跨屏移动鼠标。

调用方式：在 main.py 创建 root 后执行 dialogs.patch() 即可全局生效
（已显式传 parent 的调用不受影响，保持跟随父窗口）。
"""
import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox as _mb
from tkinter import filedialog as _fd


def _monitor_rects():
    """枚举所有显示器物理边界（虚拟屏坐标，含负值区域）。"""
    u = ctypes.windll.user32
    rects = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    def _cb(_hmon, _hdc, lprc, _lparam):
        r = lprc.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return True

    u.EnumDisplayMonitors(None, None, _cb, 0)
    return rects


def _pointer_monitor():
    """返回鼠标当前所在屏幕矩形 (l, t, r, b)，找不到返回 None。"""
    u = ctypes.windll.user32

    class _PT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _PT()
    u.GetCursorPos(ctypes.byref(pt))
    for l, t, r, b in _monitor_rects():
        if l <= pt.x < r and t <= pt.y < b:
            return (l, t, r, b)
    return None


def _anchor_parent():
    """在鼠标所在屏幕中心创建透明定位窗口（8x8），作为对话框 parent。"""
    root = tk._default_root
    if root is None:
        return None
    tmp = tk.Toplevel(root)
    tmp.withdraw()
    tmp.overrideredirect(True)
    try:
        tmp.attributes("-alpha", 0.0)
    except Exception:
        pass
    w = h = 8
    mon = _pointer_monitor()
    if mon:
        l, t, r, b = mon
        x = l + (r - l - w) // 2
        y = t + (b - t - h) // 2
    else:
        x = root.winfo_screenwidth() // 2
        y = root.winfo_screenheight() // 2
    tmp.geometry(f"{w}x{h}+{x}+{y}")
    tmp._ss_anchor = True   # 标记：仅用于定位对话框，用完即销毁
    tmp.deiconify()
    tmp.update_idletasks()
    return tmp


def _cleanup_anchor(kw):
    parent = kw.get("parent")
    if parent is not None and getattr(parent, "_ss_anchor", False):
        try:
            if parent.winfo_exists():
                parent.destroy()
        except Exception:
            pass


def _wrap_mb(kind):
    orig = getattr(_mb, kind)

    def _fn(*args, **kw):
        if kw.get("parent") is None:
            anchor = _anchor_parent()
            if anchor is not None:
                kw["parent"] = anchor
        try:
            return orig(*args, **kw)
        finally:
            _cleanup_anchor(kw)

    return _fn


def _wrap_fd(name):
    orig = getattr(_fd, name)

    def _fn(*args, **kw):
        if kw.get("parent") is None:
            anchor = _anchor_parent()
            if anchor is not None:
                kw["parent"] = anchor
        try:
            return orig(*args, **kw)
        finally:
            _cleanup_anchor(kw)

    return _fn


def patch():
    """全局启用弹窗定位补丁（幂等）。"""
    for k in ("showinfo", "showwarning", "showerror", "showquestion",
              "askyesno", "askokcancel", "askretrycancel", "askyesnocancel"):
        if hasattr(_mb, k):
            setattr(_mb, k, _wrap_mb(k))
    for k in ("asksaveasfilename", "askopenfilename", "askdirectory",
              "asksaveasfile", "askopenfile"):
        if hasattr(_fd, k):
            setattr(_fd, k, _wrap_fd(k))
