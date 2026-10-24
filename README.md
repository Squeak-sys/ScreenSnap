---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: d61c21c24e7ef9b8faace3b19fc9bf98_c59da1d5caf011f196f9525400248c00
    ReservedCode1: 4PYAkHrp1DiFlN2ceNkyE5KYzKe3c4X7HgZt7gP4RCaaXwshdj36wOjTgIeiWSBzqk4NR79PXrYjXFhfl8Maeb5YrjAg/tdt2JFtBQT5LcV0X3bo291e55VkbjJdP2c80Mcuo+g1i9wPaCQw8mZOnGmFtCrf0mYeyen1Tu6K0rdYqiPT7qBF47fMRkk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: d61c21c24e7ef9b8faace3b19fc9bf98_c59da1d5caf011f196f9525400248c00
    ReservedCode2: 4PYAkHrp1DiFlN2ceNkyE5KYzKe3c4X7HgZt7gP4RCaaXwshdj36wOjTgIeiWSBzqk4NR79PXrYjXFhfl8Maeb5YrjAg/tdt2JFtBQT5LcV0X3bo291e55VkbjJdP2c80Mcuo+g1i9wPaCQw8mZOnGmFtCrf0mYeyen1Tu6K0rdYqiPT7qBF47fMRkk=
---

# ScreenSnap

轻量、高效的 Windows 截图与贴图工具，支持全局热键截图、标注编辑、贴图与历史库管理。使用 Python + Tkinter 开发，无 Web 依赖，纯本地运行。

## 功能特性

- **全局热键截图**：默认 `Ctrl+Alt+A` 唤起全屏截图（可在设置中自定义，支持录制按键）
- **多种标注工具**：框选、椭圆选区、箭头、划线、手绘笔刷、马赛克、文字标注
- **调整选区模式**：截图后默认进入选区调整模式，支持 8 手柄缩放与位置移动
- **贴图功能**：截图后按指定键将选区贴到屏幕上，支持滚轮缩放、拖动、双击缩略
- **历史库**：自动保存截图历史，真无限画布浏览，支持按日期归档、拖拽排序、双击查看大图
- **大图查看**：内置大图查看器，支持缩放平移、二次编辑与确认保存
- **剪贴板联动**：截图后自动复制到剪贴板
- **回收站机制**：删除的截图进入本地回收站，可设置保留天数
- **单实例运行**：防止多开冲突
- **托盘常驻**：开机自启 + 系统托盘图标，随时唤起

## 安装与使用

### 方式一：安装包（推荐）

下载 `ScreenSnapSetup.exe`，运行后按向导完成安装（默认安装到 `Program Files`，数据保存在 `%APPDATA%\ScreenSnap`，卸载不影响个人数据）。

### 方式二：源码运行

```bash
pip install pillow pystray
python main.py
```

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+Alt+A` | 全局唤起截图（可自定义） |
| `Esc` | 取消截图 / 关闭窗口 |
| `Enter` | 确认 / 完成（文字输入时：第一次提交、第二次确认） |
| `Ctrl+Z` | 撤销上一步编辑 |

## 目录结构

```
ScreenSnap/
├── main.py         # 程序入口：单实例、热键、托盘
├── capture.py      # 全屏/窗口截图捕获
├── editor.py       # 截图编辑器与工具栏
├── gallery.py      # 历史库（无限画布）与大图查看
├── sticker.py      # 贴图功能
├── clipboard.py    # 剪贴板读写
├── settings.py     # 设置窗口
├── storage.py      # 历史/回收站存储管理
├── hotkey.py       # 全局热键注册
├── config.py       # 配置读写
├── dialogs.py      # 弹窗多屏定位补丁
└── assets/         # 图标等资源
```

## 配置

配置文件位于 `%APPDATA%\ScreenSnap\config.json`，可在设置界面中调整：

- 截图保存格式（JPG / PNG）与路径
- 全局热键（按键录制）
- 回收站保留天数
- 开机自启
- 截图后自动复制剪贴板

## 技术栈

- Python 3.11+ / Tkinter
- Pillow（图像处理）
- pystray（系统托盘）
- PyInstaller（打包）
- Inno Setup（安装包）

## 常见问题

- **热键无响应**：检查是否被其他程序占用（如输入法、远程控制软件），可在设置中更换快捷键
- **多显示器**：截图与弹窗会跟随鼠标所在屏幕定位

## License

本项目仅供个人使用，未经许可不得用于商业分发。
*（内容由AI生成，仅供参考）*
