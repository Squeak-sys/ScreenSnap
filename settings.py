# -*- coding: utf-8 -*-
"""设置页（SHUTTER 设计语言）：左侧分区导航 + 右侧行式表单。
字段对应 config.json 现有字段：hotkey / save_format / save_root /
auto_start / auto_copy / trash_days / column_capacity，无新增概念。"""
import os
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox

import ui_theme as T


class Switch(tk.Canvas):
    """40×22 圆角开关：选中主色 + 滑块 after(16ms) 插值动画。"""

    def __init__(self, master, value=False, command=None):
        tk.Canvas.__init__(self, master, width=40, height=22,
                           bg=T.SURFACE, highlightthickness=0, bd=0,
                           cursor="hand2")
        self._on = bool(value)
        self._pos = 20 if self._on else 2
        self._command = command
        self._draw(self._pos)
        self.bind("<Button-1>", self._toggle)

    @staticmethod
    def _rr(cv, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def _draw(self, pos):
        self.delete("all")
        bg = T.ACCENT if self._on else "#CFD4DA"
        self._rr(self, 0, 0, 40, 22, 11, fill=bg, outline="")
        self.create_oval(pos, 2, pos + 18, 20, fill="#ffffff", outline="",
                         tags="slider")

    def _toggle(self, _e=None):
        self._on = not self._on
        self._anim_to(20 if self._on else 2)
        if self._command:
            self._command(self._on)

    def _anim_to(self, target):
        def _tick(cur):
            if cur == target:
                self._pos = cur
                return
            step = 3 if target > cur else -3
            nxt = cur + step
            if (nxt - target) * step > 0:
                nxt = target
            self._draw(nxt)
            self.after(16, _tick, nxt)
        _tick(self._pos)


class Seg(tk.Frame):
    """分段选择器：surface-2 底 + 白底选中项 + 阴影（对齐原型 .seg）。"""

    def __init__(self, master, options, value, command=None):
        tk.Frame.__init__(self, master, bg=T.SURFACE_2, bd=0,
                          highlightthickness=1, highlightbackground=T.LINE)
        self._choices = list(options)
        self._value = value
        self._command = command
        self._btns = {}
        for i, (text, val) in enumerate(self._choices):
            b = tk.Button(
                self, text=text, relief="flat", bd=0, cursor="hand2",
                padx=6, pady=4,
                font=("Microsoft YaHei UI", 11,
                      "bold" if val == value else "normal"),
                bg=T.SURFACE if val == value else T.SURFACE_2,
                fg=T.INK if val == value else T.INK_2,
                activebackground=T.SURFACE_3, activeforeground=T.INK,
                command=lambda v=val: self._select(v))
            b.grid(row=0, column=i, padx=1, pady=1)
            self._btns[val] = b

    def value(self):
        return self._value

    def _select(self, v):
        if v == self._value:
            return
        self._value = v
        for val, b in self._btns.items():
            on = val == v
            b.configure(bg=T.SURFACE if on else T.SURFACE_2,
                        fg=T.INK if on else T.INK_2,
                        font=("Microsoft YaHei UI", 11,
                              "bold" if on else "normal"))
        if self._command:
            self._command(v)


class Stepper(tk.Frame):
    """回收站天数步进器：− value +（对齐原型 .stepper）。"""

    def __init__(self, master, value, command=None, lo=0, hi=30,
                 suffix=" 天"):
        tk.Frame.__init__(self, master, bg=T.SURFACE, bd=0,
                          highlightthickness=1, highlightbackground=T.LINE)
        self._lo, self._hi = lo, hi
        self._value = int(value)
        self._suffix = suffix
        self._command = command
        self._minus = self._mk_btn("−", self._dec)
        self._minus.grid(row=0, column=0)
        self._val_lbl = tk.Label(self, width=5, text=f"{self._value}{suffix}",
                                 bg=T.SURFACE, fg=T.INK,
                                 font=("Microsoft YaHei UI", 11, "bold"))
        self._val_lbl.grid(row=0, column=1)
        self._plus = self._mk_btn("+", self._inc)
        self._plus.grid(row=0, column=2)

    def _mk_btn(self, text, cmd):
        return tk.Button(self, text=text, command=cmd, relief="flat", bd=0,
                         width=3, bg=T.SURFACE, fg=T.INK_2,
                         activebackground=T.SURFACE_3, activeforeground=T.INK,
                         font=("Microsoft YaHei UI", 12), cursor="hand2")

    def _dec(self):
        self.set(self._value - 1)

    def _inc(self):
        self.set(self._value + 1)

    def set(self, v):
        v = max(self._lo, min(self._hi, v))
        if v == self._value:
            return
        self._value = v
        self._val_lbl.configure(text=f"{v}{self._suffix}")
        if self._command:
            self._command(v)

    def value(self):
        return self._value


class HotkeyBox(tk.Frame):
    """热键录制框：点击进入监听态（蓝框 + 闪烁占位），Esc 取消。"""

    def __init__(self, master, value, on_record=None):
        tk.Frame.__init__(self, master, bg=T.SURFACE_2, bd=0,
                          highlightthickness=1, highlightbackground=T.LINE,
                          cursor="hand2", padx=8, pady=4)
        self._value = value
        self._on_record = on_record
        self._recording = False
        self._mods = set()
        self._blink_job = None
        self._render()
        self.bind("<Button-1>", self._start, add="+")
        for c in self.winfo_children():
            c.bind("<Button-1>", self._start, add="+")

    def _render(self):
        for w in self.winfo_children():
            w.destroy()
        parts = [p for p in self._value.split("+") if p]
        for i, p in enumerate(parts):
            if i:
                tk.Label(self, text="+", fg=T.INK_3, bg=self["bg"]).pack(
                    side="left", padx=1)
            tk.Label(self, text=p, fg=T.INK, bg="#ffffff", relief="solid",
                     bd=1, highlightthickness=1, highlightbackground=T.LINE,
                     font=("Segoe UI", 10, "bold"), padx=4, pady=1).pack(
                side="left", padx=1)

    def _start(self, _e=None):
        if self._recording:
            return
        self._recording = True
        self._mods = set()
        self._old = self._value
        self.configure(bg="#ffffff", highlightthickness=4,
                       highlightbackground=T.ACCENT, highlightcolor=T.ACCENT)
        for w in self.winfo_children():
            w.destroy()
        self._blink_lbl = tk.Label(self, text="请按下新的组合键…（Esc 取消）",
                                   fg=T.ACCENT, bg="#ffffff",
                                   font=("Microsoft YaHei UI", 10, "bold"))
        self._blink_lbl.pack(side="left", padx=4, pady=1)

        def _tick(flag):
            if not self._recording:
                return
            self._blink_lbl.configure(fg=T.ACCENT if flag else T.INK_3)
            self._blink_job = self.after(500, _tick, not flag)
        _tick(True)
        self.focus_force()
        self.bind("<KeyPress>", self._on_key, add="+")
        self.bind("<Escape>", self._cancel, add="+")
        self.bind("<FocusOut>", self._cancel, add="+")

    def _end_recording(self):
        self._recording = False
        if self._blink_job:
            try:
                self.after_cancel(self._blink_job)
            except Exception:
                pass
            self._blink_job = None
        try:
            self.unbind("<KeyPress>")
            self.unbind("<Escape>")
            self.unbind("<FocusOut>")
        except Exception:
            pass

    def _cancel(self, _e=None):
        if not self._recording:
            return
        self._end_recording()
        self._value = self._old
        self.configure(bg=T.SURFACE_2, highlightthickness=1,
                       highlightbackground=T.LINE)
        self._render()

    def _on_key(self, event):
        ks = event.keysym
        if ks == "Escape":
            self._cancel()
            return
        if ks in ("Control_L", "Control_R"):
            self._mods.add("Ctrl")
            return
        if ks in ("Alt_L", "Alt_R"):
            self._mods.add("Alt")
            return
        if ks in ("Shift_L", "Shift_R"):
            self._mods.add("Shift")
            return
        if ks in ("Super_L", "Super_R", "Win_L", "Win_R",
                  "Caps_Lock", "Num_Lock"):
            return
        if len(ks) == 1 and ks.isalnum():
            key = ks.upper()
        elif ks.startswith("F") and ks[1:].isdigit() and 1 <= int(ks[1:]) <= 12:
            key = ks
        else:
            return
        mods = [m for m in ("Ctrl", "Alt", "Shift") if m in self._mods]
        text = "+".join(mods + [key])
        self._end_recording()
        self._value = text
        self.configure(bg=T.SURFACE_2, highlightthickness=1,
                       highlightbackground=T.LINE)
        self._render()
        if self._on_record:
            self._on_record(text)

    def value(self):
        return self._value


class SettingsWindow:
    NAV = [("general", "⚙  常规"), ("capture", "✂  截图与保存"),
           ("hotkey", "⌨  热键"), ("storage", "◱  存储"), ("about", "ℹ  关于")]

    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.cfg = app.config

        self.win = tk.Toplevel(root)
        self.win.title("ScreenSnap - 设置")
        # 多屏定位：放到鼠标所在屏幕（托盘设置点击处），避免跑偏到其他显示器
        SettingsWindow._place_on_pointer(self.win, 720, 560)
        self.win.resizable(False, False)

        # 主容器：左侧导航 + 右侧内容
        body = tk.Frame(self.win, bg=T.SURFACE)
        body.pack(fill="both", expand=True)
        nav = tk.Frame(body, width=190, bg=T.SURFACE_2)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        content = tk.Frame(body, bg=T.SURFACE)
        content.pack(side="left", fill="both", expand=True)

        # 左侧分区导航
        self._nav_items = {}
        for key, label in self.NAV:
            b = tk.Label(nav, text=label, bg=T.SURFACE_2, fg=T.INK_2,
                         font=("Microsoft YaHei UI", 11), anchor="w",
                         padx=18, pady=8, cursor="hand2")
            b.pack(fill="x", pady=1)
            b.bind("<Button-1>", lambda e, k=key: self._show_page(k))
            self._nav_items[key] = b
        tk.Label(nav, text="ScreenSnap 2.0 · SHUTTER",
                 bg=T.SURFACE_2, fg=T.INK_3,
                 font=("Microsoft YaHei UI", 9)).pack(
            side="bottom", anchor="w", padx=18, pady=(0, 10))

        # 右侧分区页（place + tkraise 切换）
        self._pages = {}
        for key, _label in self.NAV:
            f = tk.Frame(content, bg=T.SURFACE)
            f.place(x=0, y=0, relwidth=1, relheight=1)
            self._pages[key] = f
        self._current = "general"
        self._show_page("general")

        self._build_general(self._pages["general"])
        self._build_capture(self._pages["capture"])
        self._build_hotkey(self._pages["hotkey"])
        self._build_storage(self._pages["storage"])
        self._build_about(self._pages["about"])

        # 底部保存栏
        bar = tk.Frame(self.win, bg=T.SURFACE)
        tk.Frame(bar, bg=T.LINE, height=1).pack(fill="x")
        bar.pack(fill="x", side="bottom")
        tk.Button(bar, text="✓ 保存设置", command=self.save_all,
                  bg=T.SUCCESS, fg="#ffffff", activebackground=T.SUCCESS_HOVER,
                  font=("Microsoft YaHei UI", 11, "bold"), relief="flat", bd=0,
                  padx=24, pady=6, cursor="hand2",
                  highlightthickness=0).pack(side="right", padx=20, pady=10)

        # 强制显示并置顶，避免在隐藏根窗口下不渲染
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.win.update_idletasks()

    # ---------- 导航与布局 ----------
    def _show_page(self, key):
        self._current = key
        for k, f in self._pages.items():
            if k == key:
                f.tkraise()
        for k, b in self._nav_items.items():
            on = k == key
            b.configure(bg=T.ACCENT_SOFT if on else T.SURFACE_2,
                        fg=T.ACCENT if on else T.INK_2,
                        font=("Microsoft YaHei UI", 11,
                              "bold" if on else "normal"))

    def _page_head(self, parent, title, desc):
        tk.Label(parent, text=title, bg=T.SURFACE, fg=T.INK,
                 font=("Microsoft YaHei UI", 15, "bold"),
                 anchor="w").pack(anchor="w", padx=32, pady=(24, 2))
        tk.Label(parent, text=desc, bg=T.SURFACE, fg=T.INK_2,
                 font=("Microsoft YaHei UI", 11), anchor="w").pack(
            anchor="w", padx=32, pady=(0, 6))
        tk.Frame(parent, bg=T.LINE, height=1).pack(fill="x", padx=32,
                                                   pady=(0, 6))

    def _add_row(self, parent, title, desc, ctrl):
        """行式表单：标题+说明左置，控件右置。"""
        row = tk.Frame(parent, bg=T.SURFACE)
        row.pack(fill="x", padx=32, pady=8)
        txt = tk.Frame(row, bg=T.SURFACE)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=title, bg=T.SURFACE, fg=T.INK,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(txt, text=desc, bg=T.SURFACE, fg=T.INK_2,
                 font=("Microsoft YaHei UI", 10), anchor="w",
                 justify="left", wraplength=300).pack(anchor="w", pady=(1, 0))
        # in_=row：控件的 master 是 page，必须 pack 到行容器内才能与标题同行右对齐；
        # ctrl 创建早于 row（层级更低会被 row 背景盖住），pack 后必须抬到最上层。
        # 注意 Switch 是 Canvas 子类，tkraise 被覆盖为条目命令，须走 Tcl 原生 raise
        ctrl.pack(in_=row, side="right", padx=(16, 0), anchor="n")
        self.win.tk.call("raise", ctrl._w)
        tk.Frame(parent, bg=T.LINE, height=1).pack(fill="x", padx=32)

    # ---------- 分区内容 ----------
    def _build_general(self, page):
        self._page_head(page, "常规", "基础行为与启动选项")
        self.auto_var = tk.BooleanVar(
            value=bool(self.cfg.get("auto_start", True)))
        sw1 = Switch(page, self.auto_var.get(),
                     command=lambda on, v=self.auto_var: v.set(on))
        self._add_row(page, "开机自动启动",
                      "登录 Windows 后在后台运行，随时按热键唤起", sw1)
        self.auto_copy_var = tk.BooleanVar(
            value=bool(self.cfg.get("auto_copy", True)))
        sw2 = Switch(page, self.auto_copy_var.get(),
                     command=lambda on, v=self.auto_copy_var: v.set(on))
        self._add_row(page, "截图后自动复制到剪贴板",
                      "完成后无需粘贴操作，直接 Ctrl+V 即用", sw2)

    def _build_capture(self, page):
        self._page_head(page, "截图与保存", "保存格式与目录")
        self.format_var = tk.StringVar(
            value=self.cfg.get("save_format", "png"))
        seg = Seg(page, [("PNG", "png"), ("JPG", "jpg")],
                  self.format_var.get(), command=self.format_var.set)
        self._add_row(page, "保存格式",
                      "PNG 无损（推荐），JPG 体积更小", seg)
        # 录屏帧率（从框选窗口迁移至设置页，默认 30）
        self.record_fps_seg = Seg(
            page, [("15", 15), ("25", 25), ("30", 30), ("60", 60)],
            int(self.cfg.get("record_fps", 30)))
        self._add_row(page, "录屏帧率",
                      "录制视频的帧率：越高越流畅，文件体积也越大", self.record_fps_seg)
        # 保存位置
        save_root = self.cfg.get("save_root", "")
        row = tk.Frame(page, bg=T.SURFACE)
        row.pack(fill="x", padx=32, pady=8)
        txt = tk.Frame(row, bg=T.SURFACE)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text="保存位置", bg=T.SURFACE, fg=T.INK,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(txt, text="历史库与自动保存共用此目录", bg=T.SURFACE,
                 fg=T.INK_2, font=("Microsoft YaHei UI", 10),
                 anchor="w").pack(anchor="w", pady=(1, 0))
        ctrl = tk.Frame(row, bg=T.SURFACE)
        ctrl.pack(side="right", padx=(16, 0))
        self._path_lbl = tk.Label(
            ctrl, text=save_root or "（未设置，默认我的图片）",
            bg=T.SURFACE_2, fg=T.INK_2, font=("Segoe UI", 10), anchor="w",
            padx=10, pady=6, width=36, highlightthickness=1,
            highlightbackground=T.LINE)
        self._path_lbl.pack(side="left")
        browse = tk.Button(
            ctrl, text="浏览…", command=self.choose_save_root,
            relief="flat", bd=0, bg=T.SURFACE_2, fg=T.INK,
            activebackground=T.SURFACE_3, activeforeground=T.INK,
            font=("Microsoft YaHei UI", 10), cursor="hand2",
            padx=12, pady=6, highlightthickness=1,
            highlightbackground=T.LINE)
        browse.pack(side="left", padx=(8, 0))
        tk.Frame(page, bg=T.LINE, height=1).pack(fill="x", padx=32)

    def _build_hotkey(self, page):
        self._page_head(page, "热键", "全局截图快捷键")
        self.hotkey_var = tk.StringVar(
            value=self.cfg.get("hotkey", "Ctrl+Alt+A"))
        self._hk_hint = tk.Label(page, text="", bg=T.SURFACE, fg=T.SUCCESS,
                                 font=("Microsoft YaHei UI", 10), anchor="w")
        hkbox = HotkeyBox(page, self.hotkey_var.get(),
                          on_record=self._on_hotkey_recorded)
        self._add_row(page, "全局截图热键",
                      "点击输入框后直接按下新组合键；与其他软件冲突时会即时提示",
                      hkbox)
        self._hk_hint.pack(anchor="w", padx=32, pady=(0, 4))
        tk.Label(page, text="格式示例：Ctrl+Alt+A / Ctrl+Shift+S / Alt+F1",
                 bg=T.SURFACE, fg=T.INK_3,
                 font=("Microsoft YaHei UI", 10), anchor="w").pack(
            anchor="w", padx=32)

    def _on_hotkey_recorded(self, text):
        ok, err = self.app.change_hotkey(text)
        if ok:
            self.hotkey_var.set(text)
            self._hk_hint.configure(text=f"已生效：{text}", fg=T.SUCCESS)
        else:
            self.hotkey_var.set(self.cfg.get("hotkey", ""))
            self._hk_hint.configure(text=err or "快捷键无效", fg=T.DANGER)

    def _build_storage(self, page):
        self._page_head(page, "存储", "回收站与历史库")
        # 回收站信息 + 清空
        self.trash_var = tk.StringVar(
            value=f"{self.app.storage.trash_count()} 条记录（删除后保留 "
                  f"{self.cfg.get('trash_days', 3)} 天）")
        row = tk.Frame(page, bg=T.SURFACE)
        row.pack(fill="x", padx=32, pady=8)
        txt = tk.Frame(row, bg=T.SURFACE)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text="回收站", bg=T.SURFACE, fg=T.INK,
                 font=("Microsoft YaHei UI", 11, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(txt, textvariable=self.trash_var, bg=T.SURFACE,
                 fg=T.INK_2, font=("Microsoft YaHei UI", 10),
                 anchor="w").pack(anchor="w", pady=(1, 0))
        tk.Button(row, text="立即清空回收站", command=self.empty_trash,
                  relief="flat", bd=0, bg=T.DANGER, fg="#ffffff",
                  activebackground=T.DANGER_HOVER, activeforeground="#ffffff",
                  font=("Microsoft YaHei UI", 10), cursor="hand2",
                  padx=12, pady=6).pack(side="right", padx=(16, 0))
        tk.Frame(page, bg=T.LINE, height=1).pack(fill="x", padx=32)
        # 保留天数步进器
        self.trash_days_stepper = Stepper(
            page, int(self.cfg.get("trash_days", 3)), lo=0, hi=30)
        self._add_row(page, "回收站保留天数",
                      "到期自动清理，期间可随时恢复", self.trash_days_stepper)
        # 历史库每列最多张数
        cap_val = int(self.cfg.get("column_capacity", 10))
        self.column_cap_var = tk.StringVar(value=str(cap_val))
        cap_opts = ["5", "7", "10"]
        if str(cap_val) not in cap_opts:
            cap_opts.insert(0, str(cap_val))
        om = self._option_menu(page, self.column_cap_var, *cap_opts)
        self._add_row(page, "历史库每列最多张数",
                      "历史库按日期分组时每列最多显示张数", om)

    def _build_about(self, page):
        self._page_head(page, "关于", "ScreenSnap 截图工具")
        rows = [("版本", "ScreenSnap 2.0"),
                ("设计语言", "SHUTTER（取景器）· Windows Fluent 适配"),
                ("配色", f"主色 {T.ACCENT} · 完成 {T.SUCCESS} · 危险 {T.DANGER}")]
        for i, (k, v) in enumerate(rows):
            row = tk.Frame(page, bg=T.SURFACE)
            row.pack(fill="x", padx=32, pady=6)
            tk.Label(row, text=k, bg=T.SURFACE, fg=T.INK_2, width=8,
                     font=("Microsoft YaHei UI", 10), anchor="w").pack(
                side="left")
            tk.Label(row, text=v, bg=T.SURFACE, fg=T.INK,
                     font=("Microsoft YaHei UI", 10), anchor="w").pack(
                side="left")
            if i < len(rows) - 1:
                tk.Frame(page, bg=T.LINE, height=1).pack(fill="x", padx=32)

    # ---------- UI 辅助 ----------
    @staticmethod
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

    @staticmethod
    def _place_on_pointer(win, w, h, margin=24):
        """把窗口放到鼠标所在屏幕内（优先居中，并 clamp 到可见区域）。"""
        try:
            u = ctypes.windll.user32

            class _PT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = _PT()
            u.GetCursorPos(ctypes.byref(pt))
            for l, t, r, b in SettingsWindow._monitor_rects():
                if l <= pt.x < r and t <= pt.y < b:
                    x = l + (r - l - w) // 2
                    y = t + (b - t - h) // 2
                    x = max(l + margin, min(x, r - w - margin))
                    y = max(t + margin, min(y, b - h - margin))
                    win.geometry(f"{w}x{h}+{x}+{y}")
                    return
        except Exception:
            pass
        win.geometry(f"{w}x{h}")  # 兜底：交给系统默认定位

    def _option_menu(self, parent, var, *values):
        mb = tk.OptionMenu(parent, var, *values)
        mb.config(bg=T.SURFACE, fg=T.INK, activebackground=T.SURFACE_3,
                  activeforeground=T.INK, relief="flat", bd=0,
                  font=("Microsoft YaHei UI", 10), cursor="hand2",
                  highlightthickness=1, highlightbackground=T.LINE)
        menu = mb["menu"]
        menu.config(bg="#ffffff", fg=T.INK, activebackground=T.ACCENT_SOFT,
                    activeforeground=T.ACCENT, bd=0, relief="flat",
                    font=("Microsoft YaHei UI", 10))
        return mb

    # ---------- 动作 ----------
    def choose_save_root(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(title="选择截图保存目录", parent=self.win)
        if p:
            self.cfg["save_root"] = p
            from config import save_config
            save_config(self.cfg)
            self._path_lbl.configure(text=p)

    def empty_trash(self):
        import shutil
        trash_dir = os.path.join(self.app.storage.save_root, ".trash")
        try:
            if os.path.exists(trash_dir):
                shutil.rmtree(trash_dir)
            self.trash_var.set("回收站已清空")
            messagebox.showinfo("ScreenSnap", "回收站已清空", parent=self.win)
        except Exception as e:
            messagebox.showerror("ScreenSnap", f"清空失败：{e}", parent=self.win)

    def save_all(self):
        self.cfg["hotkey"] = self.hotkey_var.get().strip()
        self.cfg["save_format"] = self.format_var.get()
        self.cfg["record_fps"] = int(self.record_fps_seg.value())
        self.cfg["auto_start"] = bool(self.auto_var.get())
        self.cfg["auto_copy"] = bool(self.auto_copy_var.get())
        self.cfg["trash_days"] = int(self.trash_days_stepper.value())
        self.cfg["column_capacity"] = int(self.column_cap_var.get())
        from config import save_config
        save_config(self.cfg)
        self.app.storage.trash_days = self.cfg["trash_days"]  # 同步到已实例化的 storage，立即生效
        self.app.apply_auto_start()
        self.trash_var.set(
            f"{self.app.storage.trash_count()} 条记录（删除后保留 "
            f"{self.cfg.get('trash_days', 3)} 天）")
        # 先弹提示（跟随设置窗口），确认后再关闭设置页
        messagebox.showinfo("ScreenSnap", "设置已保存", parent=self.win)
        self.win.destroy()
