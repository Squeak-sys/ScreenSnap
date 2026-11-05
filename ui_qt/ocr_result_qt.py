# -*- coding: utf-8 -*-
"""OCR 识别结果窗（P9-3）。

视觉：沿用 SHUTTER 设计语言（10px 圆角无边框卡片、白底、1px LINE 描边）。
布局：

    ┌─────────────────────────────────────────────┐
    │ 识别文字   [高精度·RapidOCR]           ✕   │  标题栏（可拖动）
    ├─────────────────────────────────────────────┤
    │ [复制全部] [换引擎 ▾]        [重新识别]   │  工具行
    ├─────────────────────────────────────────────┤
    │  识别到的文字（可直接编辑）…                │  正文
    ├─────────────────────────────────────────────┤
    │ 共 128 字 · 用时 1.5s        引擎 RapidOCR │  状态栏
    └─────────────────────────────────────────────┘

交互要点：
    * 结果可编辑，改完再复制；不自动写剪贴板（避免覆盖用户剪贴板内容）；
    * 高精度组件包未安装时，顶部出现一条 WARN 提示 + [安装组件包] 按钮；
    * Esc 关闭，Ctrl+Enter 复制全部；
    * 浮窗置顶、可拖动；识别期间不隐藏截图选区，便于边看截图边核对文本；
    * 正文固定按原图版式还原（所见即所得），不做智能重排。
"""

from __future__ import annotations

import io
import os
import time

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import (QEventLoop, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetrics, QGuiApplication,
                           QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import config as _cfg
from ui_qt import clipboard, imageutil, ocr_qt, theme, translate_qt

WIN_W_MIN, WIN_W_MAX = 460, 1100
WIN_H_MIN, WIN_H_MAX = 300, 860
TR_MIN_H = 150                    # 翻译区最小高度（显示后）
OCR_SUBDIR = "ocr"

_OPEN_WINDOWS: list["OcrResultWindow"] = []   # 防止窗口被 GC
_PICTURE_WINDOWS: list["PictureTransWindow"] = []   # 图片翻译预览窗（同上）

# 最近一次关闭识别窗的时刻（monotonic）。关闭瞬间鼠标释放/双击的第二次按下可能落到
# 下方截图工具条的「识别」按钮上，导致弹窗刚关就又被打开；调用方据此做短暂去抖。
_LAST_CLOSE_TS = 0.0
_CLOSE_GUARD_S = 0.35

_DIAG_LOG = os.path.join(_cfg.APP_DIR, "logs", "ocr_window.log")


def recently_closed(seconds: float = _CLOSE_GUARD_S) -> bool:
    """识别窗是否刚刚被关闭（用于拦住关闭瞬间的误触/事件重放）。"""
    return (time.monotonic() - _LAST_CLOSE_TS) < seconds


def _trace(tag: str) -> None:
    """诊断日志：记录谁打开了/关闭了识别窗，便于定位「关掉又自己冒出来」。"""
    try:
        import traceback
        os.makedirs(os.path.dirname(_DIAG_LOG), exist_ok=True)
        frames = [ln.strip() for ln in traceback.format_stack(limit=10)[:-1] if ln.strip()]
        with open(_DIAG_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), tag))
            for ln in frames[-6:]:
                f.write("    " + ln.replace("\n", " ") + "\n")
            f.write("\n")
    except Exception:
        pass


def _open_dir() -> str:
    d = os.path.join(_cfg.APP_DIR, OCR_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _btn(text: str, kind: str = "ghost") -> QPushButton:
    """统一样式的文字按钮：primary / ghost / warn。"""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    b.setFont(theme.font(theme.FONT_SM_SIZE, False))
    h = 26
    if kind == "primary":
        b.setStyleSheet(
            "QPushButton{background:%s;color:#FFFFFF;border:none;border-radius:%dpx;padding:0 12px;}"
            "QPushButton:hover{background:%s;}"
            "QPushButton:disabled{background:%s;color:%s;}"
            % (theme.ACCENT, theme.R_BTN, theme.ACCENT_HOVER, theme.SURFACE_3, theme.INK_3)
        )
    elif kind == "warn":
        b.setStyleSheet(
            "QPushButton{background:%s;color:#FFFFFF;border:none;border-radius:%dpx;padding:0 10px;}"
            "QPushButton:hover{background:#E0951A;}"
            % (theme.WARN, theme.R_BTN)
        )
    else:
        b.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;border-radius:%dpx;padding:0 10px;}"
            "QPushButton:hover{background:%s;}"
            "QPushButton:disabled{color:%s;}"
            % (theme.SURFACE, theme.INK, theme.LINE, theme.R_BTN, theme.SURFACE_2, theme.INK_3)
        )
    b.setFixedHeight(h)
    return b


def _picture_translate_icon(size: int = 24) -> QIcon:
    """自绘「图片翻译」图标：圆角相框 + 两条文字线 + 右下角双向箭头。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(theme.INK_2))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(2.6, 3.4, 13.2, 12.2), 3.0, 3.0)   # 相框
    p.drawLine(QPointF(5.4, 7.9), QPointF(12.8, 7.9))           # 文字线 1
    p.drawLine(QPointF(5.4, 11.6), QPointF(10.2, 11.6))         # 文字线 2
    p.drawLine(QPointF(12.0, 18.6), QPointF(21.6, 18.6))        # 双向箭头主轴
    p.drawLine(QPointF(12.0, 18.6), QPointF(14.2, 16.4))        # 左箭头
    p.drawLine(QPointF(12.0, 18.6), QPointF(14.2, 20.8))
    p.drawLine(QPointF(21.6, 18.6), QPointF(19.4, 16.4))        # 右箭头
    p.drawLine(QPointF(21.6, 18.6), QPointF(19.4, 20.8))
    p.end()
    return QIcon(pm)


class MissingPackDialog(QDialog):
    """文字识别包缺失提示（换机后系统无 zh-CN OCR 语言包时弹出）。

    给出三条出路：安装高精度组件包、打开系统语言设置装中文语言包、
    或（仅当系统有其它语种引擎时）用系统 OCR 硬着头皮继续。
    """

    def __init__(self, status: str, can_fallback_win: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("需要文字识别包")
        self.setMinimumWidth(460)
        self.action = "cancel"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)

        head = QLabel("需要安装文字识别包", self)
        f = head.font()
        f.setPointSize(theme.FONT_TITLE_SIZE)
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)

        if status == "no_zh_pack":
            main = "未检测到 Windows 的「中文（简体）OCR」语言包，系统 OCR 无法识别中文。"
        elif status == "no_module":
            main = "本机系统 OCR 运行库不可用，且高精度组件包尚未安装，当前无法识别文字。"
        elif status == "no_engine":
            main = "当前没有可用的文字识别引擎：系统未安装 OCR 语言包，高精度组件包也未安装。"
        else:
            main = "当前没有可用的文字识别引擎，无法识别文字。"
        body = QLabel(main, self)
        body.setWordWrap(True)
        body.setFont(theme.font(theme.FONT_BODY_SIZE))
        lay.addWidget(body)

        tip = QLabel(
            "ScreenSnap 默认使用本机 Windows 系统 OCR，但当前这台电脑没有可用的系统识别"
            "（缺少中文 OCR 语言包或运行库）。点「下载组件包」即可获得离线可用的高精度识别"
            "（约 100 MB，装后不再依赖系统语言包）；也可以到 Windows 设置里给中文添加"
            "「OCR」可选功能后重试。",
            self,
        )
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        tip.setFont(theme.font(theme.FONT_SM_SIZE))
        lay.addWidget(tip)

        btns = QHBoxLayout()
        btns.addStretch(1)
        if can_fallback_win:
            b_win = QPushButton("用系统 OCR 继续", self)
            b_win.clicked.connect(lambda: self._done("winocr"))
            btns.addWidget(b_win)
        b_set = QPushButton("打开语言设置", self)
        b_set.clicked.connect(lambda: self._done("settings"))
        btns.addWidget(b_set)
        b_cancel = QPushButton("取消", self)
        b_cancel.clicked.connect(lambda: self._done("cancel"))
        btns.addWidget(b_cancel)
        b_install = QPushButton("下载组件包…", self)
        b_install.setObjectName("Primary")
        b_install.setDefault(True)
        b_install.clicked.connect(lambda: self._done("install"))
        btns.addWidget(b_install)
        lay.addLayout(btns)

    def _done(self, action: str) -> None:
        self.action = action
        self.accept()


def _choose_pack_source(parent=None) -> tuple[str, bool] | tuple[None, bool]:
    """让用户选组件包来源：本地 zip 或在线链接。返回 (src, is_url)。"""
    menu = QMenu(parent)
    a1 = menu.addAction("选择本地组件包（.zip）…")
    a2 = menu.addAction("从链接下载…")
    act = menu.exec(QCursor.pos())
    if act == a1:
        path, _ = QFileDialog.getOpenFileName(parent, "选择组件包", "", "组件包 (*.zip)")
        if path:
            return path, False
    elif act == a2:
        url, ok = QInputDialog.getText(parent, "从链接下载组件包", "下载地址（http/https）：")
        if ok and url.strip():
            return url.strip(), True
    return None, False


def install_pack_modal(parent=None) -> bool:
    """模态安装组件包（带进度条），供设置页与缺包提示复用；返回是否成功。"""
    src, is_url = _choose_pack_source(parent)
    if not src:
        return False

    dlg = QProgressDialog("正在安装组件包…", "隐藏", 0, 1000, parent)
    dlg.setWindowTitle("安装高精度组件包")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    dlg.setValue(0)
    dlg.show()

    result = {"ok": False, "msg": ""}
    loop = QEventLoop()
    task = ocr_qt.PackInstallTask(src, is_url, parent)

    def _on_prog(done: int, total: int) -> None:
        if total > 0:
            dlg.setValue(int(done * 1000 / total))
        dlg.setLabelText("%s %.1f MB" % ("下载中" if is_url else "安装中", done / 1048576))

    def _on_done(_target: str) -> None:
        result["ok"] = True
        loop.quit()

    def _on_failed(msg: str) -> None:
        result["msg"] = msg
        loop.quit()

    task.progress.connect(_on_prog)
    task.done.connect(_on_done)
    task.failed.connect(_on_failed)
    dlg.canceled.connect(loop.quit)
    task.start()
    loop.exec()
    task.wait(2000)
    dlg.close()
    ocr_qt.reset_backend_cache()
    return bool(result["ok"])


def _engine_ready(prefer: str | None) -> tuple[bool, str, bool]:
    """判断当前是否能识别中文。

    返回 ``(就绪, 系统引擎状态, 系统引擎是否可作为降级继续)``。
    """
    infos = ocr_qt.available_backends()
    rapid_ok = any(i["id"] == "rapidocr" and i["available"] for i in infos)
    status = "no_engine"
    for i in infos:
        if i["id"] == "winocr":
            status = i.get("status", "no_engine")
    if prefer == "rapidocr":
        return rapid_ok, status, False
    if prefer == "winocr":
        return status == "ok", status, status == "no_zh_pack"
    # auto：中文语言包缺失时即便存在其它语种引擎也提示，避免中文识别乱码
    return (rapid_ok or status == "ok"), status, (not rapid_ok and status == "no_zh_pack")


def engine_ready(prefer: str | None = None) -> bool:
    """只查询识别引擎是否可用，不弹任何提示，供调用方决定是否先腾出界面。"""
    return _engine_ready(prefer)[0]


def _force_topmost(win) -> None:
    """把窗口真正提到 topmost 组最上层。

    截图覆盖层是「全屏 + WindowStaysOnTopHint」，普通的新窗口即便 show/raise 也只是
    同组里排队，经常被压在下面（用户看到的就是“点了没反应”）。这里直接调
    SetWindowPos(HWND_TOPMOST) 把它插到 topmost 组顶部，覆盖层与选区框都保留不动。
    """
    try:
        import ctypes
        hwnd = int(win.winId())
        if not hwnd:
            return
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0040
        ctypes.windll.user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
        )
    except Exception:
        pass


def _claim_foreground(win) -> None:
    """把键盘焦点交还给指定窗口（识别窗关闭后交还截图覆盖层）。

    识别窗是独立顶层窗口，关掉之后系统不会自动把前台还给截图覆盖层，Qt 侧
    activeWindow 变成空，第二次按 Esc 没有任何窗口接收，表现就是“按了没反应”。
    这里同时走 Qt 与 Win32 两条路：raise_/activateWindow 负责 Qt 内部激活状态，
    SetForegroundWindow/SetFocus 负责系统前台与键盘焦点，缺一个都可能拿不回 Esc。
    """
    if win is None:
        return
    try:
        win.show()
    except Exception:
        pass
    _force_topmost(win)
    try:
        win.raise_()
        win.activateWindow()
        win.setFocus(Qt.ActiveWindowFocusReason)
    except Exception:
        pass
    try:
        import ctypes
        hwnd = int(win.winId())
        if hwnd:
            u = ctypes.windll.user32
            u.SetForegroundWindow(hwnd)
            u.SetFocus(hwnd)
    except Exception:
        pass


def _park_on_cursor_screen(win) -> None:
    """把即将弹出的窗口居中到光标所在屏幕的可用区域。"""
    try:
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is None:
            return
        win.adjustSize()
        area = scr.availableGeometry()
        win.move(area.center() - win.rect().center())
    except Exception:
        pass


def ensure_ocr_ready(prefer: str | None = None, parent=None) -> tuple[bool, str | None]:
    """识别前的引擎守门：缺包则提示安装。

    返回 ``(是否可继续, 实际要用的 prefer)``；prefer 为 None 表示交由自动选择。
    """
    ready, status, can_fallback = _engine_ready(prefer)
    if ready:
        return True, prefer
    dlg = MissingPackDialog(status, can_fallback, parent)
    # 多屏环境默认可能摆到副屏甚至屏外，按当前光标所在屏居中，确保用户看得见
    _park_on_cursor_screen(dlg)
    # 截图覆盖层仍在屏上（选区框要留着），必须先把它顶到 topmost 组最上层再 exec
    dlg.show()
    _force_topmost(dlg)
    dlg.raise_()
    dlg.activateWindow()
    dlg.exec()
    if dlg.action == "install":
        return (install_pack_modal(parent), prefer)
    if dlg.action == "settings":
        ocr_qt.open_language_settings()
        return False, None
    if dlg.action == "winocr":
        return True, "winocr"
    return False, None


def gate_entry(prefer: str | None = None, owner=None) -> tuple[bool, str | None]:
    """入口守门：点击「识别文字」的那一瞬间就判定引擎，缺引擎立刻弹提示并中止。

    与 ensure_ocr_ready 的分工：本函数必须在任何识别动作（取图、建窗、起线程、
    钉图）之前调用，且**不参与**「识别窗刚关闭」的防抖抑制，保证点击即提示，
    不会拖到识别线程返回或钉图之后才把安装提示甩给用户。

    owner 若实现了 ``yield_for_dialog()``，会先让它临时退让一层再弹框
    （全屏截图覆盖层是 topmost，压着提示框时用户看到的就是“点了没反应”），
    提示关闭后调用返回的恢复回调把它重新顶回最上层。
    """
    ok = _engine_ready(prefer)[0]
    if ok:
        return True, prefer
    restore = None
    if owner is not None:
        try:
            hook = getattr(owner, "yield_for_dialog", None)
            if callable(hook):
                restore = hook()
        except Exception:
            restore = None
    try:
        return ensure_ocr_ready(prefer)
    finally:
        if callable(restore):
            try:
                restore()
            except Exception:
                pass


class _TitleBar(QWidget):
    """自绘标题栏：标题 + 引擎徽标 + 关闭按钮，整条可拖动窗口。"""

    def __init__(self, win: "OcrResultWindow"):
        super().__init__(win)
        self._win = win
        self.setFixedHeight(42)
        self.setCursor(Qt.SizeAllCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(8)

        self.title = QLabel("识别文字", self)
        self.title.setFont(theme.font(theme.FONT_BODY_SIZE, True))
        self.title.setStyleSheet("color:%s;" % theme.INK)
        lay.addWidget(self.title)

        self.badge = QLabel("", self)
        self.badge.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.badge.setStyleSheet(
            "color:%s;background:%s;border-radius:%dpx;padding:2px 8px;"
            % (theme.INK_2, theme.SURFACE_3, theme.R_PILL)
        )
        self.badge.hide()
        lay.addWidget(self.badge)
        lay.addStretch(1)

        self.btn_close = QToolButton(self)
        self.btn_close.setText("✕")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setFont(theme.font(theme.FONT_BODY_SIZE, False))
        self.btn_close.setStyleSheet(
            "QToolButton{border:none;background:transparent;color:%s;border-radius:13px;}"
            "QToolButton:hover{background:%s;color:%s;}" % (theme.INK_2, theme.SURFACE_3, theme.DANGER)
        )
        self.btn_close.clicked.connect(win.close)
        lay.addWidget(self.btn_close)

        self._drag_from: QPoint | None = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            self._win._user_moved = True
            self._win.move(e.globalPosition().toPoint() - self._drag_from)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        super().mouseReleaseEvent(e)


class OcrResultWindow(QWidget):
    """OCR 识别结果窗。"""

    closed = Signal()

    def __init__(self, image: Image.Image, prefer: str | None = None,
                 item: dict | None = None, storage=None, parent=None,
                 anchor: QRect | None = None, owner=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("识别文字")
        # owner：发起识别的截图覆盖层。识别窗关掉后要把键盘焦点还给它，
        # 否则覆盖层收不到下一次 Esc，截图窗口退不掉。
        self._owner = owner
        self._image = image.copy() if image is not None else None
        self._prefer = prefer or ocr_qt.engine_prefer()
        self._item = item
        self._storage = storage
        self._task: ocr_qt.OcrTask | None = None
        self._pack_task: ocr_qt.PackInstallTask | None = None
        self._lines: list[ocr_qt.OcrLine] = []
        self._elapsed = 0.0
        self._backend_label = ""
        self._layout_mode = "layout"    # layout=按原图版式还原（所见即所得）
        self._anchor: QRect | None = anchor  # 选区屏幕矩形：把弹窗摆在选区旁，不遮挡截图
        self._user_moved = False            # 用户拖过窗口后不再自动重排位置
        self._user_resized = False          # 用户手动拉过窗口后不再自动套用推荐尺寸
        self._tr_task: translate_qt.TranslateTask | None = None
        self._tr_scope = "all"              # 最近一次翻译范围：all / selection
        self._pic_task: translate_qt.PictureTranslateTask | None = None
        self._pic_started = 0.0             # 图片翻译开始时刻（算耗时）
        self._pic_label = "图片翻译"         # 按钮常态文案
        self._setup_ui()
        self.start(self._prefer)

    # ------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame(self)
        self.panel.setObjectName("ocrPanel")
        self.panel.setStyleSheet(
            "#ocrPanel{background:%s;border:1px solid %s;border-radius:%dpx;}"
            % (theme.SURFACE, theme.LINE, theme.R_WIN)
        )
        outer.addWidget(self.panel)

        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        self.title_bar = _TitleBar(self)
        lay.addWidget(self.title_bar)
        lay.addWidget(self._hline())

        # ---- 工具行
        tb = QWidget(self.panel)
        tb_lay = QHBoxLayout(tb)
        tb_lay.setContentsMargins(12, 8, 12, 6)
        tb_lay.setSpacing(8)

        self.btn_copy = _btn("复制全部", "primary")
        self.btn_copy.clicked.connect(self.copy_all)
        tb_lay.addWidget(self.btn_copy)

        self.btn_engine = _btn("换引擎 ▾")
        self.btn_engine.clicked.connect(self._popup_engine_menu)
        tb_lay.addWidget(self.btn_engine)

        self.btn_tr_sel = _btn("翻译选中")
        self.btn_tr_sel.setToolTip("翻译正文里当前选中的那一段文字")
        self.btn_tr_sel.clicked.connect(self.translate_selection)
        tb_lay.addWidget(self.btn_tr_sel)

        self.btn_tr_all = _btn("翻译全部")
        self.btn_tr_all.setToolTip("翻译整篇识别结果")
        self.btn_tr_all.clicked.connect(self.translate_all)
        tb_lay.addWidget(self.btn_tr_all)
        tb_lay.addStretch(1)

        self.btn_rerun = _btn("重新识别")
        self.btn_rerun.clicked.connect(lambda: self.start(self._prefer))
        tb_lay.addWidget(self.btn_rerun)

        self.btn_pic_tr = _btn(self._pic_label)
        self.btn_pic_tr.setIcon(_picture_translate_icon(18))
        self.btn_pic_tr.setIconSize(QSize(16, 16))
        self.btn_pic_tr.setToolTip("翻译这张截图里的文字，并把译文贴回原位预览（百度图片翻译）")
        self.btn_pic_tr.clicked.connect(self.translate_picture)
        tb_lay.addWidget(self.btn_pic_tr)
        lay.addWidget(tb)

        # ---- 组件包提示条（按需显示）
        self.hint = QWidget(self.panel)
        hint_lay = QHBoxLayout(self.hint)
        hint_lay.setContentsMargins(12, 0, 12, 8)
        hint_lay.setSpacing(8)
        self.hint_text = QLabel("", self.hint)
        self.hint_text.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.hint_text.setWordWrap(True)
        self.hint_text.setStyleSheet("color:%s;" % theme.INK_2)
        hint_lay.addWidget(self.hint_text, 1)
        self.btn_install = _btn("安装组件包", "warn")
        self.btn_install.clicked.connect(self.install_pack)
        hint_lay.addWidget(self.btn_install)
        self.hint.hide()
        lay.addWidget(self.hint)

        # ---- 正文
        self.text = QPlainTextEdit(self.panel)
        # 等宽字体：让「按原图版式」的空格缩进在文本框中真正对齐
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setFamilies(["Consolas", "Microsoft YaHei UI", "Microsoft YaHei"])
        mono.setPointSizeF(theme.font(theme.FONT_BODY_SIZE, False).pointSizeF())
        self.text.setFont(mono)
        self.text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:none;color:%s;"
            "selection-background-color:%s;selection-color:#FFFFFF;"
            "padding:4px 12px 8px 12px;}" % (theme.SURFACE, theme.INK, theme.ACCENT)
        )
        self.text.setPlaceholderText("识别中…")
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Qt 内置右键菜单文案来自 qtbase 翻译包，未加载时是英文；这里自绘中文菜单
        self.text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text.customContextMenuRequested.connect(self._show_text_menu)

        # 正文与译文上下分区：分隔条可拖动，内容多时各自带滚动条
        self.split = QSplitter(Qt.Vertical, self.panel)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(6)
        self.split.setStyleSheet(
            "QSplitter::handle{background:%s;} QSplitter::handle:hover{background:%s;}"
            % (theme.LINE, theme.ACCENT_SOFT)
        )
        self.split.addWidget(self.text)
        self.tr_panel = self._build_translate_panel()
        self.split.addWidget(self.tr_panel)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.tr_panel.hide()
        lay.addWidget(self.split, 1)

        lay.addWidget(self._hline())

        # ---- 状态栏
        sb = QWidget(self.panel)
        sb_lay = QHBoxLayout(sb)
        sb_lay.setContentsMargins(12, 6, 12, 8)
        sb_lay.setSpacing(10)
        self.lbl_left = QLabel("准备中…", sb)
        self.lbl_left.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.lbl_left.setStyleSheet("color:%s;" % theme.INK_3)
        sb_lay.addWidget(self.lbl_left, 1)

        self.progress = QProgressBar(sb)
        self.progress.setFixedSize(120, 4)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.setStyleSheet(
            "QProgressBar{background:%s;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:%s;border-radius:2px;}" % (theme.SURFACE_3, theme.ACCENT)
        )
        self.progress.hide()
        sb_lay.addWidget(self.progress)

        self.lbl_right = QLabel("", sb)
        self.lbl_right.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.lbl_right.setStyleSheet("color:%s;" % theme.INK_3)
        sb_lay.addWidget(self.lbl_right)

        # 右下角拖拽手柄：无边框窗口也能手动改大小（信息多时拉大看）
        self.grip = QSizeGrip(sb)
        self.grip.setFixedSize(14, 14)
        self.grip.setStyleSheet("background:transparent;")
        sb_lay.addWidget(self.grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        lay.addWidget(sb)

        self.setMinimumSize(WIN_W_MIN, WIN_H_MIN)
        self.setMaximumSize(16777215, 16777215)
        self._auto_resizing = False
        self.resize(WIN_W_MIN, WIN_H_MIN)
        self._refresh_hint()
        if self._anchor is not None:
            self._place_near(self._anchor)
        else:
            self._center_on_cursor_screen()

    # ------------------------------------------------------------ 译文区
    def _build_translate_panel(self) -> QWidget:
        """正文下方的译文区：标题 + 方向说明 + 复制/关闭，正文只读可滚动。"""
        panel = QWidget(self.panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(0)

        head = QWidget(panel)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 0, 8, 4)
        hl.setSpacing(8)

        self.tr_title = QLabel("译文", head)
        self.tr_title.setFont(theme.font(theme.FONT_SM_SIZE, True))
        self.tr_title.setStyleSheet("color:%s;" % theme.INK_2)
        hl.addWidget(self.tr_title)

        self.tr_meta = QLabel("", head)
        self.tr_meta.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.tr_meta.setStyleSheet("color:%s;" % theme.INK_3)
        hl.addWidget(self.tr_meta, 1)

        self.btn_tr_fix = _btn("去设置", "warn")
        self.btn_tr_fix.clicked.connect(self._open_settings)
        self.btn_tr_fix.hide()
        hl.addWidget(self.btn_tr_fix)

        self.btn_tr_copy = _btn("复制译文")
        self.btn_tr_copy.clicked.connect(self._copy_translate)
        hl.addWidget(self.btn_tr_copy)

        self.btn_tr_hide = QToolButton(head)
        self.btn_tr_hide.setText("✕")
        self.btn_tr_hide.setToolTip("收起译文区")
        self.btn_tr_hide.setCursor(Qt.PointingHandCursor)
        self.btn_tr_hide.setFocusPolicy(Qt.NoFocus)
        self.btn_tr_hide.setFixedSize(22, 22)
        self.btn_tr_hide.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.btn_tr_hide.setStyleSheet(
            "QToolButton{border:none;background:transparent;color:%s;border-radius:11px;}"
            "QToolButton:hover{background:%s;color:%s;}" % (theme.INK_3, theme.SURFACE_3, theme.INK)
        )
        self.btn_tr_hide.clicked.connect(self._hide_translate)
        hl.addWidget(self.btn_tr_hide)
        lay.addWidget(head)

        self.tr_text = QPlainTextEdit(panel)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setFamilies(["Consolas", "Microsoft YaHei UI", "Microsoft YaHei"])
        mono.setPointSizeF(theme.font(theme.FONT_BODY_SIZE, False).pointSizeF())
        self.tr_text.setFont(mono)
        self.tr_text.setReadOnly(True)
        self.tr_text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:none;border-top:1px solid %s;color:%s;"
            "selection-background-color:%s;selection-color:#FFFFFF;padding:6px 12px 8px 12px;}"
            % (theme.SURFACE_2, theme.LINE, theme.INK, theme.ACCENT)
        )
        self.tr_text.setPlaceholderText("点「翻译全部」翻整篇；或在正文里选中一段，再点「翻译选中」。")
        self.tr_text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.tr_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tr_text.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tr_text.customContextMenuRequested.connect(self._show_translate_menu)
        lay.addWidget(self.tr_text, 1)
        return panel

    def _show_translate_menu(self, pos: QPoint) -> None:
        """译文区右键菜单：只有复制 / 复制全部，避免误改译文。"""
        menu = QMenu(self.tr_text)
        menu.setStyleSheet(
            "QMenu{background:%s;border:1px solid %s;border-radius:8px;padding:4px;}"
            "QMenu::item{padding:6px 26px 6px 16px;border-radius:5px;color:%s;}"
            "QMenu::item:selected{background:%s;color:%s;}"
            "QMenu::item:disabled{color:%s;}"
            % (theme.SURFACE, theme.LINE, theme.INK, theme.SURFACE_3, theme.ACCENT, theme.INK_3)
        )
        has_sel = self.tr_text.textCursor().hasSelection()
        a_copy = menu.addAction("复制")
        a_copy.setShortcut(QKeySequence.Copy)
        a_copy.setEnabled(has_sel)
        a_copy.triggered.connect(self.tr_text.copy)
        a_all = menu.addAction("复制全部译文")
        a_all.setEnabled(bool(self.tr_text.toPlainText().strip()))
        a_all.triggered.connect(self._copy_translate)
        menu.exec(self.tr_text.mapToGlobal(pos))

    def _show_tr_notice(self, message: str, need_setup: bool = False) -> None:
        """在译文区就地显示提示（不弹窗，避免打断识别核对）。"""
        self._ensure_tr_visible()
        self.tr_title.setText("译文")
        self.tr_meta.setText("")
        self.btn_tr_fix.setVisible(bool(need_setup))
        self.btn_tr_copy.setEnabled(False)
        self.tr_text.setPlainText(message)

    def _ensure_tr_visible(self) -> None:
        """第一次翻译时展开译文区，并给窗口让出一块高度。"""
        if self.tr_panel.isVisible():
            return
        self.tr_panel.show()
        h_now = max(self.height(), WIN_H_MIN)
        tr_h = max(TR_MIN_H, int(h_now * 0.42))
        self.split.setSizes([max(90, h_now - tr_h - 100), tr_h])
        if not self._user_resized:
            self._auto_resizing = True
            self.resize(self.width(), min(WIN_H_MAX, h_now + TR_MIN_H))
            QTimer.singleShot(0, self._end_auto_resize)

    def _hide_translate(self) -> None:
        """收起译文区，窗口高度还回去。"""
        if not self.tr_panel.isVisible():
            return
        self.tr_panel.hide()
        if not self._user_resized:
            self._auto_resizing = True
            self.resize(self.width(), max(WIN_H_MIN, self.height() - TR_MIN_H))
            QTimer.singleShot(0, self._end_auto_resize)

    def _end_auto_resize(self) -> None:
        self._auto_resizing = False

    def _copy_translate(self) -> None:
        txt = self.tr_text.toPlainText()
        if not txt.strip():
            return
        clipboard.copy_text(txt)
        self.tr_meta.setText("已复制译文 %d 字" % len(txt.replace("\n", "")))

    def _open_settings(self) -> None:
        """从译文区直接进设置页填翻译 Key，保存后立刻可用。"""
        from ui_qt import settings_qt

        new_cfg = settings_qt.open_settings(self)
        if new_cfg is None:
            return
        pid = str(new_cfg.get("translate_provider") or "baidu")
        if translate_qt.config_ready(new_cfg):
            self.btn_tr_fix.hide()
            self._show_tr_notice("翻译接口已配置好，点「翻译全部」或「翻译选中」再试一次。")
            self.tr_meta.setText(translate_qt.provider_label(pid))
        else:
            self._show_tr_notice("配置仍不完整：%s" % (
                translate_qt.credentials(pid, new_cfg)["hint"] or "请检查 Key"), need_setup=True)

    # ------------------------------------------------------------ 翻译动作
    def translate_selection(self) -> None:
        """只翻正文里选中的那一段。"""
        self._start_translate("selection")

    def translate_all(self) -> None:
        """翻整篇识别结果。"""
        self._start_translate("all")

    def _start_translate(self, scope: str) -> None:
        full = self.text.toPlainText().strip()
        if not full:
            return
        if scope == "selection":
            raw = self.text.textCursor().selectedText() if self.text.textCursor().hasSelection() else ""
            payload = raw.replace("\u2029", "\n").strip()
            if not payload:
                self.text.setFocus()
                self._show_tr_notice("先在正文里选中要翻译的文字，再点「翻译选中」。")
                return
        else:
            payload = full

        cfg = _cfg.load_config()
        pid = str(cfg.get("translate_provider") or "baidu")
        if not translate_qt.config_ready(cfg):
            hint = translate_qt.credentials(pid, cfg)["hint"] or "请填写 Key"
            self._show_tr_notice("还没有可用的翻译接口：%s" % hint, need_setup=True)
            return

        if self._tr_task is not None and self._tr_task.isRunning():
            self._tr_task.requestInterruption()
        self._tr_scope = scope
        self._ensure_tr_visible()
        self.tr_title.setText("译文 · " + ("选中内容" if scope == "selection" else "全文"))
        self.tr_meta.setText("翻译中…")
        self.btn_tr_fix.hide()
        self.tr_text.setPlainText("正在翻译 %d 字…" % len(payload.replace("\n", "")))
        self.btn_tr_copy.setEnabled(False)
        self.btn_tr_sel.setEnabled(False)
        self.btn_tr_all.setEnabled(False)

        self._tr_task = translate_qt.TranslateTask(payload, cfg, parent=self)
        self._tr_task.done.connect(self._on_translate_done)
        self._tr_task.failed.connect(self._on_translate_failed)
        self._tr_task.start()

    def _end_translate(self) -> None:
        self.btn_tr_sel.setEnabled(True)
        self.btn_tr_all.setEnabled(True)

    def _on_translate_done(self, result: str, meta: str) -> None:
        self._end_translate()
        self.tr_meta.setText(meta)
        self.btn_tr_copy.setEnabled(True)
        self.tr_text.setPlainText(result)

    def _on_translate_failed(self, msg: str) -> None:
        self._end_translate()
        self.tr_meta.setText("翻译失败")
        self.tr_text.setPlainText(msg)
        self.btn_tr_copy.setEnabled(False)
        # 未配置 / 鉴权类错误，直接把「去设置」按钮亮出来
        low = msg.replace(" ", "")
        if any(k in low for k in ("未配置", "尚未配置", "APPID", "密钥", "Key", "key")):
            self.btn_tr_fix.show()

    # ------------------------------------------------------------ 图片翻译
    def translate_picture(self) -> None:
        """把当前截图的图片交给百度图片翻译，完成后弹预览窗。"""
        if self._image is None:
            self._pic_notice("没有可翻译的图片")
            return
        if self._pic_task is not None and self._pic_task.isRunning():
            return
        try:
            buf = io.BytesIO()
            self._image.convert("RGB").save(buf, format="PNG")
            raw = buf.getvalue()
        except Exception as e:
            self._pic_notice("截图转换失败：%s" % e)
            return

        cfg = _cfg.load_config()
        self._pic_started = time.monotonic()
        self.btn_pic_tr.setEnabled(False)
        self.btn_pic_tr.setText("图片翻译中…")
        self.lbl_left.setText("图片翻译中…（识别文字并贴回原位）")
        self._ensure_tr_visible()

        self._pic_task = translate_qt.PictureTranslateTask(raw, cfg, paste=2, view_type=1, parent=self)
        self._pic_task.done.connect(self._on_picture_done)
        self._pic_task.failed.connect(self._on_picture_failed)
        self._pic_task.start()

    def _end_picture(self) -> None:
        self.btn_pic_tr.setEnabled(True)
        self.btn_pic_tr.setText(self._pic_label)

    def _pic_notice(self, message: str) -> None:
        self.lbl_left.setText(message)
        self._ensure_tr_visible()
        self._show_tr_notice(message)

    def _on_picture_done(self, result: dict) -> None:
        self._end_picture()
        items = (result or {}).get("contents") or []
        elapsed = max(0.0, time.monotonic() - self._pic_started)
        if not items:
            self._pic_notice("没有从这张图里找到可翻译的文字")
            return
        self.lbl_left.setText("图片翻译完成 · %d 条 · %.1f 秒" % (len(items), elapsed))
        try:
            win = PictureTransWindow(self._image, result, elapsed=elapsed, paste=2)
            win.show()
            win.raise_()
            _PICTURE_WINDOWS.append(win)
            while len(_PICTURE_WINDOWS) > 3:
                old = _PICTURE_WINDOWS.pop(0)
                try:
                    old.close()
                except Exception:
                    pass
        except Exception as e:
            self._pic_notice("预览窗打开失败：%s" % e)

    def _on_picture_failed(self, msg: str) -> None:
        self._end_picture()
        self._pic_notice("图片翻译失败：%s" % msg)

    # ------------------------------------------------------------ 对照原图
    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:%s;border:none;" % theme.LINE)
        return line

    # ------------------------------------------------------------ 引擎菜单
    def _engine_actions(self) -> list[tuple[str, str, bool]]:
        """返回 (backend_id, 显示名, 可用) 列表。"""
        infos = {i["id"]: i for i in ocr_qt.available_backends()}
        out = []
        for bid, label in (("auto", "自动（本机系统 OCR 优先）"),
                           ("winocr", "系统 OCR · 极速"),
                           ("rapidocr", "高精度组件包 · RapidOCR")):
            if bid == "auto":
                ok = any(i["available"] for i in infos.values())
            else:
                ok = infos.get(bid, {}).get("available", False)
            out.append((bid, label, ok))
        return out

    def _build_engine_menu(self) -> QMenu:
        menu = QMenu(self)
        cur = self._prefer or "auto"
        for bid, label, ok in self._engine_actions():
            act = QAction(("✓ " if bid == cur else "    ") + label + ("" if ok else "（未安装）"), menu)
            act.setEnabled(ok or bid == "auto")
            act.triggered.connect(lambda _=False, b=bid: self.start(b))
            menu.addAction(act)
        menu.addSeparator()
        pinfo = ocr_qt.pack_info()
        if pinfo["installed"]:
            act = QAction("移除高精度组件包…", menu)
            act.triggered.connect(self._remove_pack)
            menu.addAction(act)
        else:
            act = QAction("安装高精度组件包…", menu)
            act.triggered.connect(self.install_pack)
            menu.addAction(act)
        return menu

    def _popup_engine_menu(self) -> None:
        menu = self._build_engine_menu()
        menu.exec(self.btn_engine.mapToGlobal(QPoint(0, self.btn_engine.height() + 4)))

    # ------------------------------------------------------------ 识别
    def set_image(self, image: Image.Image, anchor: QRect | None = None) -> None:
        """复用已有弹窗：换图重新识别，避免连续识别堆出多个窗口。"""
        self._image = image.copy() if image is not None else None
        if anchor is not None:
            self._anchor = anchor
            self._user_moved = False
        self.start(self._prefer)

    def start(self, prefer: str | None = None) -> None:
        if self._image is None:
            self._fail("没有可识别的图像")
            return
        if self._task is not None and self._task.isRunning():
            return
        self._prefer = prefer if prefer not in (None, "", "auto") else None
        self._lines = []
        self.text.clear()
        self.text.setPlaceholderText("正在识别，请稍候…")
        self.text.setReadOnly(True)
        self.lbl_left.setText("识别中…")
        self.lbl_right.setText("")
        self.btn_copy.setEnabled(False)
        self.btn_rerun.setEnabled(False)
        # 重识别后正文会变，旧译文作废
        self.btn_tr_sel.setEnabled(False)
        self.btn_tr_all.setEnabled(False)
        if self.tr_panel.isVisible():
            self.tr_text.clear()
            self.tr_meta.setText("")
        self.progress.show()
        self._started_at = time.perf_counter()

        self._task = ocr_qt.OcrTask(self._image, self._prefer, self)
        self._task.progress.connect(lambda s: self.lbl_left.setText(s))
        self._task.done.connect(self._on_lines)
        self._task.failed.connect(self._fail)
        self._task.start()

    def _on_lines(self, lines) -> None:
        self._elapsed = (time.perf_counter() - self._started_at) * 1000
        self._lines = list(lines)
        self.text.setReadOnly(False)
        self.progress.hide()
        self.btn_copy.setEnabled(True)
        self.btn_rerun.setEnabled(True)
        self.btn_tr_sel.setEnabled(True)
        self.btn_tr_all.setEnabled(True)
        self._render()
        self._refresh_hint()
        if self._item is not None and self._storage is not None:
            self._persist()
        self._fit_window()

    def _render(self) -> None:
        layout = self._layout_mode or "layout"
        txt = ocr_qt.to_text(self._lines, layout=layout)
        self.text.setPlainText(txt)
        n = len(txt.replace("\n", ""))
        self.lbl_left.setText("共 %d 字 · 用时 %.2fs" % (n, self._elapsed / 1000))
        be = ocr_qt.get_backend(self._prefer) if self._lines else None
        self._backend_label = be.label if be is not None else ""
        self.lbl_right.setText("引擎 " + self._backend_label if self._backend_label else "")
        if self._backend_label:
            self.title_bar.badge.setText(self._backend_label)
            self.title_bar.badge.show()
        else:
            self.title_bar.badge.hide()

    def _fail(self, msg: str) -> None:
        self.progress.hide()
        self.btn_rerun.setEnabled(True)
        self.btn_copy.setEnabled(False)
        self.text.setReadOnly(False)
        self.text.setPlaceholderText("")
        self.text.setPlainText("识别失败：%s" % msg)
        self.lbl_left.setText("识别失败")
        self.lbl_right.setText("")
        self._refresh_hint()

    def _persist(self) -> None:
        """把识别文本写入历史库条目备注（item['ocr_text']），供后续复用。"""
        try:
            txt = self.text.toPlainText().strip()
            if not txt:
                return
            items = self._storage.load_gallery()
            target = None
            path = (self._item or {}).get("path")
            for it in items:
                if it.get("path") == path:
                    target = it
                    break
            if target is None:
                return
            target["ocr_text"] = txt[:4000]
            target["ocr_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._storage.save_gallery(items)
        except Exception:
            pass  # 备注写入失败不影响主体功能

    # ------------------------------------------------------------ 交互
    def copy_all(self) -> None:
        txt = self.text.toPlainText()
        if not txt.strip():
            return
        clipboard.copy_text(txt)
        self.lbl_left.setText("已复制 %d 字到剪贴板" % len(txt.replace("\n", "")))

    # ------------------------------------------------------------ 组件包
    def _refresh_hint(self) -> None:
        # 默认档就是本机系统 OCR：能识别就不打扰；只有这台电脑完全没有可用的
        # 识别引擎时，才提示并提供下载入口。
        if ocr_qt.auto_candidates():
            self.hint.hide()
            return
        self.hint_text.setText(
            "本机没有可用的文字识别：点「下载组件包」获取离线可用的高精度识别（约 100MB），"
            "或到 Windows 设置里安装中文（简体）OCR 语言包。"
        )
        self.hint.show()

    def install_pack(self) -> None:
        """安装高精度组件包：优先本地 zip，其次在线下载。"""
        src, is_url = _choose_pack_source(self)
        if src:
            self._run_install(src, is_url=is_url)

    def _run_install(self, src: str, is_url: bool) -> None:
        self.progress.show()
        self.progress.setRange(0, 1000)
        self.lbl_left.setText("正在下载组件包…" if is_url else "正在安装组件包…")
        self.btn_install.setEnabled(False)
        self._pack_task = ocr_qt.PackInstallTask(src, is_url, self)

        def _on_prog(done: int, total: int) -> None:
            if total > 0:
                self.progress.setValue(int(done * 1000 / total))
            mb = done / 1048576
            self.lbl_left.setText("%s %.1f MB" % ("下载中" if is_url else "安装中", mb))

        self._pack_task.progress.connect(_on_prog)
        self._pack_task.done.connect(self._on_pack_done)
        self._pack_task.failed.connect(self._on_pack_failed)
        self._pack_task.start()

    def _on_pack_done(self, target: str) -> None:
        self.progress.hide()
        self.progress.setRange(0, 0)
        self.btn_install.setEnabled(True)
        ocr_qt.reset_backend_cache()
        self.lbl_left.setText(
            "组件包已安装，本次用高精度引擎识别；要以后默认使用，请在设置里选「高精度组件包」并保存"
        )
        self._refresh_hint()
        self.start("rapidocr")

    def _on_pack_failed(self, msg: str) -> None:
        self.progress.hide()
        self.progress.setRange(0, 0)
        self.btn_install.setEnabled(True)
        self.lbl_left.setText("组件包安装失败：%s" % msg)

    def _remove_pack(self) -> None:
        ocr_qt.remove_pack()
        ocr_qt.reset_backend_cache()
        self._refresh_hint()
        self.lbl_left.setText("已移除高精度组件包")

    # ------------------------------------------------------------ 窗口
    def _fit_window(self) -> None:
        fm = QFontMetrics(self.text.font())
        lines = self.text.toPlainText().split("\n") or [""]
        w_text = max((fm.horizontalAdvance(ln) for ln in lines), default=200)
        w = min(max(WIN_W_MIN, w_text + 70), WIN_W_MAX)
        h_text = fm.lineSpacing() * min(len(lines), 26) + 40
        h = min(max(WIN_H_MIN, h_text + 150), WIN_H_MAX)
        if self.tr_panel.isVisible():
            h = min(WIN_H_MAX, h + TR_MIN_H)
        if self._user_resized:
            # 用户手动拉过窗口：尺寸归他，程序只在压住选区时挪位置
            w, h = self.width(), self.height()
        self._auto_resizing = True
        self.resize(int(w), int(h))
        QTimer.singleShot(0, self._end_auto_resize)
        # 只有窗口真的压住截图选区时才挪开，避免识别完成后窗口无谓跳动
        if self._anchor is not None and not self._user_moved and self.geometry().intersects(self._anchor):
            self._place_near(self._anchor)

    def _place_near(self, anchor: QRect) -> None:
        """把窗口摆在选区旁边（下→上→右→左），尽量不遮挡截图区域。"""
        scr = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        if scr is None:
            return
        area: QRect = scr.availableGeometry()
        w, h = self.width(), self.height()
        gap = 12
        x = anchor.left()
        y = anchor.bottom() + gap               # 选区下方
        if y + h > area.bottom():
            y = anchor.top() - gap - h          # 上方
        if y < area.top():                      # 上下都放不下 → 右侧 / 左侧
            y = max(area.top(), min(anchor.top(), area.bottom() - h))
            x = anchor.right() + gap
            if x + w > area.right():
                x = anchor.left() - gap - w
        x = max(area.left(), min(x, area.right() - w))
        y = max(area.top(), min(y, area.bottom() - h))
        self.move(int(x), int(y))

    def _center_on_cursor_screen(self) -> None:
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if scr is None:
            return
        area: QRect = scr.availableGeometry()
        w, h = self.width(), self.height()
        self.move(area.left() + (area.width() - w) // 2, area.top() + int(area.height() * 0.16))

    # ------------------------------------------------------------ 事件
    def _show_text_menu(self, pos: QPoint) -> None:
        """正文右键菜单（中文，只留复制类操作）。

        默认菜单是 Qt 内置的英文项；这里自绘一份精简菜单，只保留「复制」与
        「复制全部」，不做编辑操作，避免误触改坏识别结果。
        """
        menu = QMenu(self.text)
        menu.setStyleSheet(
            "QMenu{background:%s;border:1px solid %s;border-radius:8px;padding:4px;}"
            "QMenu::item{padding:6px 26px 6px 16px;border-radius:5px;color:%s;}"
            "QMenu::item:selected{background:%s;color:%s;}"
            "QMenu::item:disabled{color:%s;}"
            % (theme.SURFACE, theme.LINE, theme.INK, theme.SURFACE_3, theme.ACCENT, theme.INK_3)
        )
        has_sel = self.text.textCursor().hasSelection()

        a_copy = menu.addAction("复制")
        a_copy.setShortcut(QKeySequence.Copy)
        a_copy.setEnabled(has_sel)
        a_copy.triggered.connect(self.text.copy)

        a_copy_all = menu.addAction("复制全部")
        a_copy_all.setShortcut(QKeySequence("Ctrl+Enter"))
        a_copy_all.setEnabled(bool(self.text.toPlainText().strip()))
        a_copy_all.triggered.connect(self.copy_all)

        menu.exec(self.text.mapToGlobal(pos))

    def resizeEvent(self, e):
        # 程序自动定尺寸时不算用户操作；其余情况记住用户手拉过的尺寸
        if getattr(self, "_auto_resizing", True) is False and self.isVisible():
            self._user_resized = True
        super().resizeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            self.copy_all()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        global _LAST_CLOSE_TS
        _LAST_CLOSE_TS = time.monotonic()
        _trace("关闭识别窗")
        # 关键：不要在这里阻塞等线程收尾。旧实现 wait(1500) 会把 GUI 线程卡住 1.5s，
        # 用户以为「点了没反应」会再点一次，而这次点击在窗口消失后正好落到下方截图
        # 工具条的「识别」按钮上，于是出现「关掉的弹窗又自己弹出来」。
        # 这里只发中断请求并先断开信号，任务线程自行退出，完成后也不会再回写已关闭的窗。
        for t in (self._task, self._pack_task, self._tr_task):
            try:
                if t is not None and t.isRunning():
                    try:
                        t.done.disconnect()
                    except Exception:
                        pass
                    try:
                        t.failed.disconnect()
                    except Exception:
                        pass
                    t.requestInterruption()
            except Exception:
                pass
        if self in _OPEN_WINDOWS:
            _OPEN_WINDOWS.remove(self)
        self.closed.emit()
        super().closeEvent(e)

        # 焦点交还：先把 owner 抓在局部变量里，窗口对象随后可能被回收。
        # 两次尝试是为了兜住个别机型上 activateWindow 首次不生效的情况。
        owner = getattr(self, "_owner", None)
        if owner is not None:
            QTimer.singleShot(0, lambda o=owner: _claim_foreground(o))
            QTimer.singleShot(140, lambda o=owner: _claim_foreground(o))


class _PictureCanvas(QWidget):
    """图片翻译预览画布：原图 + 按 rect 贴回译文，支持滚轮缩放与拖拽平移。"""

    MIN_SCALE, MAX_SCALE = 0.2, 4.0

    def __init__(self, pixmap: QPixmap, items: list[dict], parent=None):
        super().__init__(parent)
        self._pix = pixmap
        self._items = items
        self._scale = 1.0
        self._pos = QPointF(0.0, 0.0)
        self._fitted = False
        self._drag: QPointF | None = None
        self.setMinimumSize(320, 240)
        self.setCursor(Qt.OpenHandCursor)

    # ---------------- 坐标换算
    def _fit(self) -> None:
        if self._fitted or self._pix.isNull():
            return
        if self.width() <= 10 or self.height() <= 10:
            return
        s = min((self.width() - 24.0) / self._pix.width(),
                (self.height() - 24.0) / self._pix.height(), 1.0)
        self._scale = max(self.MIN_SCALE, min(self.MAX_SCALE, s or 1.0))
        self._pos = QPointF((self.width() - self._pix.width() * self._scale) / 2.0,
                            (self.height() - self._pix.height() * self._scale) / 2.0)
        self._fitted = True

    def _canvas_rect(self, item: dict) -> QRectF:
        x, y, w, h = item.get("rect") or (0, 0, 0, 0)
        if w <= 0 or h <= 0:
            pts = item.get("points") or []
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                x, y, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
        return QRectF(self._pos.x() + x * self._scale, self._pos.y() + y * self._scale,
                      max(1.0, w * self._scale), max(1.0, h * self._scale))

    # ---------------- 绘制
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#F2F3F5"))
        if self._pix.isNull():
            p.end()
            return
        self._fit()
        target = QRectF(self._pos.x(), self._pos.y(),
                        self._pix.width() * self._scale, self._pix.height() * self._scale)
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        for item in self._items:
            self._paint_item(p, item)
        p.end()

    def _paint_item(self, p: QPainter, item: dict) -> None:
        rect = self._canvas_rect(item)
        pm = item.get("_paste_pm")
        if pm is not None and not pm.isNull():
            p.drawPixmap(rect, pm, QRectF(pm.rect()))
            return
        pts = item.get("points") or []
        if pts:
            poly = QPolygonF([QPointF(self._pos.x() + x * self._scale,
                                      self._pos.y() + y * self._scale) for x, y in pts])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 244, 214, 215))
            p.drawPolygon(poly)
            p.setBrush(Qt.NoBrush)
        dst = item.get("dst") or ""
        if not dst:
            return
        size = max(6.0, min(rect.height() * 0.62, 48.0))
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(max(6, int(round(size))))
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        p.setClipRect(rect.adjusted(-1, -1, 1, 1))
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap, dst)
        p.setClipping(False)

    # ---------------- 交互
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def wheelEvent(self, e):
        if self._pix.isNull():
            return
        self._fit()
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        new = max(self.MIN_SCALE, min(self.MAX_SCALE, self._scale * factor))
        if abs(new - self._scale) < 1e-4:
            return
        cursor = QPointF(e.position())
        self._pos = cursor - (cursor - self._pos) * (new / self._scale)
        self._scale = new
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = QPointF(e.position())
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            cur = QPointF(e.position())
            self._pos = self._pos + (cur - self._drag)
            self._drag = cur
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        self._fitted = False
        self._fit()
        self.update()


class PictureTransWindow(QWidget):
    """图片翻译结果预览：上半原图 + 贴回译文，下半原文/译文对照。"""

    def __init__(self, image: Image.Image, result: dict, elapsed: float = 0.0,
                 paste: int = 2, parent=None):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("图片翻译预览")
        self.resize(940, 720)
        self.setFont(theme.font(theme.FONT_BODY_SIZE, False))
        self._result = result or {}
        self._paste = paste
        self._items: list[dict] = []
        for raw in self._result.get("contents") or []:
            item = dict(raw or {})
            pb = item.get("paste_img") or b""
            item["_paste_pm"] = self._bytes_pixmap(pb) if pb else None
            self._items.append(item)
        self._pix = imageutil.pil_to_qpixmap(image) if image is not None else QPixmap()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(6)

        self.canvas = _PictureCanvas(self._pix, self._items, self)
        bottom = QWidget(self)
        b_lay = QVBoxLayout(bottom)
        b_lay.setContentsMargins(0, 0, 0, 0)
        b_lay.setSpacing(6)

        head = QWidget(bottom)
        h_lay = QHBoxLayout(head)
        h_lay.setContentsMargins(2, 0, 2, 0)
        h_lay.setSpacing(8)
        title = QLabel("原文 / 译文", head)
        title.setFont(theme.font(theme.FONT_SM_SIZE, False))
        title.setStyleSheet("color:%s;" % theme.INK_2)
        h_lay.addWidget(title, 1)
        self.btn_copy = _btn("复制译文", "primary")
        self.btn_copy.clicked.connect(self._copy_dst)
        h_lay.addWidget(self.btn_copy, 0)
        b_lay.addWidget(head)

        self.text = QPlainTextEdit(bottom)
        self.text.setReadOnly(True)
        self.text.setFont(theme.font(theme.FONT_BODY_SIZE, False))
        self.text.setStyleSheet(
            "QPlainTextEdit{background:%s;border:1px solid %s;border-radius:%dpx;"
            "color:%s;padding:6px 8px;}" % (theme.SURFACE, theme.LINE, theme.R_INPUT, theme.INK)
        )
        self.text.setPlainText(self._summary())
        b_lay.addWidget(self.text, 1)

        split = QSplitter(Qt.Vertical, self)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        split.setStyleSheet(
            "QSplitter::handle{background:%s;} QSplitter::handle:hover{background:%s;}"
            % (theme.LINE, theme.ACCENT_SOFT)
        )
        split.addWidget(self.canvas)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

        mode = {0: "不贴图", 1: "整图", 2: "逐条贴图", 3: "逐条贴图 + 原图"}.get(int(paste), str(paste))
        status = QLabel(
            "条目 %d · 耗时 %.1f 秒 · paste=%s · 滚轮缩放 / 拖拽平移 / 双击复位"
            % (len(self._items), max(0.0, elapsed), mode), self)
        status.setFont(theme.font(theme.FONT_SM_SIZE, False))
        status.setStyleSheet("color:%s;" % theme.INK_3)
        lay.addWidget(status)

    @staticmethod
    def _bytes_pixmap(raw: bytes) -> QPixmap:
        pm = QPixmap()
        try:
            pm.loadFromData(raw)
        except Exception:
            return QPixmap()
        return pm

    def _summary(self) -> str:
        blocks = []
        for i, item in enumerate(self._items, 1):
            blocks.append("【%d】原文：%s\n译文：%s"
                          % (i, item.get("src") or "", item.get("dst") or ""))
        if not blocks:
            blocks.append("原文：%s\n译文：%s"
                          % (self._result.get("src") or "", self._result.get("dst") or ""))
        return "\n\n".join(blocks).strip()

    def _copy_dst(self) -> None:
        text = "\n".join((i.get("dst") or "").strip() for i in self._items
                         if (i.get("dst") or "").strip())
        if not text:
            text = (self._result.get("dst") or "").strip()
        if clipboard.copy_text(text):
            self.btn_copy.setText("已复制")
            QTimer.singleShot(1200, lambda: self.btn_copy.setText("复制译文"))


def open_ocr(image: Image.Image, prefer: str | None = None,
             item: dict | None = None, storage=None,
             anchor: QRect | None = None, owner=None) -> OcrResultWindow | None:
    """对外入口：打开识别结果窗（同图重复点击复用已有窗口）。

    前置守门：本机没有可用的中文识别包时，先弹提示引导安装，用户放弃则不开窗。
    """
    for w in list(_OPEN_WINDOWS):
        try:
            if not w.isVisible():
                continue
        except RuntimeError:          # 底层 C++ 对象已销毁
            try:
                _OPEN_WINDOWS.remove(w)
            except ValueError:
                pass
            continue
        # 同一来源（截图编辑器 / 同一历史条目）复用同一个弹窗
        if (w._item is None) == (item is None) and (item is None or w._item is item):
            _trace("复用识别窗")
            if owner is not None:
                w._owner = owner
            w.set_image(image, anchor=anchor)
            w.show()
            _force_topmost(w)
            w.raise_()
            w.activateWindow()
            return w
    ready, prefer = ensure_ocr_ready(prefer or ocr_qt.engine_prefer())
    if not ready:
        return None
    _trace("新建识别窗")
    w = OcrResultWindow(image, prefer=prefer, item=item, storage=storage, anchor=anchor,
                        owner=owner)
    _OPEN_WINDOWS.append(w)
    w.show()
    _force_topmost(w)
    w.raise_()
    w.activateWindow()
    return w


# ---------------- 图片翻译：译文贴回渲染（供钉图使用，1:1 不缩放） ----------------
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
_FONT_CACHE: dict = {}


def _paste_font(size: int):
    """按字号取一个能显示中文的字体（缓存，取不到则回落默认字体）。"""
    size = max(6, int(size))
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = None
    for cand in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(cand, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _FONT_CACHE[size] = font
    return font


def _text_width(font, text: str, size: int) -> float:
    if font is None:
        return len(text) * size * 0.6
    try:
        return float(font.getlength(text))
    except Exception:
        return len(text) * size * 0.6


def _wrap_to_width(text: str, font, max_w: float, size: int) -> list:
    """按像素宽度折行：中文逐字断行，英文单词尽量不拆。"""
    lines = []
    for para in str(text or "").splitlines() or [""]:
        line = ""
        for ch in para:
            if line and _text_width(font, line + ch, size) > max_w:
                lines.append(line)
                line = ch
            else:
                line += ch
        lines.append(line)
    return lines or [""]


def _paste_rect(item: dict, W: int, H: int):
    """取该条译文的贴回矩形（clamp 到原图内）；无效返回 None。"""
    x, y, w, h = item.get("rect") or (0, 0, 0, 0)
    try:
        x, y, w, h = int(x), int(y), int(w), int(h)
    except Exception:
        return None
    if w <= 0 or h <= 0:
        pts = item.get("points") or []
        try:
            if pts:
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - min(xs)), int(max(ys) - min(ys))
        except Exception:
            return None
    if w <= 0 or h <= 0:
        return None
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(W, x + w), min(H, y + h)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None
    return x1, y1, x2 - x1, y2 - y1


def _paint_text_on(draw, rect, text: str) -> None:
    """在该矩形内绘制译文：浅底 + 深字，字号自适应（逐级缩小直到放得下）。"""
    x, y, w, h = rect
    size = max(8, int(round(h * 0.62)))
    font = _paste_font(size)
    lines = _wrap_to_width(text, font, max(1.0, w - 2.0), size)
    lh = int(round(size * 1.25))
    while size > 8 and (len(lines) * lh > h + 1
                        or any(_text_width(font, ln, size) > w for ln in lines)):
        size -= 1
        font = _paste_font(size)
        lines = _wrap_to_width(text, font, max(1.0, w - 2.0), size)
        lh = int(round(size * 1.25))
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(255, 255, 255, 226))
    ty = y + max(0, (h - len(lines) * lh) // 2)
    for ln in lines:
        tw = _text_width(font, ln, size)
        tx = x + max(0, int((w - tw) / 2.0))
        try:
            draw.text((tx, ty), ln, font=font, fill=(23, 24, 28, 255))
        except Exception:
            pass
        ty += lh


def render_picture_paste(image: Image.Image, result: dict) -> Image.Image:
    """把图片翻译结果按 rect 贴回原截图，返回与原图**同尺寸**的 PIL 图像。

    贴回规则与预览窗 ``_PictureCanvas`` 保持一致：
      * 接口给了 ``paste_img`` —— 尺寸约等于原图时视为整图贴回直接采用；
        否则视为该条 rect 的局部贴图，缩放后贴到对应位置；
      * 没有 ``paste_img`` —— 本地把 ``dst`` 译文画进 rect（浅底深字，字号自适应）；
      * 全程不缩放原图，输出即 1:1 译文图。
    """
    if image is None:
        raise ValueError("原图为空")
    base = image.convert("RGBA")
    W, H = base.size
    items = (result or {}).get("contents") or []
    draw = None
    for raw in items:
        item = dict(raw or {})
        dst = str(item.get("dst") or "").strip()
        rect = _paste_rect(item, W, H)
        pb = item.get("paste_img") or b""
        pm = None
        if pb:
            try:
                pm = Image.open(io.BytesIO(pb)).convert("RGBA")
            except Exception:
                pm = None
        if pm is not None:
            if abs(pm.width - W) <= 4 and abs(pm.height - H) <= 4:
                if pm.size != (W, H):
                    pm = pm.resize((W, H), Image.LANCZOS)
                base = pm                     # 整图贴回：接口已排好版，直接采用
                draw = None
                continue
            if rect is None:
                continue
            x, y, w, h = rect
            try:
                base.alpha_composite(pm.resize((w, h), Image.LANCZOS), (x, y))
            except Exception:
                pass
            continue
        if rect is None or not dst:
            continue
        if draw is None:
            draw = ImageDraw.Draw(base)
        _paint_text_on(draw, rect, dst)
    return base.convert("RGB")
