# -*- coding: utf-8 -*-
"""P5：Qt 版贴图（Pin）窗口（对齐 tk 版 ``sticker.py``）。

设计对照
--------
tk 版用「``-transparentcolor`` 镂空 + 独立 ``ShadowWindow``（Win32 layered）」
实现圆角窗 + 真半透明投影；Qt 版改为**单窗自绘**：

  * 窗口属性：``FramelessWindowHint | WindowStaysOnTopHint | Tool``
    + ``WA_TranslucentBackground``，逐像素 alpha，无系统边框、不占任务栏；
  * 投影：双层圆角阴影自绘——远层 ``σ12 / dy6 / alpha58`` 大范围淡出，
    近层 ``σ3 / dy1 / alpha92`` 紧贴底边，两层在 PIL 里各自
    ``GaussianBlur`` 后依次 ``alpha_composite`` 到同一张 QPixmap
    （**不再需要独立阴影窗**，也避开显卡驱动下
    ``QGraphicsDropShadowEffect`` 出现硬边 / 黑底的问题）。阴影块与图片
    同圆角、只位移不外扩，因此阴影沿圆角收边；留白 ``PAD=40`` 足以容纳
    远层模糊，不会被窗口边界裁成直线。缩略等小图按短边比例收窄阴影。
  * 圆角 + accent 外框：圆角 mask 抠图片 + 2px ``theme.ACCENT`` 描边，
    半径与 tk 版 ``_build_display()`` 一致（radius=12）；留白由 26 增至 40
    以容纳更柔的投影（视觉差异见 P5 文档）。

行为对齐（与 tk 版逐条对应）
--------------------------
============================  ==========================================
tk 版                          Qt 版
============================  ==========================================
左键拖动窗口                    ``mousePressEvent`` / ``mouseMoveEvent``
双击（dt<0.4s、位移≤10px）      自定义双击检测（不用 Qt 原生 doubleClick，
  → 切换 52px 正方形缩略           以保持 400ms + 10px 容差语义一致）
Ctrl+双击 → 关闭贴图            同上，``Qt.ControlModifier`` 命中即关闭
滚轮缩放 1.12 倍、以光标为锚     ``wheelEvent``，缩略模式禁用
缩放上限原图 200%、短边≥40px    同参数
右键菜单：关闭贴图（存档保留）   ``contextMenuEvent`` + ``QMenu``
  / 另存为... / 复制到剪贴板
右上角 ✕ 关闭（缩略模式隐藏）    ``paintEvent`` 自绘 + 命中矩形判定
Esc 关闭                        ``keyPressEvent``
初始超过 900×600 先等比缩小      同参数（LANCZOS）
窗口钳制在虚拟桌面内             ``geometry.clamp_rect`` + 逻辑坐标换算
============================  ==========================================

与 tk 版的微差异（已在 P5 文档登记）
  * 关闭按钮 ✕ 增加 1px 暗描边，深色/浅色图上都可辨认；
  * 鼠标悬停/拖动切换 ``OpenHand`` / ``ClosedHand`` 光标（tk 版为默认箭头）；
  * 「另存为」成功后用 ``QToolTip`` 轻提示（tk 版为阻塞式 messagebox）。

坐标说明
--------
截图是**物理像素**，Qt 窗口几何是**逻辑像素**：显示用 QPixmap 设置
``devicePixelRatio`` 做物理 1:1 渲染，窗口尺寸 / 位置统一按 dpr 折算为逻辑值，
对外暴露的 ``x, y``（来自编辑器回调）也按逻辑坐标接收。
"""
from __future__ import annotations

import time

from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import QFileDialog, QMenu, QToolTip, QWidget

from . import clipboard as qtclipboard
from . import geometry, theme
from .imageutil import pil_to_qpixmap

# ---------------- 视觉常量 ----------------
# 留白必须容得下远层模糊（≈2.5σ + 位移），否则投影会在窗口边缘被裁成一条直线
PAD = 52                    # 圆角窗四周透明留白（阴影活动区）
RADIUS = 12                 # 圆角半径
SHADOW_RGB = (10, 12, 18)   # 投影基色：近黑偏冷，避免旧版灰白色块感
# (高斯模糊 σ, 横向位移, 纵向位移, 峰值 alpha)，按顺序叠加：先远层再近层。
# 远层负责"淡出"，近层负责"贴边"，两层叠加形成自然的柔和过渡，
# 阴影块只位移、不外扩、圆角与图片一致，因此阴影沿圆角收边而不会变成方角。
SHADOW_LAYERS = (
    (14.0, 0.0, 8.0, 78),   # 远层：大模糊、中低 alpha，向下扩散淡出（尾部≈3σ=42px）
    (4.0, 0.0, 2.0, 132),   # 近层：小模糊、高 alpha，压住底边与左右轮廓形成接触影
)
SHADOW_MIN_SCALE = 0.45     # 小图（缩略 52px）阴影按比例收窄的下限系数
SHADOW_REF_SHORT = 200.0    # 短边达到该像素时阴影按 1.0 满强度绘制
THUMB_SIZE = 52             # 双击缩略边长
MAX_W, MAX_H = 900, 600     # 初始显示上限
MIN_SHORT = 40              # 缩放最小短边
MAX_SCALE = 2.0             # 缩放上限（相对原图）
ZOOM_STEP = 1.12            # 单档滚轮缩放比
DBL_TIME = 0.4              # 自定义双击时间窗（秒）
DBL_TOL = 10                # 自定义双击位移容差（px）
CLOSE_ICON_GAP = 8          # ✕ 距图片右边缘 / 上边缘的距离
CLOSE_HOVER_ANGLE = 90.0    # 悬停时 ✕ 顺时针旋转的角度（度），移开回弹到 0
CLOSE_HOVER_DURATION = 180  # 悬停旋转动画时长（毫秒）
CLOSE_HOVER_SCALE = 1.18    # 悬停时 ✕ 圆形底与笔画微放大倍数
CLOSE_HIT = 18              # ✕ 命中半径（逻辑像素），覆盖绘制区域且便于点中
CASCADE_STEP = 24           # 同位置连续贴图时的级联错开步长

# ---------------- 原文 / 译文切换条（图片翻译钉图专用） ----------------
# 图片翻译钉图时，图片下方多出一条按钮条，点击在「原文」与「译文」之间切换。
# 仅当构造时传入 alt_image 才启用；尺寸为**逻辑像素**，绘制时按 dpr 还原。
SWITCH_BAR_H = 34           # 按钮条总高（含与图片之间的间距）
SWITCH_BAR_GAP = 8          # 按钮条与图片底边的间距
SWITCH_BTN_W = 88           # 按钮宽度
SWITCH_BTN_H = 26           # 按钮高度


class StickerWindow(QWidget):
    """一张贴图 = 一个无边框置顶窗；支持拖动 / 缩放 / 缩略 / 右键菜单。"""

    closed = Signal(object)

    # 活跃实例（防止 Python 侧被回收；也便于统一关闭）
    _open: list["StickerWindow"] = []

    # -------- 生命周期 --------
    def __init__(self, image: Image.Image, x: int | None = None,
                 y: int | None = None, title: str = "截图",
                 parent: QWidget | None = None,
                 alt_image: Image.Image | None = None,
                 alt_title: str = "",
                 align_tl: bool = False,
                 init_size: tuple[int, int] | None = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setWindowTitle(title)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.setMouseTracking(True)

        self._title = title
        self._full = image.convert("RGBA")
        self._orig = (self._full.width, self._full.height)
        self._thumb_mode = False
        self._drag_off: QPoint | None = None
        self._press_t = 0.0
        self._press_xy = (0, 0)
        self._pix = None
        self._close_rect = QRect()

        # 右上角 ✕ 悬停反馈：手型光标 + 加深/加粗/微放大 + 平滑旋转一次。
        # 角度由 0ms/180ms 的 QVariantAnimation 驱动，移开鼠标回弹到 0°。
        self._close_hover = False
        self._close_angle = 0.0
        self._close_anim = QVariantAnimation(self)
        self._close_anim.setDuration(CLOSE_HOVER_DURATION)
        self._close_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._close_anim.valueChanged.connect(self._on_close_anim)

        # 原文 / 译文切换：alt_image 非空时启用（图片翻译钉图），
        # 主图 = 译文贴回图，alt_image = 原始截图。两图同尺寸，切换不改变窗口大小。
        self._alt_full = None
        self._alt_title = str(alt_title or "")
        self._toggle_enabled = alt_image is not None
        self._show_alt = False
        self._switch_rect = QRect()
        if self._toggle_enabled:
            alt = alt_image.convert("RGBA")
            if alt.size != self._full.size:      # 兜底：尺寸不一致时对齐主图
                alt = alt.resize(self._full.size, Image.LANCZOS)
            self._alt_full = alt

        # 初始显示尺寸：
        #   普通贴图——超过 900×600 先等比缩小（原图仍留作放大上限）；
        #   图片翻译钉图——**不做任何缩放**，与原截图保持物理像素 1:1；
        #   大图页钉图（init_size）——按大图当时看到的显示尺寸呈现，
        #   让"钉住"瞬间在视觉上完全等价于大图里的那一帧（原图仍留作放大上限）。
        self._align_tl = bool(align_tl)
        if init_size and init_size[0] > 0 and init_size[1] > 0 \
                and tuple(init_size) != self._orig:
            nw = min(int(init_size[0]), self._orig[0] * 4)
            nh = min(int(init_size[1]), self._orig[1] * 4)
            img = self._full.resize((nw, nh), Image.LANCZOS)
        elif self._toggle_enabled:
            img = self._full.copy()
        else:
            img = self._full.copy()
            img.thumbnail((MAX_W, MAX_H), Image.LANCZOS)
        self.img = img

        # 目标位置（逻辑坐标）→ 该点所在屏的 dpr
        pos = QPoint(int(x), int(y)) if (x is not None and y is not None) \
            else QCursor.pos()
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        self._dpr = float(scr.devicePixelRatio() or 1.0) if scr is not None else 1.0
        if self._toggle_enabled or self._align_tl:
            # x / y 传的是"图像像素左上角"，而窗口四周有 PAD 透明留白
            # （阴影活动区），需按 dpr 折算后回扣，使图像像素左上角精确落在原位。
            pad_l = int(round(PAD / self._dpr))
            pos = QPoint(pos.x() - pad_l, pos.y() - pad_l)

        self._rebuild_pixmap()
        self.setFixedSize(self._logical_size())
        if self._align_tl:
            # 对齐模式下**不做边界回夹**：位置优先，宁可让阴影留白溢出屏幕，
            # 也不能把图像本体挪离用户指定的落点。
            self.move(pos)
        else:
            self._move_clamped(pos)
        StickerWindow._open.append(self)

    # -------- 尺寸 / 坐标换算 --------
    def _bar_phys(self) -> int:
        """底部切换按钮条占用的物理像素高度（未启用时为 0）。"""
        if not self._toggle_enabled or self._thumb_mode:
            return 0
        return int(round(SWITCH_BAR_H * self._dpr))

    def _win_phys(self) -> tuple[int, int]:
        w, h = self.img.size
        return w + PAD * 2, h + PAD * 2 + self._bar_phys()

    def _logical_size(self) -> QSize:
        pw, ph = self._win_phys()
        return QSize(max(1, int(round(pw / self._dpr))),
                     max(1, int(round(ph / self._dpr))))

    def _move_clamped(self, pos: QPoint) -> None:
        r = QRect(pos, self.size())
        try:
            r = geometry.clamp_rect(r, geometry.virtual_rect_logical())
        except Exception:
            pass
        self.move(r.topLeft())

    # -------- 画面合成（圆角 + 真半透明投影） --------
    def _build_display(self) -> Image.Image:
        w, h = self.img.size
        bar = self._bar_phys()          # 底部切换条：仅占位，图形由 paintEvent 用 Qt 绘制
        W, H = w + PAD * 2, h + PAD * 2 + bar
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        # 1) 投影层：远层（大模糊淡出）+ 近层（小模糊贴边）依次叠加。
        #    阴影块与图片同圆角、只位移不外扩，因此沿圆角收边，
        #    不再出现旧版 SHADOW_GROW 造成的"平顶灰圈 / 方角"。
        k = min(1.0, min(w, h) / SHADOW_REF_SHORT)
        k = max(SHADOW_MIN_SCALE, k)
        for sigma, dx, dy, alpha in SHADOW_LAYERS:
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sx, sy = PAD + dx * k, PAD + dy * k
            ImageDraw.Draw(layer).rounded_rectangle(
                [sx, sy, sx + w - 1, sy + h - 1],
                radius=RADIUS, fill=SHADOW_RGB + (int(round(alpha)),))
            layer = layer.filter(ImageFilter.GaussianBlur(sigma * k))
            canvas = Image.alpha_composite(canvas, layer)

        # 2) 图片层：圆角 mask 抠边
        img = self.img.convert("RGBA")
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, max(0, w - 1), max(0, h - 1)], radius=RADIUS, fill=255)
        img.putalpha(mask)
        canvas.alpha_composite(img, (PAD, PAD))

        # 3) accent 外框 2px
        if w > 2 and h > 2:
            ImageDraw.Draw(canvas).rounded_rectangle(
                [PAD, PAD, PAD + w - 1, PAD + h - 1],
                radius=RADIUS, outline=theme.ACCENT, width=2)
        return canvas

    def _rebuild_pixmap(self) -> None:
        self._pix = pil_to_qpixmap(self._build_display(), self._dpr)

    # -------- 绘制 --------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if self._pix is not None:
            p.drawPixmap(0, 0, self._pix)
        if not self._thumb_mode:
            self._close_rect = self._paint_close(p)
        else:
            self._close_rect = QRect()
        if self._toggle_enabled and not self._thumb_mode:
            self._switch_rect = self._paint_switch(p)
        else:
            self._switch_rect = QRect()

    def _paint_close(self, p: QPainter) -> QRect:
        """右上角 ✕（仅非缩略模式），返回其命中矩形。

        常态：淡暗圆形底 + 白色 ✕（自带暗描边），在任何底图上都清晰；
        悬停：圆形底加深、笔画加粗、整体微放大，并沿动画角度平滑旋转一次。
        """
        w_l = self.img.width / self._dpr
        pad_l = PAD / self._dpr
        right = int(round(pad_l + w_l - CLOSE_ICON_GAP))
        top = int(round(pad_l + CLOSE_ICON_GAP))
        cx = right - 11.0
        cy = top + 6.0

        t = 0.0
        if CLOSE_HOVER_ANGLE:
            t = max(0.0, min(1.0, float(self._close_angle) / CLOSE_HOVER_ANGLE))
        scale = 1.0 + (CLOSE_HOVER_SCALE - 1.0) * t

        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.translate(cx, cy)
        p.rotate(float(self._close_angle))
        p.scale(scale, scale)

        # 圆形底：常态半透明暗底（不会被浅色截图吃掉），悬停进一步加深
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(round(70 + 90 * t))))
        p.drawEllipse(QRect(-11, -11, 22, 22))

        # ✕：先描一圈暗边再压白线，保证深/浅底都可辨认；悬停时加粗
        a = 4.6
        pen_w = 1.9 + 0.9 * t
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 150), pen_w + 1.3,
                      Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-a, -a), QPointF(a, a))
        p.drawLine(QPointF(-a, a), QPointF(a, -a))
        p.setPen(QPen(QColor(255, 255, 255), pen_w, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-a, -a), QPointF(a, a))
        p.drawLine(QPointF(-a, a), QPointF(a, -a))
        p.restore()

        # 命中区按绘制半径外扩，保证覆盖绘制区域且容易点中
        hit = max(CLOSE_HIT, int(round(11 * scale)) + 4)
        return QRect(int(round(cx)) - hit, int(round(cy)) - hit, hit * 2, hit * 2)

    # -------- 关闭按钮悬停反馈 --------
    def _on_close_anim(self, value) -> None:
        try:
            self._close_angle = float(value)
        except (TypeError, ValueError):
            self._close_angle = 0.0
        self.update()

    def _sync_cursor(self) -> None:
        """按当前状态统一光标。

        缩略图只有 72px，手掌 / 抓手光标会把画面盖掉大半，所以缩略态一律用
        普通箭头；大图态保留手掌（可拖动）与 ✕ 悬停手型。
        """
        if getattr(self, "_thumb_mode", False):
            self.setCursor(Qt.ArrowCursor)
        elif getattr(self, "_close_hover", False):
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.OpenHandCursor)

    def _animate_close(self, hover: bool) -> None:
        """切换 ✕ 悬停态：手型光标 + 旋转/加深动画（移开回弹）。"""
        hover = bool(hover)
        self._close_hover = hover
        self._sync_cursor()
        self._close_anim.stop()
        self._close_anim.setStartValue(float(self._close_angle))
        self._close_anim.setEndValue(CLOSE_HOVER_ANGLE if hover else 0.0)
        self._close_anim.start()

    def _paint_switch(self, p: QPainter) -> QRect:
        """图片下方居中的「原文 / 译文」切换按钮，返回其命中矩形。"""
        dpr = self._dpr
        pad_l = PAD / dpr
        w_l = self.img.width / dpr
        h_l = self.img.height / dpr
        bw, bh = float(SWITCH_BTN_W), float(SWITCH_BTN_H)
        x = pad_l + (w_l - bw) / 2.0
        y = pad_l + h_l + SWITCH_BAR_GAP
        r = QRect(int(round(x)), int(round(y)), int(round(bw)), int(round(bh)))

        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 155))          # 深色胶囊：任何底图上都清晰
        p.drawRoundedRect(r, bh / 2.0, bh / 2.0)
        f = QFont(theme.FONT_FAMILY)
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(QColor(255, 255, 255)))
        # 按钮文案 = 「点击后要切过去的那一版」
        p.drawText(r, Qt.AlignHCenter | Qt.AlignVCenter,
                   "译文" if self._show_alt else "原文")
        return r.adjusted(-6, -4, 6, 4)           # 命中区略放大，便于点中

    # -------- 鼠标：拖动 / 双击 / 关闭按钮 --------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            if not self._thumb_mode and self._close_rect.contains(pos):
                self.close_sticker()
                return
            if (self._toggle_enabled and not self._thumb_mode
                    and self._switch_rect.contains(pos)):
                self._press_t = 0.0
                self.toggle_source()
                return
            gpos = event.globalPosition().toPoint()
            now = time.monotonic()
            dt = now - self._press_t
            dx = abs(gpos.x() - self._press_xy[0])
            dy = abs(gpos.y() - self._press_xy[1])
            if dt < DBL_TIME and dx <= DBL_TOL and dy <= DBL_TOL:
                self._press_t = 0.0
                if event.modifiers() & Qt.ControlModifier:
                    self.close_sticker()
                else:
                    self.toggle_thumb(gpos)
                return
            self._press_t = now
            self._press_xy = (gpos.x(), gpos.y())
            self._drag_off = gpos - self.frameGeometry().topLeft()
            if self._close_hover:
                self._animate_close(False)
            if not self._thumb_mode:
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_off is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_off)
        else:
            # 悬停到右上角 ✕：手型光标 + 旋转/加深反馈，移开即回弹
            over = (not self._thumb_mode
                    and self._close_rect.contains(event.position().toPoint()))
            if over != self._close_hover:
                self._animate_close(over)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._close_hover:
            self._animate_close(False)
        self._sync_cursor()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_off = None
        self._sync_cursor()
        super().mouseReleaseEvent(event)

    # -------- 滚轮缩放（以光标为锚点） --------
    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._thumb_mode:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        k = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self.zoom(k, event.position())
        event.accept()

    def zoom(self, k: float, anchor) -> None:
        cw, ch = self.img.size
        if cw <= 0 or ch <= 0:
            return
        nw, nh = round(cw * k), round(ch * k)
        if min(nw, nh) < MIN_SHORT:                     # 最小短边 40px
            k2 = MIN_SHORT / min(cw, ch)
            nw, nh = round(cw * k2), round(ch * k2)
        nw = min(nw, round(self._orig[0] * MAX_SCALE))  # 上限原图 200%
        nh = min(nh, round(self._orig[1] * MAX_SCALE))
        nw, nh = max(1, nw), max(1, nh)
        if (nw, nh) == (cw, ch):
            return

        pad_l = PAD / self._dpr
        ax = float(anchor.x()) - pad_l                    # 光标相对图片左上角
        ay = float(anchor.y()) - pad_l
        old_w = cw / self._dpr
        old_h = ch / self._dpr
        new_w = nw / self._dpr
        new_h = nh / self._dpr
        nx = self.x() + ax - ax * new_w / old_w
        ny = self.y() + ay - ay * new_h / old_h

        self._apply_image(nw, nh, square=False)
        self.move(int(round(nx)), int(round(ny)))
        self._move_clamped(self.pos())

    def _apply_image(self, pw: int, ph: int, square: bool = False) -> None:
        """按物理像素重建显示图（square=True 拉伸成正方形，用于缩略）。"""
        img = self._full.copy()
        if square:
            img = img.resize((pw, ph), Image.LANCZOS)
        elif pw > self._full.width or ph > self._full.height:
            img = img.resize((pw, ph), Image.LANCZOS)
        else:
            img.thumbnail((pw, ph), Image.LANCZOS)
        self.img = img
        self._rebuild_pixmap()
        self.setFixedSize(self._logical_size())

    # -------- 双击：缩略 <-> 原尺寸 --------
    def toggle_thumb(self, global_pos: QPoint | None = None) -> None:
        gpos = global_pos if global_pos is not None else QCursor.pos()
        if self._close_hover:          # 缩略模式无 ✕，先复位悬停态与光标
            self._animate_close(False)
        if self._thumb_mode:
            self._thumb_mode = False
            w, h = self._orig
            self._apply_image(w, h, square=False)
        else:
            self._thumb_mode = True
            self._apply_image(THUMB_SIZE, THUMB_SIZE, square=True)
        size = self.size()
        self._move_clamped(QPoint(gpos.x() - size.width() // 2,
                                  gpos.y() - size.height() // 2))
        self._sync_cursor()
        self.raise_()
        self.setFocus()

    # -------- 底部按钮：原文 / 译文切换 --------
    def toggle_source(self) -> None:
        """在「译文贴回图」（主图）与「原始截图」（alt）之间切换，始终 1:1 显示。

        两图尺寸一致（贴回渲染保证），因此直接**交换** ``_full`` / ``_alt_full``：
        切换后 ``_full`` 恒指当前显示的那一版，之后滚轮缩放、双击缩略再恢复
        都从当前版本重建，不会莫名回退到另一版。
        """
        if self._alt_full is None or self._thumb_mode:
            return
        self._show_alt = not self._show_alt
        self._full, self._alt_full = self._alt_full, self._full
        self.img = self._full.copy()
        self._orig = (self.img.width, self.img.height)
        self._rebuild_pixmap()
        self.setFixedSize(self._logical_size())
        self.update()

    def showing_alt(self) -> bool:
        """当前是否显示原文（原始截图）。"""
        return self._show_alt

    # -------- 右键菜单 --------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        # 首项灰字提示：让不熟悉的人也能一眼看到可用的鼠标手势
        hint = menu.addAction("双击=还原原尺寸   拖动=移动" if self._thumb_mode
                              else "拖动=移动   滚轮=缩放   双击=缩略")
        hint.setEnabled(False)
        menu.addSeparator()
        act_close = menu.addAction("关闭贴图（Esc，存档保留）")
        act_save = menu.addAction("另存为...（Ctrl+S）")
        act_switch = None
        if self._toggle_enabled:
            label = "切换到译文（Tab）" if self._show_alt else "切换到原文（Tab）"
            act_switch = menu.addAction(label)
        menu.addSeparator()
        act_copy = menu.addAction("复制到剪贴板（Ctrl+C）")
        act_ocr = menu.addAction("识别文字")
        chosen = menu.exec(event.globalPos())
        if chosen is act_close:
            self.close_sticker()
        elif chosen is act_save:
            self.save_as()
        elif act_switch is not None and chosen is act_switch:
            self.toggle_source()
        elif chosen is act_copy:
            self.copy()
        elif chosen is act_ocr:
            self.ocr_text()

    def ocr_text(self) -> None:
        """P9：识别贴图内的文字（结果独立成窗，不改变贴图本身）。"""
        from . import ocr_result_qt
        try:
            ocr_result_qt.open_ocr(self.img)
        except Exception as exc:
            QToolTip.showText(QCursor.pos(), "识别失败：%s" % exc, self)

    def copy(self) -> bool:
        ok = qtclipboard.copy_image_to_clipboard(self.img)
        if ok:
            QToolTip.showText(QCursor.pos(), "已复制到剪贴板", self)
        else:
            QToolTip.showText(QCursor.pos(), "复制失败", self)
        return ok

    def save_as(self) -> str | None:
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", "截图.png", "PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg)")
        if not path:
            return None
        try:
            if path.lower().endswith((".jpg", ".jpeg")):
                self.img.convert("RGB").save(path, quality=95)
            else:
                if not path.lower().endswith(".png"):
                    path += ".png"
                self.img.save(path)
        except Exception as exc:
            QToolTip.showText(QCursor.pos(), f"保存失败：{exc}", self)
            return None
        QToolTip.showText(QCursor.pos(), "已保存", self)
        return path

    # -------- 键盘 / 关闭 --------
    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802
        """让 Tab 留给"原文 / 译文"切换，不被焦点链吞掉。"""
        return False

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mod = event.modifiers()
        if key == Qt.Key_Escape:
            self.close_sticker()
            return
        # 键盘通路：复制 / 另存 / 原文译文切换 / 缩放，不必只依赖鼠标
        if (mod & Qt.ControlModifier) and key == Qt.Key_C:
            self.copy()
            return
        if (mod & Qt.ControlModifier) and key == Qt.Key_S:
            self.save_as()
            return
        if key in (Qt.Key_Tab, Qt.Key_Space) and self._toggle_enabled \
                and not self._thumb_mode:
            self.toggle_source()
            return
        if key in (Qt.Key_Plus, Qt.Key_Equal) and not self._thumb_mode:
            self.zoom(ZOOM_STEP, self.rect().center())
            return
        if key in (Qt.Key_Minus, Qt.Key_Underscore) and not self._thumb_mode:
            self.zoom(1.0 / ZOOM_STEP, self.rect().center())
            return
        super().keyPressEvent(event)

    def close_sticker(self) -> None:
        self.closed.emit(self)
        self.clearFocus()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self in StickerWindow._open:
                StickerWindow._open.remove(self)
        except ValueError:
            pass
        super().closeEvent(event)
        self.deleteLater()

    # -------- 查询 --------
    def image(self) -> Image.Image:
        """当前显示图（物理像素，可能是缩放 / 缩略后的）。"""
        return self.img

    def is_thumb(self) -> bool:
        return self._thumb_mode


# ---------------- 模块级便捷入口 ----------------
def open_sticker(image: Image.Image, x: int | None = None, y: int | None = None,
                 title: str = "截图",
                 alt_image: Image.Image | None = None,
                 alt_title: str = "",
                 align_tl: bool = False,
                 init_size: tuple[int, int] | None = None) -> StickerWindow:
    """贴图：在 (x, y)（逻辑坐标）处钉一张图，返回窗口实例。

    ``align_tl=True`` 时 (x, y) 表示**图像像素左上角**（自动回扣阴影留白，
    且不做边界回夹），用于"原地钉住"场景。

    若该位置已有贴图（同一选区连续点两次「贴图」），自动向右下错开 24px 级联摆放，
    避免新贴图被旧贴图完全盖住、看起来像"没反应"；``align_tl`` 模式不错开，
    以保证落点严格不变。
    """
    if x is not None and y is not None and not align_tl:
        step = 0
        for other in StickerWindow._open:
            try:
                if abs(other.x() - x) <= 8 and abs(other.y() - y) <= 8:
                    step += 1
            except RuntimeError:          # 底层 C++ 对象已销毁
                continue
        if step:
            vrt = geometry.virtual_rect_logical()
            x = min(x + CASCADE_STEP * step, max(vrt.left(), vrt.right() - 120))
            y = min(y + CASCADE_STEP * step, max(vrt.top(), vrt.bottom() - 120))
    win = StickerWindow(image, x, y, title, None, alt_image=alt_image,
                        alt_title=alt_title, align_tl=align_tl,
                        init_size=init_size)
    win.show()
    win.raise_()
    win.activateWindow()
    win.setFocus()
    return win


def close_all() -> int:
    """关闭所有贴图，返回关闭数量。"""
    wins = list(StickerWindow._open)
    for w in wins:
        try:
            w.close_sticker()
        except Exception:
            pass
    return len(wins)


def open_count() -> int:
    return len(StickerWindow._open)
