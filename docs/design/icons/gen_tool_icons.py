# -*- coding: utf-8 -*-
"""ScreenSnap Qt 版工具图标生成：录屏 / 文字提取 / 图片翻译 / 保存 / 文件夹 / 删除。

单一数据源 → 产出
  1. svg/<name>.svg            24 网格矢量源（1.8px 圆头描边，与 SHUTTER 图标集一致）
  2. assets/icons/tool_<name>.png   运行时图标（20px、透明底、ink 单色，名称与
                                    ui_qt 里 tool_icon("<name>") 的调用一致）

说明：早期版本的 -40 备份件与 _accent 选中态 PNG 经核查无任何代码引用（选中态由
theme.qss 着色实现），故不再生成。注意：重跑会覆盖 assets/icons 下的同名 PNG，
属同源重渲染，抗锯齿可能与仓库现有位图有细微差异。
用法：python gen_tool_icons.py
"""
import os
import subprocess
import tempfile
import shutil
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(BASE, "svg")
# 运行时图标目录：<项目根>/assets/icons（本脚本位于 docs/design/icons）
OUT_DIR = os.path.normpath(os.path.join(BASE, "..", "..", "..", "assets", "icons"))

INK = "#1C2430"      # 一级文本 / 默认图标色（theme.INK）
ACCENT = "#2F6DF6"   # 主色 · 快门蓝（选中态）

# 24×24 网格，stroke 1.8 圆头圆角；{c} 为颜色占位符
ICONS = {
    # 录像：摄像机机身 + 右侧镜头楔形
    "record": '<rect x="2.5" y="6.5" width="12" height="11" rx="2.8"/>'
             '<path d="M14.5 10.6 20 8.2v7.6l-5.5-2.4z"/>',
    # 文字提取：四角识别框 + 三行文字线
    "ocr": '<path d="M3.5 8V6A2.5 2.5 0 0 1 6 3.5h2M16 3.5h2A2.5 2.5 0 0 1 20.5 6v2M20.5 16v2a2.5 2.5 0 0 1-2.5 2.5h-2M8 20.5H6A2.5 2.5 0 0 1 3.5 18v-2"/>'
           '<path d="M7.5 9h9M7.5 12.2h9M7.5 15.4h5"/>',
    # 图片翻译：圆角相框 + 两条文字线 + 右下双向箭头（与现有 QPainter 版语义一致）
    "pic_translate": '<rect x="2" y="3.5" width="13.5" height="11" rx="2.5"/>'
                 '<path d="M5 7h7.5M5 10.2h4.5"/>'
                 '<path d="M6.5 19h11.5M6.5 19l2.4-2.4M6.5 19l2.4 2.4M18 19l-2.4-2.4M18 19l-2.4 2.4"/>',
    # 保存：软盘——外形（右上切角）+ 顶部快门 + 底部标签
    "save": '<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h9L20 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z"/>'
            '<path d="M8 4v4.5h6.5V4"/>'
            '<rect x="7.5" y="13" width="9" height="7" rx="1"/>',
    # 文件夹：带页签的文件夹外形
    "folder": '<path d="M3.5 7a2 2 0 0 1 2-2h4.2l2 2.4h8.8v9.1a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    # 删除：垃圾桶——桶口横线 + 提手 + 桶身 + 两道竖纹
    "delete": '<path d="M4 7h16"/>'
              '<path d="M9.5 7V4.5h5V7"/>'
              '<path d="M6 7l1 11.2A2 2 0 0 0 9 20h6a2 2 0 0 0 2-1.8L18 7"/>'
              '<path d="M10.2 11v5M13.8 11v5"/>',
}

def svg_markup(color, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            + body.replace("{c}", color) + "</svg>")


def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            return p
    raise SystemExit("未找到 Chrome/Edge，无法渲染 PNG")


def render_pngs():
    chrome = find_chrome()
    tmpdir = tempfile.mkdtemp()
    try:
        outdir = OUT_DIR
        os.makedirs(outdir, exist_ok=True)
        for name, body in ICONS.items():
            tmp_svg = os.path.join(tmpdir, f"{name}.svg")
            with open(tmp_svg, "w", encoding="utf-8") as f:
                f.write(svg_markup(INK, body))
            out64 = os.path.join(tmpdir, "r64.png")
            subprocess.run(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--hide-scrollbars", "--default-background-color=00000000",
                 "--window-size=64,64", f"--screenshot={out64}",
                 "file:///" + tmp_svg.replace("\\", "/")],
                check=True, capture_output=True, timeout=30)
            img = Image.open(out64).convert("RGBA")
            # 只产出 Qt tool_icon() 直接加载的 20px 主图
            img.resize((20, 20), Image.LANCZOS).save(
                os.path.join(outdir, f"tool_{name}.png"))
        # 透明底校验
        px = Image.open(os.path.join(outdir, "tool_video.png")).convert("RGBA")
        corners = [px.getpixel(p)[3] for p in [(0, 0), (19, 0), (0, 19), (19, 19)]]
        assert all(a == 0 for a in corners), f"PNG 背景不透明: {corners}"
        print("透明底校验通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_preview():
    cells = ""
    for name, body in ICONS.items():
        svg = svg_markup("currentColor", body)
        cells += (f'<div class="card"><div class="prev">'
                  f'<span style="width:40px;height:40px">{svg}</span>'
                  f'<span style="width:20px;height:20px">{svg}</span></div>'
                  f'<div class="nm">tool_{name}</div>'
                  f'<span class="ac" style="color:{ACCENT}">{svg}</span></div>')
    html = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<title>新图标预览</title><style>'
            '*{box-sizing:border-box;margin:0;padding:0}'
            'body{font-family:"Segoe UI","Microsoft YaHei UI",sans-serif;'
            'background:#f0f1f4;color:#1c2430;padding:40px;display:flex;gap:16px}'
            '.card{background:#fff;border:1px solid #e3e6ea;border-radius:12px;'
            'padding:20px 16px 14px;text-align:center;width:150px}'
            '.prev{display:flex;justify-content:center;align-items:flex-end;gap:14px;height:48px}'
            '.prev svg{width:40px;height:40px}.prev span:last-child svg{width:20px;height:20px}'
            '.nm{font-size:12px;margin-top:10px;font-family:Consolas,monospace;color:#5b6472}'
            '.ac svg{width:20px;height:20px;margin-top:8px}'
            '</style></head><body>' + cells + '</body></html>')
    with open(os.path.join(BASE, "svg", "新图标预览.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    os.makedirs(SVG_DIR, exist_ok=True)
    # SVG 源文件（ink 色）
    for name, body in ICONS.items():
        with open(os.path.join(SVG_DIR, f"{name}.svg"), "w", encoding="utf-8") as f:
            f.write(svg_markup(INK, body))
    render_pngs()
    write_preview()
    print(f"OK: {len(ICONS)} 个图标 · SVG 写入 {SVG_DIR}，PNG {len(ICONS)} 张（20px ink）写入 {OUT_DIR}，预览页已生成")


if __name__ == "__main__":
    main()
