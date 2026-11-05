# -*- coding: utf-8 -*-
"""弹窗 / 文件对话框层（替代旧版 dialogs.patch 的猴补丁）。

旧版做法：包装 ``tkinter.messagebox`` / ``filedialog``，未传 parent 时创建一个
位于鼠标所在屏幕中心的透明锚点窗口，让对话框跟随它弹出（避免跨屏）。

Qt 版做法相同但更简单：仍然用「透明锚点 parent」，只是锚点改为 1x1 的
``QWidget(Qt.Tool)``。这样：
* 已显式传 parent 的调用 → 对话框跟随父窗口，行为不变；
* 未传 parent 的调用 → 弹出在鼠标所在屏幕中心。

对外 API（签名与旧 tk 调用习惯对齐，便于后续阶段机械替换）::

    msg_info(title, message, parent=None)
    msg_warn / msg_error
    msg_confirm(title, message, ...) -> bool
    ask_open_file / ask_open_files / ask_save_file / ask_directory -> str / list[str]
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QDialog, QFileDialog, QMessageBox, QPushButton,
                               QWidget)

from ui_qt import geometry

_APP_TITLE = "ScreenSnap"
_last_dir: str = ""


# ---------------- 透明锚点 ----------------
class _Anchor(QWidget):
    """1x1 全透明置顶窗，仅用于给无 parent 的对话框提供定位基准。"""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowTitle(_APP_TITLE)
        self.resize(1, 1)
        self.setWindowOpacity(0.0)

    def center_on_cursor_screen(self) -> None:
        pt = geometry.center_on_screen(QSize(1, 1))
        self.move(pt)


@contextmanager
def _parent_ctx(parent=None):
    """有 parent 用 parent；没有则临时创建锚点，退出时销毁。"""
    if parent is not None:
        yield parent
        return
    anchor = _Anchor()
    anchor.center_on_cursor_screen()
    anchor.show()
    try:
        yield anchor
    finally:
        anchor.close()
        anchor.deleteLater()


# ---------------- 通用工具 ----------------
def _box(icon: QMessageBox.Icon, title: str, message: str,
         parent, detail: str = "") -> QMessageBox:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title or _APP_TITLE)
    box.setText(str(message or ""))
    if detail:
        box.setInformativeText(str(detail))
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.setMinimumWidth(340)
    return box


def _style_default_buttons(box: QMessageBox) -> None:
    """把 Qt 自动生成的标准按钮纳入统一按钮体系（Accept→Primary，其余→Ghost）。

    标准按钮没有 objectName，QSS 只能套到基础样式，观感与自定义按钮不一致；
    这里在 show 之前统一命名，保证确认框 / 提示框按钮风格统一。
    """
    for btn in box.buttons():
        if btn.objectName():
            continue
        role = box.buttonRole(btn)
        btn.setObjectName("Primary" if role == QMessageBox.ButtonRole.AcceptRole else "Ghost")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)


def _remember(path: str | None) -> None:
    global _last_dir
    if path:
        _last_dir = os.path.dirname(path) if os.path.isfile(path) else path


def _start_dir(initialdir: str | None) -> str:
    if initialdir:
        return initialdir
    if _last_dir and os.path.isdir(_last_dir):
        return _last_dir
    return os.path.expanduser("~")


def _to_name_filters(filetypes) -> list[str]:
    """tk 风格 [(label, '*.png')] / Qt 风格字符串列表 -> Qt 名称过滤器列表。"""
    if not filetypes:
        return []
    out: list[str] = []
    for item in filetypes:
        if isinstance(item, str):
            out.append(item)
            continue
        try:
            label, pattern = item[0], item[1]
        except Exception:
            continue
        pats = [p for p in str(pattern).replace(";", " ").split() if p.strip()]
        pats = ["*" if p in ("*.*", "*") else p for p in pats]
        if pats:
            out.append(f"{label} ({' '.join(pats)})")
    return out


# ---------------- 消息框 ----------------
def msg_info(title: str, message: str = "", parent=None, detail: str = "") -> None:
    """信息提示。"""
    with _parent_ctx(parent) as p:
        box = _box(QMessageBox.Icon.Information, title, message, p, detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("知道了")
        _style_default_buttons(box)
        box.exec()


def msg_warn(title: str, message: str = "", parent=None, detail: str = "") -> None:
    """警告提示。"""
    with _parent_ctx(parent) as p:
        box = _box(QMessageBox.Icon.Warning, title, message, p, detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("知道了")
        _style_default_buttons(box)
        box.exec()


def msg_error(title: str, message: str = "", parent=None, detail: str = "") -> None:
    """错误提示。"""
    with _parent_ctx(parent) as p:
        box = _box(QMessageBox.Icon.Critical, title, message, p, detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("知道了")
        _style_default_buttons(box)
        box.exec()


def msg_confirm(title: str, message: str = "", parent=None, detail: str = "",
                ok_text: str = "确定", cancel_text: str = "取消",
                danger: bool = False) -> bool:
    """确认框，返回 True 表示用户点了确认。"""
    with _parent_ctx(parent) as p:
        box = _box(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question,
                   title, message, p, detail)
        ok_btn = QPushButton(ok_text)
        ok_btn.setObjectName("Danger" if danger else "Primary")
        cancel_btn = QPushButton(cancel_text)
        cancel_btn.setObjectName("Ghost")
        box.addButton(ok_btn, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(cancel_btn, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.setEscapeButton(cancel_btn)
        _style_default_buttons(box)
        box.exec()
        return box.clickedButton() is ok_btn


# tk 习惯命名别名（旧调用点可机械替换）
showinfo = msg_info
showwarning = msg_warn
showerror = msg_error
askyesno = msg_confirm
askokcancel = msg_confirm


# ---------------- 文件对话框 ----------------
def _file_dialog(parent, title: str, directory: str,
                 filters: list[str], mode: QFileDialog.FileMode,
                 default_suffix: str = "", initial_file: str = "") -> QFileDialog:
    dlg = QFileDialog(parent, title or _APP_TITLE, directory)
    dlg.setFileMode(mode)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen
                      if mode != QFileDialog.FileMode.AnyFile
                      else QFileDialog.AcceptMode.AcceptSave)
    if filters:
        dlg.setNameFilters(filters)
        dlg.selectNameFilter(filters[0])
    if default_suffix and mode == QFileDialog.FileMode.AnyFile:
        dlg.setDefaultSuffix(default_suffix)
    if initial_file:
        dlg.selectFile(initial_file)
    dlg.setOption(QFileDialog.Option.DontUseCustomDirectoryIcons, True)
    return dlg


def ask_open_file(title: str = "打开文件", initialdir: str | None = None,
                  filetypes=None, parent=None) -> str:
    """选择单个文件，返回绝对路径；取消返回空串。"""
    filters = _to_name_filters(filetypes) or ["所有文件 (*)"]
    with _parent_ctx(parent) as p:
        dlg = _file_dialog(p, title, _start_dir(initialdir), filters,
                           QFileDialog.FileMode.ExistingFile)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dlg.selectedFiles()
        path = files[0] if files else ""
        _remember(path)
        return path


def ask_open_files(title: str = "打开文件", initialdir: str | None = None,
                   filetypes=None, parent=None) -> list[str]:
    """选择多个文件，返回绝对路径列表；取消返回空列表。"""
    filters = _to_name_filters(filetypes) or ["所有文件 (*)"]
    with _parent_ctx(parent) as p:
        dlg = _file_dialog(p, title, _start_dir(initialdir), filters,
                           QFileDialog.FileMode.ExistingFiles)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        files = list(dlg.selectedFiles())
        _remember(files[0] if files else None)
        return files


def ask_save_file(title: str = "保存文件", initialdir: str | None = None,
                  initialfile: str = "", filetypes=None,
                  default_ext: str = "png", parent=None) -> str:
    """另存为，返回绝对路径；取消返回空串。自动补默认扩展名。"""
    filters = _to_name_filters(filetypes) or ["所有文件 (*)"]
    with _parent_ctx(parent) as p:
        dlg = _file_dialog(p, title, _start_dir(initialdir), filters,
                           QFileDialog.FileMode.AnyFile,
                           default_suffix=default_ext, initial_file=initialfile)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dlg.selectedFiles()
        path = files[0] if files else ""
        if path and default_ext and not os.path.splitext(path)[1]:
            path = f"{path}.{default_ext}"
        _remember(path)
        return path


def ask_directory(title: str = "选择文件夹", initialdir: str | None = None,
                  parent=None) -> str:
    """选择目录，返回绝对路径；取消返回空串。"""
    with _parent_ctx(parent) as p:
        dlg = _file_dialog(p, title, _start_dir(initialdir), [],
                           QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return ""
        files = dlg.selectedFiles()
        path = files[0] if files else ""
        _remember(path)
        return path


# ---------------- 兼容旧 API ----------------
def patch() -> None:
    """旧版入口兼容占位：Qt 下无需猴补丁，调用本函数不做任何事（幂等）。"""
    return None
