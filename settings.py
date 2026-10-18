# -*- coding: utf-8 -*-
"""设置页：快捷键自定义(冲突检测)、保存格式、OCR、开机自启、回收站管理。"""
import os
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import messagebox

# ---- UI 风格常量（对齐 editor.py ToolbarWindow：浅灰圆角条带 / 深灰文字）----
UI_BG = "#f7f7f7"          # 浅灰条带主体
UI_BG_DEEP = "#e8e8e8"     # 悬停/次级
UI_FG = "#333333"          # 深灰主文字
UI_FG_DIM = "#888888"      # 辅助文字
UI_BORDER = "#e3e3e3"      # 条带描边
UI_SELECT = "#d7e3f4"      # 选中态（editor 工具高亮）
UI_ACCEPT = "#07c160"      # 绿色确认✓
UI_ACCEPT_D = "#06ad56"    # 绿色悬停
UI_CANCEL = "#e5484d"      # 红色取消✕
UI_CANCEL_D = "#d93b40"    # 红色悬停
UI_FONT = ("Microsoft YaHei UI", 9)
UI_FONT_SM = ("Microsoft YaHei UI", 8)


class SettingsWindow:
    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.cfg = app.config

        self.win = tk.Toplevel(root)
        self.win.title("ScreenSnap - 设置")
        # 多屏定位：放到鼠标所在屏幕（托盘设置点击处），避免跑偏到其他显示器
        SettingsWindow._place_on_pointer(self.win, 560, 680)
        self.win.resizable(False, False)
        # 不设置 transient：根窗口处于 withdraw 隐藏态时，transient 子窗口在
        # 部分 Windows 环境下不会显示（点击设置"啥都没有"）。
        # self.win.transient(root)

        self._recording = False
        self._record_old = ""

        body = tk.Frame(self.win, bg=UI_BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)

        # ---------- 快捷键 ----------
        self._section(body, "全局截图快捷键")
        row = tk.Frame(body, bg=UI_BG)
        row.pack(fill="x", pady=2)
        self.hotkey_var = tk.StringVar(value=self.cfg.get("hotkey", "Ctrl+Alt+A"))
        self.hotkey_entry = tk.Entry(row, textvariable=self.hotkey_var, width=20,
                                     font=("Consolas", 12), relief="solid", bd=1,
                                     highlightthickness=1,
                                     highlightbackground=UI_BORDER,
                                     highlightcolor=UI_ACCEPT)
        self.hotkey_entry.pack(side="left", padx=4)
        self.rec_btn = self._btn(row, "录制按键", self._toggle_record)
        self.rec_btn.pack(side="left", padx=4)
        self._btn(row, "应用并检测冲突", self.apply_hotkey).pack(side="left", padx=8)
        self.rec_hint = tk.Label(body, text="", bg=UI_BG, fg=UI_ACCEPT,
                                 font=UI_FONT_SM)
        tk.Label(body, text="格式示例：Ctrl+Alt+A / Ctrl+Shift+S / Alt+F1",
                 bg=UI_BG, fg=UI_FG_DIM, font=UI_FONT_SM).pack(anchor="w", padx=4)

        # ---------- 保存格式 ----------
        self._section(body, "保存格式")
        fmt_row = tk.Frame(body, bg=UI_BG)
        fmt_row.pack(fill="x", pady=3)
        self.format_var = tk.StringVar(value=self.cfg.get("save_format", "png"))
        for text, val in (("PNG（清晰无损）", "png"), ("JPG（体积更小）", "jpg")):
            tk.Radiobutton(fmt_row, text=text, variable=self.format_var,
                           value=val, bg=UI_BG, fg=UI_FG,
                           activebackground=UI_BG, activeforeground=UI_FG,
                           selectcolor="#ffffff", font=UI_FONT,
                           highlightthickness=0).pack(side="left", padx=8)

        # ---------- OCR ----------
        self._section(body, "OCR 文字识别")
        ocr_row = tk.Frame(body, bg=UI_BG)
        ocr_row.pack(fill="x", pady=3)
        self.ocr_var = tk.BooleanVar(value=bool(self.cfg.get("ocr_enabled", False)))
        tk.Checkbutton(ocr_row, text="启用 OCR（历史库/贴图中可复制图中文字）",
                       variable=self.ocr_var, bg=UI_BG, fg=UI_FG,
                       activebackground=UI_BG, activeforeground=UI_FG,
                       selectcolor="#ffffff", font=UI_FONT,
                       highlightthickness=0).pack(side="left", padx=4)
        ocr_ok = self._ocr_available()
        tk.Label(ocr_row, text="Tesseract 引擎：已安装" if ocr_ok else "Tesseract 引擎：未安装（启用后需安装）",
                 bg=UI_BG, fg="#2f7d32" if ocr_ok else "#b26a00",
                 font=UI_FONT_SM).pack(side="left", padx=10)

        # ---------- 开机自启 ----------
        self._section(body, "开机自启")
        auto_row = tk.Frame(body, bg=UI_BG)
        auto_row.pack(fill="x", pady=3)
        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_start", True)))
        tk.Checkbutton(auto_row, text="开机自动启动（托盘常驻，随时按快捷键截图）",
                       variable=self.auto_var, bg=UI_BG, fg=UI_FG,
                       activebackground=UI_BG, activeforeground=UI_FG,
                       selectcolor="#ffffff", font=UI_FONT,
                       highlightthickness=0).pack(side="left", padx=4)

        # ---------- 保存路径 ----------
        self._section(body, "截图保存位置")
        save_row = tk.Frame(body, bg=UI_BG)
        save_row.pack(fill="x", pady=3)
        save_root = self.cfg.get("save_root", "")
        tk.Label(save_row, text=save_root, bg="#ffffff", fg=UI_FG,
                 font=("Consolas", 9), anchor="w", padx=6, pady=4,
                 relief="solid", bd=1,
                 highlightthickness=1, highlightbackground=UI_BORDER,
                 highlightcolor=UI_BORDER).pack(fill="x", padx=4)
        self._btn(save_row, "更改...", self.choose_save_root).pack(anchor="w", padx=8, pady=4)

        # ---------- 回收站 ----------
        self._section(body, "回收站")
        trash_row = tk.Frame(body, bg=UI_BG)
        trash_row.pack(fill="x", pady=3)
        self.trash_var = tk.StringVar(
            value=f"{self.app.storage.trash_count()} 条记录（删除后保留 {self.cfg.get('trash_days', 3)} 天）")
        tk.Label(trash_row, textvariable=self.trash_var, bg=UI_BG, fg=UI_FG,
                 font=UI_FONT).pack(side="left", padx=8)
        self._btn(trash_row, "立即清空回收站", self.empty_trash,
                  danger=True).pack(side="left", padx=8)

        # ---------- 回收站保留天数 ----------
        self._section(body, "回收站保留天数")
        days_row = tk.Frame(body, bg=UI_BG)
        days_row.pack(fill="x", pady=3)
        trash_days_val = int(self.cfg.get("trash_days", 3))
        self.trash_days_var = tk.StringVar(value=str(trash_days_val))
        days_opts = ["0", "3", "7", "30"]
        if str(trash_days_val) not in days_opts:
            days_opts.insert(0, str(trash_days_val))
        self._option_menu(days_row, self.trash_days_var, *days_opts).pack(side="left", padx=4)
        tk.Label(days_row, text="天（0 = 立即删除不保留）",
                 bg=UI_BG, fg=UI_FG_DIM, font=UI_FONT).pack(side="left", padx=6)

        # ---------- 历史库每列最多张数 ----------
        self._section(body, "历史库每列最多张数")
        cap_row = tk.Frame(body, bg=UI_BG)
        cap_row.pack(fill="x", pady=3)
        cap_val = int(self.cfg.get("column_capacity", 10))
        self.column_cap_var = tk.StringVar(value=str(cap_val))
        cap_opts = ["5", "7", "10"]
        if str(cap_val) not in cap_opts:
            cap_opts.insert(0, str(cap_val))
        self._option_menu(cap_row, self.column_cap_var, *cap_opts).pack(side="left", padx=4)
        tk.Label(cap_row, text="张/列（历史库按日期分组时每列最多显示张数）",
                 bg=UI_BG, fg=UI_FG_DIM, font=UI_FONT).pack(side="left", padx=6)

        # ---------- 保存 ----------
        bar = tk.Frame(self.win, bg=UI_BG)
        tk.Frame(bar, bg=UI_BORDER, height=1).pack(fill="x", pady=(0, 8))
        bar.pack(fill="x", padx=20, pady=(0, 12))
        tk.Button(bar, text="✓ 保存设置", command=self.save_all,
                  bg=UI_ACCEPT, fg="white", activebackground=UI_ACCEPT_D,
                  font=("Microsoft YaHei UI", 10, "bold"), relief="flat", bd=0,
                  padx=22, pady=5, cursor="hand2",
                  highlightthickness=1, highlightbackground=UI_ACCEPT,
                  highlightcolor=UI_ACCEPT).pack(side="right")

        # 强制显示并置顶，避免在隐藏根窗口下不渲染
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.win.update_idletasks()

    # ---------- UI 辅助（对齐 editor.py 风格） ----------
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

    @staticmethod
    def _round_rect(cv, x1, y1, x2, y2, r, **kw):
        """在 Canvas 上绘制圆角矩形（同 editor.py）。"""
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def _section(self, parent, title):
        """分区标题：深灰粗体 + 绿色圆角小条（呼应 editor 工具条带）。"""
        row = tk.Frame(parent, bg=UI_BG)
        row.pack(fill="x", anchor="w", pady=(10, 2))
        tag = tk.Canvas(row, width=4, height=14, bg=UI_BG,
                        highlightthickness=0, bd=0)
        tag.pack(side="left", padx=(2, 7))
        self._round_rect(tag, 0, 1, 4, 14, 2, fill=UI_ACCEPT, outline="")
        tk.Label(row, text=title, bg=UI_BG, fg=UI_FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

    def _btn(self, parent, text, command, danger=False):
        """统一浅灰按钮（hover 变深）；danger 为红色警示（对齐取消✕色）。"""
        b = tk.Button(parent, text=text, command=command, relief="flat", bd=0,
                      bg=UI_ACCEPT if danger else UI_BG,
                      fg="white" if danger else UI_FG,
                      activebackground=UI_ACCEPT_D if danger else UI_BG_DEEP,
                      activeforeground="white" if danger else UI_FG,
                      font=UI_FONT, cursor="hand2", padx=10, pady=3,
                      highlightthickness=1,
                      highlightbackground=(UI_ACCEPT if danger else UI_BORDER),
                      highlightcolor=(UI_ACCEPT if danger else UI_BORDER))
        if not danger:
            b.bind("<Enter>", lambda e: b.configure(bg=UI_BG_DEEP), add="+")
            b.bind("<Leave>", lambda e: b.configure(bg=UI_BG), add="+")
        return b

    def _option_menu(self, parent, var, *values):
        """统一下拉菜单：浅灰按钮 + 白底弹出、选中态高亮。"""
        mb = tk.OptionMenu(parent, var, *values)
        mb.config(bg=UI_BG, fg=UI_FG, activebackground=UI_BG_DEEP,
                  activeforeground=UI_FG, relief="flat", bd=0,
                  font=UI_FONT, cursor="hand2",
                  highlightthickness=1, highlightbackground=UI_BORDER,
                  highlightcolor=UI_BORDER)
        menu = mb["menu"]
        menu.config(bg="#ffffff", fg=UI_FG, activebackground=UI_SELECT,
                    activeforeground="#1f1f1f", bd=0, relief="flat",
                    font=UI_FONT)
        return mb

    # ---------- 按键录制 ----------
    def _toggle_record(self):
        if self._recording:
            self._stop_record(restore=True)
            return
        self._recording = True
        self._record_old = self.hotkey_var.get()
        self._record_mods = set()
        self.rec_btn.config(text="取消录制")
        self.rec_hint.config(text="请按下组合键…（Esc 取消）")
        self.rec_hint.pack(anchor="w", padx=4)
        self.win.bind("<KeyPress>", self._on_record_key, add="+")
        self.win.bind("<KeyRelease>", self._on_record_release, add="+")
        self.win.bind("<FocusOut>", self._on_record_focusout, add="+")
        # 焦点移到窗口自身，避免按键落入输入框/按钮
        self.win.focus_force()

    def _stop_record(self, restore=False):
        if not self._recording:
            return
        self._recording = False
        self._record_mods = set()
        try:
            self.win.unbind("<KeyPress>")
            self.win.unbind("<KeyRelease>")
            self.win.unbind("<FocusOut>")
        except Exception:
            pass
        self.rec_btn.config(text="录制按键")
        self.rec_hint.pack_forget()
        if restore:
            self.hotkey_var.set(self._record_old)

    def _on_record_release(self, event):
        # 修饰键释放：从维护的集合中移除，防止状态残留
        ks = event.keysym
        if ks in ("Control_L", "Control_R"):
            self._record_mods.discard("Ctrl")
        elif ks in ("Alt_L", "Alt_R"):
            self._record_mods.discard("Alt")
        elif ks in ("Shift_L", "Shift_R"):
            self._record_mods.discard("Shift")

    def _on_record_focusout(self, _event=None):
        # 失焦清空，避免残留修饰键状态
        self._record_mods.clear()

    def _on_record_key(self, event):
        # Esc：取消监听并恢复原值
        if event.keysym == "Escape":
            self._stop_record(restore=True)
            return
        # 纯修饰键：用键码维护集合，不读 event.state
        # （本机 event.state 会被键盘固件污染：按 Ctrl+Shift+A 时错误带上 Alt 位 0x8）
        ks = event.keysym
        if ks in ("Control_L", "Control_R"):
            self._record_mods.add("Ctrl")
            return
        if ks in ("Alt_L", "Alt_R"):
            self._record_mods.add("Alt")
            return
        if ks in ("Shift_L", "Shift_R"):
            self._record_mods.add("Shift")
            return
        if ks in ("Super_L", "Super_R", "Win_L", "Win_R",
                  "Caps_Lock", "Num_Lock"):
            return
        # 主键限定：字母/数字/F1-F12
        if len(ks) == 1 and ks.isalnum():
            key = ks.upper()
        elif (ks.startswith("F") and ks[1:].isdigit()
                and 1 <= int(ks[1:]) <= 12):
            key = ks
        else:
            return
        mods = []
        for m in ("Ctrl", "Alt", "Shift"):
            if m in self._record_mods:
                mods.append(m)
        self.hotkey_var.set("+".join(mods + [key]))
        self._stop_record(restore=False)

    def _ocr_available(self):
        try:
            import ocr
            return ocr.is_available()
        except Exception:
            return False

    # ---------- 动作 ----------
    def apply_hotkey(self):
        text = self.hotkey_var.get().strip()
        ok, err = self.app.change_hotkey(text)
        if ok:
            messagebox.showinfo("ScreenSnap", f"快捷键已生效：{text}", parent=self.win)
        else:
            messagebox.showerror("ScreenSnap", err or "快捷键无效", parent=self.win)
        self.hotkey_var.set(self.cfg.get("hotkey", ""))

    def choose_save_root(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(title="选择截图保存目录", parent=self.win)
        if p:
            self.cfg["save_root"] = p
            from config import save_config
            save_config(self.cfg)
            messagebox.showinfo("ScreenSnap", f"保存位置已更新：{p}", parent=self.win)

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
        self.cfg["ocr_enabled"] = bool(self.ocr_var.get())
        self.cfg["auto_start"] = bool(self.auto_var.get())
        self.cfg["trash_days"] = int(self.trash_days_var.get())
        self.cfg["column_capacity"] = int(self.column_cap_var.get())
        from config import save_config
        save_config(self.cfg)
        self.app.storage.trash_days = self.cfg["trash_days"]  # 同步到已实例化的 storage，立即生效
        self.app.apply_auto_start()
        self.trash_var.set(
            f"{self.app.storage.trash_count()} 条记录（删除后保留 {self.cfg.get('trash_days', 3)} 天）")
        # 先弹提示（跟随设置窗口），确认后再关闭设置页
        messagebox.showinfo("ScreenSnap", "设置已保存", parent=self.win)
        self.win.destroy()
