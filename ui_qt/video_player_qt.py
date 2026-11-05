# -*- coding: utf-8 -*-
"""P6：历史库内置录屏播放窗（对齐 tk 版 ``gallery.py`` 的录屏播放窗口）。

为什么不用 QtMultimedia
-----------------------
当前虚拟环境只装了 PySide6-Essentials，``PySide6.QtMultimedia`` 不在其中；
而 tk 版录屏播放本就用 ``imageio_ffmpeg.read_frames`` 逐帧解码 + 画布显示。
Qt 版沿用同一套解码方式（``QTimer`` 按 fps 取帧 → ``QImage`` → ``QLabel``），
既不新增依赖，也与 tk 版行为一致。

对外
----
``open_video(path, parent=None, on_close=None)`` → 返回播放窗实例；解码器不可用时
返回 ``None``，由调用方回退到系统播放器。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from . import dialogs, theme

PLAYER_BG = "#0B0E13"
BAR_BG = theme.INK


def _fmt_time(sec: float) -> str:
    total = int(max(0.0, sec))
    return f"{total // 60:02d}:{total % 60:02d}"


def _pill(bg: str, hover: str, fg: str = "#FFFFFF") -> str:
    return (f"QPushButton {{ background: {bg}; color: {fg}; border: none;"
            f" border-radius: 6px; padding: 4px 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {hover}; }}")


class VideoPlayerWindow(QWidget):
    """录屏播放窗：打开即播，可暂停/继续、重新播放、关闭。"""

    def __init__(self, path: str, parent=None, on_close=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.path = path
        self.on_close = on_close
        self.setWindowTitle(os.path.basename(path))
        self.setStyleSheet(f"background: {PLAYER_BG};")

        self._gen = None
        self._playing = False
        self._stopped = False
        self._frame_idx = 0
        self._total = 0
        self._fps = 15.0
        self._vw = 320
        self._vh = 180
        self._img: QImage | None = None

        self._open_decoder()
        self._build_ui()
        self._apply_size()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

        if self._gen is None:
            self._info.setText("无法解码视频")
            self._btn_play.setEnabled(False)
            return
        self._playing = True
        self._btn_play.setText("暂停")
        self._timer.start(int(1000 / max(1.0, self._fps)))
        QTimer.singleShot(0, self._step)     # 立刻出首帧，避免黑屏等待

    # ---------------- 解码器 ----------------
    def _open_decoder(self) -> None:
        try:
            import imageio_ffmpeg
            self._gen = imageio_ffmpeg.read_frames(self.path, pix_fmt="rgb24")
            meta = next(self._gen)
            size = meta.get("size", (320, 180))
            self._vw, self._vh = int(size[0]), int(size[1])
            self._fps = float(meta.get("fps", 15.0) or 15.0)
        except Exception:
            self._gen = None
        try:
            import imageio_ffmpeg as _f
            n, _ = _f.count_frames_and_secs(self.path)
            self._total = int(n or 0)
        except Exception:
            self._total = 0

    # ---------------- 界面 ----------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget(self)
        top.setStyleSheet(f"background: {BAR_BG};")
        row = QHBoxLayout(top)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)
        name = QLabel(os.path.basename(self.path), top)
        name.setFont(theme.font(9, bold=True))
        name.setStyleSheet("color: #FFFFFF; background: transparent;")
        row.addWidget(name)
        row.addStretch(1)
        self._info = QLabel("", top)
        self._info.setFont(theme.font(9))
        self._info.setStyleSheet("color: #C9CED8; background: transparent;")
        row.addWidget(self._info)
        root.addWidget(top)

        self._canvas = QLabel(self)
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas.setStyleSheet(f"background: {PLAYER_BG};")
        self._canvas.setMinimumSize(240, 135)
        root.addWidget(self._canvas, 1)

        foot = QWidget(self)
        foot.setStyleSheet(f"background: {BAR_BG};")
        frow = QHBoxLayout(foot)
        frow.setContentsMargins(8, 5, 8, 5)
        frow.setSpacing(6)

        self._btn_play = QPushButton("暂停", foot)
        self._btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_play.setStyleSheet(_pill(theme.SUCCESS, theme.SUCCESS_HOVER))
        self._btn_play.clicked.connect(self.toggle_play)
        frow.addWidget(self._btn_play)

        self._btn_restart = QPushButton("重新播放", foot)
        self._btn_restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_restart.setStyleSheet(
            _pill(theme.SURFACE_3, "#E4E6EA", theme.INK))
        self._btn_restart.clicked.connect(self.restart)
        frow.addWidget(self._btn_restart)

        frow.addStretch(1)
        btn_close = QPushButton("关闭", foot)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(_pill(theme.DANGER, theme.DANGER_HOVER))
        btn_close.clicked.connect(self.close)
        frow.addWidget(btn_close)
        root.addWidget(foot)

    def _apply_size(self) -> None:
        """按视频分辨率定初始尺寸，最大不超过可用区域的 90%。"""
        scr = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        aw = avail.width() if avail is not None else 1600
        ah = avail.height() if avail is not None else 900
        scale = min(1.0, (aw * 0.9) / max(1, self._vw),
                    (ah * 0.9) / max(1, self._vh))
        dw, dh = max(1, int(self._vw * scale)), max(1, int(self._vh * scale))
        self._canvas.setFixedSize(dw, dh)
        self.setFixedSize(dw, dh + 76)
        if avail is not None:
            self.move(avail.center().x() - self.width() // 2,
                      avail.center().y() - self.height() // 2)

    # ---------------- 播放 ----------------
    def _step(self) -> None:
        if self._stopped or not self._playing or self._gen is None:
            return
        try:
            data = next(self._gen)
        except StopIteration:
            self._playing = False
            self._timer.stop()
            self._btn_play.setText("播放")
            tail = f" · {self._total} 帧" if self._total else ""
            self._info.setText(f"播放完成{tail}")
            return
        except Exception:
            self._playing = False
            self._timer.stop()
            self._info.setText("解码中断")
            return
        img = QImage(data, self._vw, self._vh, self._vw * 3,
                     QImage.Format.Format_RGB888).copy()
        self._img = img
        self._frame_idx += 1
        self._render()
        self._update_info()

    def _render(self) -> None:
        if self._img is None:
            return
        pm = QPixmap.fromImage(self._img).scaled(
            self._canvas.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._canvas.setPixmap(pm)

    def _update_info(self) -> None:
        parts = []
        if self._fps > 0:
            parts.append(f"{_fmt_time(self._frame_idx / self._fps)}"
                         f" / {_fmt_time(self._total / self._fps)}")
        if self._total:
            parts.append(f"{self._frame_idx}/{self._total} 帧")
        self._info.setText(" · ".join(parts))

    def toggle_play(self) -> None:
        if self._gen is None:
            return
        if (not self._playing and self._frame_idx and self._total
                and self._frame_idx >= self._total - 1):
            self.restart()          # 已播完，再点即重播
            return
        self._playing = not self._playing
        self._btn_play.setText("暂停" if self._playing else "播放")
        if self._playing:
            self._timer.start(int(1000 / max(1.0, self._fps)))
        else:
            self._timer.stop()

    def restart(self) -> None:
        self._timer.stop()
        try:
            if self._gen is not None:
                self._gen.close()
        except Exception:
            pass
        self._frame_idx = 0
        self._img = None
        self._canvas.clear()
        self._open_decoder()
        if self._gen is None:
            self._info.setText("无法解码视频")
            return
        self._playing = True
        self._btn_play.setText("暂停")
        self._timer.start(int(1000 / max(1.0, self._fps)))
        self._step()

    # ---------------- 关闭 ----------------
    def closeEvent(self, event) -> None:
        self._stopped = True
        self._playing = False
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            if self._gen is not None:
                self._gen.close()
        except Exception:
            pass
        self._gen = None
        if callable(self.on_close):
            try:
                self.on_close()
            except Exception:
                pass
        super().closeEvent(event)


def open_video(path: str, parent=None, on_close=None) -> VideoPlayerWindow | None:
    """打开内置播放窗；文件缺失或解码器不可用时返回 None。"""
    if not path or not os.path.exists(path):
        dialogs.msg_error("打开失败", f"视频文件不存在：{path}")
        return None
    try:
        win = VideoPlayerWindow(path, parent=parent, on_close=on_close)
    except Exception as exc:
        dialogs.msg_error("打开失败", str(exc))
        return None
    if win._gen is None:
        win.close()
        return None
    win.show()
    win.raise_()
    win.activateWindow()
    return win
