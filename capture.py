# -*- coding: utf-8 -*-
"""截屏模块：全屏截图、窗口枚举、捕获选择界面（窗口/区域/全屏）。"""
import ctypes
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageGrab, ImageDraw, ImageTk

user32 = ctypes.windll.user32

# 虚拟屏幕范围（多显示器时左上角可能为负）
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def virtual_screen():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def grab_fullscreen():
    """抓取整个虚拟桌面，返回 (PIL Image, offset_x, offset_y)。"""
    x, y, w, h = virtual_screen()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
    return img, x, y


def enumerate_windows(exclude_hwnds=()):
    """枚举可见顶层窗口 -> [(hwnd, title, rect)]，按 z-order 顶->底。"""
    result = []

    class WinInfo(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("rect", Rect)]

    infos = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        if hwnd in exclude_hwnds:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return True
        if w < 40 or h < 30:
            return True  # 过滤过小窗口
        infos.append((hwnd, buf.value.strip(), (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(callback, 0)
    return infos


def window_under_cursor(infos, cx, cy):
    """返回包含鼠标点的最上层窗口 rect；没有则 None。"""
    for hwnd, title, rect in infos:
        l, t, r, b = rect
        if l <= cx < r and t <= cy < b:
            return (hwnd, title, rect)
    return None


def grab_region(bbox, offset_x=0, offset_y=0):
    """按屏幕坐标截取区域，offset 为虚拟屏幕原点。"""
    x, y, w, h = bbox
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)


class CaptureWindow:
    """捕获选择界面：移动鼠标高亮窗口，单击截窗口；拖拽截区域；双击截全屏；Esc 取消。"""

    def __init__(self, root, on_result, on_cancel):
        self.root = root
        self.on_result = on_result
        self.on_cancel = on_cancel
        self.img, self.offset_x, self.offset_y = grab_fullscreen()
        self.vx, self.vy = self.offset_x, self.offset_y
        self.vw = self.img.width
        self.vh = self.img.height
        self.windows = enumerate_windows()

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")

        self.canvas = tk.Canvas(self.win, width=self.vw, height=self.vh,
                                highlightthickness=0, cursor="crosshair", bg="black")
        self.canvas.pack(fill="both", expand=True)
        self.photo = ImageTk.PhotoImage(self.img)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")

        # 提示条
        tip = "单击=截窗口  拖拽=截区域  双击=全屏  Esc=取消"
        self.canvas.create_rectangle(0, 0, self.vw, 36, fill="#1f1f1f", outline="")
        self.canvas.create_text(self.vw // 2, 18, text=tip, fill="#ffffff",
                                font=("Microsoft YaHei UI", 11))

        self._hl = None          # 高亮矩形 id
        self._drag = None        # 拖拽矩形 id
        self._start = None
        self._moved = False

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.win.bind("<Escape>", lambda e: self._close())
        self.win.focus_force()

    # ---- 鼠标位置换算（屏幕坐标 -> 图片坐标） ----
    def _to_img(self, event):
        return (event.x_root - self.vx, event.y_root - self.vy)

    def _on_motion(self, event):
        ix, iy = self._to_img(event)
        hit = window_under_cursor(self.windows, event.x_root, event.y_root)
        self.canvas.delete(self._hl) if self._hl else None
        self._hl = None
        if hit and not self._start:
            hwnd, title, rect = hit
            l, t, r, b = rect
            self._hl = self.canvas.create_rectangle(
                l - self.vx, t - self.vy, r - self.vx, b - self.vy,
                outline="#e81123", width=3)

    def _on_press(self, event):
        self._start = self._to_img(event)
        self._moved = False

    def _on_drag(self, event):
        if not self._start:
            return
        cur = self._to_img(event)
        if abs(cur[0] - self._start[0]) + abs(cur[1] - self._start[1]) > 4:
            self._moved = True
        if self._drag:
            self.canvas.delete(self._drag)
        x1, y1 = self._start
        x2, y2 = cur
        self._drag = self.canvas.create_rectangle(
            min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2),
            outline="#e81123", width=2, fill="", dash=(4, 2))

    def _on_release(self, event):
        if not self._start:
            return
        if self._drag:
            self.canvas.delete(self._drag)
            self._drag = None
        if self._moved:
            # 区域截图
            x1, y1 = self._start
            x2, y2 = self._to_img(event)
            self._start = None
            bbox = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            if w >= 2 and h >= 2:
                self._finish(self.img.crop(bbox), bbox=bbox)
            return
        # 单击：截取鼠标下窗口
        self._start = None
        hit = window_under_cursor(self.windows, event.x_root, event.y_root)
        if hit:
            hwnd, title, rect = hit
            l, t, r, b = rect
            bbox = (l - self.vx, t - self.vy, r - self.vx, b - self.vy)
            self._finish(self.img.crop(bbox), bbox=bbox)
        else:
            # 没点中窗口则全屏
            self._finish(self.img.copy(), bbox=(0, 0, self.vw, self.vh))

    def _on_double(self, event):
        self._finish(self.img.copy(), bbox=(0, 0, self.vw, self.vh))

    def _finish(self, result_img, bbox=None, full_img=None):
        try:
            self.win.destroy()
        except Exception:
            pass
        if bbox is None:
            offset_x, offset_y = self.vx, self.vy
        else:
            x1, y1, _, _ = bbox
            offset_x, offset_y = self.vx + x1, self.vy + y1
        # 全屏图与初始选区 bbox 一并传给下游，供编辑窗口默认"调整选区"模式使用
        self.on_result(result_img, offset_x, offset_y,
                       full_img if full_img is not None else self.img.copy(),
                       bbox)

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.on_cancel()
