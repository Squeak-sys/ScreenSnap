---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d61c21c24e7ef9b8faace3b19fc9bf98_880c4734d59c11f180d5525400248c00
    ReservedCode1: CwIBYGFFZDWdG67BWh9TLteYM+7mwVheU1YLwyln4Y+26PQoEiDb9OLz9SilHn5cKnqRWQux3DsUIdpZuNI0dgGTt+ifCCvRoV7ZhVnpPWAaC6V9JlkOmPMZNEWpSuEn8ZoA0+obkNn1nJpQ6yt+44mByxivJMNwMBtjXAoWw0RVvyhXB5a5+TTVA+w=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d61c21c24e7ef9b8faace3b19fc9bf98_880c4734d59c11f180d5525400248c00
    ReservedCode2: CwIBYGFFZDWdG67BWh9TLteYM+7mwVheU1YLwyln4Y+26PQoEiDb9OLz9SilHn5cKnqRWQux3DsUIdpZuNI0dgGTt+ifCCvRoV7ZhVnpPWAaC6V9JlkOmPMZNEWpSuEn8ZoA0+obkNn1nJpQ6yt+44mByxivJMNwMBtjXAoWw0RVvyhXB5a5+TTVA+w=
---

# ScreenSnap P9（OCR 图片文字提取）验收文档

- 验收日期：2026-11-01
- 验收对象：`E:\PythonProjects\Python\ScreenSnap`（Qt 版，PySide6）
- 打包产物：`dist_qt\ScreenSnap-Qt\ScreenSnap-Qt.exe`
- 结论：**P9 五阶段全部完成，验收通过**（端到端链路已在打包版上实测）

---

## 一、方案落地：双引擎、体积零增长

| 引擎 | 形态 | 依赖 | 精度 | 耗时（1080p 中文文档） | 体积增量 |
|---|---|---|---|---|---|
| Windows 系统 OCR（winocr） | **内置**，主程序自带 | `winrt-*` 投影 + 系统 zh-CN OCR 语言包 | 中上，界面/文档文字好 | 约 0.6–1.0s | **+1.72 MB**（181.87 → 183.59 MB 全目录） |
| RapidOCR（PP-OCRv6） | **外挂组件包**，按需安装 | onnxruntime + 3 个 onnx 模型 | 高，中文/表格/代码稳 | 约 2.3s（含首次加载） | 不占主包（约 +40~60MB 单独目录） |

- 主包 `ScreenSnap-Qt.spec` 已 `excludes`：`rapidocr / onnxruntime / cv2 / shapely / pyclipper / yaml / omegaconf / tokenizers`，并 `hiddenimports` 收全 `winrt.*`（8 个文件入包：`_winrt.cp313-win_amd64.pyd` + 7 个 `_table.pywinrt`）。
- 组件包装到 `%APPDATA%\ScreenSnap\ocr_pack`，运行时 `_inject_pack_path()` 注入 `sys.path` 与 DLL 目录，装完即离线可用，可一键移除。
- `trim_qt_dist.py` 白名单只删 Qt 插件/翻译/PIL AVIF，不触碰 `winrt`，裁剪前后均为 183.59 MB。

## 二、交互入口（四处，全部可用）

| 入口 | 位置 | 触发方式 |
|---|---|---|
| 截图编辑器工具条 | 图钉按钮之后新增“识别文字”图标（`assets/icons/tool_ocr.png`，20×20 深灰线性风格） | 点击，或 **Ctrl+T** |
| 截图编辑器快捷 | `EditorWindow.keyPressEvent` | Ctrl+T（选区阶段识别选区，标注阶段识别整图） |
| 贴图窗 | 右键菜单 →「识别文字」 | 菜单项 |
| 历史库 | 列表右键（单张非视频）→「识别文字」；大图查看器按钮区 →「识别文字」 | 菜单项 / 按钮 |

## 三、结果窗能力

- 白底圆角无边框卡片，出现于工作区居中，Esc 关闭。
- 正文**可编辑**（识别后可人工纠错再复制）。
- 复制：全部 / 按行 / 合并单行；排版切换「按行 / 段落 / 代码」；Ctrl+S 保存 `.txt`。
- 顶部状态：`共 N 字 · 用时 x.xxs`、当前引擎名、换引擎按钮（系统 OCR ↔ RapidOCR）。
- 无 RapidOCR 时提示条提供「选择本地组件包(.zip)…」与「下载组件包」；已安装则显示「移除」。
- 识别文本回写历史库 `item['ocr_text']`（从历史库入口进入时生效），下次打开不用重识别。

## 四、实测记录

### 1. 源码版（编辑器链路）
- 样本：`doc_cn.png`（社保缴费基数申报说明）
- 结果：识别 **205 字**，RapidOCR，用时 **2.28s**，首行「社保缴费基数申报说明(2026年度)」正确，结果窗状态栏文案正确。

### 2. 打包版（端到端，UI 模拟）
1. 启动 `ScreenSnap-Qt.exe`（pid 44960），`hotkey.log` 记录 `Ctrl+Shift+A OK`；
2. 模拟热键 → 全屏遮罩 → 拖拽选区 → 编辑窗出现（工具条 13 个图标渲染正常）；
3. 编辑窗发送 **Ctrl+T** → 结果窗弹出并完成识别（使用内置 **Windows 系统 OCR**，包内不含 RapidOCR）。

| 项 | 结果 |
|---|---|
| 热键注册 | OK |
| 编辑窗工具条 | 新增识别按钮渲染正常，无裁切 |
| Ctrl+T → 结果窗 | 成功，文本非空 |
| 主包体积 | 183.59 MB（+1.72 MB） |
| 打包耗时 | 33.4s |

截图证据：`output\e2e_1_overlay.png`、`e2e_2_editor.png`、`e2e_3_result.png`、`editor_toolbar.png`、`ocr_from_editor.png`。

## 五、新增/修改文件

| 文件 | 变更 |
|---|---|
| `ui_qt/ocr_qt.py` | 新增（P9-1）：引擎抽象层、WinOcrBackend、RapidOcrBackend、文本清理与按行聚合、QThread 任务、组件包安装/下载/移除 |
| `ui_qt/ocr_result_qt.py` | 新增（P9-3）：结果窗、复制/保存/排版/换引擎/重识别、组件包引导 |
| `ui_qt/editor.py` | 工具条新增识别按钮、`request_ocr()`、Ctrl+T、`tool_icon("ocr")` |
| `ui_qt/gallery_qt.py` | 右键「识别文字」、`ocr_selected/ocr_path`、大图查看器 `ocr_text()` + 按钮 |
| `ui_qt/sticker_qt.py` | 右键菜单「识别文字」、`ocr_text()` |
| `assets/icons/tool_ocr.png` | 新增图标 |
| `ScreenSnap-Qt.spec` | 加 winrt hiddenimports、排除 RapidOCR 系依赖 |

## 六、遗留与待确认

1. **系统 OCR 语言包依赖**：目标机若未安装 zh-CN OCR 语言包，内置引擎会不可用，此时只能装组件包。建议设置页展示一行状态（当前未做，等确认）。
2. **组件包与 numpy**：主包 `excludes` 了 numpy，RapidOCR 组件包需自带 numpy（含 `_multiarray_umath.pyd`）。若用户从本地 zip 安装的包不含 numpy，会导入失败——需确认组件包分发内容。
3. **分发回退**：换机/无语言包场景的回退文案已就位（结果窗提示 + 组件包入口），但组件包下载地址尚未定（`download_pack(url)` 已留接口）。
4. 设置页暂无「OCR」分页（引擎状态、组件包体积/移除、默认精度档）。是否加入待老板确认。
*（内容由AI生成，仅供参考）*
