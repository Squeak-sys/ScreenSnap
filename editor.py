# -*- coding: utf-8 -*-
"""编辑层：截图窗口（尺寸=截图尺寸）+ 独立悬浮工具栏（跟随截图窗口排布）。
支持框选/圆选/划线/画笔/马赛克/文字 + 撤销/取消/完成。"""
import ctypes
import math
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk

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

COLORS = {
    "红": (230, 33, 23), "橙": (255, 133, 27), "黄": (255, 204, 0),
    "绿": (34, 167, 24), "蓝": (0, 102, 204), "紫": (128, 0, 128),
    "黑": (10, 10, 10), "白": (255, 255, 255),
}
WIDTHS = {"细": 2, "中": 5, "粗": 10}

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
        tk.Label(self.tip, text=self.text, bg="#ffffe0", fg="#333",
                 font=("Microsoft YaHei UI", 9), relief="solid",
                 borderwidth=1, padx=5, pady=2).pack()

    def _hide(self, event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


def _make_tool_icons():
    """生成工具栏工具图标（PIL 绘制），返回 {tool: PhotoImage}。"""
    size = 20
    icons = {}

    def _new():
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        return img, ImageDraw.Draw(img)

    # 框选：矩形边框
    img, d = _new()
    d.rectangle([3, 3, size - 4, size - 4], outline="#4a4a4a", width=2)
    icons[TOOL_RECT] = ImageTk.PhotoImage(img)
    # 圆选：圆形边框
    img, d = _new()
    d.ellipse([3, 3, size - 4, size - 4], outline="#4a4a4a", width=2)
    icons[TOOL_ELLIPSE] = ImageTk.PhotoImage(img)
    # 划线：斜线
    img, d = _new()
    d.line([(3, size - 4), (size - 4, 3)], fill="#4a4a4a", width=2)
    icons[TOOL_LINE] = ImageTk.PhotoImage(img)
    # 画笔：S 型曲线（两段弧拼接）
    img, d = _new()
    d.arc([3, 3, size - 3, size - 9], start=0, end=180, fill="#4a4a4a", width=2)
    d.arc([3, size - 9, size - 3, size - 3], start=180, end=360, fill="#4a4a4a", width=2)
    icons[TOOL_PEN] = ImageTk.PhotoImage(img)
    # 马赛克：3x3 色块阵列
    img, d = _new()
    cell = (size - 4) // 3
    for i in range(3):
        for j in range(3):
            d.rectangle([2 + i * cell, 2 + j * cell,
                         1 + (i + 1) * cell, 1 + (j + 1) * cell], fill="#4a4a4a")
    icons[TOOL_MOSAIC] = ImageTk.PhotoImage(img)
    # 文字：大写 T
    img, d = _new()
    try:
        font = ImageFont.truetype(FONT_PATH, 13)
    except Exception:
        font = ImageFont.load_default()
    d.text((4, 2), "T", font=font, fill="#4a4a4a")
    icons[TOOL_TEXT] = ImageTk.PhotoImage(img)
    # 箭头：斜线 + 实心三角箭头（指向右上，加粗加大更醒目；
    # 三角头底边垂直箭身、尖在箭身延长线上，头身方向一致）
    img, d = _new()
    d.line([(2, 17), (10, 9)], fill="#4a4a4a", width=3)
    d.polygon([(14, 5), (12, 11), (8, 7)], fill="#4a4a4a")
    icons[TOOL_ARROW] = ImageTk.PhotoImage(img)
    # 盯住：图钉（横向收窄的窄图钉）
    img, d = _new()
    d.ellipse([6, 2, 14, 10], outline="#4a4a4a", width=2)
    d.line([(10, 10), (10, 15)], fill="#4a4a4a", width=2)
    d.line([(8, 15), (12, 15)], fill="#4a4a4a", width=2)
    icons["pin"] = ImageTk.PhotoImage(img)
    return icons


class ToolbarWindow:
    """独立悬浮工具栏：不占用截图窗口空间，位置由截图窗口按屏幕边界排布。"""

    def __init__(self, editor, root):
        self.editor = editor
        self.tool_h = tool_h = 32
        # 透明色：圆角条带以外的区域不显示（Windows transparentcolor）
        self._transparent = "#010203"
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-transparentcolor", self._transparent)
        except Exception:
            pass
        self.win.configure(bg=self._transparent)

        self._icons = _make_tool_icons()
        # Canvas 容器：承载浅灰圆角条带背景与全部按钮（create_window 挂载）
        self.canvas = tk.Canvas(self.win, bg=self._transparent,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        btn_h = 24          # 所有按钮统一高度（紧凑）
        cy = 15             # 按钮中心 y（条带主体 y=1..28 中心 14.5）
        gap = 2             # 按钮间间距
        self._normal_bg = {}   # 按钮常态底色（hover 离开时恢复）

        def _btn(bg="#f7f7f7", **kw):
            kw.setdefault("relief", "flat")
            kw.setdefault("bd", 0)
            kw.setdefault("cursor", "hand2")
            kw.setdefault("bg", bg)
            kw.setdefault("activebackground", "#e8e8e8")
            kw.setdefault("highlightthickness", 1)
            kw.setdefault("highlightbackground", bg)
            kw.setdefault("highlightcolor", bg)
            # 统一字体，保证文字/色块按钮请求高度一致（行高由字体决定）
            kw.setdefault("font", ("Microsoft YaHei UI", 9))
            return tk.Button(self.win, **kw)

        def _hover_bg(b, hover_bg):
            b.bind("<Enter>",
                   lambda e, bb=b, h=hover_bg: bb.configure(bg=h), add="+")
            b.bind("<Leave>",
                   lambda e, bb=b: bb.configure(bg=self._normal_bg.get(bb, "#f7f7f7")),
                   add="+")

        items = []          # [(widget, width), ...]，None 表示分隔线占位

        # ---- 分区A：工具图标按钮（深灰图标，紧凑） ----
        self._tool_btns = {}
        for i, tool in enumerate(TOOLS, 1):
            b = _btn(image=self._icons[tool],
                     command=lambda t=tool: self.editor.set_tool(t))
            self._tool_btns[tool] = b
            self._normal_bg[b] = "#f7f7f7"
            _hover_bg(b, "#e8e8e8")
            items.append((30, b))
            Tooltip(b, f"{TOOL_NAMES[tool]}（快捷键 {i}）")

        # ---- 分区B：颜色 ----
        items.append(None)
        self._color_palette = None
        # 色块按钮：与其它按钮同字体（_btn 默认），不额外设置 height，
        # 使请求高度与相邻图标/文字按钮一致（实际渲染高度由 create_window 统一钳制）
        self._color_btn = _btn(text="",
                               bg="#e62117", activebackground="#e62117",
                               highlightbackground="#d0d0d0", highlightcolor="#d0d0d0",
                               command=self._open_color_palette)
        items.append((26, self._color_btn))
        Tooltip(self._color_btn, "颜色（点击展开选择）")

        # ---- 分区C：线宽 ----
        items.append(None)
        self._width_name = "中"
        self._width_btn = _btn(text="线宽·中", font=("Microsoft YaHei UI", 9),
                               fg="#333333", padx=6,
                               command=self._open_width_menu)
        self._normal_bg[self._width_btn] = "#f7f7f7"
        _hover_bg(self._width_btn, "#e8e8e8")
        items.append([None, self._width_btn])   # 宽度稍后按 reqwidth 填充

        # ---- 分区D：撤销 ----
        items.append(None)
        undo_b = _btn(text="撤销", font=("Microsoft YaHei UI", 9),
                      fg="#333333", padx=8,
                      command=self.editor.undo)
        self._normal_bg[undo_b] = "#f7f7f7"
        _hover_bg(undo_b, "#e8e8e8")
        items.append([None, undo_b])            # 宽度稍后按 reqwidth 填充
        Tooltip(undo_b, "撤销上一步（Ctrl+Z）")

        # ---- 右侧按钮：盯住（pin）| 取消（红✕）| 完成（绿✓） ----
        pin_b = _btn(image=self._icons["pin"], command=self.editor.pin)
        self._normal_bg[pin_b] = "#f7f7f7"
        _hover_bg(pin_b, "#e8e8e8")
        cancel_b = _btn(text="✕", font=("Microsoft YaHei UI", 12, "bold"),
                        bg="#e5484d", fg="white", activebackground="#d93b40",
                        highlightbackground="#e5484d", highlightcolor="#e5484d",
                        command=self.editor.cancel)
        done_b = _btn(text="✓", font=("Microsoft YaHei UI", 12, "bold"),
                      bg="#07c160", fg="white", activebackground="#06ad56",
                      highlightbackground="#07c160", highlightcolor="#07c160",
                      command=self.editor.done)

        # ---- 计算总宽 ----
        self.win.update_idletasks()
        left_w = 12
        for it in items:
            if it is None:
                left_w += 11          # 分隔线占位（4 + 1 + 6）
            else:
                w, b = it
                if w is None:
                    w = b.winfo_reqwidth()
                    it[0] = w
                left_w += w + gap
        left_w += 14                  # 左右分区之间的留白
        right_w = 30 + gap + 34 + gap + 34   # pin + cancel + done
        self.bar_w = max(left_w + right_w + 12, 320)

        # ---- 圆角条带背景（先画背景，按钮随后挂在上层） ----
        self._round_rect(self.canvas, 1, 3, self.bar_w - 2, tool_h - 1, 10,
                         fill="#d9d9d9", outline="")          # 垫底：轻微底部阴影
        self._round_rect(self.canvas, 1, 1, self.bar_w - 2, tool_h - 4, 10,
                         fill="#f7f7f7", outline="#e3e3e3")   # 主体：浅灰圆角条带

        # ---- 放置左侧按钮 ----
        x = 12
        for it in items:
            if it is None:
                x += 11
            else:
                w, b = it
                self.canvas.create_window(x + w / 2, cy, anchor="center",
                                          width=w, height=btn_h, window=b)
                x += w + gap

        # ---- 放置右侧按钮（右对齐：完成最右、取消次之、盯住再左） ----
        rx = self.bar_w - 12
        c_done = rx - 17
        c_cancel = c_done - 36
        c_pin = c_cancel - 36
        self.canvas.create_window(c_pin, cy, anchor="center",
                                  width=30, height=btn_h, window=pin_b)
        self.canvas.create_window(c_cancel, cy, anchor="center",
                                  width=34, height=btn_h, window=cancel_b)
        self.canvas.create_window(c_done, cy, anchor="center",
                                  width=34, height=btn_h, window=done_b)
        Tooltip(cancel_b, "取消（Esc）")
        Tooltip(done_b, "完成（Enter）")
        Tooltip(pin_b, "盯住：钉在屏幕（不进历史库）")

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

    def _highlight_tool(self):
        for t, b in self._tool_btns.items():
            if self.editor.mode == MODE_ADJUST:
                # 调整选区模式：无激活工具，全部不高亮
                b.configure(bg="#f7f7f7", activebackground="#e8e8e8",
                            highlightbackground="#f7f7f7", highlightcolor="#f7f7f7")
            elif t == self.editor.tool:
                b.configure(bg="#d7e3f4", activebackground="#c4d6ef",
                            highlightbackground="#c4d6ef", highlightcolor="#c4d6ef")
            else:
                b.configure(bg="#f7f7f7", activebackground="#e8e8e8",
                            highlightbackground="#f7f7f7", highlightcolor="#f7f7f7")

    @staticmethod
    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        """在 Canvas 上绘制圆角矩形（smooth polygon 经典做法）。"""
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def set_color(self, rgb):
        hexc = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        self._color_btn.configure(bg=hexc,
                                  highlightbackground="#ffb800",
                                  highlightcolor="#ffb800")
        self._close_color_palette()

    def _open_color_palette(self):
        if self._color_palette is not None:
            self._close_color_palette()
            return
        pal = tk.Toplevel(self.win)
        pal.overrideredirect(True)
        pal.attributes("-topmost", True)
        bx = self._color_btn.winfo_rootx()
        by = self._color_btn.winfo_rooty() + self._color_btn.winfo_height()
        ph = 2 * 34 + 12
        if by + ph > self.win.winfo_screenheight():
            by = self._color_btn.winfo_rooty() - ph
        pal.geometry(f"+{bx}+{by}")
        for i, (name, rgb) in enumerate(COLORS.items()):
            hexc = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
            b = tk.Button(pal, text="", width=3, relief="flat", bd=0, bg=hexc,
                          command=lambda c=rgb: self.editor.set_color(c))
            b.grid(row=i // 4, column=i % 4, padx=2, pady=2)
            Tooltip(b, name)
        self._color_palette = pal

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
        self._width_btn.configure(text=f"线宽·{name}")

    def _open_width_menu(self):
        # 浮层互斥：打开线宽菜单前先关闭已展开的颜色面板
        self._close_color_palette()
        menu = tk.Menu(self.win, tearoff=0)
        for name, w in WIDTHS.items():
            menu.add_command(label=f"{name}（{w}px）",
                             command=lambda v=w, n=name: self.editor.set_width(v, n))
        try:
            menu.tk_popup(self._width_btn.winfo_rootx(),
                          self._width_btn.winfo_rooty() + self._width_btn.winfo_height())
        finally:
            menu.grab_release()

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class EditorWindow:
    """截图编辑窗口：窗口尺寸=截图尺寸，截图内容停在原屏幕坐标；
    工具栏为独立悬浮窗口，由 _position_toolbar 按屏幕边界排布并跟随。"""

    def __init__(self, root, image, on_done, on_cancel, offset_x=0, offset_y=0,
                 on_pin=None, full_image=None, sel_box=None):
        self.root = root
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.on_pin = on_pin
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.undo_stack = []

        self.tool = TOOL_RECT
        self.color = COLORS["红"]
        self.width = WIDTHS["中"]
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
        self.tool_h = tool_h = 32

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
                                highlightthickness=0, cursor="crosshair", bg="#ececec")
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
        """悬浮工具栏排布：优先截图（调整模式=当前选区框）下方，下方无空间则放上方；左右越界自动反向/clamp。"""
        try:
            if self.mode == MODE_ADJUST:
                # 调整选区模式：工具栏跟随选区红框（窗口固定全屏，不能作为基准）
                x1, y1, x2, y2 = self.sel
                win_x = self.vx + x1
                win_y = self.vy + y1
                win_h = y2 - y1
            else:
                win_x = self.win.winfo_x()
                win_y = self.win.winfo_y()
                win_h = self.win.winfo_height()
        except Exception:
            return
        bar_w = self.toolbar.bar_w
        tx = win_x
        if tx + bar_w > self.sw:
            tx = max(self.vx, self.sw - bar_w)
        ty = win_y + win_h
        if ty + self.tool_h > self.sh:
            ty = win_y - self.tool_h
        if ty < self.vy:
            ty = max(self.vy, self.sh - self.tool_h)
        self.toolbar.win.geometry(f"{bar_w}x{self.tool_h}+{tx}+{ty}")
        # 工具栏必须保持在截图窗口之上：全屏截图窗口被点击/提升时会盖住工具栏
        try:
            self.toolbar.win.lift()
        except Exception:
            pass

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
                                         outline="#e81123", width=2)

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

    def _draw_adjust_overlay(self, tags=("adjust",)):
        """绘制选区红框（#e81123 2px）+ 8 个调整手柄。"""
        self.canvas.delete("adjust")
        x1, y1, x2, y2 = self.sel
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#e81123",
                                     width=2, tags=tags)
        hs = 8
        pts = {
            "nw": (x1, y1), "n": ((x1 + x2) // 2, y1), "ne": (x2, y1),
            "w": (x1, (y1 + y2) // 2), "e": (x2, (y1 + y2) // 2),
            "sw": (x1, y2), "s": ((x1 + x2) // 2, y2), "se": (x2, y2),
        }
        for name, (hx, hy) in pts.items():
            self.canvas.create_rectangle(
                hx - hs // 2, hy - hs // 2, hx + hs // 2, hy + hs // 2,
                fill="#ffffff", outline="#e81123", width=1, tags=tags)

    def _redraw_adjust_only(self):
        """拖动过程中仅重画选区 overlay，避免整图重载闪烁。"""
        self._draw_adjust_overlay(tags=("adjust",))

    def _hit_adjust_handle(self, x, y):
        """命中检测：返回手柄名（nw/ne/sw/se/n/s/e/w），未命中返回 None。"""
        x1, y1, x2, y2 = self.sel
        hs = 8
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
