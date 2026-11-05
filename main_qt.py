# -*- coding: utf-8 -*-
"""ScreenSnap Qt 入口（P1 骨架 + P2 截图选区 + P3 图像编辑器）。

本文件是迁移期与旧版 ``main.py`` 并存的**新入口**，当前职责：

1. 建 QApplication（Fusion 样式 + 全局 QSS）；
2. 单实例互斥（与 tk 版互不干扰）；
3. 常驻托盘（QSystemTrayIcon）+ 菜单；
4. 全局热键桥接（沿用 ``hotkey.HotkeyManager``，回调转 Qt 信号）；
5. **P2：热键/按钮/托盘触发真实截图** —— 调用 ``ui_qt.capture_overlay``
   铺全屏遮罩选区层，双击全屏 / 单击窗口 / 拖拽框选 / Esc 取消；
6. **P3：进编辑器** —— 选区结果交给 ``ui_qt.editor.EditorWindow``，先调整
   选区（8 手柄 + 整体拖动，可 Enter 确认或直接切标注工具），再逐笔标注，
   最后「完成」才落盘 + 复制到剪贴板。

7. **P6：录屏** —— 主面板「录制」按钮与编辑器工具条「屏幕录制」按钮均走
   ``ui_qt.recorder_qt.start_record``：选区（或沿用编辑器选区）→ 3-2-1 倒计时
   → ffmpeg gdigrab 录制 → 控制条计时/停止/取消 → mp4 落盘 ``save_root/录屏/日期``
   → 登记进历史库并以视频条目呈现。

P2 阶段选区完成后直接落盘；P3 起改为「选区 → 编辑器 → 完成」三段式，
编辑器异常时仍会自动退回 P2 的直接落地逻辑，保证不丢图。

运行（使用项目虚拟环境）::

    E:\\PythonProjects\\Python\\.venv\\Scripts\\python.exe main_qt.py

参数::

    --no-hotkey    不注册全局热键（避免与其他实例冲突，调试用）
    --no-tray      不创建托盘图标（纯窗口调试用）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

import config
import storage
from ui_qt import app as qtapp
from ui_qt import (capture_overlay, clipboard, dialogs, editor, gallery_qt,
                   geometry, imageutil, info_panel, recorder_qt, settings_qt,
                   sticker_qt, theme)

# ---------------- 骨架窗参数 ----------------
MAX_TOOL_WINDOWS = 2            # 同时存在的截图/编辑窗口上限，超出忽略新的截图触发
WIN_W = 460
WIN_H = 292                 # 状态文字独占一行后增高，保证底部完整显示
PAD = theme.SHADOW_PAD          # 透明投影预留边距
CARD_RADIUS = theme.R_WIN

ICON_CANDIDATES = ("icon.ico", "assets/icon.ico", "resources/icon.ico",
                   "assets/icon.png", "logo.png")

def find_tray_icon() -> str | None:
    """在项目内找一张可用图标；找不到返回 None（走 letter_icon 兜底）。"""
    base = os.path.dirname(os.path.abspath(__file__))
    for rel in ICON_CANDIDATES:
        path = os.path.join(base, rel)
        if os.path.isfile(path):
            return path
    return None

class SkeletonWindow(QWidget):
    """无边框圆角骨架窗：验证透明背景 + 自绘圆角 + 投影 + 拖动。

    P2 之后截图遮罩层、P6 贴图窗都会复用同一套「WA_TranslucentBackground
    + paintEvent 自绘圆角」的做法，提前在 P1 打通。
    """

    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowTitle("ScreenSnap（Qt）")
        self.setWindowIcon(qtapp.app_icon())    # 任务栏 / Alt-Tab 图标
        # 宽度固定；高度按内容实际需要自适应（见 _fit_window_height）。
        # 原先 setFixedSize(WIN_W, WIN_H) 把窗口钉死在 292px，加了「图片翻译」行与
        # 「取消启动提示」勾选框后内容需要 355px，信息面板被强压到 105px，
        # 显示器那行 2 行文字需要 30px 却只分到 24px，于是溢出压到下一行 —— 表现为
        # 首次打开文字重叠、移动窗口后重新布局才“看起来正常”。
        self.setFixedWidth(WIN_W)
        self.resize(WIN_W, WIN_H)
        self._drag_offset: QPoint | None = None
        self._status_text = "就绪"
        # P2：截图会话 / 配置 / 上次结果
        self._session: capture_overlay.CaptureSession | None = None
        self._cfg: dict = {}
        self._was_visible = False
        self._last_capture: str | None = None
        self._editor: editor.EditorWindow | None = None
        self._recording = False          # P6：是否有录制流程在跑
        self._record_was_visible = False  # P6：录制前主面板是否可见

        self._build_ui()
        self._refresh_screen_info()
        self._fit_window_height()
        self._place_center()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(PAD + 18, PAD + 16, PAD + 18, PAD + 16)
        outer.setSpacing(10)

        # 顶部：图标 + 标题 + 关闭
        head = QHBoxLayout()
        head.setSpacing(10)

        icon_label = QLabel(self)
        icon_label.setFixedSize(40, 40)
        # 标题栏左上角统一用应用图标（与任务栏 / 各子窗口同源），取不到时才退回字母兜底
        _app_ico = qtapp.app_icon()
        if not _app_ico.isNull():
            icon_label.setPixmap(_app_ico.pixmap(40, 40))
        else:
            icon_label.setPixmap(imageutil.letter_icon("S", theme.ACCENT, "#FFFFFF", 80).pixmap(40, 40))
        head.addWidget(icon_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("ScreenSnap", self)
        title.setObjectName("Title")
        subtitle = QLabel("Qt 截图 · 录制 · 编辑器", self)
        subtitle.setObjectName("Caption")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        head.addLayout(title_box, 1)

        btn_settings = QPushButton("⚙", self)
        btn_settings.setObjectName("IconBtn")
        btn_settings.setFixedSize(26, 26)
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_settings.setToolTip("设置（热键 / 保存目录 / 录屏帧率 等）")
        btn_settings.clicked.connect(self.open_settings)
        head.addWidget(btn_settings, 0, Qt.AlignmentFlag.AlignTop)

        btn_close = QPushButton("×", self)
        btn_close.setObjectName("IconBtn")
        btn_close.setFixedSize(26, 26)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setToolTip("隐藏到托盘（Esc）")
        btn_close.clicked.connect(self.hide)
        head.addWidget(btn_close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        divider = QLabel(self)
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme.LINE};")
        outer.addWidget(divider)

        # 启动信息：显示器 / 文字识别 / 版本（显示器 / 文字识别 / 图片翻译 / 版本；官网有配置时追加）
        self._info = info_panel.StartupInfoPanel(
            self._cfg, on_status=self.set_status, parent=self,
            on_open_settings=self.open_settings)
        outer.addWidget(self._info, 1)

        # 底部：状态（独占一行，避免被右侧按钮挤到裁切） + 操作按钮
        self._status = QLabel(self._status_text, self)
        self._status.setObjectName("Caption")
        self._status.setWordWrap(True)
        self._status.setMinimumWidth(0)
        self._status.setToolTip(self._status_text)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self._status)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addStretch(1)

        btn_copy = QPushButton("诊断信息", self)
        btn_copy.setToolTip("复制启动信息与多屏 / DPI 详情（报障用）")
        btn_copy.setObjectName("Ghost")
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.clicked.connect(self._copy_info)
        foot.addWidget(btn_copy)

        outer.addLayout(foot)

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = QRectF(self.rect()).adjusted(PAD, PAD, -PAD, -PAD)

        # 1) 模拟柔和投影：多层递减 alpha，模拟 blur（避免额外依赖 QGraphicsDropShadowEffect）
        for i in range(PAD, 0, -2):
            alpha = max(2, int(26 * (1 - i / (PAD + 2)) ** 2) + 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(16, 22, 32, alpha))
            p.drawRoundedRect(card.adjusted(-i, -i + 2, i, i + 2),
                              CARD_RADIUS + i, CARD_RADIUS + i)

        # 2) 卡片本体 + 1px 边框
        path = QPainterPath()
        path.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        p.fillPath(path, QColor(theme.SURFACE))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.LINE), 1))
        p.drawPath(path)
        p.end()

    # ---------------- 交互 ----------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        """关闭按钮 = 收进托盘（托盘常驻由 main_qt 控制）。"""
        event.ignore()
        self.hide()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_screen_info()
        handle = self.windowHandle()
        if handle is not None and not getattr(self, "_screen_hooked", False):
            handle.screenChanged.connect(lambda _s: self._refresh_screen_info())
            self._screen_hooked = True

    # ---------------- 数据 ----------------
    def _place_center(self) -> None:
        qtapp.place_on_cursor_screen(self)

    def _fit_window_height(self) -> None:
        """按内容实际需要的高度固定窗口（下限 WIN_H），避免信息行被压扁重叠。

        窗口宽度固定为 WIN_W，高度取 sizeHint 与 WIN_H 的较大值；高度一旦定下就
        用 setFixedHeight 锁住，保证「首次显示」与「移动 / 重排之后」渲染结果一致。
        """
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        try:
            need = max(WIN_H, self.sizeHint().height())
        except Exception:
            return
        if self.height() != need:
            self.setFixedHeight(need)
        if self.isVisible():
            self.update()

    def _refresh_screen_info(self) -> None:
        self._info.refresh()
        # 行数可能随屏幕数量变化：延迟到事件循环里再校正高度（此时子控件已重新布局）
        QTimer.singleShot(0, self._fit_window_height)

    def _copy_info(self) -> None:
        ok = clipboard.copy_text(self._info.diagnostic_text())
        self.set_status("诊断信息已复制到剪贴板" if ok else "复制失败：剪贴板不可用")

    def set_status(self, text: str) -> None:
        self._status_text = text
        self._status.setText(text)
        # 极端情况下（超长错误串）仍可能被省略，tooltip 保证完整可读
        self._status.setToolTip(text)

    # ---------------- P2：截图选区 ----------------
    def set_config(self, cfg: dict | None) -> None:
        """注入 config.load_config() 结果（main 里调用）。"""
        self._cfg = dict(cfg or {})
        self._info.set_config(self._cfg)

    def open_gallery(self):
        """打开 Qt 版截图历史库（单例窗口，重复点击只唤醒）。"""
        try:
            # parent=None：历史库是独立顶层窗口，面板在截图时隐藏不应连带隐藏它
            win = gallery_qt.open_gallery(None, self._cfg or config.load_config())
        except Exception as exc:
            self.set_status(f"历史库打开失败：{exc}")
            dialogs.msg_error("历史库打开失败", str(exc), parent=self)
            return
        self.set_status("已打开历史库 · 双击看大图 · Delete 删除到回收站")
        return win

    # ---------------- P7：设置 ----------------
    def apply_hotkey(self, hotkey_text: str) -> str | None:
        """设置保存后立即重新注册全局热键，无需重启程序。返回 None 表示生效。"""
        text = str(hotkey_text or "").strip()
        bridge = getattr(self, "_bridge", None)
        if bridge is None:
            return "热键模块未初始化，请重启程序生效"
        try:
            bridge.unregister()
        except Exception:
            pass
        err = bridge.register(text, "capture")
        _p = os.path.join(os.environ.get("APPDATA", "."), "ScreenSnap", "hotkey.log")
        try:
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            with open(_p, "a", encoding="utf-8") as _f:
                _f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{text}\t{'OK' if err is None else 'FAIL: ' + str(err)}\tpid={os.getpid()}\n")
        except Exception:
            pass
        if err is None:
            self._hotkey_text = text
            tray = getattr(self, "tray", None)
            if tray is not None:
                try:
                    tray.set_tooltip(f"ScreenSnap（Qt 截图 · {text}）")
                except Exception:
                    pass
                try:
                    acts = tray.menu.actions()
                    if acts:
                        acts[0].setText(f"截图 {text}")
                except Exception:
                    pass
        return err

    def open_settings(self):
        """打开 Qt 版设置页；保存后同步内存配置并刷新已开的历史库。"""
        # 优先取磁盘最新配置：主面板「取消启动提示」勾选后立即落盘，
        # 这里若沿用启动时的内存副本，设置页勾选框就会读到旧值、两处不同步。
        try:
            cfg = config.load_config() or self._cfg or {}
        except Exception:
            cfg = self._cfg or {}
        self._cfg = cfg
        old = dict(cfg)
        try:
            new_cfg = settings_qt.open_settings(self, cfg)
        except Exception as exc:
            dialogs.msg_error("设置打开失败", str(exc), parent=self)
            return None
        if not new_cfg:
            return None
        self.set_config(new_cfg)
        # 历史库的回收站天数 / 列容量取自 cfg，已开的窗口直接重开以应用新值
        win = getattr(gallery_qt.GalleryWindow, "_active", None)
        if win is not None:
            try:
                win.close()
            except RuntimeError:
                pass
            try:
                gallery_qt.open_gallery(None, new_cfg)
            except Exception:
                pass
        changed_hotkey = str(old.get("hotkey")) != str(new_cfg.get("hotkey"))
        if changed_hotkey:
            err = self.apply_hotkey(str(new_cfg.get("hotkey") or ""))
            if err is None:
                self.set_status(f"设置已保存 · 热键已生效（{new_cfg.get('hotkey')}）")
            else:
                self.set_status(f"热键未生效：{err}")
                try:
                    dialogs.msg_warn("热键注册失败", str(err), parent=self)
                except Exception:
                    pass
        else:
            self.set_status("设置已保存")
        return new_cfg

    # ---------------- P6：录屏 ----------------
    def start_record(self, rect=None) -> None:
        """录制入口：主面板按钮传 ``rect=None``（先框选），编辑器工具条传选区。

        ``rect`` 为**物理像素**虚拟屏坐标 ``(left, top, w, h)``，与 ffmpeg
        gdigrab 的 offset/video_size 语义一致，直接透传不做换算。
        """
        if self._recording or recorder_qt.is_recording():
            self.set_status("已有录制流程在进行中")
            return
        cfg = self._cfg or config.load_config()
        self._cfg = cfg
        root = str(cfg.get("save_root") or config.DEFAULT_SAVE_ROOT)
        fps = int(cfg.get("record_fps", 30) or 30)
        # 录制选区时同样要先隐藏面板，否则会把自己录进画面
        # （编辑器工具条进入时面板已被截图流程隐藏，此时沿用 _was_visible 判断）
        self._record_was_visible = self.isVisible() or bool(self._was_visible)
        if self.isVisible():
            self.hide()
        self.set_status("准备录制… 拖拽框选区域 / 单击录窗口 / 双击全屏 / Esc 取消")
        try:
            st = storage.Storage(root, trash_days=int(cfg.get("trash_days", 3)))
            ok = recorder_qt.start_record(
                st, rect=rect, on_record_started=self._on_record_started,
                on_finish=self._on_record_finished, fps=fps)
        except Exception as exc:
            self._recording = False
            if self._record_was_visible:
                window_show(self)()
            self.set_status(f"录制启动失败：{exc}")
            dialogs.msg_error("录制启动失败", str(exc), parent=self)
            return
        if not ok:
            if self._record_was_visible:
                window_show(self)()
            self.set_status("录制未启动（可能已在录制中或缺少 ffmpeg）")

    def _on_record_started(self) -> None:
        self._recording = True
        self.set_status("录制中… 控制条「停止」或 Esc 结束并保存")

    def _on_record_finished(self, path: str | None) -> None:
        """录制结束：登记历史库（kind=video）+ 刷新已开的历史库窗口。"""
        self._recording = False
        if self._record_was_visible:
            window_show(self)()
        if not path:
            self.set_status("已取消录制，未生成文件")
            return
        cfg = self._cfg or {}
        root = str(cfg.get("save_root") or config.DEFAULT_SAVE_ROOT)
        try:
            st = storage.Storage(root, trash_days=int(cfg.get("trash_days", 3)))
            st.scan_videos()             # 抽取首帧缩略图并写入 gallery.json
        except Exception:
            pass
        win = getattr(gallery_qt.GalleryWindow, "_active", None)
        if win is not None:
            try:
                win.reload(keep_view=True)
            except RuntimeError:
                pass
        self.set_status(f"录制完成：{os.path.basename(path)}")
        dialogs.msg_info("录制完成", f"视频已保存并登记到历史库：\n{path}",
                         parent=self)

    # ---------------- P2：截图选区 ----------------
    def start_capture(self, source: str = "manual") -> None:
        """进入全屏选区层：先隐藏自身窗口，避免把自己的面板截进去。"""
        if self._session is not None:
            return
        # 并发上限：每个编辑窗算 1 个，截图会话最多 1 个，合计最多 2 个
        busy = editor.open_editor_count()
        if busy >= MAX_TOOL_WINDOWS:
            self.set_status(f"已达上限：最多同时 {MAX_TOOL_WINDOWS} 个截图/编辑窗口，本次已忽略")
            tray = getattr(self, "tray", None)
            now = datetime.now()
            last = getattr(self, "_limit_notice_at", None)
            if tray is not None and not self.isVisible() and (
                    last is None or (now - last).total_seconds() >= 2):
                self._limit_notice_at = now
                try:
                    tray.notify("ScreenSnap",
                                f"最多同时 {MAX_TOOL_WINDOWS} 个截图/编辑窗口，请先处理已打开的窗口")
                except Exception:
                    pass
            return
        self._was_visible = self.isVisible()
        if self._was_visible:
            self.hide()
        self.set_status("正在抓屏… 拖拽截区域 / 单击截窗口 / 双击全屏 / Esc 取消")
        # 等桌面重绘完成再抓屏，否则会抓到刚隐藏的窗口残影
        QTimer.singleShot(140, self._begin_capture)

    def _begin_capture(self) -> None:
        if self._session is not None:
            return
        try:
            session = capture_overlay.CaptureSession()
        except Exception as exc:
            self._capture_failed(f"选区层创建失败：{exc}")
            return
        session.finished.connect(self._on_capture_finished)
        session.cancelled.connect(self._on_capture_cancelled)
        self._session = session
        try:
            session.start()
        except Exception as exc:
            self._session = None
            self._capture_failed(f"抓屏失败：{exc}")

    def _capture_failed(self, message: str) -> None:
        self.set_status(message)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify("ScreenSnap 截图失败", message)
        if self._was_visible:
            window_show(self)()

    def _on_capture_cancelled(self) -> None:
        self._session = None
        self.set_status("已取消截图")
        if self._was_visible:
            window_show(self)()

    def _on_capture_finished(self, crop, offset_x: int, offset_y: int,
                            full_img, bbox) -> None:
        """P3：选区结果直接进编辑器，编辑完成后再落地保存。"""
        self._session = None
        if self._editor is not None:
            try:
                self._editor.close()
            except Exception:
                pass
            self._editor = None
        try:
            win = editor.EditorWindow(
                None, crop, self._on_editor_done, self._on_editor_cancel,
                offset_x=offset_x, offset_y=offset_y,
                on_pin=self._on_editor_pin, full_image=full_img, sel_box=bbox,
                on_record=self._on_editor_record,
                on_picture_pin=self._on_editor_picture_pin)
        except Exception as exc:  # 编辑器异常时退回 P2 的直接落地，保证不丢图
            self.set_status(f"编辑器启动失败：{exc} · 已直接保存")
            self._deliver_capture(crop, offset_x, offset_y)
            return
        self._editor = win
        win.show()
        win.toolbar.show()
        win.raise_()
        win.toolbar.raise_()
        win.activateWindow()
        win.setFocus()
        self.set_status(f"编辑中 {crop.width} × {crop.height} · "
                        "Enter 完成 · Esc 取消")

    def _on_editor_done(self, img, x: int, y: int) -> None:
        """编辑器「完成 / 贴图」回调：落盘 + 复制 + 回显。"""
        self._editor = None
        self._deliver_capture(img, x, y)

    def _on_editor_record(self, rect) -> None:
        """P6：编辑器工具条「屏幕录制」→ 按当前编辑器选区直接开录。

        ``rect`` 由 ``editor.ToolbarWindow.request_record`` 给出，已是**物理像素**
        虚拟屏坐标（调整模式取实时选区，标注模式取整图范围），与 tk 版
        ``Main.start_record(rect)`` 语义一致；编辑器在回调前已完成 teardown。
        """
        self._editor = None
        self.start_record(tuple(int(v) for v in rect))

    def _on_editor_pin(self, img, x: int = 0, y: int = 0) -> None:
        """P5「贴图」：钉在屏幕原位 + 存档进历史库（不复制剪贴板）。

        对齐 tk 版 ``Main._on_pin``：先 ``storage.save_image`` 落盘入历史库，
        再开贴图窗；区别是 Qt 版直接复用 ``ui_qt.sticker_qt`` 单窗自绘实现。
        """
        self._editor = None
        cfg = self._cfg or {}
        notes: list[str] = []
        path: str | None = None
        try:
            root = str(cfg.get("save_root") or config.DEFAULT_SAVE_ROOT)
            fmt = str(cfg.get("save_format") or "png").lower()
            fmt = "jpg" if fmt in ("jpg", "jpeg") else "png"
            st = storage.Storage(root, trash_days=int(cfg.get("trash_days", 3)))
            item = st.save_image(img.convert("RGB"), None, fmt)
            if isinstance(item, dict):
                path = item.get("path")
            if path:
                self._last_capture = path
            notes.append("已存档")
            active = getattr(gallery_qt.GalleryWindow, "_active", None)
            if active is not None:
                try:
                    active.reload(keep_view=True)
                except Exception:
                    pass
        except Exception as exc:
            notes.append(f"存档失败：{exc}")
        try:
            sticker_qt.open_sticker(img, x, y)
            notes.append(f"已贴图 · 当前 {sticker_qt.open_count()} 张")
        except Exception as exc:
            notes.append(f"贴图失败：{exc}")
        summary = f"贴图 {img.width} × {img.height} · " + " · ".join(notes)
        self.set_status(summary)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify("ScreenSnap 贴图", "\n".join(notes))
        if self._was_visible:
            window_show(self)()

    def _on_editor_picture_pin(self, img, x: int = 0, y: int = 0,
                               alt_img=None) -> None:
        """图片翻译完成：把译文图钉在选区原位（可选带原文图用于切换）。

        与 ``_on_editor_pin`` 的区别：**不存档、不复制剪贴板**，纯呈现；
        钉图尺寸与原截图 1:1，图下方提供「原文 / 译文」切换按钮。
        """
        self._editor = None
        notes: list[str] = []
        try:
            sticker_qt.open_sticker(img, x, y, title="图片翻译",
                                    alt_image=alt_img, alt_title="原图")
            notes.append(f"已钉图 · 当前 {sticker_qt.open_count()} 张")
        except Exception as exc:
            notes.append(f"钉图失败：{exc}")
        self.set_status(f"图片翻译 {img.width} × {img.height} · " + " · ".join(notes))
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify("ScreenSnap 图片翻译", "\n".join(notes))
        if self._was_visible:
            window_show(self)()

    def _on_editor_cancel(self) -> None:
        self._editor = None
        self.set_status("已取消截图")
        if self._was_visible:
            window_show(self)()

    def _deliver_capture(self, crop, offset_x: int, offset_y: int) -> None:
        """编辑完成（或编辑器不可用）后的落地：复制 + 存档 + 回显。"""
        cfg = self._cfg or {}
        size_text = f"{crop.width} × {crop.height}"
        notes: list[str] = []

        # 1) 复制到剪贴板（受 auto_copy 控制，默认开）
        if cfg.get("auto_copy", True):
            notes.append("已复制到剪贴板" if clipboard.copy_image_to_clipboard(crop)
                         else "复制剪贴板失败")
        else:
            notes.append("未复制（auto_copy=false）")

        # 2) 保存文件
        path: str | None = None
        try:
            root = str(cfg.get("save_root") or config.DEFAULT_SAVE_ROOT)
            os.makedirs(root, exist_ok=True)
            fmt = str(cfg.get("save_format") or "png").lower()
            ext = "jpg" if fmt in ("jpg", "jpeg") else "png"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(root, f"ScreenSnap_{stamp}.{ext}")
            if ext == "jpg":
                crop.convert("RGB").save(path, quality=95)
            else:
                crop.save(path)
            notes.append(f"已保存 {os.path.basename(path)}")
            self._last_capture = path
            # 3) 登记进历史库（P4）：文件名/目录保持 P3 行为，仅补一条 gallery.json 记录
            try:
                if gallery_qt.register_capture(path, save_root=root):
                    notes.append("已入历史库")
            except Exception:
                pass
            active = getattr(gallery_qt.GalleryWindow, "_active", None)
            if active is not None:
                try:
                    active.reload(keep_view=True)
                except Exception:
                    pass
        except Exception as exc:
            notes.append(f"保存失败：{exc}")

        summary = f"截图完成 {size_text} · " + " · ".join(notes)
        self.set_status(summary)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.notify("ScreenSnap 截图完成", f"{size_text}\n" + " · ".join(notes))
        if self._was_visible:
            window_show(self)()

def build_tray(window: SkeletonWindow, hotkey_text: str,
               menu_on_left_click: bool = False) -> qtapp.TrayIcon:
    """构建托盘 + 菜单。

    ``menu_on_left_click=True``（打包态）时左键在鼠标位置弹出上下文菜单；
    否则（开发态）左键直接显示主面板。
    """
    tray = qtapp.TrayIcon(find_tray_icon(), window.parent() or window,
                          tooltip=f"ScreenSnap（Qt 截图 · {hotkey_text}）")
    tray.set_menu_items([
        ("capture", f"截图  {hotkey_text}", lambda: window.start_capture("tray")),
        ("gallery", "历史库", window.open_gallery),
        ("settings", "设置…", window.open_settings),
        "-",
        ("quit", "退出 ScreenSnap", QApplication.instance().quit),
    ])
    if menu_on_left_click:
        def _popup_menu():
            tray.menu.popup(QCursor.pos())
        tray.set_left_click(_popup_menu)
    else:
        tray.set_left_click(window_show(window))
    window.tray = tray
    return tray

def window_show(window: SkeletonWindow):
    def _show():
        window.show()
        window.raise_()
        window.activateWindow()
    return _show

def ocr_selftest(argv: list[str]) -> int:
    """打包版 OCR 自检：不建 GUI，直接跑一次识别并把结果写成 JSON。

    用法::

        ScreenSnap-Qt.exe --ocr-selftest <图片路径> --ocr-selftest-out <json路径>

    主要用于确认冻结产物里 winrt 投影与 _table.pywinrt 是否齐备，
    以及打包态下「Windows 系统 OCR」是否真的可用。
    """
    import json

    from ui_qt import ocr_qt

    args = list(argv)
    img_path = ""
    out_path = ""
    pos = args.index("--ocr-selftest")
    if len(args) > pos + 1 and not args[pos + 1].startswith("--"):
        img_path = args[pos + 1]
    if "--ocr-selftest-out" in args:
        k = args.index("--ocr-selftest-out")
        if len(args) > k + 1:
            out_path = args[k + 1]
    if not out_path:
        out_path = os.path.join(os.path.dirname(sys.executable), "ocr_selftest.json")

    report = {
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "meipass": getattr(sys, "_MEIPASS", ""),
        "ocr_module": getattr(ocr_qt, "__file__", ""),
        "backends": [],
        "engine": "",
        "image": img_path,
        "lines": [],
        "text": "",
        "error": "",
    }
    try:
        from PIL import Image

        report["backends"] = ocr_qt.available_backends()
        if img_path:
            with Image.open(img_path) as im:
                lines = ocr_qt.recognize(im.convert("RGB"))
            report["engine"] = ocr_qt.get_backend().id
            report["lines"] = [{"text": ln.text, "box": list(ln.box)} for ln in lines]
            report["text"] = ocr_qt.to_text(lines)
    except Exception as exc:  # noqa: BLE001 —— 自检要把异常原样带出来
        import traceback

        report["error"] = "%r\n%s" % (exc, traceback.format_exc())

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    ok = bool(report["text"].strip()) and not report["error"]
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = qtapp.create_app(argv)

    inst = qtapp.SingleInstance()
    if not inst.acquire():
        dialogs.msg_warn("ScreenSnap 已在运行",
                         "检测到 ScreenSnap（Qt 版）已经在后台运行。",
                         detail="请查看任务栏右下角的托盘图标。")
        return 0

    qtapp.setup_debug_dump()

    try:
        cfg = config.load_config() or {}
    except Exception:
        cfg = {}
    hotkey_text = str(cfg.get("hotkey") or "Ctrl+Alt+A")

    window = SkeletonWindow()
    window.set_config(cfg)
    # 打包态（PyInstaller 等）：默认静默驻留托盘，不自动弹主面板
    frozen = bool(getattr(sys, "frozen", False))
    tray = None
    tray_ready = False
    if "--no-tray" not in argv:
        tray = build_tray(window, hotkey_text, menu_on_left_click=frozen)
        tray_ready = tray.start()
        if not tray_ready:
            # 无托盘环境（远程桌面/精简系统）退回普通窗口，跌破不退出
            window.set_status("系统托盘不可用，已使用普通窗口模式")

    bridge = qtapp.HotkeyBridge(parent=window)
    window._bridge = bridge
    bridge.triggered.connect(lambda name: window.start_capture(f"hotkey:{name}"))
    err = None
    if "--no-hotkey" not in argv:
        err = window.apply_hotkey(hotkey_text)

    window.set_status(
        f"托盘就绪 · 热键 {hotkey_text}" if err is None else f"热键不可用：{err}")

    # 保持引用，防止托盘 / 热键对象被 GC 回收
    app._qt_parts = {"window": window, "tray": tray, "bridge": bridge, "instance": inst}

    # 启动页显示策略由「取消启动提示」勾选框（startup_show_panel）决定：
    # 勾选 = 启动时弹出主面板；不勾选 = 启动时不弹出，静默驻留托盘。
    # 托盘不可用时无论如何都要显示，否则用户没有任何入口。
    show_panel = bool(cfg.get("startup_show_panel", True)) or not tray_ready
    if show_panel:
        window.show()
        try:
            window.raise_()
            window.activateWindow()
        except Exception:
            pass
    else:
        window.set_status(f"已静默驻留托盘 · 热键 {hotkey_text}")
    if tray is not None:
        if not tray_ready:
            tray.notify("ScreenSnap", f"托盘不可用，已显示面板 · 热键 {hotkey_text}")
        else:
            tray.notify("ScreenSnap", f"已就绪 · 热键 {hotkey_text}")
    code = app.exec()
    recorder_qt.cancel_active()     # 退出前收掉进行中的录制/选区，不留残片
    bridge.unregister()
    inst.release()
    return code

if __name__ == "__main__":
    if "--ocr-selftest" in sys.argv:
        sys.exit(ocr_selftest(sys.argv))
    sys.exit(main())
