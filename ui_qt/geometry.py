# -*- coding: utf-8 -*-
"""DPI / 多屏坐标层 —— 内部统一使用「物理像素」。

背景（迁移方案 §3.3-1）
------------------------
旧版进程未声明 DPI 感知，Windows 对窗口做位图拉伸，tk 坐标与 ``ImageGrab``
返回的物理像素天然一致。Qt6 默认 Per-Monitor V2 感知，``QScreen.geometry()``
返回**逻辑像素**，而抓屏仍是物理像素 —— 如果直接照搬，在 125% / 150% 缩放下
选区、贴图、大图会出现错位或尺寸不符。

因此本模块约定：

* ``virtual_rect()`` / ``screen_at_physical()`` / ``PIL`` 抓屏 bbox —— 全部**物理像素**；
* ``QWidget.setGeometry()`` / ``move()`` —— 需要**逻辑像素**，用 ``*_to_logical*`` 转换；
* 窗口内的绘制坐标 = 物理像素 - 屏幕物理原点（与旧版 ``_to_img`` 语义一致）。

典型用法::

    from ui_qt import geometry as G

    phys = G.virtual_rect()                    # 物理像素虚拟屏并集
    img, ox, oy = ImageGrab.grab(G.grab_bbox(), all_screens=True)
    win.setGeometry(G.logical_geometry_for_physical_rect(phys))

混排 DPI 说明：单个 Qt 窗口无法在多个不同 DPR 的屏幕上正确铺满，因此全屏覆盖层
必须**每屏一个窗口**，用 :func:`overlay_geometries_logical` 取得每屏逻辑几何。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QCursor, QGuiApplication, QScreen

# ---------------- Win32 常量 ----------------
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITORINFOF_PRIMARY = 0x00000001
MONITOR_DEFAULTTONEAREST = 0x00000002

user32 = ctypes.windll.user32

if not hasattr(user32, "GetDpiForWindow"):  # 老系统兜底（Win8.1 之前）
    user32.GetDpiForWindow = None  # type: ignore[assignment]

_HMONITOR = getattr(wintypes, "HMONITOR", ctypes.c_void_p)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


# ---------------- 屏幕信息 ----------------
@dataclass(frozen=True)
class ScreenInfo:
    """一块物理显示器的坐标信息。"""

    name: str                    # 设备名，如 "\\\\.\\DISPLAY1"
    index: int                   # Qt 屏幕序号（QGuiApplication.screens() 顺序）
    physical: QRect              # 物理像素矩形（Win32 坐标，可能为负）
    logical: QRect               # Qt 逻辑像素矩形（QScreen.geometry()）
    dpr: float                   # devicePixelRatio
    primary: bool = False
    work_logical: QRect | None = None   # Qt 可用区（去掉任务栏）

    # --- 单屏内换算 ---
    @property
    def scale_x(self) -> float:
        return (self.logical.width() / self.physical.width()) if self.physical.width() else 1.0

    @property
    def scale_y(self) -> float:
        return (self.logical.height() / self.physical.height()) if self.physical.height() else 1.0

    def to_logical(self, point: QPoint) -> QPoint:
        return QPoint(
            self.logical.left() + int(round((point.x() - self.physical.left()) * self.scale_x)),
            self.logical.top() + int(round((point.y() - self.physical.top()) * self.scale_y)),
        )

    def to_physical(self, point: QPoint) -> QPoint:
        return QPoint(
            self.physical.left() + int(round((point.x() - self.logical.left()) / self.scale_x)),
            self.physical.top() + int(round((point.y() - self.logical.top()) / self.scale_y)),
        )

    def rect_to_logical(self, rect: QRect) -> QRect:
        tl = self.to_logical(rect.topLeft())
        return QRect(tl, QSize(int(round(rect.width() * self.scale_x)),
                               int(round(rect.height() * self.scale_y))))

    def rect_to_physical(self, rect: QRect) -> QRect:
        tl = self.to_physical(rect.topLeft())
        return QRect(tl, QSize(int(round(rect.width() / self.scale_x)),
                               int(round(rect.height() / self.scale_y))))

    def contains_physical(self, point: QPoint) -> bool:
        return self.physical.contains(point)

    def __str__(self) -> str:  # pragma: no cover - 调试输出
        return (f"{self.name} #{self.index} dpr={self.dpr:g} "
                f"phys={self.physical.left()},{self.physical.top()} "
                f"{self.physical.width()}x{self.physical.height()} "
                f"logical={self.logical.width()}x{self.logical.height()}")


# ---------------- 物理屏枚举（Win32） ----------------
def enum_monitors() -> list[tuple[str, QRect, QRect, bool]]:
    """枚举物理显示器 -> [(设备名, 物理矩形, 工作区矩形, 是否主屏)]。"""
    result: list[tuple[str, QRect, QRect, bool]] = []
    if not hasattr(user32, "EnumDisplayMonitors"):
        return result

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, _HMONITOR, wintypes.HDC,
                              ctypes.POINTER(_RECT), wintypes.LPARAM)

    def _cb(hmon, _hdc, _lprc, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            w = info.rcWork
            result.append((
                str(info.szDevice),
                QRect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                QRect(w.left, w.top, w.right - w.left, w.bottom - w.top),
                bool(info.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    try:
        user32.EnumDisplayMonitors(None, None, proc(_cb), 0)
    except Exception:
        pass
    return result


# ---------------- 屏幕清单（Qt 逻辑 + Win32 物理 对齐） ----------------
_screen_cache: list[ScreenInfo] = []


def _build_screens() -> list[ScreenInfo]:
    app = QGuiApplication.instance()
    if app is None:
        return []
    qt_screens: list[QScreen] = list(QGuiApplication.screens())
    monitors = enum_monitors()
    used: set[int] = set()
    infos: list[ScreenInfo] = []

    for idx, scr in enumerate(qt_screens):
        logical = scr.geometry()
        dpr = float(scr.devicePixelRatio() or 1.0)
        name = scr.name() or f"screen{idx}"

        # 1) 优先按设备名对齐（Windows 上 QScreen.name() 即 "\\.\DISPLAYn"）
        match = None
        for i, (dev, phys, work, primary) in enumerate(monitors):
            if i in used:
                continue
            if dev.lower() == name.lower():
                match = (i, dev, phys, work, primary)
                break

        # 2) 名称对不上时按「逻辑矩形 × dpr」与物理矩形的重叠度对齐
        if match is None:
            best_score = -1
            for i, (dev, phys, work, primary) in enumerate(monitors):
                if i in used:
                    continue
                guess = QRect(int(round(logical.left() * dpr)), int(round(logical.top() * dpr)),
                              int(round(logical.width() * dpr)), int(round(logical.height() * dpr)))
                inter = guess.intersected(phys)
                score = inter.width() * inter.height()
                if score > best_score:
                    best_score = score
                    match = (i, dev, phys, work, primary)

        if match is None:  # 无法匹配（极少见）：用 dpr 反推
            phys = QRect(int(round(logical.left() * dpr)), int(round(logical.top() * dpr)),
                         int(round(logical.width() * dpr)), int(round(logical.height() * dpr)))
            name, work, primary = name, QRect(), scr is QGuiApplication.primaryScreen()
        else:
            used.add(match[0])
            phys, work, primary = match[2], match[3], match[4]

        infos.append(ScreenInfo(
            name=name,
            index=idx,
            physical=phys,
            logical=QRect(logical),
            dpr=dpr,
            primary=bool(primary),
            work_logical=QRect(scr.availableGeometry()) if not scr.availableGeometry().isNull() else None,
        ))

    infos.sort(key=lambda s: s.index)
    return infos


def screens(force: bool = False) -> list[ScreenInfo]:
    """当前屏幕清单（按 Qt 序号排序，带进程内缓存）。显示器热插拔后传 force=True。"""
    global _screen_cache
    if force or not _screen_cache:
        _screen_cache = _build_screens()
    return _screen_cache


def refresh() -> list[ScreenInfo]:
    """丢弃缓存并重新枚举（接显示器 / 改缩放比例后调用）。"""
    return screens(force=True)


def primary_screen() -> ScreenInfo | None:
    for s in screens():
        if s.primary:
            return s
    ss = screens()
    return ss[0] if ss else None


def screens_have_mixed_dpi() -> bool:
    """是否存在多屏不同缩放（决定是否需要「每屏一个覆盖窗」）。"""
    return len({round(s.scale_x, 3) for s in screens()}) > 1


# ---------------- 虚拟屏 ----------------
def virtual_screen() -> tuple[int, int, int, int]:
    """虚拟桌面 (x, y, w, h)，物理像素，兼容 capture.virtual_screen() 语义。"""
    gsm = user32.GetSystemMetrics
    return (
        int(gsm(SM_XVIRTUALSCREEN)),
        int(gsm(SM_YVIRTUALSCREEN)),
        int(gsm(SM_CXVIRTUALSCREEN)),
        int(gsm(SM_CYVIRTUALSCREEN)),
    )


def virtual_rect() -> QRect:
    """虚拟桌面矩形，**物理像素**（左上角在负坐标副屏时可为负）。"""
    x, y, w, h = virtual_screen()
    return QRect(x, y, w, h)


def grab_bbox() -> tuple[int, int, int, int]:
    """``PIL.ImageGrab.grab(bbox=...)`` 用的物理像素 bbox: (x1, y1, x2, y2)。"""
    x, y, w, h = virtual_screen()
    return (x, y, x + w, y + h)


def virtual_rect_logical() -> QRect:
    """虚拟桌面矩形，**Qt 逻辑像素**（各屏 geometry() 并集）。"""
    rect = QRect()
    for s in screens():
        rect = s.logical if rect.isNull() else rect.united(s.logical)
    if rect.isNull():
        return QRect(0, 0, 1, 1)
    return rect


def overlay_geometries_logical() -> list[tuple[str, QRect]]:
    """全屏覆盖层用的「每屏逻辑几何」列表 [(屏幕名, 逻辑矩形)]。

    混排 DPI 下必须按屏创建窗口，单窗口无法同时正确铺满不同 DPR 的屏幕。
    """
    return [(s.name, QRect(s.logical)) for s in screens()]


# ---------------- 点 / 矩形换算 ----------------
def _nearest_screen_physical(point: QPoint) -> ScreenInfo | None:
    ss = screens()
    if not ss:
        return None
    for s in ss:
        if s.physical.contains(point):
            return s
    # 落在屏幕之间空隙（理论上不存在）：取中心距离最近者
    return min(ss, key=lambda s: (_dist2(s.physical.center(), point), s.index))


def _nearest_screen_logical(point: QPoint) -> ScreenInfo | None:
    ss = screens()
    if not ss:
        return None
    for s in ss:
        if s.logical.contains(point):
            return s
    return min(ss, key=lambda s: (_dist2(s.logical.center(), point), s.index))


def _dist2(a: QPoint, b: QPoint) -> int:
    dx, dy = a.x() - b.x(), a.y() - b.y()
    return dx * dx + dy * dy


def screen_at_physical(point: QPoint | tuple[int, int]) -> ScreenInfo | None:
    """物理像素点 -> 所在屏幕。"""
    if isinstance(point, tuple):
        point = QPoint(int(point[0]), int(point[1]))
    return _nearest_screen_physical(point)


def screen_at_logical(point: QPoint | tuple[int, int]) -> ScreenInfo | None:
    """逻辑像素点 -> 所在屏幕。"""
    if isinstance(point, tuple):
        point = QPoint(int(point[0]), int(point[1]))
    return _nearest_screen_logical(point)


def cursor_screen() -> ScreenInfo | None:
    """鼠标当前所在屏幕（物理 + 逻辑信息都可用）。"""
    return screen_at_physical(QCursor.pos())


def qt_screen_for(screen: ScreenInfo | None) -> QScreen | None:
    """ScreenInfo -> QScreen（用于取 availableGeometry 等 Qt 专属信息）。"""
    if screen is None:
        return None
    ss = QGuiApplication.screens()
    if 0 <= screen.index < len(ss):
        return ss[screen.index]
    return None


def physical_to_logical(point: QPoint | tuple[int, int]) -> QPoint:
    """物理像素点 -> 全局逻辑像素点。"""
    if isinstance(point, tuple):
        point = QPoint(int(point[0]), int(point[1]))
    s = screen_at_physical(point)
    if s is None:
        return QPoint(point)
    return s.to_logical(point)


def logical_to_physical(point: QPoint | tuple[int, int]) -> QPoint:
    """全局逻辑像素点 -> 物理像素点。"""
    if isinstance(point, tuple):
        point = QPoint(int(point[0]), int(point[1]))
    s = screen_at_logical(point)
    if s is None:
        return QPoint(point)
    return s.to_physical(point)


def physical_rect_to_logical(rect: QRect) -> QRect:
    """物理矩形 -> 逻辑矩形（跨屏时按各屏分别映射后取并集）。"""
    ss = screens()
    if not ss:
        return QRect(rect)
    union = QRect()
    for s in ss:
        inter = rect.intersected(s.physical)
        if inter.isEmpty():
            continue
        mapped = s.rect_to_logical(inter)
        union = mapped if union.isNull() else union.united(mapped)
    if union.isNull():
        s = screen_at_physical(rect.center())
        return s.rect_to_logical(rect) if s else QRect(rect)
    return union


def logical_rect_to_physical(rect: QRect) -> QRect:
    """逻辑矩形 -> 物理矩形（跨屏时按各屏分别映射后取并集）。"""
    ss = screens()
    if not ss:
        return QRect(rect)
    union = QRect()
    for s in ss:
        inter = rect.intersected(s.logical)
        if inter.isEmpty():
            continue
        mapped = s.rect_to_physical(inter)
        union = mapped if union.isNull() else union.united(mapped)
    if union.isNull():
        s = screen_at_logical(rect.center())
        return s.rect_to_physical(rect) if s else QRect(rect)
    return union


def logical_geometry_for_physical_rect(rect: QRect) -> QRect:
    """物理矩形 -> 可直接传给 ``QWidget.setGeometry()`` 的逻辑矩形。"""
    return physical_rect_to_logical(rect)


def logical_size_for_physical(size: QSize, screen: ScreenInfo | None = None) -> QSize:
    """物理像素尺寸 -> 逻辑尺寸（默认按鼠标所在屏的缩放比例）。"""
    s = screen or cursor_screen() or primary_screen()
    if s is None:
        return QSize(size)
    return QSize(max(1, int(round(size.width() * s.scale_x))),
                 max(1, int(round(size.height() * s.scale_y))))


def dpr_at_physical(point: QPoint | tuple[int, int] | None = None) -> float:
    """某物理点所在屏幕的 devicePixelRatio（不传则用鼠标位置）。"""
    s = screen_at_physical(point) if point is not None else cursor_screen()
    return s.dpr if s else 1.0


# ---------------- 窗口定位辅助 ----------------
def center_on_screen(size: QSize, screen: ScreenInfo | None = None) -> QPoint:
    """给逻辑尺寸，返回让窗口在某屏居中的逻辑左上角坐标。"""
    s = screen or cursor_screen() or primary_screen()
    if s is None:
        return QPoint(0, 0)
    area = s.work_logical or s.logical
    return QPoint(area.center().x() - size.width() // 2,
                  area.center().y() - size.height() // 2)


def clamp_rect(rect: QRect, area: QRect | None = None) -> QRect:
    """把逻辑矩形钳制在给定区域（默认虚拟屏逻辑并集）内，尺寸超界时先收缩。"""
    bounds = area or virtual_rect_logical()
    r = QRect(rect)
    if r.width() > bounds.width():
        r.setWidth(bounds.width())
    if r.height() > bounds.height():
        r.setHeight(bounds.height())
    if r.left() < bounds.left():
        r.moveLeft(bounds.left())
    if r.top() < bounds.top():
        r.moveTop(bounds.top())
    if r.right() > bounds.right():
        r.moveRight(bounds.right())
    if r.bottom() > bounds.bottom():
        r.moveBottom(bounds.bottom())
    return r


def screen_of_widget(widget) -> ScreenInfo | None:
    """Qt 窗口当前所在屏幕对应的 ScreenInfo（找不到时退回鼠标所在屏）。"""
    try:
        handle = widget.windowHandle()
        scr = handle.screen() if handle else None
        if scr is not None:
            for s in screens():
                if s.name.lower() == (scr.name() or "").lower():
                    return s
    except Exception:
        pass
    return cursor_screen()


# ---------------- 调试 ----------------
def describe() -> str:
    """多屏 / DPI 诊断文本（自检与日志用）。"""
    vs = virtual_screen()
    lines = [
        f"virtual(physical) = x={vs[0]} y={vs[1]} w={vs[2]} h={vs[3]}",
        f"virtual(logical)  = x={virtual_rect_logical().left()} "
        f"y={virtual_rect_logical().top()} "
        f"w={virtual_rect_logical().width()} h={virtual_rect_logical().height()}",
        f"mixed_dpi = {screens_have_mixed_dpi()}",
    ]
    for s in screens():
        lines.append("  " + str(s))
    return "\n".join(lines)
