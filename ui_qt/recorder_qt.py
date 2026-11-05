# -*- coding: utf-8 -*-
"""P6：Qt 版录屏（对齐 tk 版 ``recorder.py``）。

设计对照
--------
tk 版由 5 个 tkinter Toplevel + 一个 subprocess 组成；Qt 版保持同样的分工，
但全部换成「无边框 + 自绘」的 QWidget，避免 tk 的 ``-transparentcolor`` 镂空：

============================  ==========================================
tk 版                          Qt 版
============================  ==========================================
RecordRegionWindow（框选区域）   复用 ``ui_qt.capture_overlay.CaptureSession``
                                （拖拽框选 / 单击选窗口 / 双击全屏 / Esc 取消，
                                多屏 + 混合 DPI 已由 P2 打通；提示文案在录制
                                会话期间临时替换为「录制」措辞）
RecordRangeOverlay（红框常驻）   ``RecordRangeOverlay``：2px DANGER 描边，
                                并 ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``
                                保证不被录进视频
RecCountdown（3-2-1 倒计时）     ``RecCountdown``：64px 深色圆 + 红色数字，
                                倒计时结束才真正拉起 ffmpeg
RecStatusBar（红点 + 计时 + 控制条） ``RecStatusBar``：圆角灰白卡片包裹框（同截图页工具条）+ 计时
                                + 「停止」「取消」两个胶囊按钮（ApplicationShortcut
                                绑定 Esc = 停止）
ScreenRecorder（ffmpeg gdigrab）  ``ScreenRecorder``：逐行照搬（参数、偶数化、
                                ``q`` 优雅收尾、超时 terminate、取消删残片）
============================  ==========================================

对外接口
--------
``start_record(storage, rect=None, on_record_started=None, on_finish=None, fps=30)``
    * ``rect=None`` → 先进选区层（点/拖/双击/Esc）；
    * ``rect=(left, top, w, h)``（**物理像素**虚拟屏坐标）→ 跳过选区直接开录；
    * ``on_finish(path)``：正常结束传 mp4 绝对路径；取消 / 失败传 ``None``。

``is_recording()`` / ``cancel_active()``：供主面板状态与退出清理使用。

坐标约定
--------
gdigrab 的 ``-offset_x/-offset_y/-video_size`` 全部是**物理像素**，所以
``rect`` 全程按物理像素传递（与 tk 版一致，也与编辑器 ``request_record()``
给出的 rect 一致）；只有窗口几何在放置时按 dpr 折算成逻辑像素。

与 tk 版的微差异（P6 文档已登记）
--------------------------------
* 选区交互沿用 Qt 截图选区层（单/双击 + 松开即生效），不再需要 Enter 二次确认；
* 开始前先探测 ffmpeg，缺失时直接弹错，不进入倒计时；
* 控制条 / 倒计时 / 红框都做了「不被录制」处理，tk 版仅红框做了。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QKeySequence, QPainter, QPainterPath,
                           QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QWidget)

from . import capture_overlay, dialogs, geometry as G, theme

# ---------------- 视觉常量 ----------------
BAR_HPAD = 10               # 控制条内容左右内边距
BAR_VPAD = 6                # 控制条内容上下内边距
BAR_RADIUS = 10             # 控制条圆角
BAR_GAP = 12                # 控制条与录制区域上沿的间距（逻辑像素）
COUNTDOWN_SIZE = 64         # 倒计时圆直径（逻辑像素）
COUNT_RADIUS = 25           # tk 版为 25px 半径的深色圆

TIMER_FONT_SIZE = 11
DOT_FONT_SIZE = 11

WDA_EXCLUDEFROMCAPTURE = 0x00000011

# 录制期间的选区提示文案（默认文案是「截」的语义，录制会换掉并在结束后还原）
TIP_TEXT_MAIN = "单击=录制该窗口   拖拽=框选录制区域   双击=全屏录制   Esc=取消"
TIP_TEXT_DRAG = "松开=确定录制区域   双击=全屏录制   Esc=取消"

# 保持会话对象引用，避免顶层窗口被 GC（tk 版靠 Toplevel 自我持有）
_active = None
_pending_session = None
_ffmpeg_exe: str | None = None


# ---------------- ffmpeg ----------------
def ffmpeg_exe() -> str | None:
    """返回 imageio-ffmpeg 自带的 ffmpeg 路径；不可用返回 None。"""
    global _ffmpeg_exe
    if _ffmpeg_exe is not None:
        return _ffmpeg_exe or None
    try:
        import imageio_ffmpeg
        _ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _ffmpeg_exe = ""
    return _ffmpeg_exe or None


class ScreenRecorder:
    """ffmpeg(gdigrab) 录制封装，行为与 tk 版逐条对齐。"""

    def __init__(self):
        self._proc = None
        self._log_path = None
        self._out_path = None

    def start(self, rect, out_path: str, fps: int = 15) -> None:
        left, top, w, h = rect
        w, h = int(w), int(h)
        if w < 2 or h < 2:
            raise RuntimeError("录制区域过小（宽高至少 2 像素）")
        # libx264 的 yuv420p 要求宽高各自为偶数，须分别修正（不能一起减）
        if w % 2:
            w -= 1
        if h % 2:
            h -= 1
        exe = ffmpeg_exe()
        if not exe:
            raise RuntimeError("未找到 ffmpeg，请先安装依赖：pip install imageio-ffmpeg")
        cmd = [exe, "-f", "gdigrab", "-framerate", str(fps),
               "-offset_x", str(int(left)), "-offset_y", str(int(top)),
               "-video_size", f"{w}x{h}", "-i", "desktop",
               "-c:v", "libx264", "-preset", "ultrafast",
               "-pix_fmt", "yuv420p", out_path]
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

    def stop(self) -> str | None:
        if self._proc is None:
            return None
        proc = self._proc
        try:
            try:
                if proc.stdin:
                    proc.stdin.write(b"q\n")      # ffmpeg 优雅收尾，保证 moov 写完
                    proc.stdin.flush()
                    proc.stdin.close()
            except OSError:
                pass                              # ffmpeg 已自行退出，管道断开属正常
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
        finally:
            self._proc = None
        if proc.returncode not in (0, None):
            detail = self._log_tail()
            raise RuntimeError(f"ffmpeg 退出码 {proc.returncode}：{detail}")
        if not (self._out_path and os.path.exists(self._out_path) and os.path.getsize(self._out_path) > 0):
            raise RuntimeError(f"录制文件为空：{self._log_tail()}")
        return self._out_path

    def _log_tail(self, limit: int = 400) -> str:
        """从 ffmpeg 日志里摘出最能说明问题的几行。"""
        try:
            if not self._log_path or not os.path.exists(self._log_path):
                return "无日志"
            text = open(self._log_path, encoding="utf-8", errors="replace").read()
        except Exception:
            return "日志读取失败"
        keys = ("Error", "error", "Invalid", "not divisible", "failed", "Failed", "Conversion")
        hits = [ln.strip() for ln in text.splitlines() if any(k in ln for k in keys)]
        tail = " | ".join(hits[-3:]) if hits else text.strip()[-limit:]
        return tail[-limit:] or "无有效日志"

    def cancel(self) -> None:
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
                os.remove(self._out_path)       # 取消不留残片
            except Exception:
                pass


def plan_output_path(storage_obj) -> str:
    """在 save_root/录屏/YYYY-MM-DD 下生成不冲突的 mp4 路径（与 tk 版一致）。"""
    day_dir = os.path.join(storage_obj.save_root, "录屏",
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


# ---------------- Win32 小工具 ----------------
def _exclude_from_capture(win: QWidget) -> bool:
    """把窗口排除在截屏/录屏之外（Win10 2004+ 有效），失败静默。"""
    try:
        hwnd = int(win.winId())
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def _pill_style(bg: str, hover: str) -> str:
    return (f"QPushButton {{ background: {bg}; color: #FFFFFF; border: none;"
            f" border-radius: 8px; padding: 3px 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {hover}; }}")


# ---------------- 红框（录制范围常驻提示） ----------------
class RecordRangeOverlay(QWidget):
    """录制范围红框：不接收鼠标、不进任务栏、不被录进视频。"""

    def __init__(self, rect_phys):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._rect_phys = tuple(int(v) for v in rect_phys)
        self.setGeometry(G.logical_geometry_for_physical_rect(QRect(*self._rect_phys)))
        self.show()
        self.raise_()
        _exclude_from_capture(self)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.DANGER), 2))
        # 描边贴合窗口内侧，避免半像素发虚
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))


# ---------------- 录制中控制条 ----------------
class RecStatusBar(QWidget):
    """录制控制条：红点 + 计时 + 停止 / 取消。"""

    def __init__(self, rect_phys, on_stop=None, on_cancel=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self._rect_phys = tuple(int(v) for v in rect_phys)
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(500)        # 500ms 刷新，秒数跳变不延迟
        self._timer.timeout.connect(self._tick)

        row = QHBoxLayout(self)
        row.setContentsMargins(BAR_HPAD, BAR_VPAD, BAR_HPAD, BAR_VPAD)
        row.setSpacing(8)

        self._dot = QLabel("●", self)
        self._dot.setFont(theme.font(DOT_FONT_SIZE, bold=True))
        self._dot.setStyleSheet(f"color: {theme.DANGER}; background: transparent;")
        row.addWidget(self._dot)

        self._time = QLabel("00:00", self)
        self._time.setFont(theme.font(TIMER_FONT_SIZE, bold=True, digit=True))
        self._time.setStyleSheet(f"color: {theme.INK}; background: transparent;")
        self._time.setMinimumWidth(46)
        row.addWidget(self._time)

        self._btn_stop = QPushButton("停止", self)
        self._btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_stop.setToolTip("停止并保存视频（Esc）")
        self._btn_stop.setStyleSheet(_pill_style(theme.SUCCESS, theme.SUCCESS_HOVER))
        self._btn_stop.clicked.connect(lambda: self._fire(self.on_stop))
        row.addWidget(self._btn_stop)

        self._btn_cancel = QPushButton("取消", self)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.setToolTip("放弃本次录制，不留文件")
        self._btn_cancel.setStyleSheet(_pill_style(theme.DANGER, theme.DANGER_HOVER))
        self._btn_cancel.clicked.connect(lambda: self._fire(self.on_cancel))
        row.addWidget(self._btn_cancel)

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._esc.activated.connect(lambda: self._fire(self.on_stop))

        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        _exclude_from_capture(self)

    # ---- 外观 ----
    def paintEvent(self, event) -> None:            # noqa: N802
        """自绘包裹框：与截图页工具条同款圆角卡片底，把停止/取消按钮包住。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height()),
                            BAR_RADIUS, BAR_RADIUS)
        p.fillPath(path, QColor(theme.TOOLBAR))
        p.end()

    # ---- 布局 ----
    def _place(self) -> None:
        """放在录制区域上沿外侧；上边距不够时塞进区域内顶部。"""
        geo = G.logical_geometry_for_physical_rect(QRect(*self._rect_phys))
        w, h = self.width(), self.height()
        x = geo.center().x() - w // 2
        y = geo.top() - h - BAR_GAP
        screen = G.screen_at_physical(
            (self._rect_phys[0] + self._rect_phys[2] // 2, self._rect_phys[1]))
        area = screen.logical if screen is not None else None
        if area is not None:
            if y < area.top():
                y = min(geo.top() + BAR_GAP, area.bottom() - h)
            x = max(area.left(), min(x, area.right() - w))
        self.move(int(x), int(y))

    def _fire(self, cb) -> None:
        if callable(cb):
            cb()

    # ---- 计时 ----
    def start(self) -> None:
        self._t0 = time.monotonic()
        self._time.setText("00:00")
        self._timer.start()

    def _tick(self) -> None:
        if not self._t0:
            return
        total = int(time.monotonic() - self._t0)
        self._time.setText(f"{total // 60:02d}:{total % 60:02d}")

    def elapsed_text(self) -> str:
        return self._time.text()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


# ---------------- 倒计时 ----------------
class RecCountdown(QWidget):
    """3-2-1 倒计时：数字结束时才真正开始录屏，期间可按 Esc 取消。"""

    def __init__(self, rect_phys, on_done=None, on_cancel=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.on_done = on_done
        self.on_cancel = on_cancel
        self._n = 3
        self._rect_phys = tuple(int(v) for v in rect_phys)

        self.setFixedSize(COUNTDOWN_SIZE, COUNTDOWN_SIZE)
        self._place()
        self.show()
        self.raise_()
        _exclude_from_capture(self)

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._esc.activated.connect(self._cancel)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _place(self) -> None:
        geo = G.logical_geometry_for_physical_rect(QRect(*self._rect_phys))
        self.move(int(geo.center().x() - COUNTDOWN_SIZE / 2),
                  int(geo.center().y() - COUNTDOWN_SIZE / 2))

    def _tick(self) -> None:
        self._n -= 1
        if self._n <= 0:
            self._timer.stop()
            self.close()
            if callable(self.on_done):
                self.on_done()
            return
        self.update()

    def _cancel(self) -> None:
        self._timer.stop()
        self.close()
        if callable(self.on_cancel):
            self.on_cancel()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(28, 36, 48, 204))
        cx, cy, r = COUNTDOWN_SIZE / 2, COUNTDOWN_SIZE / 2, COUNT_RADIUS
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        f = QFont(theme.FONT_DIGIT)
        f.setPointSize(26)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.DANGER))
        p.drawText(QRectF(0, 0, COUNTDOWN_SIZE, COUNTDOWN_SIZE),
                   Qt.AlignmentFlag.AlignCenter, str(max(1, self._n)))


# ---------------- 录制会话 ----------------
class _Recording:
    """一次录制会话：红框 + 倒计时 + 控制条 + ffmpeg 进程的生命周期管理。"""

    def __init__(self, storage_obj, rect, on_record_started, on_finish, fps):
        self.storage = storage_obj
        self.rect = tuple(int(v) for v in rect)
        self.on_record_started = on_record_started
        self.on_finish = on_finish
        self.fps = int(fps or 30)
        self.recorder = ScreenRecorder()
        self.running = False
        self.closed = False
        self.overlay: RecordRangeOverlay | None = None
        self.bar: RecStatusBar | None = None
        self.cd: RecCountdown | None = None

    # ---- 启动 ----
    def begin(self) -> None:
        if self.rect[2] < 2 or self.rect[3] < 2:
            self._notify("录制失败", "录制区域过小（宽高至少 2 像素）")
            self._done(None)
            return
        try:
            self.overlay = RecordRangeOverlay(self.rect)
        except Exception:
            self.overlay = None
        self.bar = RecStatusBar(self.rect, on_stop=self._stop_btn,
                                on_cancel=self._cancel_btn)
        self.cd = RecCountdown(self.rect, on_done=self._start_now,
                               on_cancel=self._early_cancel)

    def _notify(self, title: str, message: str) -> None:
        try:
            dialogs.msg_error(title, message)
        except Exception:
            pass

    # ---- 阶段 1：倒计时期间取消 ----
    def _early_cancel(self) -> None:
        if self.closed or self.running:
            return
        self._close_windows()
        self._done(None)

    # ---- 阶段 2：倒计时结束，拉起 ffmpeg ----
    def _start_now(self) -> None:
        if self.closed:
            return
        try:
            out_path = plan_output_path(self.storage)
            self.recorder.start(self.rect, out_path, self.fps)
        except Exception as exc:
            self._close_windows()
            self._notify("录制失败", f"启动录制失败：{exc}")
            self._done(None)
            return
        self.running = True
        if callable(self.on_record_started):
            try:
                self.on_record_started()
            except Exception:
                pass
        if self.bar is not None:
            self.bar.start()

    # ---- 阶段 3：停止 / 取消 ----
    def _stop_btn(self) -> None:
        if self.closed:
            return
        if not self.running:
            self._early_cancel()
            return
        if self.cd is not None:
            self._close(self.cd)
            self.cd = None
        if self.bar is not None:            # 先收控制条，避免编码期间残留在画面外
            self._close(self.bar)
            self.bar = None
        path = None
        try:
            path = self.recorder.stop()
        except Exception as exc:
            self._notify("录制失败", f"保存视频失败：{exc}")
        if self.overlay is not None:
            self._close(self.overlay)
            self.overlay = None
        self._done(path)

    def _cancel_btn(self) -> None:
        if self.closed:
            return
        if not self.running:
            self._early_cancel()
            return
        self.recorder.cancel()
        self._close_windows()
        self._done(None)

    # ---- 收尾 ----
    def _close(self, win) -> None:
        try:
            win.close()
            win.deleteLater()
        except Exception:
            pass

    def _close_windows(self) -> None:
        for attr in ("cd", "bar", "overlay"):
            win = getattr(self, attr, None)
            if win is not None:
                self._close(win)
                setattr(self, attr, None)

    def _done(self, path: str | None) -> None:
        if self.closed:
            return
        self.closed = True
        self.running = False
        global _active
        if _active is self:
            _active = None
        if callable(self.on_finish):
            try:
                self.on_finish(path)
            except Exception:
                pass


# ---------------- 选区文案切换 ----------------
def _swap_tip_texts() -> tuple[str, str]:
    """录制会话期间把选区层文案换成「录制」语义，返回原值以便还原。"""
    old = (capture_overlay.TIP_TEXT, capture_overlay.TIP_TEXT_DRAG)
    capture_overlay.TIP_TEXT = TIP_TEXT_MAIN
    capture_overlay.TIP_TEXT_DRAG = TIP_TEXT_DRAG
    return old


def _restore_tip_texts(old) -> None:
    if not old:
        return
    capture_overlay.TIP_TEXT, capture_overlay.TIP_TEXT_DRAG = old


# ---------------- 对外入口 ----------------
def start_record(storage_obj, rect=None, on_record_started=None,
                 on_finish=None, fps: int = 30) -> bool:
    """开始录制；返回是否成功进入流程。

    ``rect`` 为 ``None`` 时先弹选区层；否则直接按物理像素矩形录制。
    """
    global _active, _pending_session

    if _active is not None or _pending_session is not None:
        return False        # 已有录制 / 选区进行中，由调用方提示

    if ffmpeg_exe() is None:
        try:
            dialogs.msg_error("录制失败",
                              "未找到 ffmpeg，请先安装依赖：pip install imageio-ffmpeg")
        except Exception:
            pass
        return False

    if rect is not None:
        rec = _Recording(storage_obj, rect, on_record_started, on_finish, fps)
        _active = rec
        rec.begin()
        return True

    # 选区模式：复用截图选区层（多屏 / 混合 DPI 已由 P2 打通）
    old_tips = _swap_tip_texts()
    try:
        session = capture_overlay.CaptureSession()
    except Exception as exc:
        _restore_tip_texts(old_tips)
        try:
            dialogs.msg_error("录制失败", f"选区层创建失败：{exc}")
        except Exception:
            pass
        return False

    def _on_finished(crop, offset_x: int, offset_y: int, full_img, bbox) -> None:
        global _pending_session
        _restore_tip_texts(old_tips)
        _pending_session = None
        rec = _Recording(storage_obj, (offset_x, offset_y, crop.width, crop.height),
                         on_record_started, on_finish, fps)
        globals()["_active"] = rec
        rec.begin()

    def _on_cancelled() -> None:
        global _pending_session
        _restore_tip_texts(old_tips)
        _pending_session = None
        if callable(on_finish):
            try:
                on_finish(None)
            except Exception:
                pass

    session.finished.connect(_on_finished)
    session.cancelled.connect(_on_cancelled)
    _pending_session = session
    try:
        session.start()
    except Exception as exc:
        _pending_session = None
        _restore_tip_texts(old_tips)
        try:
            dialogs.msg_error("录制失败", f"抓屏失败：{exc}")
        except Exception:
            pass
        return False
    return True


def is_recording() -> bool:
    """是否有录制 / 选区流程在跑（主面板据此禁用重复触发）。"""
    rec = _active
    return bool(_pending_session is not None or (rec is not None and not rec.closed))


def cancel_active() -> None:
    """退出程序时清理：取消选区或终止录制，不留半成品。"""
    global _active, _pending_session
    if _pending_session is not None:
        try:
            _pending_session.cancel()
        except Exception:
            pass
        _pending_session = None
    rec = _active
    if rec is not None:
        try:
            rec._cancel_btn()
        except Exception:
            pass
        _active = None
