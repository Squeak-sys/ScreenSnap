# ScreenSnap 文档索引

本目录存放开发过程中的记录与设计源文件，**不参与 PyInstaller 打包**（spec 只收 `assets/`）。面向使用者的说明请看根目录 [README.md](../README.md)。

## 一、目录约定

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `acceptance/` | 阶段性验收记录、实测结论、打包体积与自检数据 | 需求草稿、临时日志 |
| `design/` | 设计源文件：图标 SVG、生成脚本、预览页 | 运行时资源（运行时资源统一在 `assets/`） |

命名规则：`acceptance/P<阶段号>-<主题>.md`（如 `P9-OCR验收.md`）；日期统一 `YYYY-MM-DD`。

## 二、文档清单

| 文档 | 主题 | 要点 |
|---|---|---|
| [acceptance/P9-OCR验收.md](acceptance/P9-OCR验收.md) | OCR 文字提取（P9） | 双引擎方案：Windows 系统 OCR 内置、RapidOCR 作为可选组件包；主包体积增量、组件包隔离方式、打包 excludes 清单、五阶段验收结论 |
| [acceptance/P10-收尾整理与安装包验收.md](acceptance/P10-收尾整理与安装包验收.md) | 收尾整理与 v1.0.0 安装包（P10） | 资源裁剪前后对比（assets 56→22）、文档归置、重打包数据、Inno Setup 中文安装包体积与静默安装/卸载验证结论 |
| [design/icons/gen_tool_icons.py](design/icons/gen_tool_icons.py) | 工具条图标生成脚本 | SVG 单一数据源 → 渲染 20px PNG 到 `assets/icons`，含透明底校验 |
| [design/icons/svg/](design/icons/svg/) | 图标矢量源 | 24 网格、1.8px 圆头描边，与界面图标集风格一致；`新图标预览.html` 可浏览器直接打开预览 |

## 三、图标再生成

改了 SVG 或新增图标后，在 `docs/design/icons` 下执行：

```powershell
cd E:\PythonProjects\Python\ScreenSnap\docs\design\icons
E:\PythonProjects\Python\.venv\Scripts\python.exe gen_tool_icons.py
```

脚本用本机 Chrome / Edge 无头渲染（`find_chrome()` 自动定位），产物直接写入 `<项目根>\assets\icons`，只生成运行时真正加载的 `tool_<name>.png`（20px、透明底、ink 单色）。选中态颜色由 `ui_qt/theme.qss` 着色实现，不需要额外的 accent 图。

## 四、维护约定

1. 新增功能或改动打包配置时，同步更新根 `README.md` 的「功能」「目录结构」「构建与打包」三节。
2. 每次完整打包验证（含静默安装 / 卸载）后，把实测数据补进对应阶段验收文档，不要新建零散笔记。
3. 文档里引用的路径一律写绝对路径或相对仓库根的路径，不写个人机器上的临时目录。
