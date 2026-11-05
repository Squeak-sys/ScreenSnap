---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d61c21c24e7ef9b8faace3b19fc9bf98_4431f758d92f11f19280525400393706
    ReservedCode1: +GdSgfUIvn2gjEkihPjAUvsIDkUpN+3ng0NKJFDoi/ds/qt0fQJnY79m95NKOX/AbLJz4QODU9wwdA8khje7TDGu4RO1Tx8HiP2cZNgVX2YBMtuNRbPxA4hCyoY6l6oLro9I0B6uGGu006EMADHWygUgE517ouQFr1jrchiYeeOIJeu+io/S3TDvBWQ=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d61c21c24e7ef9b8faace3b19fc9bf98_4431f758d92f11f19280525400393706
    ReservedCode2: +GdSgfUIvn2gjEkihPjAUvsIDkUpN+3ng0NKJFDoi/ds/qt0fQJnY79m95NKOX/AbLJz4QODU9wwdA8khje7TDGu4RO1Tx8HiP2cZNgVX2YBMtuNRbPxA4hCyoY6l6oLro9I0B6uGGu006EMADHWygUgE517ouQFr1jrchiYeeOIJeu+io/S3TDvBWQ=
---



# ScreenSnap（PySide6 版）

Windows 桌面截图与标注工具：热键唤起全屏选区 → 编辑器逐笔标注 → 一键落盘、复制到剪贴板或钉在屏幕原位，并内置 Windows 系统 OCR、图片文字翻译、屏幕录制与截图历史库。

- 当前版本：**1.0.0**（`config.APP_VERSION`）
- 入口文件：`main_qt.py`
- 运行形态：托盘常驻程序（单实例），关闭主面板即收进托盘
- 技术栈：Python 3.13 + PySide6 6.12（原 Tkinter 版 `main.py` 已下线，仓库不再包含 tk 代码与 pystray 依赖）

---

## 一、功能

| 功能 | 说明 |
|---|---|
| 截图选区 | 全屏遮罩层：拖拽框选 / 单击自动识别窗口 / 双击全屏，Esc 取消；支持多显示器与高 DPI（按物理像素换算） |
| 选区调整 | 进入编辑器后可先调整选区（8 个手柄 + 整体拖动），再切标注工具 |
| 标注编辑 | 框选、圆选、划线、画笔、马赛克、文字、箭头 7 种工具；线宽可调、Ctrl+Z 逐步撤销；「完成」才落盘，Esc 取消不产生文件 |
| 贴图（钉图） | 「贴图」把当前截图钉在屏幕原位，同时存档进历史库；贴图窗可缩放、复制、另存、关闭 |
| OCR 文字提取 | 双引擎：Windows 系统 OCR（内置，零模型体积）与 RapidOCR（高精度，按需安装组件包）；结果窗支持逐行选取、复制全部 |
| 图片翻译 | 编辑器「图片翻译」把选区图像交百度图片翻译接口，译文图回贴原位并钉图，可「原文 / 译文」一键切换 |
| 屏幕录制 | 主面板「录制」或编辑器工具条「屏幕录制」：选区 → 3-2-1 倒计时 → ffmpeg gdigrab 录制 → 控制条计时/停止；mp4 存到 `保存目录\录屏\日期`，结束自动登记历史库 |
| 历史库 | 按日期列浏览截图与录屏，缩略图、大图查看、复制、删除（进回收站）、打开所在文件夹、内置视频播放 |
| 托盘与热键 | 托盘菜单：截图 / 历史库 / 设置 / 退出；全局热键默认 `Ctrl+Alt+A`，设置页改完即时生效无需重启 |
| 设置 | 截图热键、保存目录、图片格式、录屏帧率、回收站保留天数、历史库列容量、自动复制、开机自启、启动页面、OCR 引擎与组件包、翻译服务/Azure/DeepL/LibreTranslate、目标语言、密钥连通测试 |
| 诊断 | 启动信息面板（显示器 / OCR / 翻译 / 版本）+「诊断信息」一键复制多屏与 DPI 详情 |

---

## 二、运行环境与依赖

- 操作系统：Windows 10 1809+ / Windows 11（系统 OCR 依赖 `Windows.Media.Ocr` 与对应语言包）
- Python：3.13（本机虚拟环境 `E:\PythonProjects\Python\.venv`）
- 必装依赖：

| 包 | 版本（本机验证） | 用途 |
|---|---|---|
| PySide6 | 6.12.0 | 全部界面（Widgets / Gui） |
| Pillow | 12.3.0 | 图像处理、缩略图、剪贴板位图 |
| winrt-runtime | 4.0.3 | WinRT 投影运行时 |
| winrt-Windows.Media.Ocr 等 7 个 `winrt-Windows.*` | 4!10.0.28000.2705 | Windows 系统 OCR |
| imageio-ffmpeg | 0.6.0 | 提供录屏所用 ffmpeg 可执行文件 |
| numpy | 2.5.1 | RapidOCR 组件包依赖 |

- 可选依赖（**不进主包**，由设置页「高精度组件包」按需安装到 `%APPDATA%\ScreenSnap\ocr_pack`）：`rapidocr` 3.10.0、`onnxruntime` 1.31.1
- 已移除的旧依赖：`tkinter`、`pystray`、`pystray` 相关的图标资源与菜单逻辑

安装依赖（在项目根目录执行）：

```powershell
E:\PythonProjects\Python\.venv\Scripts\python.exe -m pip install PySide6 Pillow imageio-ffmpeg numpy `
  winrt-runtime winrt-Windows.Foundation winrt-Windows.Foundation.Collections `
  winrt-Windows.Globalization winrt-Windows.Graphics.Imaging winrt-Windows.Media.Ocr `
  winrt-Windows.Storage winrt-Windows.Storage.Streams
```

---

## 三、快捷运行（开发调试）

```powershell
cd E:\PythonProjects\Python\ScreenSnap
E:\PythonProjects\Python\.venv\Scripts\python.exe main_qt.py
```

命令行参数：

| 参数 | 说明 |
|---|---|
| `--no-hotkey` | 不注册全局热键（避免与已运行实例冲突，调试用） |
| `--no-tray` | 不创建托盘图标，以普通窗口方式运行 |
| `--ocr-selftest <图片路径> [--ocr-selftest-out <json路径>]` | OCR 自检：不建 GUI，直接识别一张图并把结果写成 JSON，退出码 0 表示通过；用于验证冻结产物里 `winrt` 投影是否齐备 |

---

## 四、快捷键

| 场景 | 快捷键 | 动作 |
|---|---|---|
| 全局 | `Ctrl+Alt+A`（可在设置页改） | 唤起截图选区 |
| 选区层 | 拖拽 / 单击 / 双击 | 框选区域 / 截取窗口 / 全屏 |
| 选区层 | `Esc` | 取消截图 |
| 编辑器 | `Enter` | 完成（落盘 + 复制到剪贴板） |
| 编辑器 | `Esc` | 取消，不产生文件 |
| 编辑器 | `Ctrl+Z` | 撤销上一笔标注 |
| 编辑器 | `Ctrl+T` | 识别文字（OCR） |
| 编辑器 | `1` ~ `7` | 切换标注工具（框选/圆选/划线/画笔/马赛克/文字/箭头） |
| 历史库列表 | `F5` / `Ctrl+A` / `Ctrl+C` / `Enter` / `Delete` | 刷新 / 全选 / 复制 / 打开 / 删除到回收站 |
| 历史库大图 | `+` `-` `0` / `Esc` | 放大 / 缩小 / 适应窗口 / 关闭 |
| 贴图窗 | `Ctrl+C` / `Ctrl+S` / `Tab`·`Space` / `+` `-` / `Esc` | 复制 / 另存为 / 原文译文切换 / 缩放 / 关闭 |
| 录制控制条 | `Esc` | 结束录制并保存 |
| OCR 结果窗 | `Ctrl+Enter` / `Esc` | 复制全部文本 / 关闭 |

---

## 五、目录结构

```
ScreenSnap/
├─ main_qt.py              入口：托盘、热键桥接、截图/录制流程编排
├─ config.py               配置读写（config.json）、热键解析、版本号
├─ hotkey.py               Win32 RegisterHotKey 全局热键管理
├─ storage.py              文件落盘、缩略图、回收站清理、gallery.json 索引
├─ ScreenSnap-Qt.spec      PyInstaller 打包配置（含 winrt 收集与体积裁剪）
├─ assets/                 运行时资源（打包后位于 _internal/assets）
│  ├─ icon.ico             应用图标（16~256 多尺寸，窗口/任务栏/安装包共用）
│  ├─ icon.png             图标源图（ico 缺失时回退）
│  └─ icons/               工具条图标 18 个 + 微调箭头 2 个（PNG，20px）
├─ ui_qt/                  界面层
│  ├─ app.py               QApplication、图标、单实例、托盘、热键桥
│  ├─ capture_overlay.py   全屏选区遮罩层
│  ├─ editor.py            编辑器（工具条、标注绘制、撤销、选区调整）
│  ├─ sticker_qt.py        贴图窗（缩放、复制、另存、原文/译文切换）
│  ├─ ocr_qt.py            OCR 引擎抽象（Windows 系统 OCR / RapidOCR）
│  ├─ ocr_result_qt.py     OCR 结果窗
│  ├─ translate_qt.py      翻译服务（百度/百度大模型/Azure/DeepL/Libre/MyMemory）
│  ├─ recorder_qt.py       屏幕录制（选区、倒计时、ffmpeg、控制条）
│  ├─ video_player_qt.py   历史库内置视频播放
│  ├─ gallery_qt.py        截图历史库（列表 / 大图 / 回收站）
│  ├─ settings_qt.py       设置页
│  ├─ info_panel.py        主面板启动信息
│  ├─ dialogs.py           消息框与文件对话框封装
│  ├─ clipboard.py         剪贴板（图片/文本）
│  ├─ geometry.py          多屏与物理像素换算
│  ├─ imageutil.py         图像工具与字母兜底图标
│  ├─ theme.py / theme.qss 配色与全局样式
│  └─ __init__.py
├─ docs/                   过程文档（索引见 docs/README.md）
│  ├─ acceptance/          阶段性验收记录
│  └─ design/icons/        图标设计源（SVG + 生成脚本 + 预览页，不参与打包）
├─ build_qt/               PyInstaller 中间产物
├─ dist_qt/                打包产物：dist_qt\ScreenSnap-Qt\ScreenSnap-Qt.exe
└─ release/                安装包输出：ScreenSnap-Setup-1.0.0.exe
```

---

## 六、配置文件

- 位置：`%APPDATA%\ScreenSnap\config.json`（首次运行自动生成，缺项回落到 `config.DEFAULT_CONFIG`）
- 热键运行日志：`%APPDATA%\ScreenSnap\hotkey.log`（记录每次注册结果与 PID，便于排查热键冲突）
- 主要键：

| 键 | 默认值 | 说明 |
|---|---|---|
| `hotkey` | `Ctrl+Alt+A` | 截图热键 |
| `save_root` | `~/Pictures/ScreenSnap` | 存档根目录（截图按日期、录屏存 `录屏\日期`） |
| `save_format` | `png` | 图片格式（png / jpg） |
| `auto_copy` | `true` | 完成后自动复制到剪贴板 |
| `auto_start` | `true` | 写入注册表 Run 项实现开机自启 |
| `startup_show_panel` | `true` | 启动时是否弹出主面板（否则静默驻留托盘） |
| `trash_days` | `3` | 回收站保留天数 |
| `column_capacity` | `7` | 历史库每日列容量 |
| `record_fps` | `30` | 录屏帧率 |
| `ocr_engine` | `auto` | OCR 引擎（auto / 系统 OCR / RapidOCR） |
| `translate_provider` | `baidu` | 翻译服务商 |
| `translate_target` | `auto` | 目标语言（auto 表示中文→英文、其它语言→中文） |
| `translate_env_enabled` | `true` | 是否允许从环境变量读取密钥 |
| `translate_baidu_appid` / `translate_baidu_key` / `translate_baidu_apikey` / `translate_baidu_model` | 空 | 百度翻译 / 图片翻译凭据 |
| `translate_azure_key` / `translate_azure_region` / `translate_deepl_key` / `translate_libre_url` | 空 | 其它服务商凭据 |

---

## 七、构建与打包

### 1. 打成绿色版目录（PyInstaller）

```powershell
cd E:\PythonProjects\Python\ScreenSnap
# 结束可能占用产物的旧进程
Get-Process ScreenSnap-Qt -ErrorAction SilentlyContinue | Stop-Process -Force
E:\PythonProjects\Python\.venv\Scripts\pyinstaller.exe ScreenSnap-Qt.spec --noconfirm `
  --distpath dist_qt --workpath build_qt
```

- 产物：`dist_qt\ScreenSnap-Qt\ScreenSnap-Qt.exe`（onedir，资源在 `_internal\assets`）
- spec 要点：`datas=[(BASE+'/assets','assets')]` 整目录打包（避免资源被套一层子目录）；`icon=BASE+'/assets/icon.ico'`；`console=False`；`excludes` 已剔除 QtQml/QtQuick/QtWebEngine/QtMultimedia 等未用模块与 `rapidocr`、`onnxruntime` 整包

### 2. 打包后自检

```powershell
dist_qt\ScreenSnap-Qt\ScreenSnap-Qt.exe --ocr-selftest <一张含文字的图片> `
  --ocr-selftest-out temp\ocr_selftest.json
```

退出码 0 且 JSON 中 `text` 非空，说明冻结包内 Windows 系统 OCR 可用。

### 3. 制作中文安装包（Inno Setup 6）

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp ScreenSnap.iss
```

- 脚本：`ScreenSnap.iss`（仓库根目录）
- 产物：`release\ScreenSnap-Setup-1.0.0.exe`
- 安装向导仅简体中文（`Languages\ChineseSimplified.isl`），默认安装到 `{autopf}\ScreenSnap`，创建开始菜单与桌面快捷方式，写入卸载信息，LZMA2 压缩，安装包图标取 `assets\icon.ico`

### 4. 静默安装 / 卸载（验证与部署）

```powershell
# 静默安装到指定目录
release\ScreenSnap-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="D:\Temp\ScreenSnapTest"
# 静默卸载
& "D:\Temp\ScreenSnapTest\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

---

## 八、行为约定与限制

- **单实例**：重复启动只会提示「已在运行」，不新开窗口（`ui_qt.app.SingleInstance`）。
- **托盘不可用时**（远程桌面、精简系统）：自动退回普通窗口模式，程序不会因缺少托盘而退出。
- **截图/编辑窗口并发上限 2 个**，超出会忽略并在托盘气泡提示。
- **打包态默认静默驻留托盘**：是否开机弹面板由「启动页面」勾选框（`startup_show_panel`）控制。
- **录屏需要 ffmpeg**：由 `imageio-ffmpeg` 随包提供；缺少时录制入口会提示未启动。
- **图片翻译需要百度 APPID + API Key**，缺配置时设置页「连通测试」会直接给出缺口提示。
- 历史库删除走系统回收站；超过 `trash_days` 的回收站条目在下次打开历史库时自动清理。

---

## 九、文档索引

| 文档 | 说明 |
|---|---|
| [docs/README.md](docs/README.md) | 文档总索引与维护约定 |
| [docs/acceptance/P9-OCR验收.md](docs/acceptance/P9-OCR验收.md) | OCR 双引擎方案与打包体积、验收结论 |
| [docs/acceptance/P10-收尾整理与安装包验收.md](docs/acceptance/P10-收尾整理与安装包验收.md) | 资源裁剪与文档整理记录、v1.0.0 安装包体积与安装/卸载验证结论 |
| [docs/design/icons/](docs/design/icons/) | 工具条图标设计源（SVG 矢量 + `gen_tool_icons.py` 生成脚本 + 预览页） |

图标再生成（改了 SVG 或调整描边后）：

```powershell
cd docs\design\icons
E:\PythonProjects\Python\.venv\Scripts\python.exe gen_tool_icons.py   # 输出 PNG 到 assets\icons
```
*（内容由AI生成，仅供参考）*
