# -*- coding: utf-8 -*-
"""ScreenSnap 2.0 · SHUTTER（取景器）设计语言 —— 统一设计 Token。

色彩 / 圆角 / 字体 / 尺寸 集中管理；capture.py / editor.py / settings.py
统一引用本模块。历史库 gallery.py 不参与本次改造，不使用本模块。

配套设计文档：design/ScreenSnap-UI设计方案.md、design/prototype.html
"""

# ---------------- 色彩系统 ----------------
ACCENT = "#2F6DF6"            # 主色 · 快门蓝
ACCENT_HOVER = "#2559D6"      # 主按钮悬停
ACCENT_SOFT = "#E8EFFE"       # 选中底色 / 聚焦光环
SUCCESS = "#0FA958"           # 完成 / 绿色确认
SUCCESS_HOVER = "#0D9550"     # 完成按钮悬停
DANGER = "#E5484D"            # 删除 / 录制红点 / 关闭钮
DANGER_HOVER = "#D93B40"      # 危险悬停
WARN = "#F5A623"              # 冲突提示 / 即将过期
INK = "#1C2430"               # 一级文本
INK_2 = "#5B6472"             # 二级文本
INK_3 = "#9AA2AD"             # 三级文本 / 占位 / 快捷键角标
LINE = "#E3E6EA"              # 分割线 / 边框
SURFACE = "#FFFFFF"           # 窗口面板
SURFACE_2 = "#F7F8FA"         # 侧栏 / 输入底
SURFACE_3 = "#EEF0F3"         # 悬停底
CANVAS = "#F0F1F4"            # 页面 / 工作区底
TOOLBAR = "#FBFBFC"           # 悬浮工具栏底
MASK_RGB = (16, 22, 32)       # 截图遮罩 RGB（绘制时叠加 alpha 0.46）

# ---------------- 圆角 ----------------
R_WIN = 10                    # 窗口 / 大卡片
R_CARD = 10                   # 卡片
R_BTN = 8                     # 按钮 / 输入框
R_INPUT = 8
R_PILL = 999                  # 胶囊 / 开关 / 徽标

# ---------------- 字体 ----------------
FONT_FAMILY = "Microsoft YaHei UI"
FONT_DIGIT = "Segoe UI"       # 尺寸徽标 / 数字
FONT_MAIN = (FONT_FAMILY, 9)          # 通用控件
FONT_SM = (FONT_FAMILY, 8)            # 辅助 caption
FONT_BODY = (FONT_FAMILY, 10)         # 表单正文 / 设置行标题
FONT_SECTION = (FONT_FAMILY, 12, "bold")
FONT_TITLE = (FONT_FAMILY, 14, "bold")
FONT_BADGE = (FONT_DIGIT, 9, "bold")  # 尺寸徽标（12px / 600）

# ---------------- 尺寸 ----------------
TOOLBAR_H = 52                # 悬浮工具条高度
TOOL_BTN = 40                 # 工具按钮直径
TOOL_ICON = 24                # 图标显示尺寸
SWITCH_W = 40                 # Switch 宽
SWITCH_H = 22                 # Switch 高

# ---------------- 编辑器色板（10 常用色 + 自定义） ----------------
PALETTE = [
    (229, 72, 77),    # danger 红
    (245, 166, 35),   # warn 橙
    (242, 201, 76),   # 金黄
    (15, 169, 88),    # success 绿
    (47, 109, 246),   # accent 蓝
    (123, 97, 255),   # 紫
    (232, 121, 185),  # 粉
    (28, 36, 48),     # ink 黑
    (91, 100, 114),   # ink-2 灰
    (255, 255, 255),  # 白
]
PALETTE_NAMES = ["红", "橙", "黄", "绿", "蓝", "紫", "粉", "黑", "灰", "白"]

# ---------------- 线宽预设（1 / 2 / 4 / 6 / 8） ----------------
WIDTH_PRESETS = [1, 2, 4, 6, 8]
WIDTH_NAMES = {1: "极细", 2: "细", 4: "常规", 6: "粗", 8: "特粗"}


def hex_of(rgb):
    """RGB 三元组 -> '#RRGGBB'。"""
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def rgba_hex(rgb, alpha=0.46):
    """RGB 三元组 + alpha(0-1) -> '#RRGGBBAA'（Tk 8.6 支持 8 位 hex 颜色）。"""
    a = max(0, min(255, int(round(alpha * 255))))
    return "#%02x%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]), a)
