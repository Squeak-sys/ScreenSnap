# -*- coding: utf-8 -*-
"""Qt 应用基础设施：QApplication 创建、单实例、托盘、热键桥接、异常兜底。

本模块只提供「可复用的骨架零件」，不含业务逻辑；具体窗口与流程由
``main_qt.py``（骨架）以及 P2 之后的截图 / 编辑器等模块组装。

依赖约束：不 import 任何旧 UI 模块；仅在需要错误日志目录时惰性 import config。
"""

from __future__ import annotations

import ctypes
import os
import sys
import traceback

from PySide6.QtCore import QEvent, QMetaObject, QObject, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from ui_qt import dialogs, geometry, imageutil, theme

APP_NAME = "ScreenSnap"
APP_DISPLAY_NAME = "ScreenSnap"
ORG_NAME = "ScreenSnap"
MUTEX_NAME = "Local\\ScreenSnap.Qt.SingleInstance"   # 与旧 tk 版互不干扰，便于迁移期并行
APP_USER_MODEL_ID = "ScreenSnap.Qt.App"

kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
ERROR_ALREADY_EXISTS = 183


# ---------------- 进程级设置 ----------------
def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """让任务栏使用独立图标分组，而不是 python.exe 的图标。"""
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


class SingleInstance:
    """基于命名互斥体的单实例；已在运行时 ``acquire()`` 返回 False。"""

    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self._handle = None
        self.already_running = False

    def acquire(self) -> bool:
        if self._handle:
            return True
        try:
            handle = kernel32.CreateMutexW(None, False, ctypes.c_wchar_p(self.name))
            err = kernel32.GetLastError()
            if not handle:
                # 创建失败（极少见）时不阻塞启动
                self.already_running = False
                return True
            if err == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                self.already_running = True
                return False
            self._handle = handle
            self.already_running = False
            return True
        except Exception:
            self.already_running = False
            return True

    def release(self) -> None:
        if self._handle:
            try:
                kernel32.ReleaseMutex(self._handle)
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


# ---------------- QApplication ----------------
# ---------------- 应用图标 ----------------
# 相对项目根目录（打包后为 _internal/）：优先多尺寸 ico，退回 png，最后字母兜底
ICON_CANDIDATES = ("assets/icon.ico", "assets/icon.png", "icon.ico", "icon.png")

_APP_ICON: QIcon | None = None


def find_app_icon() -> str | None:
    """找到项目内的应用图标文件；找不到返回 None。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ICON_CANDIDATES:
        path = os.path.join(base, rel)
        if os.path.isfile(path):
            return path
    return None


def app_icon() -> QIcon:
    """应用图标单例：ico > png > 字母兜底，保证任何环境都不返回空图标。"""
    global _APP_ICON
    if _APP_ICON is not None:
        return _APP_ICON
    icon = QIcon()
    path = find_app_icon()
    if path:
        try:
            icon = QIcon(path)
        except Exception:
            icon = QIcon()
    if icon.isNull():
        try:
            icon = QIcon(imageutil.letter_icon("S", theme.ACCENT, "#FFFFFF", 256))
        except Exception:
            icon = QIcon()
    _APP_ICON = icon
    return icon


def apply_window_icon(widget: QWidget | None) -> None:
    """给窗口挂应用图标（已显式设置过的窗口不覆盖）。"""
    try:
        if widget is not None and widget.windowIcon().isNull():
            widget.setWindowIcon(app_icon())
    except Exception:
        pass


class _WindowIconFilter(QObject):
    """自动给所有顶层窗口（含运行时弹出的对话框）挂应用图标。

    逐个窗口改代码容易漏；这里用应用级事件过滤器在窗口 Show 时统一兜底，
    主窗口、设置、历史库、OCR 结果、翻译、贴图、视频播放器都能拿到图标。
    """

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
        try:
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) \
                    and obj.isWindow():
                apply_window_icon(obj)
        except Exception:
            pass
        return False


def _install_app_icon(app: QApplication) -> None:
    """把应用图标装到 QApplication 上，并挂全局窗口图标过滤器（幂等）。"""
    try:
        app.setWindowIcon(app_icon())
        if getattr(app, "_icon_filter", None) is None:
            app._icon_filter = _WindowIconFilter(app)   # 保持引用，防被 GC
            app.installEventFilter(app._icon_filter)
    except Exception:
        pass


def create_app(argv: list[str] | None = None) -> QApplication:
    """创建并配置 QApplication（幂等：已存在则直接返回）。

    * 圆角/贴图窗口要求的 ``WA_TranslucentBackground`` 与 Fusion 样式配合；
    * 全局字体与 QSS 由 ``theme.apply`` 注入；
    * 高分屏缩放策略保持 PassThrough，再做一次 DPI 平台设置（Per-Monitor V2）。
    """
    app = QApplication.instance()
    if app is not None:
        _install_app_icon(app)
        return app

    set_app_user_model_id()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # Qt6 默认已启用高 DPI 缩放，这里显式置位以兼容不同 Qt 小版本
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)   # 托盘常驻：关窗不退出
    app.setStyle("Fusion")
    theme.apply(app)
    # 应用级图标：任务栏 / 窗口标题栏 / Alt-Tab 切图都取这里
    _install_app_icon(app)
    install_excepthook()
    return app


def process_events(ms: int = 0) -> None:
    """轻量让出事件循环（给阻塞式循环如热键回调后刷新 UI 用）。"""
    app = QApplication.instance()
    if app is None:
        return
    if ms > 0:
        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    else:
        app.processEvents()


# ---------------- 异常兜底 ----------------
_installing = False
_hook_installed = False


def _log_dir() -> str:
    try:
        from config import APP_DIR  # 复用旧配置目录，避免两套日志
        return APP_DIR
    except Exception:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ScreenSnap")


def install_excepthook(log_name: str = "qt_error.log") -> None:
    """把未捕获异常写入日志并弹窗提示（只安装一次，避免递归弹窗）。"""
    global _hook_installed
    if _hook_installed:
        return
    _hook_installed = True
    log_path = os.path.join(_log_dir(), log_name)

    def _hook(exc_type, exc_value, exc_tb):
        global _installing
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {QApplication.instance() and 'qt' or 'app'} error =====\n")
                f.write(text)
        except Exception:
            pass
        sys.stderr.write(text)
        if _installing:
            return
        _installing = True
        try:
            if QApplication.instance() is not None:
                dialogs.msg_error("出了点问题",
                                  f"{exc_type.__name__}: {exc_value}",
                                  detail=f"详细信息已写入：\n{log_path}")
        except Exception:
            pass
        finally:
            _installing = False

    sys.excepthook = _hook


# ---------------- 托盘 ----------------
# 全站静默开关：默认 True，托盘气泡不再弹出右下角系统提示。
# 需要临时恢复气泡时调用 set_tray_notify_silent(False)，或对单个 tray 调 set_notify_silent(False)。
_TRAY_NOTIFY_SILENT = True


def set_tray_notify_silent(silent: bool = True) -> None:
    """全局开关托盘气泡是否静默（默认静默，不弹系统气泡）。"""
    global _TRAY_NOTIFY_SILENT
    _TRAY_NOTIFY_SILENT = bool(silent)


def tray_notify_silent() -> bool:
    """当前全局是否静默托盘气泡。"""
    return _TRAY_NOTIFY_SILENT


class TrayIcon(QSystemTrayIcon):
    """托盘图标 + 菜单骨架。

    菜单项通过 ``set_menu_items`` 用数据描述，P5 设置页与 P1 骨架共用同一套接口::

        tray.set_menu_items([
            ("capture", "截图  Ctrl+Alt+A", on_capture),
            "-",
            ("settings", "设置…", on_settings),
            ("quit", "退出 ScreenSnap", on_quit),
        ])
    """

    def __init__(self, icon_path: str | None = None, parent=None, tooltip: str = APP_NAME):
        icon = imageutil.file_to_qicon(icon_path, QSize(32, 32)) if icon_path else None
        if icon is None:
            icon = imageutil.letter_icon("S", theme.ACCENT, "#FFFFFF", 64)
        super().__init__(icon, parent)
        self.setToolTip(tooltip)
        # 关键：QMenu 必须有 QWidget 父对象并挂到 setContextMenu，
        # 否则右键（Windows 还有左键按住拖出）点击托盘图标不会弹出菜单。
        self._menu = QMenu(parent if isinstance(parent, QWidget) else None)
        self._actions: dict[str, QAction] = {}
        self._left_click = None
        # None = 跟随全局开关；True/False = 该托盘单独指定
        self._notify_silent: bool | None = None
        self._silent_log: list[tuple[str, str]] = []
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)

    # --- 菜单 ---
    @property
    def menu(self) -> QMenu:
        return self._menu

    def set_menu_items(self, items) -> None:
        """items: [(key, text, callback) | "-"]；"-" 为分隔线。"""
        self._menu.clear()
        self._actions.clear()
        for item in items:
            if item == "-":
                self._menu.addSeparator()
                continue
            key, text, cb = item[0], item[1], (item[2] if len(item) > 2 else None)
            act = QAction(text, self._menu)
            if cb is not None:
                act.triggered.connect(cb)
            self._menu.addAction(act)
            self._actions[key] = act

    def action(self, key: str) -> QAction | None:
        return self._actions.get(key)

    def set_action_text(self, key: str, text: str) -> None:
        act = self._actions.get(key)
        if act is not None:
            act.setText(text)

    def set_action_enabled(self, key: str, enabled: bool) -> None:
        act = self._actions.get(key)
        if act is not None:
            act.setEnabled(bool(enabled))

    # --- 左键单击 ---
    def set_left_click(self, callback) -> None:
        self._left_click = callback

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self._left_click:
            try:
                self._left_click()
            except Exception:
                pass

    # --- 状态 ---
    def start(self) -> bool:
        """显示托盘图标；系统不支持托盘返回 False。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.show()
        return True

    # --- 气泡 ---
    def set_notify_silent(self, silent: bool | None = True) -> None:
        """单托盘静默开关：True/False 单独指定，None 表示跟随全局。"""
        self._notify_silent = None if silent is None else bool(silent)

    def notify_silent(self) -> bool:
        """当前该托盘是否静默（实例设置优先，否则跟随全局）。"""
        if self._notify_silent is not None:
            return bool(self._notify_silent)
        return _TRAY_NOTIFY_SILENT

    def notify(self, title: str, message: str,
               icon=QSystemTrayIcon.MessageIcon.Information, msecs: int = 3000) -> None:
        """气泡提示：默认全站静默，不再弹右下角系统气泡。

        静默时只写入 ``_silent_log``（保留最近 50 条，便于排查），
        不调用 ``showMessage``；状态栏、对话框等其它 UI 提示不受影响。
        需要临时恢复气泡可 ``set_notify_silent(False)`` 或
        ``app.set_tray_notify_silent(False)``。
        """
        if self.notify_silent():
            try:
                self._silent_log.append((str(title), str(message)))
                del self._silent_log[:-50]
            except Exception:
                pass
            return
        if self.isVisible():
            self.showMessage(title, message, icon, msecs)


# ---------------- 热键桥接 ----------------
class HotkeyBridge(QObject):
    """把 ``HotkeyManager`` 的工作线程回调转成主线程 Qt 信号。

    * ``set_root_getter(None)``：不再走 tk 的 ``root.after``；
    * 回调在工作线程触发 → ``QMetaObject.invokeMethod`` 队列投递到主线程；
    * 主线程再 ``triggered.emit(name)``，槽函数里可安全操作 UI。
    """

    triggered = Signal(str)

    def __init__(self, manager=None, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._name = "capture"

    @property
    def manager(self):
        return self._manager

    def attach(self, manager) -> None:
        """绑定一个已存在的 HotkeyManager（例如 main_qt 自己创建的实例）。"""
        self._manager = manager

    # 工作线程调用（不可直接碰 UI）
    def _on_hotkey(self) -> None:
        QMetaObject.invokeMethod(self, "_deliver", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _deliver(self) -> None:
        self.triggered.emit(self._name)

    def register(self, hotkey_text: str, name: str = "capture") -> str | None:
        """注册热键。成功返回 None，失败返回中文错误信息。"""
        if self._manager is None:
            from hotkey import HotkeyManager  # 惰性导入，避免无热键场景的 ctypes 开销
            self._manager = HotkeyManager()
        self._manager.set_root_getter(None)
        self._name = name
        return self._manager.register(hotkey_text, self._on_hotkey)

    def unregister(self) -> None:
        if self._manager is not None:
            self._manager.unregister()


# ---------------- 窗口定位辅助 ----------------
def place_on_cursor_screen(widget, offset: tuple[int, int] = (0, 0),
                           center: bool = True) -> None:
    """把已在逻辑尺寸下的窗口放到鼠标所在屏（居中或按偏移）。"""
    if center:
        pos = geometry.center_on_screen(widget.size())
        widget.move(pos.x() + offset[0], pos.y() + offset[1])


def enable_translucent(widget) -> None:
    """开启无边框圆角窗口所需的三个属性（截图遮罩/贴图/骨架窗共用）。"""
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)


def setup_debug_dump() -> None:
    """把多屏 / DPI 诊断写入日志（排查错位问题时用）。"""
    try:
        path = os.path.join(_log_dir(), "qt_geometry.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            v = wintypes  # 占位，避免 linter 报未使用
            del v
            f.write("\n" + geometry.describe() + "\n")
    except Exception:
        pass
