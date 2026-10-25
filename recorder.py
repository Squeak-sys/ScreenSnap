# -*- coding: utf-8 -*-
"""屏幕录制模块：录制区域选择 + ffmpeg(gdigrab) 录制 + 悬浮状态条 + 统一入口。

后端：imageio-ffmpeg 提供 ffmpeg.exe，使用 gdigrab 直接捕获屏幕区域，
CPU 占用低、编码质量好，无需 numpy/opencv。
"""
import ctypes
import os
import subprocess
import time
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk

import ui_theme as T
from capture import virtual_screen

# ---------- ffmpeg 路径（惰性获取，取不到时友好报错） ----------
_ffmpeg_exe = None


def _get_ffmpeg_exe():
    global _ffmpeg_exe
    if _ffmpeg_exe is not None:
        return _ffmpeg_exe or None
    try:
        import imageio_ffmpeg
        _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _ffmpeg_exe = ""
    return _ffmpeg_exe or None


# ---------- 屏幕/显示器辅助（状态条定位到鼠标所在屏幕） ----------
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_long), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_long)]


def _monitor_rect_at(x, y):
    """返回鼠标所在显示器的 (left, top, right, bottom)；失败返回 None。"""
    try:
        u = ctypes.windll.user32
        pt = ctypes.wintypes.POINT(x, y)
        hmon = u.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        if not hmon:
            return None
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if not u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def _notify(title, msg):
    try:
        import dialogs
        dialogs.patch()
    except Exception:
        pass
    messagebox.showerror(title, msg)


# ---------- 录制区域选择窗口 ----------
class RecordRegionWindow:
    """无边框半透明全屏覆盖层，拖拽框选录制区域。

    返回 (left, top, width, height) 屏幕坐标；Enter/双击=确定，Esc=取消。
    多显示器使用虚拟屏幕坐标（capture.virtual_screen()）。
    """

    def __init__(self, root, on_result, on_cancel):
        self.root = root
        self.on_result = on_result
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self._start = None
        self._drag = None
        self._moved = False

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.canvas = tk.Canvas(self.win, width=self.vw, height=self.vh,
                                highlightthickness=0, cursor="crosshair",
                                bg="black")
        self.canvas.pack(fill="both", expand=True)
        try:
            # 半透明遮罩（Tk 8.6+ 支持 RGBA）；不支持时退化为纯黑
            self.canvas.create_rectangle(0, 0, self.vw, self.vh,
                                         fill="#000000b3", outline="")
        except Exception:
            self.canvas.create_rectangle(0, 0, self.vw, self.vh,
                                         fill="#000000", outline="")
        tip = "拖拽=框选录制区域  Enter/双击=确定  Esc=取消"
        # 右上角深色圆角提示条（半透明，不遮内容）
        tip_w, tip_h = 452, 30
        tip_x = self.vw - tip_w - 14
        tip_y = 14
        _cv_round_rect(self.canvas, tip_x, tip_y, tip_x + tip_w, tip_y + tip_h,
                       15, fill="#1C2430CC", outline="#ffffff22")
        self.canvas.create_text(tip_x + tip_w / 2, tip_y + tip_h / 2,
                                text=tip, fill="#ffffff", anchor="center",
                                font=("Microsoft YaHei UI", 10))

        # 帧率已迁移至设置页（record_fps），框选窗口不再提供帧率切换

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.win.bind("<Escape>", lambda e: self._close())
        self.win.bind("<Return>", lambda e: self._confirm())
        self.win.focus_force()

    def _to_screen(self, event):
        """事件物理屏幕坐标 -> 虚拟屏幕画布坐标。"""
        return (event.x_root - self.vx, event.y_root - self.vy)

    def _on_press(self, event):
        if self._drag:
            self.canvas.delete(self._drag)
            self._drag = None
        self._start = self._to_screen(event)
        self._moved = False

    def _on_drag(self, event):
        if self._start is None:
            return
        cur = self._to_screen(event)
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
        # 拖拽结束后保留选区矩形，等待 Enter/双击 确定
        pass

    def _on_double(self, event):
        self._finish((self.vx, self.vy, self.vw, self.vh))

    def _confirm(self):
        if self._drag is not None:
            coords = self.canvas.coords(self._drag)
            if coords:
                x1, y1, x2, y2 = coords
                w = abs(x2 - x1)
                h = abs(y2 - y1)
                if w >= 2 and h >= 2:
                    self._finish((self.vx + int(min(x1, x2)),
                                  self.vy + int(min(y1, y2)),
                                  int(w), int(h)))
                    return
        # 无有效选区：录制整个虚拟屏幕
        self._finish((self.vx, self.vy, self.vw, self.vh))

    def _finish(self, rect):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.on_result(rect)

    def _close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.on_cancel()


# ---------- ffmpeg(gdigrab) 录制封装 ----------
class ScreenRecorder:
    """封装 ffmpeg(gdigrab) 子进程。start(rect, out_path) -> stop()/cancel()。"""

    def __init__(self):
        self._proc = None
        self._log_path = None
        self._out_path = None

    def start(self, rect, out_path, fps=15):
        left, top, w, h = rect
        w, h = int(w), int(h)
        # libx264/yuv420p 要求宽高均为偶数，奇数尺寸会编码失败导致输出 0 字节
        if w < 2 or h < 2:
            raise RuntimeError("录制区域过小（宽高至少 2 像素）")
        if w % 2:
            w -= 1
        if h % 2:
            h -= 1
        exe = _get_ffmpeg_exe()
        if not exe:
            raise RuntimeError(
                "未找到 ffmpeg，请先安装依赖：pip install imageio-ffmpeg")
        cmd = [exe, "-f", "gdigrab", "-framerate", str(fps),
               "-offset_x", str(int(left)), "-offset_y", str(int(top)),
               "-video_size", f"{w}x{h}",
               "-i", "desktop",
               "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p",
               out_path]
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        self._log_path = os.path.splitext(out_path)[0] + ".log"
        logf = open(self._log_path, "w", encoding="utf-8", errors="replace")
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=logf,
                stderr=subprocess.STDOUT, creationflags=flags)
        except Exception:
            logf.close()
            raise
        self._out_path = out_path

    def stop(self):
        """正常结束：向 ffmpeg 发送 'q' 优雅退出并保存视频，返回输出路径。"""
        if self._proc is None:
            return None
        try:
            if self._proc.stdin:
                try:
                    self._proc.stdin.write(b"q\n")
                    self._proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except Exception:
                    self._proc.kill()
        finally:
            self._proc = None
        return self._out_path

    def cancel(self):
        """放弃录制：终止进程并删除半成品文件。"""
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._out_path and os.path.exists(self._out_path):
            try:
                os.remove(self._out_path)
            except Exception:
                pass


# ---------- 录制状态悬浮条 ----------
class RecStatusBar:
    """录制状态悬浮条：红点 + 计时（00:12）+ 停止（保存）/取消（删除）按钮。"""

    def __init__(self, root, rect, on_stop, on_cancel):
        self.root = root
        self.rect = rect
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self._t0 = None
        self._timer = None
        self._stopped = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=T.INK)

        frame = tk.Frame(self.win, bg=T.INK)
        frame.pack(padx=8, pady=4)
        tk.Label(frame, text="●", fg=T.DANGER, bg=T.INK,
                 font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 6))
        self.time_lbl = tk.Label(frame, text="00:00", fg="#ffffff",
                                 bg=T.INK, font=("Consolas", 11, "bold"))
        self.time_lbl.pack(side="left", padx=(0, 10))
        # 停止/取消：圆角胶囊按钮（风格对齐截图页完成按钮，Pillow 绘制两态图）
        stop_n, stop_h = T.make_pill_text("停止", T.SUCCESS, T.SUCCESS_HOVER,
                                          font_size=9, padx=10, pady=4)
        self._stop_n, self._stop_h = stop_n, stop_h
        stop_b = tk.Button(frame, image=stop_n, command=self._stop,
                           bd=0, relief="flat", highlightthickness=0,
                           cursor="hand2", bg=T.INK, activebackground=T.INK)
        stop_b.img = stop_n
        stop_b.config(width=stop_n.width(), height=stop_n.height())
        stop_b.pack(side="left", padx=(0, 4))
        stop_b.bind("<Enter>", lambda e: stop_b.configure(image=stop_h))
        stop_b.bind("<Leave>", lambda e: stop_b.configure(image=stop_n))
        cancel_n, cancel_h = T.make_pill_text("取消", T.DANGER, T.DANGER_HOVER,
                                              font_size=9, padx=10, pady=4)
        self._cancel_n, self._cancel_h = cancel_n, cancel_h
        cancel_b = tk.Button(frame, image=cancel_n, command=self._cancel,
                             bd=0, relief="flat", highlightthickness=0,
                             cursor="hand2", bg=T.INK, activebackground=T.INK)
        cancel_b.img = cancel_n
        cancel_b.config(width=cancel_n.width(), height=cancel_n.height())
        cancel_b.pack(side="left")
        cancel_b.bind("<Enter>", lambda e: cancel_b.configure(image=cancel_h))
        cancel_b.bind("<Leave>", lambda e: cancel_b.configure(image=cancel_n))
        self.win.bind("<Escape>", lambda e: self._stop())

        self._place()
        _exclude_from_capture(self.win)
        self.win.deiconify()

    def start(self):
        """正式开始计时（倒计时结束后由调用方触发）。"""
        if self._t0 is None:
            self._t0 = time.time()
        if not self._stopped:
            self._tick()

    def close(self):
        self._close_win()

    def _place(self):
        """悬停在录制框上沿中间，随录制框位置走（root 是隐藏托盘窗口，必须用真实屏幕坐标）。"""
        left, top, w, _h = self.rect
        cx = left + w // 2
        y = top - 44
        if y < 0:
            y = top + 8  # 录制框贴屏幕顶端时改放框内顶部
        self.win.geometry(f"+{cx - 92}+{y}")

    def _tick(self):
        if self._stopped:
            return
        el = int(time.time() - self._t0)
        mm, ss = divmod(el, 60)
        self.time_lbl.configure(text=f"{mm:02d}:{ss:02d}")
        try:
            self._timer = self.win.after(1000, self._tick)
        except Exception:
            pass

    def _close_win(self):
        if self._timer:
            try:
                self.win.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            self.win.destroy()
        except Exception:
            pass
        # 点击按钮后键盘焦点还给 root
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._close_win()
        self.on_stop()

    def _cancel(self):
        if self._stopped:
            return
        self._stopped = True
        self._close_win()
        self.on_cancel()


# ---------- 录制范围框（常驻显示） ----------
def _exclude_from_capture(win):
    """让窗口不出现在任何屏幕录制/截屏中（Win10 2004+ WDA_EXCLUDEFROMCAPTURE）。"""
    try:
        win.update_idletasks()
        hwnd = int(win.winfo_id())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass


def _cv_round_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上绘制圆角矩形（smooth polygon 经典做法）。"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class RecordRangeOverlay:
    """录制期间常驻显示的红色范围框。

    覆盖在录制区域边缘的透明置顶窗口，仅绘制红色边框；
    通过 WDA_EXCLUDEFROMCAPTURE 从录制画面中排除，用户可见但不会录进视频。
    """

    def __init__(self, root, rect):
        left, top, w, h = rect
        w, h = int(w), int(h)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#000000")
        try:
            self.win.attributes("-transparentcolor", "#000000")
        except Exception:
            pass
        self.win.geometry(f"{w}x{h}+{int(left)}+{int(top)}")
        self.canvas = tk.Canvas(self.win, width=w, height=h,
                                highlightthickness=0, bg="#000000")
        self.canvas.pack(fill="both", expand=True)
        # 内缩 1px 的红色边框，避免遮挡选区边缘内容
        self.canvas.create_rectangle(1, 1, w - 2, h - 2,
                                     outline="#e81123", width=2)
        _exclude_from_capture(self.win)
        self.win.deiconify()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------- 录制倒计时（3-2-1） ----------
class RecCountdown:
    """录制开始前倒计时提示：半透明灰色圆 + 红色数字（3→2→1），位于录制区域中央。"""

    def __init__(self, root, rect, on_done, on_cancel=None, count=3):
        self.root = root
        self.rect = rect
        self.on_done = on_done
        self.on_cancel = on_cancel
        self._left = count
        self._timer = None
        self._done = False

        left, top, w, h = rect
        cx = left + w // 2
        cy = top + h // 2
        size = 64  # 窗口边长：圆直径 50 + 四周留白
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#000000")
        try:
            self.win.attributes("-transparentcolor", "#000000")
        except Exception:
            pass
        self.win.geometry(f"{size}x{size}+{cx - size // 2}+{cy - size // 2}")
        self.canvas = tk.Canvas(self.win, width=size, height=size,
                                highlightthickness=0, bg="#000000")
        self.canvas.pack(fill="both", expand=True)
        # 深色半透明圆（RGBA 背景色），直径 50 居中
        try:
            self.canvas.create_oval(7, 7, 57, 57, fill="#1C2430CC", outline="")
        except Exception:
            self.canvas.create_oval(7, 7, 57, 57, fill="#1C2430", outline="")
        self.num_lbl = self.canvas.create_text(
            32, 32, text=str(self._left), fill=T.DANGER,
            font=("Microsoft YaHei UI", 40, "bold"))
        self.win.bind("<Escape>", lambda e: self.cancel())
        _exclude_from_capture(self.win)
        self.win.deiconify()
        self.win.focus_force()
        # 先完整显示当前数字（3），1 秒后再递减
        try:
            self._timer = self.win.after(1000, self._tick)
        except Exception:
            pass

    def _tick(self):
        if self._done:
            return
        if self._left <= 1:
            self._finish()
            return
        self._left -= 1
        try:
            self.canvas.itemconfigure(self.num_lbl, text=str(self._left))
        except Exception:
            pass
        try:
            self._timer = self.win.after(1000, self._tick)
        except Exception:
            pass

    def _finish(self):
        if self._done:
            return
        self._done = True
        if self._timer:
            try:
                self.win.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_done:
            self.on_done()

    def cancel(self):
        """用户中途取消（如按 Esc）：销毁倒计时并回调取消。"""
        if self._done:
            return
        self._done = True
        if self._timer:
            try:
                self.win.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_cancel:
            self.on_cancel()


# ---------- 统一入口 ----------
def start_record(root, storage, rect=None, on_record_started=None, on_finish=None, fps=30):
    """统一入口：区域选择 -> 保存路径 -> 启动录制 -> 状态条。

    rect 为 None 时弹出全屏框选窗口；传入 (left, top, width, height)
    虚拟屏幕坐标时跳过框选，直接按该范围录制。

    fps：录制帧率（15/25/30/60 等），默认 30。帧率已从框选窗口迁移到
    设置页（config.record_fps），调用方（main.App.start_record）负责从
    设置读取并传入。

    on_finish(path)：正常结束传 mp4 路径；取消/失败传 None。
    所有回调均在 root（Tk 主线程）事件循环中执行。
    """

    def _region_ok(rect):
        _begin_recording(root, storage, rect, on_record_started, on_finish, fps)

    if rect is not None:
        _begin_recording(root, storage, rect, on_record_started, on_finish, fps)
        return

    RecordRegionWindow(
        root,
        on_result=_region_ok,
        on_cancel=lambda: on_finish and on_finish(None))


def _begin_recording(root, storage, rect, on_record_started, on_finish, fps=30):
    """统一入口内部：红框 + 状态条（停止/取消按钮）与倒计时同时出现，倒计时结束后才正式开录。"""
    overlay = RecordRangeOverlay(root, rect)
    recorder = ScreenRecorder()
    _running = {"value": False}
    _cancelled = {"value": False}

    def _early_cancel():
        """倒计时期间点停止/取消：取消本次录制，不启动 ffmpeg。"""
        if _cancelled["value"]:
            return
        _cancelled["value"] = True
        try:
            cd.cancel()
        except Exception:
            pass
        overlay.close()
        bar.close()
        if on_finish:
            on_finish(None)

    def _start_now():
        if _cancelled["value"]:
            return
        try:
            out_path = _plan_output_path(storage)
            recorder.start(rect, out_path, fps)
        except Exception as e:
            overlay.close()
            bar.close()
            if on_finish:
                on_finish(None)
            _notify("录制失败", f"启动录制失败：{e}")
            return
        _running["value"] = True
        if on_record_started:
            try:
                on_record_started()
            except Exception:
                pass
        bar.start()

    def _stop_btn():
        if _running["value"]:
            _finish_recording(recorder, overlay, on_finish)
        else:
            _early_cancel()

    def _cancel_btn():
        if _running["value"]:
            _cancel_recording(recorder, overlay, on_finish)
        else:
            _early_cancel()

    bar = RecStatusBar(root, rect, on_stop=_stop_btn, on_cancel=_cancel_btn)
    cd = RecCountdown(root, rect, on_done=_start_now, on_cancel=_early_cancel)


def _plan_output_path(storage):
    """在 storage.save_root/录屏/YYYY-MM-DD 下生成不冲突的 mp4 路径。"""
    day_dir = os.path.join(storage.save_root, "录屏",
                           time.strftime("%Y-%m-%d", time.localtime()))
    os.makedirs(day_dir, exist_ok=True)
    base = time.strftime("ScreenRec_%Y%m%d_%H%M%S", time.localtime())
    idx = 1
    while True:
        name = base if idx == 1 else f"{base}_{idx}"
        out_path = os.path.join(day_dir, f"{name}.mp4")
        if not os.path.exists(out_path):
            return out_path
        idx += 1


def _finish_recording(recorder, overlay, on_finish):
    path = None
    try:
        path = recorder.stop()
    except Exception as e:
        _notify("录制失败", f"保存视频失败：{e}")
    if overlay:
        overlay.close()
    if on_finish:
        on_finish(path)


def _cancel_recording(recorder, overlay, on_finish):
    recorder.cancel()
    if overlay:
        overlay.close()
    if on_finish:
        on_finish(None)
