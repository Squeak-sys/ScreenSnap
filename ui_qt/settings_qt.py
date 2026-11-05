"""Qt 版设置页：与 tk 版 ``settings.py`` 相同的配置字段，界面换成 Qt。

字段：hotkey / save_format / save_root / record_fps / auto_start /
auto_copy / trash_days / column_capacity，全部落在 ``config.json``。

设计约束：
- 只读写 ``config``，不 import 任何 tk 模块；
- 开机自启沿用 tk 版同一套 HKCU\\...\\Run 注册表实现；
- 热键改动需重启生效（热键由 HotkeyBridge 在启动时注册一次）。
"""

from __future__ import annotations

import os
import sys
import winreg

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

import config
from ui_qt import ocr_qt, theme, translate_qt

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ScreenSnap"

# 图标目录：开发态取项目 assets，打包态取 _internal/assets
if getattr(sys, "frozen", False):
    _ASSETS = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "assets")
else:
    _ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_ICON_DIR = os.path.join(_ASSETS, "icons").replace("\\", "/")

C_CAPACITY = (5, 7, 10)     # 历史库「每列张数」可选档位
C_FPS = (24, 30, 60, 120)   # 录屏帧率可选档位
# OCR 默认精度档：(配置值, 按钮文案)
C_OCR_ENGINE = (
    ("auto", "自动（本机系统 OCR 优先）"),
    ("winocr", "系统 OCR"),
    ("rapidocr", "高精度组件包"),
)
ROW_H = 34                  # 表单行最小高度，避免控件被压扁

_FORM_STYLE = f"""
QDialog {{ background: {theme.SURFACE}; }}
QLabel#Head {{ color: {theme.INK}; font-family: "{theme.FONT_FAMILY}"; }}
QLabel#Hint {{ color: {theme.INK_3}; }}
QLabel#Row  {{ color: {theme.INK_2}; }}
QLineEdit, QSpinBox, QComboBox {{
    background: {theme.SURFACE_2};
    border: 1px solid {theme.LINE};
    border-radius: {theme.R_INPUT}px;
    padding: 5px 8px;
    color: {theme.INK};
    min-height: 18px;
}}
/* 数字框右侧留按钮位，上下箭头自绘，避免系统默认深色箭头看不清 */
QSpinBox {{ padding-right: 26px; }}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    height: 15px;
    margin: 1px 1px 0 0;
    border-left: 1px solid {theme.LINE};
    border-top-right-radius: {theme.R_INPUT}px;
    background: transparent;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 15px;
    margin: 0 1px 1px 0;
    border-left: 1px solid {theme.LINE};
    border-bottom-right-radius: {theme.R_INPUT}px;
    background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {theme.ACCENT_SOFT};
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
    background: {theme.SURFACE_3};
}}
QSpinBox::up-arrow {{
    image: url("{_ICON_DIR}/spin_up.png");
    width: 9px;
    height: 9px;
}}
QSpinBox::down-arrow {{
    image: url("{_ICON_DIR}/spin_down.png");
    width: 9px;
    height: 9px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: url("{_ICON_DIR}/spin_down.png");
    width: 9px;
    height: 9px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {theme.ACCENT};
    background: {theme.SURFACE};
}}
/* 档位分段选择（5 / 7 / 10） */
QPushButton#Seg {{
    background: {theme.SURFACE_2};
    border: 1px solid {theme.LINE};
    border-radius: {theme.R_BTN}px;
    padding: 5px 0;
    color: {theme.INK_2};
    min-width: 52px;
    min-height: 20px;
}}
QPushButton#Seg:hover {{ background: {theme.SURFACE_3}; }}
QPushButton#Seg:checked {{
    background: {theme.ACCENT_SOFT};
    border: 1px solid {theme.ACCENT};
    color: {theme.ACCENT};
}}
QCheckBox {{ color: {theme.INK}; }}
QPushButton {{
    background: {theme.SURFACE_2};
    border: 1px solid {theme.LINE};
    border-radius: {theme.R_BTN}px;
    padding: 6px 14px;
    color: {theme.INK};
}}
QPushButton:hover {{ background: {theme.SURFACE_3}; }}
QPushButton#Primary {{
    background: {theme.ACCENT};
    border: 1px solid {theme.ACCENT};
    color: #FFFFFF;
}}
QPushButton#Primary:hover {{ background: {theme.ACCENT_HOVER}; }}
/* 分页：通用 / OCR */
QTabWidget::pane {{
    border: 1px solid {theme.LINE};
    border-radius: {theme.R_INPUT}px;
    background: {theme.SURFACE};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid transparent;
    border-bottom: none;
    padding: 7px 20px;
    margin-right: 4px;
    color: {theme.INK_3};
    border-top-left-radius: {theme.R_INPUT}px;
    border-top-right-radius: {theme.R_INPUT}px;
}}
QTabBar::tab:hover {{ color: {theme.INK}; background: {theme.SURFACE_2}; }}
QTabBar::tab:selected {{
    background: {theme.SURFACE};
    border: 1px solid {theme.LINE};
    border-bottom: 1px solid {theme.SURFACE};
    color: {theme.ACCENT};
}}
"""


def build_launch_cmd() -> str:
    """开机自启命令行：优先 pythonw.exe，避免每次开机弹黑框。"""
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "main_qt.py"))
    return f'"{exe}" "{script}"'


def set_auto_start(enabled: bool) -> bool:
    """写入 / 移除 HKCU Run 项，返回是否成功。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, build_launch_cmd())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        finally:
            key.Close()
        return True
    except Exception:
        return False


class SettingsDialog(QDialog):
    """设置对话框；exec() 返回 Accepted 表示已保存到 config.json。"""

    def __init__(self, parent=None, cfg: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("ScreenSnap - 设置")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(_FORM_STYLE)
        self.setMinimumWidth(560)

        self.cfg = dict(cfg or config.load_config())

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(12)

        head = QLabel("设置", self)
        head.setObjectName("Head")
        f = head.font()
        f.setPointSize(theme.FONT_TITLE_SIZE)
        f.setBold(True)
        head.setFont(f)
        root.addWidget(head)

        hint = QLabel("修改后点「保存」写入 config.json；热键改动需重启程序生效。", self)
        hint.setObjectName("Hint")
        hint.setFont(theme.font(theme.FONT_SM_SIZE))
        root.addWidget(hint)

        # 分页：通用 / OCR
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("Tabs")
        page_general = QWidget(self)
        gen_lay = QVBoxLayout(page_general)
        gen_lay.setContentsMargins(4, 14, 4, 6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        def row_label(text: str) -> QLabel:
            lab = QLabel(text, self)
            lab.setObjectName("Row")
            lab.setFont(theme.font(theme.FONT_BODY_SIZE))
            lab.setMinimumHeight(ROW_H)
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return lab

        def field_holder(widget: QWidget) -> QWidget:
            """统一给控件一个包裹层，保证行高不被表单压缩。"""
            box = QWidget(self)
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            lay.addWidget(widget, 1)
            box.setMinimumHeight(ROW_H)
            return box

        # 热键
        self.ed_hotkey = QLineEdit(str(self.cfg.get("hotkey") or "Ctrl+Alt+A"), self)
        self.ed_hotkey.setPlaceholderText("Ctrl+Alt+A")
        form.addRow(row_label("截图热键"), field_holder(self.ed_hotkey))

        # 保存目录（输入框 + 浏览按钮）
        self.ed_root = QLineEdit(str(self.cfg.get("save_root") or config.DEFAULT_SAVE_ROOT), self)
        self.ed_root.setPlaceholderText(str(config.DEFAULT_SAVE_ROOT))
        self.ed_root.setMinimumHeight(28)
        btn_browse = QPushButton("浏览…", self)
        btn_browse.setMinimumHeight(28)
        btn_browse.clicked.connect(self._choose_root)
        dir_box = QWidget(self)
        dir_lay = QHBoxLayout(dir_box)
        dir_lay.setContentsMargins(0, 0, 0, 0)
        dir_lay.setSpacing(8)
        dir_lay.addWidget(self.ed_root, 1)
        dir_lay.addWidget(btn_browse)
        dir_box.setMinimumHeight(ROW_H)
        form.addRow(row_label("保存目录"), dir_box)

        # 保存格式
        self.cb_format = QComboBox(self)
        self.cb_format.addItems(["png", "jpg"])
        fmt = str(self.cfg.get("save_format") or "png").lower()
        self.cb_format.setCurrentText("jpg" if fmt in ("jpg", "jpeg") else "png")
        form.addRow(row_label("图片格式"), field_holder(self.cb_format))

        # 录屏帧率：固定 24 / 30 / 60 / 120 四档
        self._fps = self._pick_fps(self.cfg.get("record_fps", 30))
        fps_box = QWidget(self)
        fps_lay = QHBoxLayout(fps_box)
        fps_lay.setContentsMargins(0, 0, 0, 0)
        fps_lay.setSpacing(8)
        self._fps_group = QButtonGroup(self)
        self._fps_group.setExclusive(True)
        for n in C_FPS:
            b = QPushButton(f"{n} fps", fps_box)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(n == self._fps)
            self._fps_group.addButton(b, n)
            fps_lay.addWidget(b)
        fps_lay.addStretch(1)
        fps_box.setMinimumHeight(ROW_H)
        form.addRow(row_label("录屏帧率"), fps_box)

        # 回收站保留天数
        self.sp_trash = QSpinBox(self)
        self.sp_trash.setRange(0, 30)
        self.sp_trash.setSuffix(" 天")
        self.sp_trash.setValue(int(self.cfg.get("trash_days", 3) or 0))
        form.addRow(row_label("回收站保留"), field_holder(self.sp_trash))

        # 历史库每列张数：固定 5 / 7 / 10 三档
        self._cap = self._pick_capacity(self.cfg.get("column_capacity"))
        cap_box = QWidget(self)
        cap_lay = QHBoxLayout(cap_box)
        cap_lay.setContentsMargins(0, 0, 0, 0)
        cap_lay.setSpacing(8)
        self._cap_group = QButtonGroup(self)
        self._cap_group.setExclusive(True)
        for n in C_CAPACITY:
            b = QPushButton(f"{n} 张", cap_box)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(n == self._cap)
            self._cap_group.addButton(b, n)
            cap_lay.addWidget(b)
        cap_lay.addStretch(1)
        cap_box.setMinimumHeight(ROW_H)
        form.addRow(row_label("历史库列容量"), cap_box)

        # 开关项
        self.ck_copy = QCheckBox("截图完成后自动复制到剪贴板", self)
        self.ck_copy.setChecked(bool(self.cfg.get("auto_copy", True)))
        self.ck_copy.setMinimumHeight(ROW_H)
        form.addRow(row_label("自动复制"), field_holder(self.ck_copy))

        self.ck_start = QCheckBox("开机自动启动", self)
        self.ck_start.setChecked(bool(self.cfg.get("auto_start", True)))
        self.ck_start.setMinimumHeight(ROW_H)
        form.addRow(row_label("开机自启"), field_holder(self.ck_start))

        # 与主面板「取消启动提示」读写同一配置项（startup_show_panel）
        self.ck_startup_page = QCheckBox("启动时打开启动页面", self)
        self.ck_startup_page.setChecked(bool(self.cfg.get("startup_show_panel", True)))
        self.ck_startup_page.setToolTip("取消勾选后，启动时不弹出启动页面，仅驻留系统托盘")
        self.ck_startup_page.setMinimumHeight(ROW_H)
        form.addRow(row_label("启动页面"), field_holder(self.ck_startup_page))

        gen_lay.addLayout(form)
        gen_lay.addStretch(1)
        self.tabs.addTab(page_general, "通用")
        self.tabs.addTab(self._build_ocr_page(row_label), "OCR")
        self.tabs.addTab(self._build_translate_page(row_label), "翻译")
        root.addWidget(self.tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        btn_save = QPushButton("保存设置", self)
        btn_save.setObjectName("Primary")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_save)
        root.addLayout(btns)

        self.setMinimumHeight(self.sizeHint().height())

    # ---------------- OCR 分页 ----------------
    def _build_ocr_page(self, row_label) -> QWidget:
        """OCR 页：引擎状态 + 默认精度档 + 组件包安装/移除。"""
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 14, 4, 6)
        lay.setSpacing(12)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        # 系统 OCR 引擎状态
        self.lbl_win_status = QLabel("检测中…", page)
        self.lbl_win_status.setObjectName("Row")
        self.lbl_win_status.setFont(theme.font(theme.FONT_BODY_SIZE))
        self.btn_lang = QPushButton("打开语言设置", page)
        self.btn_lang.setMinimumHeight(28)
        self.btn_lang.clicked.connect(self._open_lang_settings)
        btn_refresh = QPushButton("刷新状态", page)
        btn_refresh.setMinimumHeight(28)
        btn_refresh.clicked.connect(self._refresh_ocr_status)
        win_row = QWidget(page)
        win_lay = QHBoxLayout(win_row)
        win_lay.setContentsMargins(0, 0, 0, 0)
        win_lay.setSpacing(8)
        win_lay.addWidget(self.lbl_win_status, 1)
        win_lay.addWidget(self.btn_lang)
        win_lay.addWidget(btn_refresh)
        win_row.setMinimumHeight(ROW_H)
        form.addRow(row_label("系统 OCR 引擎"), win_row)

        # 高精度组件包（体积 / 移除）
        self.lbl_pack = QLabel("检测中…", page)
        self.lbl_pack.setObjectName("Row")
        self.lbl_pack.setFont(theme.font(theme.FONT_BODY_SIZE))
        self.btn_pack_install = QPushButton("安装…", page)
        self.btn_pack_install.setMinimumHeight(28)
        self.btn_pack_install.clicked.connect(self._install_pack)
        self.btn_pack_remove = QPushButton("移除", page)
        self.btn_pack_remove.setMinimumHeight(28)
        self.btn_pack_remove.clicked.connect(self._remove_pack)
        pack_row = QWidget(page)
        pack_lay = QHBoxLayout(pack_row)
        pack_lay.setContentsMargins(0, 0, 0, 0)
        pack_lay.setSpacing(8)
        pack_lay.addWidget(self.lbl_pack, 1)
        pack_lay.addWidget(self.btn_pack_install)
        pack_lay.addWidget(self.btn_pack_remove)
        pack_row.setMinimumHeight(ROW_H)
        form.addRow(row_label("高精度组件包"), pack_row)

        # 默认精度档
        self._engine = self._pick_engine(self.cfg.get("ocr_engine"))
        eng_box = QWidget(page)
        eng_lay = QHBoxLayout(eng_box)
        eng_lay.setContentsMargins(0, 0, 0, 0)
        eng_lay.setSpacing(8)
        self._engine_group = QButtonGroup(self)
        self._engine_group.setExclusive(True)
        for idx, (key, text) in enumerate(C_OCR_ENGINE):
            b = QPushButton(text, eng_box)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(key == self._engine)
            self._engine_group.addButton(b, idx)
            eng_lay.addWidget(b)
        eng_lay.addStretch(1)
        eng_box.setMinimumHeight(ROW_H)
        form.addRow(row_label("默认精度档"), eng_box)

        lay.addLayout(form)

        note = QLabel(
            "默认「自动」= 优先使用本机 Windows 系统 OCR，零模型、速度最快；"
            "系统缺少中文（简体）OCR 语言包时，才自动改用你安装的高精度组件包。"
            "组件包装好后需在此手动选「高精度组件包」并保存，之后启动才会使用它。"
            "两者都没有时，点击「提取文字」会弹提示并提供下载按钮。",
            page,
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        note.setFont(theme.font(theme.FONT_SM_SIZE))
        lay.addWidget(note)
        lay.addStretch(1)

        self._refresh_ocr_status()
        return page

    # ---------------- 翻译分页 ----------------
    def _build_translate_page(self, row_label) -> QWidget:
        """翻译页：服务商 + 目标语言 + 密钥（或读环境变量）+ 连通测试。"""
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 14, 4, 6)
        lay.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        def wrap(widgets, stretch_index: int = 0) -> QWidget:
            box = QWidget(page)
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            for i, w in enumerate(widgets):
                h.addWidget(w, 1 if i == stretch_index else 0)
            box.setMinimumHeight(ROW_H)
            return box

        def secret(value, placeholder: str) -> QLineEdit:
            ed = QLineEdit(str(value or ""), page)
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setPlaceholderText(placeholder)
            ed.setMinimumHeight(28)
            return ed

        def plain(value, placeholder: str) -> QLineEdit:
            ed = QLineEdit(str(value or ""), page)
            ed.setPlaceholderText(placeholder)
            ed.setMinimumHeight(28)
            return ed

        # 服务商（决定下面显示哪几行密钥）
        self.cb_provider = QComboBox(page)
        for pid, label in translate_qt.PROVIDERS:
            self.cb_provider.addItem(label, pid)
        p_idx = self.cb_provider.findData(str(self.cfg.get("translate_provider") or "baidu"))
        self.cb_provider.setCurrentIndex(max(0, p_idx))
        self.cb_provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow(row_label("翻译服务"), wrap([self.cb_provider]))

        # 目标语言
        self.cb_target = QComboBox(page)
        for tid, label in translate_qt.TARGETS:
            self.cb_target.addItem(label, tid)
        t_idx = self.cb_target.findData(str(self.cfg.get("translate_target") or "auto"))
        self.cb_target.setCurrentIndex(max(0, t_idx))
        form.addRow(row_label("目标语言"), wrap([self.cb_target]))

        self.ck_env = QCheckBox("输入框留空时，自动读取本机环境变量", page)
        self.ck_env.setChecked(bool(self.cfg.get("translate_env_enabled", True)))
        self.ck_env.setMinimumHeight(ROW_H)
        self.ck_env.toggled.connect(lambda _=False: self._refresh_env_status())
        form.addRow(row_label("环境变量"), wrap([self.ck_env]))

        self._tr_rows: dict[str, tuple[QLabel, QWidget]] = {}

        def add_field(key: str, text: str, widget: QWidget) -> None:
            lab = row_label(text)
            box = wrap([widget])
            form.addRow(lab, box)
            self._tr_rows[key] = (lab, box)

        self.ed_baidu_appid = plain(self.cfg.get("translate_baidu_appid"), "百度翻译开放平台的 APPID")
        add_field("baidu_appid", "百度 APPID", self.ed_baidu_appid)
        self.ed_baidu_key = secret(self.cfg.get("translate_baidu_key"), "百度翻译开放平台「管理控制台」里的密钥")
        add_field("baidu_key", "百度密钥", self.ed_baidu_key)

        self.ed_baidu_apikey = secret(
            self.cfg.get("translate_baidu_apikey"),
            "选填：管理控制台 → API Key 管理 里创建的 Key（走 Bearer 鉴权）")
        add_field("baidu_apikey", "百度 API Key", self.ed_baidu_apikey)

        self.cb_baidu_model = QComboBox(page)
        self.cb_baidu_model.addItem("大模型翻译（llm）", "llm")
        self.cb_baidu_model.addItem("机器翻译（nmt）", "nmt")
        _m = self.cb_baidu_model.findData(str(self.cfg.get("translate_baidu_model") or "llm"))
        self.cb_baidu_model.setCurrentIndex(max(0, _m))
        self.cb_baidu_model.currentIndexChanged.connect(lambda _=None: self._sync_baidu_model())
        add_field("baidu_model", "百度模型", self.cb_baidu_model)

        self.ed_baidu_reference = plain(
            self.cfg.get("translate_baidu_reference"),
            "翻译指令，如「保持专业术语，语气正式」（仅大模型模式，500 字以内）")
        self.ed_baidu_reference.setMaxLength(500)
        add_field("baidu_reference", "翻译指令", self.ed_baidu_reference)

        self.ed_azure_key = secret(self.cfg.get("translate_azure_key"), "Azure 翻译资源的密钥（KEY 1 / KEY 2）")
        add_field("azure_key", "Azure 密钥", self.ed_azure_key)
        self.ed_azure_region = plain(self.cfg.get("translate_azure_region"), "如 eastasia；全球资源可留空")
        add_field("azure_region", "Azure 区域", self.ed_azure_region)

        self.ed_deepl_key = secret(self.cfg.get("translate_deepl_key"), "免费版 Key 以 :fx 结尾")
        add_field("deepl_key", "DeepL Key", self.ed_deepl_key)

        self.ed_libre_url = plain(self.cfg.get("translate_libre_url"), "http://127.0.0.1:5000")
        add_field("libre_url", "LibreTranslate", self.ed_libre_url)

        self.ck_show = QCheckBox("显示密钥明文", page)
        self.ck_show.setMinimumHeight(ROW_H)
        self.ck_show.toggled.connect(self._toggle_secret_echo)
        form.addRow(row_label("密钥展示"), wrap([self.ck_show]))

        self.lbl_tr_test = QLabel("未测试", page)
        self.lbl_tr_test.setObjectName("Hint")
        self.lbl_tr_test.setWordWrap(True)
        self.lbl_tr_test.setFont(theme.font(theme.FONT_SM_SIZE))
        self.btn_tr_test = QPushButton("测试翻译", page)
        self.btn_tr_test.setMinimumHeight(28)
        self.btn_tr_test.clicked.connect(self._test_translate)
        form.addRow(row_label("连通测试"), wrap([self.lbl_tr_test, self.btn_tr_test]))

        lay.addLayout(form)

        self.lbl_baidu_hint = QLabel(
            "百度说明：APPID 与密钥在百度翻译开放平台「开发者信息」页查看，"
            "API Key 在「管理控制台 → API Key 管理」创建。"
            "填 API Key 时走 Bearer 鉴权（无需 salt/sign）；只填 APPID + 密钥时走 sign 鉴权。"
            "「百度大模型翻译」支持 llm / nmt 两种模型，翻译指令仅 llm 生效。",
            page,
        )
        self.lbl_baidu_hint.setObjectName("Hint")
        self.lbl_baidu_hint.setWordWrap(True)
        self.lbl_baidu_hint.setFont(theme.font(theme.FONT_SM_SIZE))
        lay.addWidget(self.lbl_baidu_hint)

        self.w_pic_test = QWidget(page)
        pic_row = QHBoxLayout(self.w_pic_test)
        pic_row.setContentsMargins(0, 0, 0, 0)
        pic_row.setSpacing(8)
        self.btn_pic_test = QPushButton("图片翻译测试", page)
        self.btn_pic_test.setMinimumHeight(28)
        self.btn_pic_test.setToolTip(
            "现场生成一张含中英文的测试图，调用百度图片翻译接口，验证 APPID 与 API Key 是否可用")
        self.btn_pic_test.clicked.connect(self._test_picture_translate)
        self.lbl_pic_test = QLabel("", page)
        self.lbl_pic_test.setObjectName("Hint")
        self.lbl_pic_test.setWordWrap(True)
        self.lbl_pic_test.setFont(theme.font(theme.FONT_SM_SIZE))
        pic_row.addWidget(self.btn_pic_test, 0)
        pic_row.addWidget(self.lbl_pic_test, 1)
        lay.addWidget(self.w_pic_test)

        self.lbl_env = QLabel("", page)
        self.lbl_env.setObjectName("Hint")
        self.lbl_env.setWordWrap(True)
        self.lbl_env.setFont(theme.font(theme.FONT_SM_SIZE))
        lay.addWidget(self.lbl_env)

        note = QLabel(
            "用法：识别窗工具行有「翻译选中」「翻译全部」。先在正文里选中一段再点前者，"
            "点后者翻整篇；译文出现在正文下方，分隔条可拖动，窗口右下角可手动拉大拉小，"
            "内容超出时各自带滚动条。\n"
            "密钥优先级：本页填写 > 本机环境变量 > 未配置。环境变量名：SCREENSNAP_BAIDU_APPID、"
            "SCREENSNAP_BAIDU_KEY、SCREENSNAP_BAIDU_APIKEY、SCREENSNAP_AZURE_KEY、SCREENSNAP_AZURE_REGION、"
            "SCREENSNAP_DEEPL_KEY、SCREENSNAP_LIBRE_URL（同时兼容 BAIDU_TRANSLATE_APPID、"
            "BAIDU_TRANSLATE_KEY、BAIDU_TRANSLATE_APIKEY、AZURE_TRANSLATOR_KEY、AZURE_TRANSLATOR_REGION、"
            "DEEPL_AUTH_KEY、LIBRETRANSLATE_URL）。设置环境变量后需重启 ScreenSnap 才读到。\n"
            "没有 Key 时可先选「MyMemory（免 Key，量小）」验证链路；百度翻译个人实名后每月有免费额度，"
            "Azure F0 每月 200 万字符，DeepL 免费版 50 万字符/月；LibreTranslate 自建后完全离线、不外发。",
            page,
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        note.setFont(theme.font(theme.FONT_SM_SIZE))
        lay.addWidget(note)
        lay.addStretch(1)

        self._on_provider_changed()

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _on_provider_changed(self) -> None:
        """按服务商显示/隐藏对应的密钥行，避免一堆用不上的输入框。"""
        pid = str(self.cb_provider.currentData() or "baidu")
        need = {
            "baidu": ("baidu_appid", "baidu_key", "baidu_apikey"),
            "baidu_llm": ("baidu_appid", "baidu_key", "baidu_apikey",
                          "baidu_model", "baidu_reference"),
            "azure": ("azure_key", "azure_region"),
            "deepl": ("deepl_key",),
            "libre": ("libre_url",),
            "mymemory": (),
        }.get(pid, ())
        for key, (lab, box) in getattr(self, "_tr_rows", {}).items():
            on = key in need
            lab.setVisible(on)
            box.setVisible(on)
        if hasattr(self, "ed_baidu_apikey"):
            if pid == "baidu_llm":
                self.ed_baidu_apikey.setPlaceholderText(
                    "选填：管理控制台 → API Key 管理 里创建的 Key（走 Bearer 鉴权，大模型翻译必填）")
            else:
                self.ed_baidu_apikey.setPlaceholderText(
                    "选填：用于 Bearer 鉴权与图片翻译；管理控制台 → API Key 管理 创建")
        if hasattr(self, "w_pic_test"):
            self.w_pic_test.setVisible(pid in ("baidu", "baidu_llm"))
        if hasattr(self, "lbl_baidu_hint"):
            self.lbl_baidu_hint.setVisible(pid in ("baidu", "baidu_llm"))
        self._sync_baidu_model()
        if hasattr(self, "lbl_tr_test"):
            self.lbl_tr_test.setStyleSheet("color: %s;" % theme.INK_3)
            self.lbl_tr_test.setText("未测试")
        self._refresh_env_status()

    def _sync_baidu_model(self) -> None:
        """翻译指令（reference）只在大模型 llm 模式下可用。"""
        if not hasattr(self, "ed_baidu_reference") or not hasattr(self, "cb_baidu_model"):
            return
        llm = str(self.cb_baidu_model.currentData() or "llm") == "llm"
        self.ed_baidu_reference.setEnabled(llm)
        if llm:
            self.ed_baidu_reference.setPlaceholderText(
                "翻译指令，如「保持专业术语，语气正式」（仅大模型模式，500 字以内）")
        else:
            self.ed_baidu_reference.setPlaceholderText("机器翻译（nmt）模式不支持翻译指令")

    def _refresh_env_status(self) -> None:
        """显示当前进程真实读到的翻译环境变量（值做了脱敏）。"""
        if not hasattr(self, "lbl_env") or not hasattr(self, "ck_env"):
            return
        if not self.ck_env.isChecked():
            self.lbl_env.setText("环境变量读取已关闭：只使用本页填写的密钥。")
            return
        found = translate_qt.env_status(self._collect_translate())
        if not found:
            self.lbl_env.setText("当前进程没有读到翻译相关环境变量（只使用本页填写的密钥）。")
            return
        self.lbl_env.setText("已读到环境变量：" + "、".join("%s=%s" % (k, v) for k, v in found))

    def _collect_translate(self) -> dict:
        """把翻译页当前输入整理成配置副本（保存与测试共用同一份）。"""
        cfg = dict(self.cfg)
        cfg["translate_provider"] = str(self.cb_provider.currentData() or "baidu")
        cfg["translate_target"] = str(self.cb_target.currentData() or "auto")
        cfg["translate_env_enabled"] = bool(self.ck_env.isChecked())
        cfg["translate_baidu_appid"] = self.ed_baidu_appid.text().strip()
        cfg["translate_baidu_key"] = self.ed_baidu_key.text().strip()
        cfg["translate_baidu_apikey"] = self.ed_baidu_apikey.text().strip()
        cfg["translate_baidu_model"] = str(self.cb_baidu_model.currentData() or "llm")
        cfg["translate_baidu_reference"] = self.ed_baidu_reference.text().strip()[:500]
        cfg["translate_azure_key"] = self.ed_azure_key.text().strip()
        cfg["translate_azure_region"] = self.ed_azure_region.text().strip()
        cfg["translate_deepl_key"] = self.ed_deepl_key.text().strip()
        cfg["translate_libre_url"] = self.ed_libre_url.text().strip()
        return cfg

    def _toggle_secret_echo(self, on: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        for ed in (self.ed_baidu_key, self.ed_baidu_apikey, self.ed_azure_key, self.ed_deepl_key):
            ed.setEchoMode(mode)

    def _test_translate(self) -> None:
        """按当前填写实翻一句，验证密钥与网络通不通。"""
        cfg = self._collect_translate()
        pid = cfg["translate_provider"]
        cred = translate_qt.credentials(pid, cfg)
        if not cred["ok"]:
            self.lbl_tr_test.setStyleSheet("color: #C0392B;")
            self.lbl_tr_test.setText("还差配置：%s" % (cred["hint"] or "请填写密钥"))
            return
        self.btn_tr_test.setEnabled(False)
        self.lbl_tr_test.setStyleSheet("color: %s;" % theme.INK_3)
        self.lbl_tr_test.setText("测试中…")
        self._tr_test_task = translate_qt.TranslateTask(
            "Hello, world. 你好，世界。", cfg, parent=self)
        self._tr_test_task.done.connect(self._on_test_done)
        self._tr_test_task.failed.connect(self._on_test_failed)
        self._tr_test_task.start()

    def _on_test_done(self, result: str, meta: str) -> None:
        self.btn_tr_test.setEnabled(True)
        self.lbl_tr_test.setStyleSheet("color: #1E7B45;")
        self.lbl_tr_test.setText("通过 · %s（%s）" % (result.replace("\n", " "), meta))

    def _on_test_failed(self, msg: str) -> None:
        self.btn_tr_test.setEnabled(True)
        self.lbl_tr_test.setStyleSheet("color: #C0392B;")
        self.lbl_tr_test.setText("失败：%s" % msg)

    # ------------------------------------------------ 图片翻译测试（百度）
    @staticmethod
    def _make_picture_sample() -> bytes:
        """现场画一张 480x200 的白底测试图（含中文与英文），返回 PNG 字节，不落盘。"""
        img = QImage(480, 200, QImage.Format_RGB32)
        img.fill(QColor("#FFFFFF"))
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QColor("#111111"))
            f_zh = QFont("Microsoft YaHei")
            f_zh.setPixelSize(34)
            p.setFont(f_zh)
            p.drawText(40, 80, "你好，世界")
            f_en = QFont("Arial")
            f_en.setPixelSize(30)
            p.setFont(f_en)
            p.drawText(40, 140, "Hello")
        finally:
            p.end()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data

    def _test_picture_translate(self) -> None:
        """用现场生成的测试图调一次百度图片翻译，只验证凭据与链路，不写盘、不改配置。"""
        cfg = self._collect_translate()
        cred = translate_qt.credentials("baidu_llm", cfg)
        fields = cred.get("fields") or {}
        if not str(fields.get("apikey") or "").strip():
            self.lbl_pic_test.setStyleSheet("color: #C0392B;")
            self.lbl_pic_test.setText("还差配置：图片翻译需在「百度 API Key」中填写 API Key")
            return
        if not str(fields.get("appid") or "").strip():
            self.lbl_pic_test.setStyleSheet("color: #C0392B;")
            self.lbl_pic_test.setText("还差配置：请填写百度 APPID")
            return
        try:
            raw = self._make_picture_sample()
        except Exception as e:
            self.lbl_pic_test.setStyleSheet("color: #C0392B;")
            self.lbl_pic_test.setText("测试图生成失败：%s" % e)
            return
        self.btn_pic_test.setEnabled(False)
        self.lbl_pic_test.setStyleSheet("color: %s;" % theme.INK_3)
        self.lbl_pic_test.setText("图片翻译测试中…")
        self._pic_test_task = translate_qt.PictureTranslateTask(raw, cfg, paste=2, parent=self)
        self._pic_test_task.done.connect(self._on_pic_test_done)
        self._pic_test_task.failed.connect(self._on_pic_test_failed)
        self._pic_test_task.start()

    def _on_pic_test_done(self, result: dict) -> None:
        self.btn_pic_test.setEnabled(True)
        items = (result or {}).get("contents") or []
        dst = " / ".join((i.get("dst") or "").strip() for i in items
                         if (i.get("dst") or "").strip())
        if not dst:
            dst = (result or {}).get("dst") or ""
            dst = str(dst).replace("\n", " ").strip()
        self.lbl_pic_test.setStyleSheet("color: #1E7B45;")
        self.lbl_pic_test.setText("通过 · %d 条 · %s" % (len(items), dst or "（接口未返回译文）"))

    def _on_pic_test_failed(self, msg: str) -> None:
        self.btn_pic_test.setEnabled(True)
        self.lbl_pic_test.setStyleSheet("color: #C0392B;")
        self.lbl_pic_test.setText("失败：%s" % msg)

    def _refresh_ocr_status(self) -> None:
        """重新探测引擎与组件包状态（安装/移除语言包后可手动刷新）。"""
        try:
            infos = {i["id"]: i for i in ocr_qt.available_backends()}
        except Exception:
            infos = {}
        status = (infos.get("winocr") or {}).get("status", "no_module")
        text, color = {
            "ok": ("可用（中文识别包已安装）", theme.INK_2),
            "no_zh_pack": ("缺少中文（简体）OCR 语言包", "#C0392B"),
            "no_engine": ("系统未安装任何 OCR 语言包", "#C0392B"),
            "no_module": ("运行库缺失，无法启用", "#C0392B"),
        }.get(status, ("不可用", "#C0392B"))
        self.lbl_win_status.setText(text)
        self.lbl_win_status.setStyleSheet("color: %s;" % color)
        self.btn_lang.setVisible(status != "ok")

        try:
            pack = ocr_qt.pack_info()
        except Exception:
            pack = {"installed": False, "size_mb": 0}
        if pack.get("installed"):
            self.lbl_pack.setText("已安装 · %.1f MB" % float(pack.get("size_mb") or 0))
            self.lbl_pack.setStyleSheet("color: %s;" % theme.INK_2)
        else:
            self.lbl_pack.setText("未安装（离线包约 100 MB）")
            self.lbl_pack.setStyleSheet("color: %s;" % theme.INK_3)
        self.btn_pack_remove.setEnabled(bool(pack.get("installed")))

    def _open_lang_settings(self) -> None:
        ocr_qt.open_language_settings()

    def _install_pack(self) -> None:
        from ui_qt.ocr_result_qt import install_pack_modal

        if install_pack_modal(self):
            self._refresh_ocr_status()

    def _remove_pack(self) -> None:
        ok = QMessageBox.question(
            self,
            "移除高精度组件包",
            "将删除已安装的高精度 OCR 组件包，删除后只能使用系统 OCR。确定移除？",
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            ocr_qt.remove_pack()
            ocr_qt.reset_backend_cache()
        except Exception:
            pass
        self._refresh_ocr_status()

    # ---------------- 交互 ----------------
    @staticmethod
    def _pick_capacity(value) -> int:
        """把配置值归一到 5 / 7 / 10 中最接近的一档。"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 7
        return min(C_CAPACITY, key=lambda c: (abs(c - n), -c))

    @staticmethod
    def _pick_fps(value) -> int:
        """把配置值归一到 24 / 30 / 60 / 120 中最接近的一档。"""
        try:
            n = int(value)
        except (TypeError, ValueError):
            return 30
        return min(C_FPS, key=lambda c: (abs(c - n), c))

    @staticmethod
    def _pick_engine(value) -> str:
        """把配置值归一到 auto / rapidocr / winocr。"""
        v = str(value or "auto").strip().lower()
        return v if v in [k for k, _ in C_OCR_ENGINE] else "auto"

    def _choose_root(self) -> None:
        start = self.ed_root.text().strip() or config.DEFAULT_SAVE_ROOT
        path = QFileDialog.getExistingDirectory(self, "选择截图保存目录", start)
        if path:
            self.ed_root.setText(path)

    def _save(self) -> None:
        self.cfg["hotkey"] = self.ed_hotkey.text().strip() or "Ctrl+Alt+A"
        root = self.ed_root.text().strip() or config.DEFAULT_SAVE_ROOT
        self.cfg["save_root"] = root
        try:
            os.makedirs(root, exist_ok=True)
        except Exception:
            pass
        self.cfg["save_format"] = self.cb_format.currentText()
        fps = self._fps_group.checkedId()
        self.cfg["record_fps"] = int(fps if fps in C_FPS else self._fps)
        self.cfg["trash_days"] = int(self.sp_trash.value())
        checked = self._cap_group.checkedId()
        self.cfg["column_capacity"] = int(checked if checked in C_CAPACITY else self._cap)
        self.cfg["auto_copy"] = bool(self.ck_copy.isChecked())
        self.cfg["auto_start"] = bool(self.ck_start.isChecked())
        self.cfg["startup_show_panel"] = bool(self.ck_startup_page.isChecked())
        idx = self._engine_group.checkedId()
        self.cfg["ocr_engine"] = (
            C_OCR_ENGINE[idx][0] if 0 <= idx < len(C_OCR_ENGINE) else self._engine
        )
        ocr_qt.reset_backend_cache()
        self.cfg.update(self._collect_translate())

        try:
            config.save_config(self.cfg)
        except Exception:
            pass
        set_auto_start(self.cfg["auto_start"])
        self.accept()


def open_settings(parent=None, cfg: dict | None = None) -> dict | None:
    """模态打开设置页，保存则返回新配置，取消返回 None。"""
    dlg = SettingsDialog(parent, cfg)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dict(dlg.cfg)
    return None
