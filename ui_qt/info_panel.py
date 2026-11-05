# -*- coding: utf-8 -*-
"""启动信息面板。

原先主面板中间直接贴 ``geometry.describe()`` 的调试串（virtual(physical)=…
mixed_dpi=…），只有排查多屏问题时有用。这里换成看得懂的启动概览：

    显示器    显示屏 1（主）· 1920×1080 · 100%
              显示屏 2 · 1920×1080 · 100%
    文字识别  Windows 系统 OCR · 已就绪
    版本      v1.0.0 · 已是最新            [检查更新]

后续要加「官网 / 翻译 / 关于」等条目，调 ``add_row()`` 即可，样式统一。
原始调试串仍然保留在「诊断信息」按钮里，报障时一键复制。
"""
from __future__ import annotations

import json
import threading
import urllib.request

from PySide6.QtCore import QObject, Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

import config
from ui_qt import dialogs, geometry, ocr_qt, theme, translate_qt

_TAG_W = 56          # 左侧标签列宽，保证各行值左对齐
_ROW_GAP = 3


class _Row(QWidget):
    """一行「标签 + 值 +（可选）按钮」。"""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.tag = QLabel(label, self)
        self.tag.setFixedWidth(_TAG_W)
        self.tag.setFont(theme.font(theme.FONT_SM_SIZE))
        self.tag.setStyleSheet(f"color:{theme.INK_3};")
        self.tag.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.value = QLabel("—", self)
        self.value.setFont(theme.font(theme.FONT_SM_SIZE + 1))
        self.value.setStyleSheet(f"color:{theme.INK};")
        self.value.setWordWrap(True)
        # wordWrap 标签的默认最小高度只有「一行」：容器高度不够时布局会把它压到
        # 单行高度，多余的行直接画到下一行上（首帧「显示器」行文字重叠就是这个原因）。
        # 这里把「按行数算出的高度」设成最小高度，布局无法再压缩，只能让父窗口长高。
        self.value.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Minimum)
        self.value.setMinimumHeight(self._value_min_height("—"))
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.btn = QPushButton(self)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setFixedHeight(20)
        self.btn.setFont(theme.font(theme.FONT_SM_SIZE))
        self.btn.setStyleSheet(
            "QPushButton{border:1px solid %s;border-radius:10px;padding:0 10px;"
            "background:%s;color:%s;}"
            "QPushButton:hover{background:%s;color:%s;}"
            "QPushButton:disabled{color:%s;}"
            % (theme.LINE, theme.SURFACE, theme.INK_2, theme.ACCENT_SOFT,
               theme.ACCENT, theme.INK_3))
        self.btn.hide()
        self._slot = None

        lay.addWidget(self.tag, 0)
        lay.addWidget(self.value, 1, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignVCenter)

    # ---- 便捷设置 ----
    def _value_min_height(self, text: str) -> int:
        """按文本行数算值标签需要的最小高度（行高 × 行数 + 少量余量）。"""
        try:
            line_spacing = self.value.fontMetrics().lineSpacing()
        except Exception:
            line_spacing = 16
        lines = max(1, str(text).count("\n") + 1)
        return int(line_spacing * lines + 2)

    def set_value(self, text: str, tone: str | None = None) -> None:
        self.value.setText(text)
        self.value.setMinimumHeight(self._value_min_height(text))
        self.updateGeometry()
        color = {"warn": theme.WARN, "ok": theme.SUCCESS,
                 "muted": theme.INK_3}.get(tone or "", theme.INK)
        self.value.setStyleSheet(f"color:{color};")

    def set_action(self, text: str | None, slot=None, enabled: bool = True) -> None:
        if not text:
            self.btn.hide()
            return
        self.btn.setText(text)
        self.btn.setEnabled(enabled)
        self.btn.show()
        if slot is not None and slot is not self._slot:
            if self._slot is not None:
                try:
                    self.btn.clicked.disconnect(self._slot)
                except (RuntimeError, TypeError):
                    pass
            self.btn.clicked.connect(slot)
            self._slot = slot


class _UpdateFetcher(QObject):
    """后台拉版本 JSON，回到主线程发信号（不阻塞启动）。"""

    done = Signal(bool, dict)

    def fetch(self, url: str, timeout: float = 5.0) -> None:
        threading.Thread(target=self._run, args=(url, timeout), daemon=True).start()

    def _run(self, url: str, timeout: float) -> None:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            self.done.emit(True, data if isinstance(data, dict) else {})
        except Exception as exc:  # 网络 / JSON 异常都按「检查失败」处理
            self.done.emit(False, {"error": str(exc)})


class StartupInfoPanel(QWidget):
    """启动信息面板（显示器 / 文字识别 / 版本 / 官网）。"""

    def __init__(self, cfg: dict | None = None, on_status=None, parent=None,
                 on_open_settings=None):
        super().__init__(parent)
        self._cfg = dict(cfg or {})
        self._on_status = on_status
        self._on_open_settings = on_open_settings
        self._state = "idle"          # idle / checking / latest / available / failed
        self._remote: dict = {}
        self._fetcher = _UpdateFetcher(self)
        self._fetcher.done.connect(self._on_fetched)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_ROW_GAP)

        self.row_screen = _Row("显示器", self)
        self.row_ocr = _Row("文字识别", self)
        self.row_translate = _Row("图片翻译", self)
        self.row_version = _Row("版本", self)
        self.row_site = _Row("官网", self)
        self._rows = [self.row_screen, self.row_ocr, self.row_translate,
                      self.row_version, self.row_site]
        for row in self._rows:
            lay.addWidget(row)

        # 信息区下方：取消启动提示
        # 勾选 = 启动时自动弹出本页面；不勾选 = 启动时不弹出（自动关闭，仅驻留托盘）
        self.ck_startup = QCheckBox("取消启动提示", self)
        self.ck_startup.setFont(theme.font(theme.FONT_SM_SIZE, False))
        self.ck_startup.setStyleSheet("QCheckBox{color:%s;}" % theme.INK_2)
        self.ck_startup.setToolTip(
            "勾选：启动时自动弹出本页面；不勾选：启动时不弹出（自动关闭，仅驻留系统托盘）")
        self.ck_startup.setChecked(bool(self._cfg.get("startup_show_panel", True)))
        self.ck_startup.toggled.connect(self._on_startup_toggled)
        lay.addWidget(self.ck_startup)
        lay.addStretch(1)

        self.refresh()
        # 启动后延迟查一次；没配更新源就静默跳过。
        QTimer.singleShot(2500, lambda: self.check_update(manual=False))

    # ------------------------------------------------------------ 对外
    def add_row(self, label: str) -> _Row:
        """追加自定义条目（翻译、关于等扩展位）。"""
        row = _Row(label, self)
        self.layout().insertWidget(self.layout().count() - 1, row)
        self._rows.append(row)
        return row

    def set_config(self, cfg: dict | None) -> None:
        self._cfg = dict(cfg or {})
        # 设置页保存后回填：两处勾选框同步到同一配置项
        try:
            self.ck_startup.blockSignals(True)
            self.ck_startup.setChecked(bool(self._cfg.get("startup_show_panel", True)))
            self.ck_startup.blockSignals(False)
        except Exception:
            pass
        self.refresh()

    def _on_startup_toggled(self, checked: bool) -> None:
        """勾选「取消启动提示」＝启动时弹出本页面；状态直接落盘，与设置页双向同步。"""
        self._cfg["startup_show_panel"] = bool(checked)
        try:
            cfg = config.load_config() or {}
        except Exception:
            cfg = {}
        cfg["startup_show_panel"] = bool(checked)
        try:
            config.save_config(cfg)
        except Exception:
            self._status("启动页设置保存失败")
            return
        self._status("启动时%s本页面" % ("弹出" if checked else "不弹出，仅驻留托盘"))

    def refresh(self) -> None:
        self._refresh_screen()
        self._refresh_ocr()
        self._refresh_translate()
        self._refresh_version()
        self._refresh_site()
        # 各行高度按新文本变化，主动通知父布局重算，避免沿用旧的压缩后几何
        self.updateGeometry()
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()

    def check_update(self, manual: bool = True) -> None:
        url = (config.UPDATE_URL or "").strip()
        if not url:
            self._state = "idle"
            self._refresh_version()
            if manual:
                self._status("尚未配置更新源")
                try:
                    dialogs.msg_info(
                        "尚未配置更新源",
                        "版本检查需要一个返回版本信息的 JSON 地址。\n\n"
                        "请在 config.py 里填 UPDATE_URL，例如：\n"
                        "  UPDATE_URL = \"https://你的域名/screensnap/version.json\"\n\n"
                        "JSON 内容示例：\n"
                        "  {\"version\": \"1.1.0\",\n"
                        "   \"url\": \"https://…/download\",\n"
                        "   \"notes\": \"本次更新说明\"}\n\n"
                        "未配置时面板只显示当前版本号，不影响其他功能。",
                        parent=self)
                except Exception:
                    pass
            return
        if self._state == "checking":
            return
        self._state = "checking"
        self._refresh_version()
        self._status("正在检查新版本…")
        self._fetcher.fetch(url)

    def diagnostic_text(self) -> str:
        """复制用：可读信息 + 原始多屏 / DPI 调试串。"""
        lines = [f"{config.APP_NAME} v{config.APP_VERSION}"]
        for row in self._rows:
            if row.isHidden():
                continue
            val = row.value.text().strip().replace("\n", " / ")
            lines.append(f"{row.tag.text()}：{val}")
        lines.append("")
        lines.append("---- 多屏 / DPI 详情 ----")
        lines.append(geometry.describe())
        return "\n".join(lines)

    # ------------------------------------------------------------ 内部
    def _status(self, text: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(text)
            except Exception:
                pass

    def _refresh_screen(self) -> None:
        try:
            infos = geometry.screens()
        except Exception:
            infos = []
        if not infos:
            self.row_screen.set_value("未检测到显示器", "warn")
            return
        parts = []
        for i, s in enumerate(infos, 1):
            w = getattr(s, "physical", None)
            lg = getattr(s, "logical", None)
            width = w.width() if w is not None else 0
            height = w.height() if w is not None else 0
            if not width or not height:
                width = lg.width() if lg is not None else 0
                height = lg.height() if lg is not None else 0
            scale = 100
            try:
                if w is not None and lg is not None and lg.width():
                    scale = int(round(w.width() / lg.width() * 100))
            except Exception:
                scale = 100
            flag = "（主）" if getattr(s, "primary", False) else ""
            parts.append(f"显示屏 {i}{flag} · {width}×{height} · {scale}%")
        self.row_screen.set_value("\n".join(parts))
        self.row_screen.setToolTip("\n".join(getattr(s, "name", "") for s in infos))

    def _refresh_ocr(self) -> None:
        try:
            backs = ocr_qt.available_backends()
        except Exception:
            backs = []
        alive = [b for b in backs if b.get("available")]
        if not alive:
            self.row_ocr.set_value("未安装 · 本机缺中文识别组件", "warn")
            self.row_ocr.setToolTip(
                "未检测到可用的文字识别引擎（Windows 系统 OCR 或 RapidOCR 组件包）。\n"
                "点右侧「去设置」可切换引擎或查看安装引导。")
            self._guide(self.row_ocr)
            return
        try:
            pref = ocr_qt.engine_prefer(self._cfg)
        except Exception:
            pref = None
        real = next((b for b in alive if b.get("id") == pref), alive[0])
        label = real.get("label") or "OCR"
        if real.get("status") == "no_zh_pack":
            self.row_ocr.set_value(f"{label} · 缺中文语言包", "warn")
            self._guide(self.row_ocr)
            return
        if real.get("id") == "winocr":
            label = f"{label}（中文）"
        extra = ""
        if real.get("id") == "rapidocr":
            try:
                info = ocr_qt.pack_info()
                extra = f" · 组件包 v{info.get('version')} {info.get('size_mb')}MB"
            except Exception:
                extra = ""
        self.row_ocr.set_value(f"{label} · 已就绪{extra}", "ok")
        self.row_ocr.setToolTip(f"{label}{extra}")
        self.row_ocr.set_action(None)

    def _refresh_translate(self) -> None:
        """图片翻译（百度）是否已加载可用：校验 APPID / API Key 配置完整性。"""
        try:
            cred = translate_qt.credentials("baidu_llm", self._cfg) or {}
        except Exception:
            cred = {}
        fields = cred.get("fields") or {}
        appid = str(fields.get("appid") or "").strip()
        apikey = str(fields.get("apikey") or "").strip()
        if cred.get("ok") and (appid and apikey):
            src = ""
            try:
                src = translate_qt.source_label(cred.get("source") or "")
            except Exception:
                src = ""
            extra = f" · 来源：{src}" if src else ""
            self.row_translate.set_value(f"百度大模型翻译 · 已加载可用{extra}", "ok")
            self.row_translate.setToolTip(
                "图片翻译已就绪：可在编辑器里对截图做整图翻译。")
            self.row_translate.set_action("设置", self._open_settings)
            return

        missing = []
        if not appid:
            missing.append("APPID")
        if not apikey:
            missing.append("API Key")
        if not missing:
            missing = ["凭据"]
        hint = " / ".join(missing)
        self.row_translate.set_value(
            f"未配置 · 缺 {hint}，图片翻译不可用", "warn")
        self.row_translate.setToolTip(
            f"百度图片翻译需要 APPID 与 API Key 同时配置（当前缺 {hint}）。\n"
            "点右侧「去设置」→ 翻译服务 → 百度大模型翻译 填写后即可用。")
        self._guide(self.row_translate)

    def _guide(self, row: "_Row", text: str = "去设置") -> None:
        """给未就绪的行挂一个跳设置的引导按钮；无回调时隐藏按钮。"""
        if self._on_open_settings is None:
            row.set_action(None)
            return
        row.set_action(text, self._open_settings)

    def _open_settings(self) -> None:
        if self._on_open_settings is None:
            return
        try:
            self._on_open_settings()
        except Exception:
            pass

    def _refresh_version(self) -> None:
        v = config.APP_VERSION
        state = self._state
        if state == "checking":
            self.row_version.set_value(f"v{v} · 检查中…")
            self.row_version.set_action("检查中", self.check_update, enabled=False)
        elif state == "latest":
            self.row_version.set_value(f"v{v} · 已是最新", "ok")
            self.row_version.set_action("重新检查", self.check_update)
        elif state == "available":
            remote = self._remote.get("version", "")
            self.row_version.set_value(f"v{v} · 可更新至 v{remote}", "warn")
            self.row_version.set_action("更新", self._open_update_page)
        elif state == "failed":
            self.row_version.set_value(f"v{v} · 检查失败", "muted")
            self.row_version.set_action("重试", self.check_update)
        else:
            self.row_version.set_value(f"v{v}")
            self.row_version.set_action("检查更新", self.check_update)

    def _refresh_site(self) -> None:
        site = (config.APP_SITE or "").strip()
        if not site:
            self.row_site.hide()
            return
        self.row_site.show()
        self.row_site.set_value(site)
        self.row_site.set_action(
            "打开", lambda: QDesktopServices.openUrl(QUrl(site)))

    def _on_fetched(self, ok: bool, data: dict) -> None:
        if not ok:
            self._state = "failed"
            self._refresh_version()
            self._status(f"检查更新失败：{data.get('error', '网络异常')}")
            return
        self._remote = dict(data or {})
        remote = str(self._remote.get("version") or "").strip()
        if remote and config.version_newer(remote, config.APP_VERSION):
            self._state = "available"
            self._refresh_version()
            self._status(f"发现新版本 v{remote}")
        else:
            self._state = "latest"
            self._refresh_version()
            self._status("已是最新版本")

    def _open_update_page(self) -> None:
        url = (config.UPDATE_PAGE or self._remote.get("url") or "").strip()
        if not url:
            self._status("更新页未配置，暂无法打开")
            return
        QDesktopServices.openUrl(QUrl(url))
