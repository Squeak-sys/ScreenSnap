# -*- coding: utf-8 -*-
"""编辑层：截图窗口（尺寸=截图尺寸）+ 独立悬浮工具栏（跟随截图窗口排布）。
支持框选/圆选/划线/画笔/马赛克/文字 + 撤销/取消/完成。"""
import os
import sys
import ctypes
import math
import tkinter as tk
from tkinter import colorchooser
from PIL import Image, ImageDraw, ImageFont, ImageTk

import ui_theme as T

TOOL_RECT = "rect"
TOOL_ELLIPSE = "ellipse"
TOOL_LINE = "line"
TOOL_PEN = "pen"
TOOL_MOSAIC = "mosaic"
TOOL_TEXT = "text"
TOOL_ARROW = "arrow"

TOOL_NAMES = {
    TOOL_RECT: "框选", TOOL_ELLIPSE: "圆选", TOOL_LINE: "划线",
    TOOL_PEN: "画笔", TOOL_MOSAIC: "马赛克", TOOL_TEXT: "文字",
    TOOL_ARROW: "箭头",
}
TOOLS = [TOOL_RECT, TOOL_ELLIPSE, TOOL_LINE, TOOL_PEN, TOOL_MOSAIC, TOOL_TEXT,
         TOOL_ARROW]

# 编辑模式：调整选区 / 标注
MODE_ADJUST = "adjust"
MODE_DRAW = "draw"

# SHUTTER 设计 token：颜色与线宽统一由 ui_theme 提供（10 常用色 + 自定义）
COLORS = {name: rgb for name, rgb in zip(T.PALETTE_NAMES, T.PALETTE)}
WIDTHS = {T.WIDTH_NAMES[w]: w for w in T.WIDTH_PRESETS}

FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FONT_FALLBACK = r"C:\Windows\Fonts\msyhbd.ttc"


def _load_font(size):
    for fp in (FONT_PATH, FONT_FALLBACK):
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


class Tooltip:
    """悬浮提示：鼠标悬停显示说明文字。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, event=None):
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#ffffff", fg=T.INK_2,
                 font=("Microsoft YaHei UI", 9), relief="solid",
                 borderwidth=1, padx=5, pady=2,
                 highlightthickness=1, highlightbackground=T.LINE,
                 highlightcolor=T.LINE).pack()

    def _hide(self, event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


def _fallback_icons():
    """保底方案：PIL 绘制工具栏图标（仅当 assets/icons 中 PNG 缺失时使用）。
    与 _make_tool_icons 键一致，返回 {tool: PhotoImage}。

    图标：直观版 v4.0 —— 框选/圆选/划线/画笔/马赛克/文字/箭头 + 撤销（undo
    备用）；另保留 pin/record 两枚功能图标，兼容 ToolbarWindow 按钮引用。
    颜色 INK 深灰 #4a4a4a（浅灰工具栏底上清晰），20x20 画布居中绘制，
    四周保留 1px 安全边。"""
    size = 20
    INK = "#4a4a4a"
    icons = {}

    def _new():
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    # 框选：圆角矩形 + 四角实心角柄（选区感）
    img, d = _new()
    d.rounded_rectangle([2.5, 2.5, 17.5, 17.5], radius=2.5, outline=INK, width=2)
    for cx, cy in ((4, 4), (16, 4), (4, 16), (16, 16)):
        d.rectangle([cx - 1.2, cy - 1.2, cx + 1.2, cy + 1.2], fill=INK)
    icons[TOOL_RECT] = ImageTk.PhotoImage(img)

    # 圆选：空心椭圆（横扁，区别于方框）
    img, d = _new()
    d.ellipse([2.5, 6, 17.5, 14], outline=INK, width=2)
    icons[TOOL_ELLIPSE] = ImageTk.PhotoImage(img)

    # 划线：斜线段 + 两端实心圆点（直尺/线段语义）
    img, d = _new()
    d.line([(4, 17.5), (16, 4.5)], fill=INK, width=2)
    d.ellipse([2.8, 16.3, 5.2, 18.7], fill=INK)
    d.ellipse([14.8, 3.3, 17.2, 5.7], fill=INK)
    icons[TOOL_LINE] = ImageTk.PhotoImage(img)

    # 画笔：实心斜铅笔（笔杆平行四边形 + 笔尖三角，45°）
    img, d = _new()
    d.polygon([(2.75, 16.25), (6.25, 19.75), (14.25, 12.25), (10.75, 8.75)], fill=INK)
    d.polygon([(17.5, 5.5), (13.75, 11.5), (11.25, 9.5)], fill=INK)
    icons[TOOL_PEN] = ImageTk.PhotoImage(img)

    # 马赛克：3x3 实心圆角色块
    img, d = _new()
    for y in (3, 8.7, 14.4):
        for x in (3, 8.7, 14.4):
            d.rounded_rectangle([x, y, x + 4.6, y + 4.6], radius=0.8, fill=INK)
    icons[TOOL_MOSAIC] = ImageTk.PhotoImage(img)

    # 文字：标准 T + 下划线
    img, d = _new()
    d.line([(5, 4.5), (15, 4.5)], fill=INK, width=2)
    d.line([(10, 4.5), (10, 15.5)], fill=INK, width=2)
    d.line([(7, 17.5), (13, 17.5)], fill=INK, width=1)
    icons[TOOL_TEXT] = ImageTk.PhotoImage(img)

    # 箭头：斜线 + 实心三角头（尖在箭身延长线上，底边垂直箭身）
    img, d = _new()
    d.line([(3.3, 16.3), (12.5, 7.1)], fill=INK, width=2)
    d.polygon([(16.25, 3.33), (13.75, 8.33), (11.25, 5.83)], fill=INK)
    icons[TOOL_ARROW] = ImageTk.PhotoImage(img)

    # 撤销（备用）：左弯弧 + 箭头尖（Material undo 线性形态）
    img, d = _new()
    d.arc([4, 8, 16, 20], start=180, end=330, fill=INK, width=2)
    d.line([(4, 14), (7, 10.5)], fill=INK, width=2)
    d.line([(4, 14), (7, 17.5)], fill=INK, width=2)
    icons["undo"] = ImageTk.PhotoImage(img)

    # 盯住：图钉（横向收窄的窄图钉，兼容保留）
    img, d = _new()
    d.ellipse([6, 2, 14, 10], outline=INK, width=2)
    d.line([(10, 10), (10, 15)], fill=INK, width=2)
    d.line([(8, 15), (12, 15)], fill=INK, width=2)
    icons["pin"] = ImageTk.PhotoImage(img)

    # 录制：摄像机（机身圆角矩形 + 镜头圆 + 顶部小三角指示灯，兼容保留）
    img, d = _new()
    d.rounded_rectangle([2, 7, size - 2, size - 3], radius=3,
                        outline=INK, width=2)
    d.ellipse([7, 10, 13, 16], outline=INK, width=2)
    d.polygon([(9, 4), (13, 4), (11, 8)], fill=INK)
    icons["record"] = ImageTk.PhotoImage(img)

    return icons


def _icon_dir():
    """定位图标资源目录：PyInstaller 打包后从 sys._MEIPASS 读取，开发态用项目相对路径。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets", "icons")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "icons")


# 图标键 -> 资源文件名（40x40 PNG）
_PNG_NAMES = {
    TOOL_RECT: "tool_rect.png",
    TOOL_ELLIPSE: "tool_ellipse.png",
    TOOL_LINE: "tool_line.png",
    TOOL_PEN: "tool_pen.png",
    TOOL_MOSAIC: "tool_mosaic.png",
    TOOL_TEXT: "tool_text.png",
    TOOL_ARROW: "tool_arrow.png",
    "undo": "tool_undo.png",
    "pin": "tool_pin.png",
    "record": "tool_record.png",
}


def _tint(img, color):
    """保留 alpha，将非透明像素统一替换为指定颜色（图标着色）。"""
    img = img.convert("RGBA")
    px = img.load()
    r, g, b = color
    for y in range(img.height):
        for x in range(img.width):
            a = px[x, y][3]
            if a:
                px[x, y] = (r, g, b, a)
    return img


def _harden_alpha(img, ink=(74, 74, 74)):
    """抗锯齿稀释修复：alpha>0 的像素统一提升为实心深灰（alpha=255），
    消除 40x40 PNG 经 LANCZOS 缩放后产生的半透明稀释——半透明像素在
    transparentcolor 窗口下几乎变透明导致图标淡/断裂。"""
    img = img.convert("RGBA")
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y][3] > 0:
                px[x, y] = (ink[0], ink[1], ink[2], 255)
    return img


def _badge_font():
    """快捷键角标数字字体（Windows Segoe UI，缺省时用 PIL 默认字体）。"""
    try:
        return ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 11)
    except Exception:
        return ImageFont.load_default()


def _draw_badge(img, digit):
    """在 40x40 图标右下角绘制快捷键数字角标（白底小方块 + 细边框 + 数字）。
    纯白不透明工具栏下 Canvas 装饰会被按钮窗口遮挡，角标必须合成进图标。"""
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)
    d.rectangle([29, 29, 39, 38], fill=T.TOOLBAR,
                outline=T.LINE, width=1)
    d.text((34, 33.5), str(digit), fill=T.INK_3,
           font=_badge_font(), anchor="mm")
    return img


def _make_color_dot_icon(rgb, size=40):
    """颜色按钮图标：外圈灰线 + 白色内环 + 当前色圆点（模拟原 Canvas 圆点视觉）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size // 2
    d.ellipse([c - 14, c - 14, c + 14, c + 14], outline=T.LINE, width=1)
    d.ellipse([c - 11, c - 11, c + 11, c + 11], outline="#ffffff", width=2)
    d.ellipse([c - 8, c - 8, c + 8, c + 8],
              fill=(rgb[0], rgb[1], rgb[2], 255))
    return img


def _make_width_icon(size=40):
    """线宽按钮图标：三条横线（1/2/3 磅，模拟原 Canvas 三线视觉）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y, w in ((9, 2), (19, 3), (29, 4)):
        d.line([(8, y), (32, y)], fill=(28, 36, 48, 255), width=w)
    return img


def _make_tool_icons():
    """生成两套工具栏图标：常态深灰（INK）+ 选中主色（ACCENT）。
    加载 assets/icons 下 40x40 高清 PNG 以 40x40 显示（高分辨率源图缩放，
    清晰且与旧版尺寸一致）；工具图标右下角合成快捷键数字角标。
    返回 (icons, icons_accent)。"""
    icons = {}
    icons_accent = {}
    fallback = None
    d = _icon_dir()
    for idx, (key, fname) in enumerate(_PNG_NAMES.items()):
        tool_badge = idx < 7  # 前 7 枚为工具（rect..arrow），叠加数字角标
        path = os.path.join(d, fname)
        if os.path.exists(path):
            # 直接使用最小档 20px 原图 1:1（不做缩放/放大）；40px 原图（tool_line）直接用。
            # 小图居中贴到 40px 按钮画布，角标仍画在画布右下角。
            img = Image.open(path).convert("RGBA")
            if img.size != (40, 40):
                canvas = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
                canvas.paste(img, (10, 10), img)
                img = canvas
            img = _harden_alpha(img)
            if tool_badge:
                img_a = _tint(img, (47, 109, 246))
                img = _draw_badge(img, idx + 1)
                img_a = _draw_badge(img_a, idx + 1)
            else:
                img_a = _tint(img, (47, 109, 246))
            icons[key] = ImageTk.PhotoImage(img)
            icons_accent[key] = ImageTk.PhotoImage(img_a)
        else:
            if fallback is None:
                fallback = _fallback_icons()
            icons[key] = fallback[key]
            icons_accent[key] = fallback[key]
    return icons, icons_accent


class ToolbarWindow:
    """独立悬浮工具栏：高 52px，纯白不透明填充（#FBFBFC），无 transparentcolor 透明机制。
    工具按钮选中 = accent-soft 圆底 + accent 图标，悬浮 = surface-3；
    快捷键角标（1-6）右下角常显；完成 = 绿色实底胶囊 + ✓（唯一强主操作）。"""

    def __init__(self, editor, root, topmost=True, master=None):
        self.editor = editor
        self.tool_h = tool_h = T.TOOLBAR_H
        # 纯白不透明填充：窗口整体为 #FBFBFC 实心块，不使用 transparentcolor 透明机制
        self._bar_bg = T.TOOLBAR
        # master 为所属窗口时，工具栏成为其 owned 子窗口
        self.win = tk.Toplevel(master if master is not None else root)
        self.win.overrideredirect(True)
        if topmost:
            self.win.attributes("-topmost", True)
        self.win.configure(bg=self._bar_bg)

        self._icons, self._icons_accent = _make_tool_icons()
        # Canvas 容器：纯白背景承载全部按钮（create_window 挂载）
        self.canvas = tk.Canvas(self.win, bg=self._bar_bg,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        btn_h = T.TOOL_BTN          # 40：圆形命中区
        cy = tool_h // 2            # 26：按钮中心 y
        gap = 2                     # 按钮间间距
        self._btn_bgs = {}          # button -> Canvas 高亮底 oval id
        self._btn_tool = {}         # button -> tool（非工具按钮为 None）
        self._tool_btns = {}
        self._normal_icons = {}
        self._active_icons = {}
        self._color_palette = None
        self._width_menu = None

        def _tbtn(image=None, **kw):
            kw.setdefault("relief", "flat")
            kw.setdefault("bd", 0)
            kw.setdefault("cursor", "hand2")
            kw.setdefault("bg", self._bar_bg)
            kw.setdefault("activebackground", self._bar_bg)
            kw.setdefault("highlightthickness", 0)
            kw.setdefault("image", image)
            return tk.Button(self.win, **kw)

        def _hover_bind(b, oval, tool):
            b.bind("<Enter>",
                   lambda e, o=oval, tl=tool: self._btn_enter(o, tl), add="+")
            b.bind("<Leave>",
                   lambda e, o=oval, tl=tool: self._btn_leave(o, tl), add="+")

        x = 10
        # ---- 分区A：工具图标按钮（1-6 快捷键角标常显） ----
        for i, tool in enumerate(TOOLS, 1):
            cx = x + btn_h // 2
            oval = self.canvas.create_oval(
                cx - btn_h // 2, cy - btn_h // 2,
                cx + btn_h // 2, cy + btn_h // 2, fill="", outline="")
            b = _tbtn(self._icons[tool],
                      command=lambda t=tool: self.editor.set_tool(t))
            # 按钮 window 创建，完整 40x40 区域可点击；快捷键角标已合成进图标
            self.canvas.create_window(cx, cy, width=btn_h, height=btn_h, window=b)
            self._tool_btns[tool] = b
            self._btn_bgs[b] = oval
            self._btn_tool[b] = tool
            self._normal_icons[tool] = self._icons[tool]
            self._active_icons[tool] = self._icons_accent[tool]
            _hover_bind(b, oval, tool)
            Tooltip(b, f"{TOOL_NAMES[tool]}（快捷键 {i}）")
            x += btn_h + gap

        # ---- 分隔线 ----
        x += 6
        self.canvas.create_line(x, cy - 12, x, cy + 12, fill=T.LINE, width=1)
        x += 6

        # ---- 分区B：颜色圆点（展开色板 10 色 + 自定义） ----
        self._color_cx = x + btn_h // 2
        self._color_oval = self.canvas.create_oval(
            self._color_cx - 20, cy - 20, self._color_cx + 20, cy + 20,
            fill="", outline="")
        self._color_rgb = tuple(self.editor.color)
        self._color_dot_img = ImageTk.PhotoImage(
            _make_color_dot_icon(self._color_rgb))
        self._color_btn = _tbtn(self._color_dot_img,
                                command=self._open_color_palette)
        self.canvas.create_window(self._color_cx, cy, width=btn_h, height=btn_h,
                                  window=self._color_btn)
        self._btn_bgs[self._color_btn] = self._color_oval
        self._btn_tool[self._color_btn] = None
        _hover_bind(self._color_btn, self._color_oval, None)
        Tooltip(self._color_btn, "颜色（点击展开选择）")
        x += btn_h + gap

        # ---- 分区C：线宽（三线图标，展开预设） ----
        self._width_cx = x + btn_h // 2
        self._width_oval = self.canvas.create_oval(
            self._width_cx - 20, cy - 20, self._width_cx + 20, cy + 20,
            fill="", outline="")
        self._width_img = ImageTk.PhotoImage(_make_width_icon())
        self._width_btn = _tbtn(self._width_img, command=self._open_width_menu)
        self.canvas.create_window(self._width_cx, cy, width=btn_h, height=btn_h,
                                  window=self._width_btn)
        self._btn_bgs[self._width_btn] = self._width_oval
        self._btn_tool[self._width_btn] = None
        _hover_bind(self._width_btn, self._width_oval, None)
        Tooltip(self._width_btn, "线条粗细")
        self._width_name = "常规"
        x += btn_h + gap

        # ---- 分隔线 ----
        x += 6
        self.canvas.create_line(x, cy - 12, x, cy + 12, fill=T.LINE, width=1)
        x += 6

        # ---- 分区D：撤销 ----
        cx = x + btn_h // 2
        oval = self.canvas.create_oval(cx - 20, cy - 20, cx + 20, cy + 20,
                                       fill="", outline="")
        undo_b = _tbtn(self._icons["undo"], command=self.editor.undo)
        self.canvas.create_window(cx, cy, width=btn_h, height=btn_h, window=undo_b)
        self._btn_bgs[undo_b] = oval
        self._btn_tool[undo_b] = None
        _hover_bind(undo_b, oval, None)
        Tooltip(undo_b, "撤销上一步（Ctrl+Z）")
        x += btn_h + gap

        # ---- 分区E：录制（红点） ----
        cx = x + btn_h // 2
        oval = self.canvas.create_oval(cx - 20, cy - 20, cx + 20, cy + 20,
                                       fill="", outline="")
        try:
            rec_img = Image.open(os.path.join(_icon_dir(), "tool_record.png"))
            rec_img = rec_img.convert("RGBA")
            if rec_img.size != (40, 40):
                rec_canvas = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
                rec_canvas.paste(rec_img, (10, 10), rec_img)
                rec_img = rec_canvas
            rec_img = _harden_alpha(rec_img)
            rec_img = _tint(rec_img, (229, 72, 77))
            # 录制红点角标合成进图标右上（按钮窗口内 Canvas 装饰不可见）
            ImageDraw.Draw(rec_img).ellipse(
                [30, 8, 34, 12], fill=(229, 72, 77, 255))
            self._rec_img = ImageTk.PhotoImage(rec_img)
        except Exception:
            self._rec_img = self._icons["record"]
        rec_b = _tbtn(self._rec_img, command=self.editor.request_record)
        self.canvas.create_window(cx, cy, width=btn_h, height=btn_h, window=rec_b)
        self._btn_bgs[rec_b] = oval
        self._btn_tool[rec_b] = None
        _hover_bind(rec_b, oval, None)
        Tooltip(rec_b, "屏幕录制（开始录屏）")
        x += btn_h + gap

        # ---- 分区F：贴图（钉在屏幕） ----
        cx = x + btn_h // 2
        oval = self.canvas.create_oval(cx - 20, cy - 20, cx + 20, cy + 20,
                                       fill="", outline="")
        pin_b = _tbtn(self._icons["pin"], command=self.editor.pin)
        self.canvas.create_window(cx, cy, width=btn_h, height=btn_h, window=pin_b)
        self._btn_bgs[pin_b] = oval
        self._btn_tool[pin_b] = None
        _hover_bind(pin_b, oval, None)
        Tooltip(pin_b, "贴图：钉在屏幕（不进历史库）")
        x += btn_h + gap + 4

        # ---- 分区G：完成（绿色实底胶囊 + ✓，唯一强主操作） ----
        done_w = 92
        done_cx = x + done_w // 2
        self._done_pill = self._round_rect(
            self.canvas, done_cx - done_w // 2, cy - 20,
            done_cx + done_w // 2, cy + 20, 20,
            fill=T.SUCCESS, outline="")
        done_b = tk.Button(self.win, text="✓ 完成",
                           font=("Microsoft YaHei UI", 12, "bold"),
                           fg="#ffffff", bg=T.SUCCESS,
                           activebackground=T.SUCCESS_HOVER,
                           relief="flat", bd=0, highlightthickness=0,
                           cursor="hand2", command=self.editor.done)
        self.canvas.create_window(done_cx, cy, width=done_w, height=btn_h,
                                  window=done_b)
        done_b.bind("<Enter>",
                    lambda e: self.canvas.itemconfig(self._done_pill,
                                                     fill=T.SUCCESS_HOVER),
                    add="+")
        done_b.bind("<Leave>",
                    lambda e: self.canvas.itemconfig(self._done_pill,
                                                     fill=T.SUCCESS),
                    add="+")
        Tooltip(done_b, "完成并复制（Enter）")
        self.bar_w = done_cx + done_w // 2 + 10

        # 键盘事件转发（点击工具栏时焦点在工具栏窗口）
        for key, handler in (("<Escape>", self.editor.cancel), ("<Return>", self.editor.done),
                             ("<Control-z>", self.editor.undo), ("<Control-Z>", self.editor.undo)):
            self.win.bind(key, lambda e, h=handler: h())
        for i, tool in enumerate(TOOLS, 1):
            self.win.bind(f"<Key-{i}>",
                          lambda e, t=tool: self.editor._on_tool_key(t))

        # 点击工具栏按钮后把键盘焦点还给编辑窗口，保证后续快捷键持续可用
        for b in self.win.winfo_children():
            if isinstance(b, tk.Button):
                b.bind("<Button-1>", lambda e: self.editor.win.focus_force(), add="+")

        self.win.geometry(f"{self.bar_w}x{tool_h}+0+0")
        self._highlight_tool()

    def _btn_enter(self, oval, tool):
        """按钮悬浮：非选中态显示 surface-3 圆底。"""
        if tool is not None and self.editor.mode != MODE_ADJUST \
                and tool == self.editor.tool:
            return  # 选中工具保持 accent-soft，不响应悬浮
        self.canvas.itemconfig(oval, fill=T.SURFACE_3)

    def _btn_leave(self, oval, tool):
        if tool is not None and self.editor.mode != MODE_ADJUST \
                and tool == self.editor.tool:
            self.canvas.itemconfig(oval, fill=T.ACCENT_SOFT)
        else:
            self.canvas.itemconfig(oval, fill="")

    def _highlight_tool(self):
        """选中工具 = accent-soft 圆底 + accent 图标；调整模式无激活工具。"""
        for t, b in self._tool_btns.items():
            oval = self._btn_bgs[b]
            if self.editor.mode == MODE_ADJUST:
                self.canvas.itemconfig(oval, fill="")
                b.configure(image=self._normal_icons[t])
            elif t == self.editor.tool:
                self.canvas.itemconfig(oval, fill=T.ACCENT_SOFT)
                b.configure(image=self._active_icons[t])
            else:
                self.canvas.itemconfig(oval, fill="")
                b.configure(image=self._normal_icons[t])

    @staticmethod
    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        """在 Canvas 上绘制圆角矩形（smooth polygon 经典做法）。"""
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    @staticmethod
    def _hex_rgb(rgb):
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    def set_color(self, rgb):
        self._color_rgb = tuple(rgb)
        self._color_dot_img = ImageTk.PhotoImage(
            _make_color_dot_icon(self._color_rgb))
        self._color_btn.configure(image=self._color_dot_img)
        self._close_color_palette()

    def _open_color_palette(self):
        if self._color_palette is not None:
            self._close_color_palette()
            return
        pal = tk.Toplevel(self.win)
        pal.overrideredirect(True)
        pal.attributes("-topmost", True)
        # 色板定位：按钮上方居中；越界则放下方
        ph = 2 * 34 + 34 + 12
        bx = self._color_btn.winfo_rootx() - 75
        by = self._color_btn.winfo_rooty() - ph
        if by < 0:
            by = self._color_btn.winfo_rooty() + self._color_btn.winfo_height() + 6
        pal.geometry(f"+{max(0, bx)}+{max(0, by)}")
        pal.configure(bg="#ffffff", highlightthickness=1,
                      highlightbackground=T.LINE)
        for i, (name, rgb) in enumerate(COLORS.items()):
            hexc = self._hex_rgb(rgb)
            b = tk.Button(pal, width=3, relief="flat", bd=0, bg=hexc,
                          activebackground=hexc, cursor="hand2",
                          command=lambda c=rgb: self.editor.set_color(c))
            b.grid(row=i // 5, column=i % 5, padx=4, pady=4)
            Tooltip(b, name)
        cus = tk.Button(pal, text="…", font=("Microsoft YaHei UI", 13, "bold"),
                        relief="flat", bd=0, fg=T.INK_2, bg="#ffffff",
                        activebackground=T.SURFACE_3, cursor="hand2",
                        command=self._pick_custom_color)
        cus.grid(row=2, column=0, padx=4, pady=4)
        self._color_palette = pal
        pal.bind("<FocusOut>", lambda e: self._close_color_palette())

    def _pick_custom_color(self):
        self._close_color_palette()
        rgb, _ok = colorchooser.askcolor(color=self._hex_rgb(self._color_rgb),
                                         parent=self.win, title="自定义颜色")
        if rgb:
            self.editor.set_color((int(rgb[0]), int(rgb[1]), int(rgb[2])))

    def _close_color_palette(self):
        if self._color_palette is not None:
            try:
                self._color_palette.destroy()
            except Exception:
                pass
            self._color_palette = None

    def set_width(self, w, name=None):
        if name is None:
            name = next((n for n, v in WIDTHS.items() if v == w), str(w))
        self._width_name = name

    def _open_width_menu(self):
        self._close_color_palette()
        menu_win = tk.Toplevel(self.win)
        menu_win.overrideredirect(True)
        menu_win.attributes("-topmost", True)
        rows = len(WIDTHS)
        bx = self._width_btn.winfo_rootx() - 24
        by = self._width_btn.winfo_rooty() + self._width_btn.winfo_height() + 6
        if by + rows * 28 > self.win.winfo_screenheight():
            by = self._width_btn.winfo_rooty() - rows * 28 - 6
        menu_win.geometry(f"+{max(0, bx)}+{max(0, by)}")
        menu_win.configure(bg="#ffffff", highlightthickness=1,
                           highlightbackground=T.LINE)
        self._width_menu = menu_win
        for i, (name, w) in enumerate(WIDTHS.items()):
            row = tk.Frame(menu_win, bg="#ffffff", cursor="hand2")
            row.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            cv = tk.Canvas(row, width=52, height=22, bg="#ffffff",
                           highlightthickness=0, cursor="hand2")
            cv.pack(side="left")
            cv.create_line(8, 7, 42, 7, fill=T.INK_2, width=1.2)
            cv.create_line(8, 12, 42, 12, fill=T.INK_2, width=2.2)
            cv.create_line(8, 17, 42, 17, fill=T.INK_2, width=3.4)
            lb = tk.Label(row, text=f"{name}（{w}px）", bg="#ffffff",
                          fg=T.INK, font=("Microsoft YaHei UI", 10),
                          cursor="hand2")
            lb.pack(side="left", padx=6)

            def _pick(v=w, n=name):
                try:
                    menu_win.destroy()
                except Exception:
                    pass
                self._width_menu = None
                self.editor.set_width(v, n)

            def _enter(_e=None, r=row, l=lb):
                r.configure(bg=T.ACCENT_SOFT)
                l.configure(bg=T.ACCENT_SOFT)

            def _leave(_e=None, r=row, l=lb):
                r.configure(bg="#ffffff")
                l.configure(bg="#ffffff")

            for wid in (row, cv, lb):
                wid.bind("<Button-1>", lambda e, f=_pick: f())
                wid.bind("<Enter>", _enter)
                wid.bind("<Leave>", _leave)
            if name == self._width_name:
                _enter()
        menu_win.bind("<FocusOut>", lambda e: self._close_width_menu())

    def _close_width_menu(self):
        if self._width_menu is not None:
            try:
                self._width_menu.destroy()
            except Exception:
                pass
            self._width_menu = None

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class EditorWindow:
    """截图编辑窗口：窗口尺寸=截图尺寸，截图内容停在原屏幕坐标；
    工具栏为独立悬浮窗口，由 _position_toolbar 按屏幕边界排布并跟随。"""

    def __init__(self, root, image, on_done, on_cancel, offset_x=0, offset_y=0,
                 on_pin=None, full_image=None, sel_box=None, on_record=None):
        self.root = root
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_pin = on_pin
        self.on_record = on_record
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.undo_stack = []

        self.tool = TOOL_RECT
        self.color = COLORS["红"]
        self.width = WIDTHS["常规"]
        self._start = None
        self._preview = None
        self._pen_points = []
        self._text_entry = None
        self._text_objs = []
        self._drag_text = None
        self._drag_off = (0, 0)
        # 调整选区状态
        self._adjust_handle = None
        self._adjust_move = False
        self._adjust_off = (0, 0)

        # 默认调整选区模式：基于整张全屏截图工作，初始选区为截图范围
        if full_image is not None:
            self.full_img = full_image.convert("RGB")
            self.mode = MODE_ADJUST
            if sel_box:
                x1, y1, x2, y2 = sel_box
                self.sel = [max(0, x1), max(0, y1),
                            min(x2, self.full_img.width),
                            min(y2, self.full_img.height)]
            else:
                self.sel = [0, 0, self.full_img.width, self.full_img.height]
            self.img = self.full_img.copy()
        else:
            # 旧路径（无全屏图）：直接进入标注模式
            self.full_img = image.convert("RGB")
            self.mode = MODE_DRAW
            self.sel = [0, 0, self.full_img.width, self.full_img.height]
            self.img = self.full_img.copy()

        self.w = self.img.width
        self.h = self.img.height
        self.tool_h = tool_h = T.TOOLBAR_H

        # 跨屏支持：窗口坐标用虚拟屏幕范围（多显示器时原点可能为负）
        user32 = ctypes.windll.user32
        self.vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        self.vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        self.vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        self.vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        self.sw = self.vx + self.vw
        self.sh = self.vy + self.vh
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        # ---------- 画布：尺寸=当前工作图尺寸（大图可滚动） ----------
        self.canvas = tk.Canvas(self.win, width=self.w,
                                height=min(self.h, self.sh - tool_h),
                                highlightthickness=0, cursor="crosshair", bg=T.CANVAS)
        self.canvas.pack(fill="both", expand=True)
        self._setup_scrollbar()
        self._refresh_display()
        if self.mode == MODE_ADJUST:
            # 调整选区模式：默认光标=箭头（悬停/按下时按位置即时切换）
            self.canvas.configure(cursor="arrow")

        # ---------- 窗口尺寸与位置：调整模式=全屏（虚拟屏幕原点）；标注=选区原位 ----------
        self.win.update_idletasks()
        self._apply_window_geometry()

        # ---------- 独立悬浮工具栏 ----------
        self.toolbar = ToolbarWindow(self, root)
        self._position_toolbar()
        self.win.bind("<Configure>", lambda e: self._position_toolbar())

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_hover)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Return>", lambda e: self.done())
        self.win.bind("<Control-z>", lambda e: self.undo())
        self.win.bind("<Control-Z>", lambda e: self.undo())
        for i, tool in enumerate(TOOLS, 1):
            self.win.bind(f"<Key-{i}>",
                          lambda e, t=tool: self._on_tool_key(t))
        self.win.focus_force()
        self.toolbar._highlight_tool()
        try:
            self.toolbar.win.lift()
        except Exception:
            pass

    def _setup_scrollbar(self):
        """按当前工作图高度动态启用/停用纵向滚动条。"""
        avail_h = self.sh - self.vy
        need = self.h > avail_h
        sb = getattr(self, "_sb", None)
        if need:
            if sb is None or not sb.winfo_exists():
                sb = tk.Scrollbar(self.win, orient="vertical",
                                  command=self.canvas.yview)
                self._sb = sb
                self.canvas.pack_forget()
                self.canvas.pack(side="left", fill="both", expand=True)
                sb.pack(side="right", fill="y")
            self.canvas.configure(yscrollcommand=self._sb.set,
                                  scrollregion=(0, 0, self.w, self.h))
        else:
            if sb is not None and sb.winfo_exists():
                try:
                    sb.pack_forget()
                    sb.destroy()
                except Exception:
                    pass
                try:
                    del self._sb
                except Exception:
                    pass
            self.canvas.configure(scrollregion=(0, 0, self.w, self.h))

    def _apply_window_geometry(self):
        """按当前模式设置窗口尺寸与位置：调整模式=全屏图（虚拟屏幕原点）；
        标注模式=选区原位（clamp 虚拟屏幕）。"""
        canvas_h = min(self.h, self.sh - self.vy)
        win_w, win_h = self.w, canvas_h
        if self.mode == MODE_ADJUST:
            wx, wy = self.vx, self.vy
        else:
            wx, wy = self.offset_x, self.offset_y
        x = max(self.vx, min(wx, self.sw - win_w))
        y = max(self.vy, min(wy, self.sh - win_h))
        self.win.geometry(f"{win_w}x{win_h}+{x}+{y}")

    def _position_toolbar(self):
        """悬浮工具栏排布：优先截图（调整模式=当前选区框）下方外侧，下方无空间则放上方外侧；
        上下均无空间时选择遮挡最小的一侧；左右越界自动 clamp。
        标注模式用 winfo_rootx/rooty 真实屏幕坐标（winfo_x/y 是相对父窗口，
        ScreenSnap 的 root 是隐藏托盘窗口、不在屏幕原点，会导致工具栏错位盖住图片）。"""
        try:
            self.win.update_idletasks()
            if self.mode == MODE_ADJUST:
                # 调整选区模式：工具栏跟随选区红框（窗口固定全屏，不能作为基准）
                x1, y1, x2, y2 = self.sel
                win_x = self.vx + x1
                win_y = self.vy + y1
                win_h = y2 - y1
            else:
                win_x = self.win.winfo_rootx()
                win_y = self.win.winfo_rooty()
                win_h = self.win.winfo_height()
        except Exception:
            return
        bar_w = self.toolbar.bar_w
        tx = win_x
        if tx + bar_w > self.sw:
            tx = max(self.vx, self.sw - bar_w)
        # 垂直排布：先试窗口正下方外侧，再试正上方外侧；
        # 上下都放不下（窗口几乎占满屏幕）时，选遮挡图片面积最小的一侧
        ty = win_y + win_h
        if ty + self.tool_h > self.sh:
            ty_up = win_y - self.tool_h
            if ty_up >= self.vy:
                ty = ty_up
            else:
                over_down = win_y + win_h + self.tool_h - self.sh   # 放下方会遮挡的像素数
                over_up = self.tool_h - (win_y - self.vy)           # 放上方会遮挡的像素数
                ty = ty_up if over_up <= over_down else (self.sh - self.tool_h)
        ty = max(self.vy, min(ty, self.sh - self.tool_h))
        self.toolbar.win.geometry(f"{bar_w}x{self.tool_h}+{tx}+{ty}")
        # 泵一次空闲事件：overrideredirect 窗口首次移动时 SetWindowPos 可能延迟，
        # 强制应用位置，避免背景条按旧 rooty 绘制
        self.toolbar.win.update_idletasks()
        # 工具栏必须保持在截图窗口之上：全屏截图窗口被点击/提升时会盖住工具栏
        try:
            self.toolbar.win.lift()
        except Exception:
            pass
        # 工具条正下方不再绘制背景条

    # ---------- 显示 ----------
    def _refresh_display(self):
        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(self.img)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        # 文字层：未合成前以透明图层形式预览（可拖动/双击重编辑）
        for obj in self._text_objs:
            layer, off = self._render_text_img(obj)
            obj["photo"] = ImageTk.PhotoImage(layer)
            obj["item"] = self.canvas.create_image(
                obj["x"] - off, obj["y"] - off, image=obj["photo"], anchor="nw",
                tags=("textitem",))
        # 调整模式：选区红框+手柄；标注模式：红框包裹工作图（与贴图同色同宽）
        if self.mode == MODE_ADJUST:
            self._draw_adjust_overlay()
        else:
            self.canvas.create_rectangle(1, 1, self.w - 1, self.h - 1,
                                         outline=T.DANGER, width=2)

    def _render_text_img(self, obj):
        """将单个文字对象渲染为透明图层（含白描边），返回 (RGBA图, 左上留白)。"""
        font = _load_font(obj["font_size"])
        tmp = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        bbox = ImageDraw.Draw(tmp).textbbox((0, 0), obj["txt"], font=font,
                                            stroke_width=2)
        tw = max(4, bbox[2] - bbox[0] + 6)
        th = max(4, bbox[3] - bbox[1] + 6)
        layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((3 - bbox[0], 3 - bbox[1]), obj["txt"], font=font,
               fill=tuple(obj["color"]) + (255,),
               stroke_width=2, stroke_fill=(255, 255, 255, 255))
        return layer, 3

    def _flatten_texts(self):
        """完成/盯住前把文字对象合成进图片。"""
        if not self._text_objs:
            return
        draw = ImageDraw.Draw(self.img)
        for obj in self._text_objs:
            font = _load_font(obj["font_size"])
            draw.text((obj["x"], obj["y"]), obj["txt"], font=font,
                      fill=tuple(obj["color"]),
                      stroke_width=2, stroke_fill=(255, 255, 255))

    def _text_at(self, x, y):
        """命中检测：返回坐标处的文字对象（后添加的优先）。"""
        for obj in reversed(self._text_objs):
            item = obj.get("item")
            if item is None:
                continue
            bbox = self.canvas.bbox(item)
            if bbox and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
                return obj
        return None

    # ---------- 工具/样式 ----------
    def _on_tool_key(self, tool):
        """数字键切工具；文字输入中不切换（数字作为正常字符输入）。"""
        if self._text_entry is not None:
            return
        self.set_tool(tool)

    def set_tool(self, tool):
        if self.mode == MODE_ADJUST:
            # 调整模式下选择任意标注工具：先提交当前选区（裁剪出工作图），再切换
            if not self._commit_selection():
                return
        self.tool = tool
        self.toolbar._highlight_tool()

    def set_color(self, rgb):
        self.color = rgb
        self.toolbar.set_color(rgb)

    def set_width(self, w, name=None):
        self.width = w
        self.toolbar.set_width(w, name)

    # ---------- 撤销 / 完成 / 盯住 / 取消 ----------
    def _push_undo(self):
        self.undo_stack.append((self.img.copy(),
                                [dict(o) for o in self._text_objs]))
        if len(self.undo_stack) > 10:
            self.undo_stack.pop(0)

    def undo(self):
        if self.mode == MODE_ADJUST:
            return  # 调整选区模式不压撤销栈，撤销不可用
        if self.undo_stack:
            self.img, self._text_objs = self.undo_stack.pop()
            self._clear_preview()
            self._refresh_display()

    def _finish_and_close(self, callback):
        self._finish_text_entry()
        self._flatten_texts()
        try:
            x, y = self.win.winfo_x(), self.win.winfo_y()
        except Exception:
            x, y = self.offset_x, self.offset_y
        self.toolbar.destroy()
        try:
            self.win.destroy()
        except Exception:
            pass
        callback(self.img, x, y)

    def done(self):
        if self.mode == MODE_ADJUST:
            # 调整模式完成：以当前选区裁剪后直接输出（不压撤销栈）
            self._finish_adjust()
            return
        self._finish_and_close(self.on_done)

    def pin(self):
        """盯住：完成并钉在屏幕（不进历史库、不复制剪贴板）。"""
        if self.mode == MODE_ADJUST:
            self._finish_adjust(self.on_pin if self.on_pin else self.on_done)
            return
        self._finish_and_close(self.on_pin if self.on_pin else self.on_done)

    def request_record(self):
        """录制入口：关闭当前编辑窗口（不保存标注），以当前截图选区作为录屏范围回调 on_record。

        回调参数 rect=(left, top, width, height) 为虚拟屏幕坐标；
        调整选区模式用 self.sel（全屏图坐标）加虚拟原点换算，
        标注模式直接用窗口停靠坐标 offset_x/offset_y 与截图尺寸。
        """
        if self.mode == MODE_ADJUST:
            x1, y1, x2, y2 = self.sel
            rect = (self.vx + x1, self.vy + y1, x2 - x1, y2 - y1)
        else:
            rect = (self.offset_x, self.offset_y, self.w, self.h)
        self.cancel()
        if self.on_record:
            self.on_record(rect)

    def _finish_adjust(self, callback=None):
        """调整模式输出：按当前选区裁剪出最终图，以选区在虚拟屏幕的位置回调。"""
        if callback is None:
            callback = self.on_done
        x1, y1, x2, y2 = self.sel
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.full_img.width, x2), min(self.full_img.height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        img = self.full_img.crop((x1, y1, x2, y2))
        out_x, out_y = self.vx + x1, self.vy + y1
        self.toolbar.destroy()
        try:
            self.win.destroy()
        except Exception:
            pass
        callback(img, out_x, out_y)

    def cancel(self):
        self.toolbar.destroy()
        try:
            self.win.destroy()
        except Exception:
            pass
        self.on_cancel()

    # ---------- 鼠标交互 ----------
    def _img_coords(self, event):
        """canvas 可视坐标 -> 图片坐标（滚动后自动换算偏移）。"""
        return int(self.canvas.canvasx(event.x)), int(self.canvas.canvasy(event.y))

    def _on_hover(self, event):
        # 调整选区模式：光标随悬停位置即时切换（四角/四边/内部/外部）
        if self.mode == MODE_ADJUST:
            # 按下拖动中保持按下时设定的光标，不随位置闪跳
            if self._adjust_handle or self._adjust_move:
                return
            x, y = self._img_coords(event)
            self.canvas.configure(cursor=self._adjust_cursor_name(x, y))
            return
        if self.tool == TOOL_TEXT and self._text_entry:
            pass  # 输入框打开时不操作

    def _on_press(self, event):
        # 输入框打开时，点击别处 = 提交文字
        if self._text_entry is not None:
            self._commit_text()
            return
        x, y = self._img_coords(event)
        # 调整选区模式：拖手柄/移动选区（不压撤销栈）
        if self.mode == MODE_ADJUST:
            self._adjust_press(x, y)
            return
        # 点击已有文字：进入拖动移动模式
        hit = self._text_at(x, y)
        if hit is not None:
            self._drag_text = hit
            bbox = self.canvas.bbox(hit["item"])
            self._drag_off = (x - bbox[0], y - bbox[1])
            self.canvas.configure(cursor="fleur")
            return
        self._push_undo()
        if self.tool == TOOL_TEXT:
            self._open_text_entry(x, y, event.x, event.y)
            return
        self._start = (x, y)
        self._pen_points = [(x, y)]
        if self.tool == TOOL_PEN:
            self._preview = self.canvas.create_line(
                x, y, x + 1, y + 1, fill=self._hex(self.color), width=self.width)

    def _on_double_click(self, event):
        """双击已有文字：打开输入框重编辑。"""
        if self._text_entry is not None:
            return
        x, y = self._img_coords(event)
        hit = self._text_at(x, y)
        if hit is not None:
            self._edit_text_obj(hit, event.x, event.y)

    def _on_drag(self, event):
        # 调整选区模式：拖动手柄/移动选区
        if self.mode == MODE_ADJUST:
            x, y = self._img_coords(event)
            self._adjust_drag(x, y)
            return
        # 拖动文字对象：实时移动
        if self._drag_text is not None:
            x, y = self._img_coords(event)
            obj = self._drag_text
            nx, ny = x - self._drag_off[0], y - self._drag_off[1]
            dx, dy = nx - obj["x"], ny - obj["y"]
            obj["x"], obj["y"] = nx, ny
            if obj["item"] is not None:
                self.canvas.move(obj["item"], dx, dy)
            return
        if not self._start:
            return
        x, y = self._img_coords(event)
        x0, y0 = self._start
        if self.tool == TOOL_PEN:
            self._pen_points.append((x, y))
            self.canvas.coords(self._preview, *[v for p in self._pen_points for v in p])
            return
        if self._preview:
            self.canvas.delete(self._preview)
        self.canvas.delete("preview")
        if self.tool in (TOOL_RECT, TOOL_MOSAIC):
            self._preview = self.canvas.create_rectangle(
                x0, y0, x, y, outline=self._hex(self.color), width=self.width)
        elif self.tool == TOOL_ELLIPSE:
            self._preview = self.canvas.create_oval(
                x0, y0, x, y, outline=self._hex(self.color), width=self.width)
        elif self.tool == TOOL_LINE:
            self._preview = self.canvas.create_line(
                x0, y0, x, y, fill=self._hex(self.color), width=self.width)
        elif self.tool == TOOL_ARROW:
            self._preview = self.canvas.create_line(
                x0, y0, x, y, fill=self._hex(self.color), width=self.width)
            # 预览箭头头部（实心三角，方向指向当前鼠标）
            length = math.hypot(x - x0, y - y0)
            if length >= 8:
                ux, uy = (x - x0) / length, (y - y0) / length
                px, py = -uy, ux
                hl = max(10, self.width * 3)
                self.canvas.create_polygon(
                    [(x, y),
                     (x - ux * hl + px * hl * 0.55, y - uy * hl + py * hl * 0.55),
                     (x - ux * hl - px * hl * 0.55, y - uy * hl - py * hl * 0.55)],
                    fill=self._hex(self.color), tags=("preview",))

    def _on_release(self, event):
        # 调整选区模式：结束手柄/移动操作，释放后恢复悬停判定
        if self.mode == MODE_ADJUST:
            self._adjust_handle = None
            self._adjust_move = False
            x, y = self._img_coords(event)
            self.canvas.configure(cursor=self._adjust_cursor_name(x, y))
            return
        # 结束文字拖动
        if self._drag_text is not None:
            self._drag_text = None
            self.canvas.configure(cursor="crosshair")
            return
        if not self._start:
            return
        x, y = self._img_coords(event)
        x0, y0 = self._start
        self._start = None
        self._clear_preview()
        draw = ImageDraw.Draw(self.img)
        w = self.width
        if self.tool == TOOL_RECT:
            draw.rectangle([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                           outline=self.color, width=w)
        elif self.tool == TOOL_ELLIPSE:
            draw.ellipse([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                         outline=self.color, width=w)
        elif self.tool == TOOL_LINE:
            draw.line([(x0, y0), (x, y)], fill=self.color, width=w)
        elif self.tool == TOOL_ARROW:
            self._draw_arrow(draw, x0, y0, x, y)
        elif self.tool == TOOL_PEN:
            if len(self._pen_points) >= 2:
                draw.line(self._pen_points, fill=self.color, width=w, joint="curve")
        elif self.tool == TOOL_MOSAIC:
            self._apply_mosaic(min(x0, x), min(y0, y), max(x0, x), max(y0, y))
        self._refresh_display()

    # ---------- 调整选区模式 ----------
    def _commit_selection(self):
        """调整模式 -> 标注模式：按当前选区裁剪出工作图并重定位窗口。"""
        x1, y1, x2, y2 = self.sel
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.full_img.width, x2), min(self.full_img.height, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return False
        self.mode = MODE_DRAW
        self.sel = [x1, y1, x2, y2]
        self.img = self.full_img.crop((x1, y1, x2, y2))
        self.w = self.img.width
        self.h = self.img.height
        # 选区在虚拟屏幕的位置（全屏图坐标 + 虚拟原点）
        self.offset_x = self.vx + x1
        self.offset_y = self.vy + y1
        self.canvas.configure(width=self.w,
                              height=min(self.h, self.sh - self.vy))
        self._setup_scrollbar()
        self._apply_window_geometry()
        self._refresh_display()
        return True

    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, r, fill="", outline="",
                    width=1, tags=()):
        """Canvas 圆角矩形（平滑多边形方案，与 ToolbarWindow 同款）。"""
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return canvas.create_polygon(pts, smooth=True, fill=fill,
                                     outline=outline, width=width, tags=tags)

    def _draw_dim_badge(self, tags, x1, y1, x2, text):
        """顶部居中尺寸徽标：accent 底白字 12px/600 Segoe UI。"""
        import tkinter.font as tkfont
        f = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        tw = f.measure(text)
        pad, bh = 11, 22
        bw = tw + pad * 2
        cx = (x1 + x2) // 2
        by = y1 + 10
        if by + bh > self.sel[3]:
            by = max(0, y1 - bh - 6)
        self._round_rect(self.canvas, cx - bw // 2, by, cx + bw // 2, by + bh,
                         6, fill=T.ACCENT, outline="", tags=tags)
        self.canvas.create_text(cx, by + bh // 2, text=text, fill="#ffffff",
                                font=("Segoe UI", 12, "bold"), tags=tags)

    def _draw_hint_strip(self, tags, x1, y1):
        """左上角编辑态提示条：半透明白胶囊（Canvas 无 alpha，用近白模拟）。"""
        import tkinter.font as tkfont
        text = "调整选区 · 拖动移动 · 拉角缩放 · Enter 确认 · Esc 取消"
        f = tkfont.Font(family="Microsoft YaHei UI", size=11)
        tw = f.measure(text)
        pad, bh = 14, 26
        bw = tw + pad * 2
        hx, hy = x1 + 8, y1 + 8
        if hy + bh > self.sel[3]:
            hy = max(0, y1 - bh - 8)
        self._round_rect(self.canvas, hx, hy, hx + bw, hy + bh, 13,
                         fill="#F4F6F9", outline=T.LINE, width=1, tags=tags)
        self.canvas.create_text(hx + bw // 2, hy + bh // 2, text=text,
                                fill=T.INK_2,
                                font=("Microsoft YaHei UI", 11), tags=tags)

    def _draw_adjust_overlay(self, tags=("adjust",)):
        """SHUTTER 取景器选区：accent 2px 描边 + 四角 L 形取景角标（14px/2.5px）
        + 8 手柄（视觉 8px 白底 accent 边）+ 顶部尺寸徽标 + 左上角编辑态提示条。"""
        self.canvas.delete("adjust")
        x1, y1, x2, y2 = self.sel
        # 主色描边
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=T.ACCENT,
                                     width=2, tags=tags)
        # 四角取景器角标（L 形，14px，2.5px 主色，圆帽）
        L = 14
        for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1),
                               (x1, y2, 1, -1), (x2, y2, -1, -1)):
            self.canvas.create_line(cx, cy, cx + dx * L, cy,
                                    fill=T.ACCENT, width=3,
                                    capstyle=tk.ROUND, tags=tags)
            self.canvas.create_line(cx, cy, cx, cy + dy * L,
                                    fill=T.ACCENT, width=3,
                                    capstyle=tk.ROUND, tags=tags)
        # 8 个手柄：视觉 8px 白底 + accent 1.5px 边
        hs = 8
        pts = {
            "nw": (x1, y1), "n": ((x1 + x2) // 2, y1), "ne": (x2, y1),
            "w": (x1, (y1 + y2) // 2), "e": (x2, (y1 + y2) // 2),
            "sw": (x1, y2), "s": ((x1 + x2) // 2, y2), "se": (x2, y2),
        }
        for name, (hx, hy) in pts.items():
            self.canvas.create_rectangle(
                hx - hs // 2, hy - hs // 2, hx + hs // 2, hy + hs // 2,
                fill="#ffffff", outline=T.ACCENT, width=1.5, tags=tags)
        # 顶部居中尺寸徽标 + 左上角编辑态提示条
        self._draw_dim_badge(tags, x1, y1, x2, f"{x2 - x1} × {y2 - y1}")
        self._draw_hint_strip(tags, x1, y1)

    def _redraw_adjust_only(self):
        """拖动过程中仅重画选区 overlay，避免整图重载闪烁。"""
        self._draw_adjust_overlay(tags=("adjust",))

    def _hit_adjust_handle(self, x, y):
        """命中检测：返回手柄名（nw/ne/sw/se/n/s/e/w），未命中返回 None。
        命中区 12×12（视觉手柄 8px）。"""
        x1, y1, x2, y2 = self.sel
        hs = 12
        pts = {
            "nw": (x1, y1), "n": ((x1 + x2) // 2, y1), "ne": (x2, y1),
            "w": (x1, (y1 + y2) // 2), "e": (x2, (y1 + y2) // 2),
            "sw": (x1, y2), "s": ((x1 + x2) // 2, y2), "se": (x2, y2),
        }
        for name, (hx, hy) in pts.items():
            if abs(x - hx) <= hs and abs(y - hy) <= hs:
                return name
        return None

    def _adjust_cursor_name(self, x, y):
        """调整选区模式光标判定：四角=对角调整、四边=水平/垂直调整、
        内部=移动(fleur)、外部=默认(arrow)。"""
        h = self._hit_adjust_handle(x, y)
        if h:
            return {"nw": "size_nw_se", "se": "size_nw_se",
                    "ne": "size_ne_sw", "sw": "size_ne_sw",
                    "n": "size_ns", "s": "size_ns",
                    "e": "size_we", "w": "size_we"}[h]
        x1, y1, x2, y2 = self.sel
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "fleur"
        return "arrow"

    def _adjust_press(self, x, y):
        h = self._hit_adjust_handle(x, y)
        if h:
            self._adjust_handle = h
            # 按下即切换为对应方向调整光标，拖动过程中保持不变
            self.canvas.configure(cursor=self._adjust_cursor_name(x, y))
            return
        x1, y1, x2, y2 = self.sel
        if x1 <= x <= x2 and y1 <= y <= y2:
            self._adjust_move = True
            self._adjust_off = (x - x1, y - y1)
            # 移动选区：手型光标，拖动过程中保持不变
            self.canvas.configure(cursor="fleur")
        else:
            # 选区外点击：不响应，光标保持箭头
            self._adjust_handle = None
            self._adjust_move = False
            self.canvas.configure(cursor="arrow")

    def _adjust_drag(self, x, y):
        x1, y1, x2, y2 = self.sel
        MIN = 8
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
            # clamp 到全屏图范围并保持最小尺寸，防止翻转
            x1 = max(0, min(x1, self.w - MIN))
            y1 = max(0, min(y1, self.h - MIN))
            x2 = max(MIN, min(x2, self.w))
            y2 = max(MIN, min(y2, self.h))
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
            self._redraw_adjust_only()
            self._position_toolbar()
        elif self._adjust_move:
            dx = x - self._adjust_off[0]
            dy = y - self._adjust_off[1]
            w = x2 - x1
            hh = y2 - y1
            nx1 = max(0, min(dx, self.w - w))
            ny1 = max(0, min(dy, self.h - hh))
            self.sel = [nx1, ny1, nx1 + w, ny1 + hh]
            self._redraw_adjust_only()
            self._position_toolbar()

    # ---------- 马赛克 ----------
    def _draw_arrow(self, draw, x0, y0, x1, y1):
        """画带实心三角箭头的线（跟随当前颜色/线宽）。"""
        draw.line([(x0, y0), (x1, y1)], fill=self.color, width=self.width)
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 8:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        hl = max(10, self.width * 3)
        ax1 = x1 - ux * hl + px * hl * 0.55
        ay1 = y1 - uy * hl + py * hl * 0.55
        ax2 = x1 - ux * hl - px * hl * 0.55
        ay2 = y1 - uy * hl - py * hl * 0.55
        draw.polygon([(x1, y1), (ax1, ay1), (ax2, ay2)], fill=self.color)

    def _apply_mosaic(self, x1, y1, x2, y2):
        x1 = max(0, x1); y1 = max(0, y1)
        x2 = min(self.w, x2); y2 = min(self.h, y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            return
        region = self.img.crop((x1, y1, x2, y2))
        w2, h2 = region.size
        small = region.resize((max(1, w2 // 8), max(1, h2 // 8)), Image.NEAREST)
        block = small.resize((w2, h2), Image.NEAREST)
        self.img.paste(block, (x1, y1))

    # ---------- 文字 ----------
    def _open_text_entry(self, ix, iy, vx, vy):
        """新建文字输入框。"""
        self._text_pos = (ix, iy)
        self._editing_obj = None
        self._text_entry = tk.Entry(self.win, font=("Microsoft YaHei UI", 12),
                                    bg="white", fg="black")
        # 工具栏已独立悬浮，截图窗口内无工具栏占位，可视坐标直接使用
        self._text_entry.place(x=vx, y=vy - 6, width=200)
        self._text_entry.focus_set()
        self._text_entry.bind("<Return>", lambda e: self._commit_text() or "break")
        self._text_entry.bind("<Escape>", lambda e: self._finish_text_entry(cancel=True) or "break")

    def _edit_text_obj(self, obj, vx, vy):
        """双击已有文字：打开输入框回填内容重编辑。"""
        self._text_pos = (obj["x"], obj["y"])
        self._editing_obj = obj
        self._text_entry = tk.Entry(self.win, font=("Microsoft YaHei UI", 12),
                                    bg="white", fg="black")
        self._text_entry.place(x=vx, y=vy - 6, width=200)
        self._text_entry.insert(0, obj["txt"])
        self._text_entry.focus_set()
        self._text_entry.bind("<Return>", lambda e: self._commit_text() or "break")
        self._text_entry.bind("<Escape>", lambda e: self._finish_text_entry(cancel=True) or "break")

    def _add_text(self, pos, txt):
        self._text_objs.append({"x": pos[0], "y": pos[1], "txt": txt,
                                "font_size": max(12, self.width * 4),
                                "color": tuple(self.color),
                                "item": None, "photo": None})
        self._refresh_display()

    def _commit_text(self):
        """提交当前输入框内容（点击别处 / Return / 完成时调用）。"""
        if self._text_entry is None:
            return
        txt = self._text_entry.get().strip()
        try:
            self._text_entry.destroy()
        except Exception:
            pass
        self._text_entry = None
        if not txt:
            self._editing_obj = None
            self.win.focus_force()
            return
        if self._editing_obj is not None:
            # 重编辑：替换原文字对象
            self._editing_obj["txt"] = txt
            self._editing_obj["font_size"] = max(12, self.width * 4)
            self._editing_obj["color"] = tuple(self.color)
            self._editing_obj = None
            self._refresh_display()
        else:
            self._add_text(self._text_pos, txt)
        # 焦点还给编辑窗口：文字已提交，下次 Enter 即确认并关闭
        self.win.focus_force()

    def _finish_text_entry(self, cancel=False):
        """Esc 取消时丢弃；其余调用等同提交。"""
        if self._text_entry is not None:
            if cancel:
                try:
                    self._text_entry.destroy()
                except Exception:
                    pass
                self._text_entry = None
                self._editing_obj = None
            else:
                self._commit_text()

    # ---------- 工具 ----------
    def _clear_preview(self):
        if self._preview:
            try:
                self.canvas.delete(self._preview)
            except Exception:
                pass
            self._preview = None
        try:
            self.canvas.delete("preview")
        except Exception:
            pass

    @staticmethod
    def _hex(rgb):
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
