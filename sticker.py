# -*- coding: utf-8 -*-
"""贴图窗口：截图完成后钉在桌面上，可拖动，右键菜单。"""
import time
import tkinter as tk
from PIL import ImageTk


class StickerWindow:
    """无边框置顶窗口，显示图片；鼠标拖动，右键菜单。"""
    # 双击缩略尺寸（40 增大 30%）
    THUMB_SIZE = 52

    def __init__(self, root, image, x=None, y=None, title="截图"):
        self.root = root
        # 原始图：缩放/恢复时保证质量
        self._full = image.copy()
        self._thumb_mode = False

        self.img = image.copy()
        # 限制贴图最大尺寸，避免超大屏截图
        max_w, max_h = 900, 600
        if self.img.width > max_w or self.img.height > max_h:
            self.img.thumbnail((max_w, max_h), 3)
        self._orig = (self.img.width, self.img.height)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        w, h = self.img.width, self.img.height
        vx, vy, vw, vh = self._virtual_screen()
        if x is None or y is None:
            x = vx + vw - w - 40
            y = vy + 60
        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(self.win, width=w, height=h,
                                highlightthickness=0, bg="#1f1f1f")
        self.canvas.pack()
        self._redraw()

        # 拖动
        self._drag_off = None
        # 自定义双击检测：固定 400ms + 位置容差 10px，替代系统双击判定
        self._last_click_time = 0.0
        self._last_click_xy = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        # 右键菜单
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_command(label="关闭贴图（存档保留）", command=self.close)
        self.menu.add_command(label="另存为...", command=self.save_as)
        self.menu.add_separator()
        self.menu.add_command(label="复制到剪贴板", command=self.copy)
        self.canvas.bind("<Button-3>", self._on_menu)

        # 交互：悬停滚轮缩放；自定义双击（_on_press 内判定）：40x40 缩略/还原、Ctrl+双击关闭
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.win.bind("<Escape>", lambda e: self.close())

    @staticmethod
    def _virtual_screen():
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))

    def _redraw(self):
        w, h = self.img.size
        self.photo = ImageTk.PhotoImage(self.img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.create_rectangle(1, 1, w - 1, h - 1, outline="#e81123", width=2)
        # 右上角关闭按钮：仅非缩略模式显示，点击关闭贴图
        if not self._thumb_mode:
            self._close_btn = self.canvas.create_text(
                w - 8, 8, text="✕", fill="#ffffff", anchor="ne", font=("Arial", 12))
            self.canvas.tag_bind(self._close_btn, "<Button-1>", self._on_close_btn)

    def _on_close_btn(self, event):
        """点击右上角 ✕：关闭贴图（存档保留）。返回 break 阻止拖动逻辑。"""
        self.close()
        return "break"

    def _set_size(self, nw, nh, square=False, x=None, y=None):
        """调整尺寸：square=True 拉伸为正方形缩略，否则等比缩放。
        x/y 指定新位置；不传则保持当前窗口左上角。"""
        if square:
            img = self._full.copy().resize((nw, nh), 3)
        else:
            img = self._full.copy()
            img.thumbnail((nw, nh), 3)
        self.img = img
        self.canvas.configure(width=img.width, height=img.height)
        self._redraw()
        if x is None:
            x = self.win.winfo_x()
        if y is None:
            y = self.win.winfo_y()
        self.win.geometry(f"{img.width}x{img.height}+{x}+{y}")

    def _zoom(self, k, ax=0, ay=0):
        """以鼠标位置为锚点等比缩放：最大原始尺寸的 200%，最小短边 40px。"""
        cw, ch = self.img.size
        nw = round(cw * k)
        nh = round(ch * k)
        # 下限：短边 >= 40（等比，不强拉正方形）
        if min(nw, nh) < 40:
            k2 = 40 / min(cw, ch)
            nw = round(cw * k2)
            nh = round(ch * k2)
        # 上限：原始尺寸的 200%（到达后停止，避免位置漂移）
        nw = min(nw, self._orig[0] * 2)
        nh = min(nh, self._orig[1] * 2)
        if (nw, nh) == (cw, ch):
            return
        x, y = self.win.winfo_x(), self.win.winfo_y()
        nx = x + ax - round(ax * nw / cw)
        ny = y + ay - round(ay * nh / ch)
        vx, vy, vw, vh = self._virtual_screen()
        nx = max(vx, min(nx, vx + vw - nw))
        ny = max(vy, min(ny, vy + vh - nh))
        img = self._full.copy()
        if nw > img.width or nh > img.height:
            # 放大超过原始像素尺寸时需显式放大（thumbnail 不放大）
            img = img.resize((nw, nh), 3)
        else:
            img.thumbnail((nw, nh), 3)
        self.img = img
        self.canvas.configure(width=img.width, height=img.height)
        self._redraw()
        self.win.geometry(f"{img.width}x{img.height}+{nx}+{ny}")

    def _on_wheel(self, event):
        k = 1.12 if event.delta > 0 else 1 / 1.12
        self._zoom(k, event.x, event.y)

    def _toggle_thumb(self, event=None):
        """双击：切换正方形缩略显示与原始大小。
        由 _on_press 自定义双击检测触发；缩略显示在鼠标位置，
        展开在缩略图当前位置（缩略可被拖动，不回缩略前位置）。"""
        if self._thumb_mode:
            self._thumb_mode = False
            w, h = self._orig
            vx, vy, vw, vh = self._virtual_screen()
            if event is not None:
                # 展开以鼠标点击处为锚点：大图中心对齐鼠标点，并 clamp 到虚拟屏幕
                tx = event.x_root - w // 2
                ty = event.y_root - h // 2
            else:
                tx, ty = self.win.winfo_x(), self.win.winfo_y()
            tx = max(vx, min(tx, vx + vw - w))
            ty = max(vy, min(ty, vy + vh - h))
            self._set_size(w, h, x=tx, y=ty)
        else:
            self._thumb_mode = True
            vx, vy, vw, vh = self._virtual_screen()
            if event is not None:
                # 缩略以鼠标为锚点居中放置
                tx = event.x_root - self.THUMB_SIZE // 2
                ty = event.y_root - self.THUMB_SIZE // 2
            else:
                tx, ty = self.win.winfo_x(), self.win.winfo_y()
            tx = max(vx, min(tx, vx + vw - self.THUMB_SIZE))
            ty = max(vy, min(ty, vy + vh - self.THUMB_SIZE))
            self._set_size(self.THUMB_SIZE, self.THUMB_SIZE, square=True, x=tx, y=ty)

    def _on_press(self, event):
        # 自定义双击检测：两次点击间隔 < 400ms 且位置偏移 <= 10px 才视为双击。
        # 替代 Tk 原生 <Double-Button-1>（依赖系统双击间隔，且旧双击序列残留会导致
        # 两次分开的单击被误判为双击），使双击行为稳定可控。
        now = time.monotonic()
        dx = abs(event.x_root - self._last_click_xy[0])
        dy = abs(event.y_root - self._last_click_xy[1])
        self._last_click_xy = (event.x_root, event.y_root)
        dt = now - self._last_click_time
        self._last_click_time = now
        if dt < 0.4 and dx <= 10 and dy <= 10:
            # 双击命中：重置时间戳，防止三连击连续触发
            self._last_click_time = 0.0
            if event.state & 0x0004:  # Ctrl 按下：关闭贴图
                self.close()
            else:
                self._toggle_thumb(event)
            return
        self._drag_off = (event.x_root - self.win.winfo_x(),
                          event.y_root - self.win.winfo_y())

    def _on_drag(self, event):
        if self._drag_off:
            nx = event.x_root - self._drag_off[0]
            ny = event.y_root - self._drag_off[1]
            self.win.geometry(f"+{nx}+{ny}")

    def _on_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def copy(self):
        from clipboard import copy_image_to_clipboard
        copy_image_to_clipboard(self.img)

    def save_as(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg")],
            initialfile="截图.png")
        if path:
            self.img.save(path)
            try:
                from tkinter import messagebox
                messagebox.showinfo("ScreenSnap", f"已保存到：{path}")
            except Exception:
                pass

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
