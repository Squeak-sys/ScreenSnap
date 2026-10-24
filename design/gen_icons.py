# -*- coding: utf-8 -*-
"""ScreenSnap UI 图标资产生成脚本（SHUTTER 设计语言）。

单一数据源：ICONS 字典 → 同时产出
  1. design/icons/svg/*.svg          独立 SVG 源文件（24 网格 · 1.8px 圆头描边）
  2. design/icons/png/<色>/名-尺寸.png  透明底 PNG（ink/accent/white × 20/40/64px，Tkinter 直接可用）
  3. design/icons/图标总览.html        图标规范总览页
用法：python gen_icons.py
"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(BASE, "icons", "svg")
PNG_DIR = os.path.join(BASE, "icons", "png")

INK = "#1C2430"    # 一级文本色：默认图标色
ACCENT = "#2F6DF6"  # 主色·快门蓝：选中态
WHITE = "#FFFFFF"   # 深色底 / 反白态
COLORS = {"ink": INK, "accent": ACCENT, "white": WHITE}
SIZES = [20, 40, 64]  # 1x 工具钮 / 2x 高清 / 大图

# ---------- 图标数据（24×24 网格，stroke 1.8 圆头；{c} 为颜色占位符） ----------
ICONS = {
    # --- 编辑器工具组 ---
    "rect":    ("矩形框选", "工具1 · R", '<rect x="4" y="5" width="16" height="14" rx="2"/>'),
    "ellipse": ("椭圆选区", "工具2 · O", '<circle cx="12" cy="12" r="8"/>'),
    "arrow":   ("箭头", "工具3 · A", '<path d="M5 19 18 6M18 6h-7M18 6v7"/>'),
    "pen":     ("画笔", "工具4 · P", '<path d="M4 20c3-8 6-12 9-13 2.5-.8 5 .5 5 3 0 3-4 5-8 6"/><path d="M4 20l4-1"/>'),
    "mosaic":  ("马赛克", "工具5 · M", '<rect x="4" y="4" width="7" height="7" rx="1" fill="{c}" stroke="none"/><rect x="13" y="4" width="7" height="7" rx="1" fill="{c}" stroke="none" opacity=".55"/><rect x="4" y="13" width="7" height="7" rx="1" fill="{c}" stroke="none" opacity=".55"/><rect x="13" y="13" width="7" height="7" rx="1" fill="{c}" stroke="none"/>'),
    "text":    ("文字标注", "工具6 · T", '<path d="M5 6h14M12 6v13"/>'),
    # --- 编辑器属性组 ---
    "width":   ("线条粗细", "属性 · 弹层", '<path d="M4 7h16" stroke-width="1.4"/><path d="M4 12h16" stroke-width="2.4"/><path d="M4 17h16" stroke-width="3.6"/>'),
    # --- 编辑器操作组 ---
    "undo":    ("撤销", "Ctrl+Z", '<path d="M8 5 4 9l4 4"/><path d="M4 9h9a6 6 0 0 1 0 12h-3"/>'),
    "record":  ("屏幕录制", "操作", '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.5" fill="{c}" stroke="none"/>'),
    "pin":     ("贴图", "操作 · 钉在原位", '<path d="M9 4h6l1 6 3 3v2H5v-2l3-3z"/><path d="M12 15v5"/>'),
    "check":   ("完成", "Enter · 唯一强主操作", '<path d="M5 12.5 10 17.5 19 7" stroke-width="2.4"/>'),
    # --- 历史库 / 通用 ---
    "search":  ("搜索", "历史库", '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>'),
    "grid":    ("网格视图", "历史库", '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'),
    "list":    ("列表视图", "历史库", '<path d="M4 6h16M4 12h16M4 18h16"/>'),
    "edit":    ("编辑", "卡片操作", '<path d="M4 20l4-1L19.5 7.5a2 2 0 0 0-3-3L5 16z"/>'),
    "copy":    ("复制", "卡片操作", '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/>'),
    "trash":   ("删除", "卡片操作 · 危险", '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>'),
    "plus":    ("新建/立即截图", "主按钮", '<path d="M12 5v14M5 12h14" stroke-width="2.2"/>'),
}

GROUPS = [
    ("编辑器 · 工具组", ["rect", "ellipse", "arrow", "pen", "mosaic", "text"]),
    ("编辑器 · 属性/操作组", ["width", "undo", "record", "pin", "check"]),
    ("历史库 / 通用", ["search", "grid", "list", "edit", "copy", "trash", "plus"]),
]


def svg_markup(color, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            + body.replace("{c}", color) + "</svg>")


def render_pngs():
    """用 Chrome headless 渲染 SVG → 64px 透明 PNG，再用 Pillow 缩放出 40/20px。"""
    import subprocess, tempfile, shutil
    from PIL import Image
    chrome = None
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(p):
            chrome = p
            break
    if not chrome:
        raise SystemExit("未找到 Chrome/Edge，无法渲染 PNG")

    tmpdir = tempfile.mkdtemp()
    try:
        for cname, cval in COLORS.items():
            outdir = os.path.join(PNG_DIR, cname)
            for name, (_cn, _u, body) in ICONS.items():
                tmp_svg = os.path.join(tmpdir, f"{name}.svg")
                with open(tmp_svg, "w", encoding="utf-8") as f:
                    f.write(svg_markup(cval, body))
                out64 = os.path.join(outdir, f"{name}-64.png")
                subprocess.run(
                    [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                     "--hide-scrollbars", "--default-background-color=00000000",
                     "--window-size=64,64", f"--screenshot={out64}",
                     "file:///" + tmp_svg.replace("\\", "/")],
                    check=True, capture_output=True, timeout=30)
                img = Image.open(out64).convert("RGBA")
                for s in (40, 20):
                    img.resize((s, s), Image.LANCZOS).save(os.path.join(outdir, f"{name}-{s}.png"))
        # 校验透明底：ink/rect-64.png 四角应为全透明
        px = Image.open(os.path.join(PNG_DIR, "ink", "rect-64.png")).convert("RGBA")
        corners = [px.getpixel(p)[3] for p in [(0, 0), (63, 0), (0, 63), (63, 63)]]
        assert all(a == 0 for a in corners), f"PNG 背景不透明: {corners}"
        print("透明底校验通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def write_icon_sheet():
    """图标总览 HTML。"""
    rows = ""
    for gname, names in GROUPS:
        cards = ""
        for n in names:
            cn, usage, body = ICONS[n]
            svg = svg_markup("currentColor", body)
            cards += f'''<div class="card">
  <div class="prev"><span class="i40" style="color:{INK}">{svg}</span><span class="i20" style="color:{INK}">{svg}</span></div>
  <div class="nm">{cn}</div><div class="en">{n}.svg</div><div class="us">{usage}</div>
  <div class="vars"><span style="color:{ACCENT}">{svg}</span><span class="chip" style="color:#fff;background:{INK}">{svg}</span></div>
</div>'''
        rows += f'<h2>{gname}</h2><div class="grid">{cards}</div>'

    html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>ScreenSnap 图标规范总览</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Segoe UI","Microsoft YaHei UI",system-ui,sans-serif;background:#f0f1f4;color:#1c2430;padding:36px 44px}}
.hd{{display:flex;align-items:center;gap:12px;margin-bottom:6px}}
.hd .mk{{width:30px;height:30px;background:#1c2430;border-radius:8px;display:grid;place-items:center}}
.hd h1{{font-size:18px;font-weight:650}} .hd em{{font-style:normal;color:#2f6df6}}
.sub{{font-size:12px;color:#9aa2ad;margin-bottom:8px}}
.spec{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 26px}}
.spec span{{font-size:11.5px;color:#5b6472;background:#fff;border:1px solid #e3e6ea;border-radius:999px;padding:4px 11px}}
.spec b{{color:#2f6df6;font-weight:600}}
h2{{font-size:13px;font-weight:600;margin:22px 0 12px;color:#5b6472}}
.grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}}
.card{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:14px 12px 12px;text-align:center}}
.prev{{display:flex;justify-content:center;align-items:flex-end;gap:14px;height:46px}}
.i40 svg{{width:40px;height:40px}} .i20 svg{{width:20px;height:20px}}
.nm{{font-size:12.5px;font-weight:600;margin-top:8px}}
.en{{font-size:10.5px;color:#9aa2ad;font-family:Consolas,monospace}}
.us{{font-size:10.5px;color:#9aa2ad;margin-top:2px}}
.vars{{display:flex;justify-content:center;gap:8px;margin-top:8px;padding-top:8px;border-top:1px dashed #e3e6ea}}
.vars svg{{width:18px;height:18px}}
.chip{{border-radius:6px;padding:2px;display:grid;place-items:center}}
</style></head><body>
<div class="hd"><span class="mk"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9.5" stroke="#fff" stroke-width="1.8"/><path d="M12 2.5 15 10 12 12 9 10Z" fill="#fff"/><path d="M12 21.5 9 14 12 12 15 14Z" fill="#fff" opacity=".55"/></svg></span>
<h1>ScreenSnap <em>2.0</em> 图标规范总览</h1></div>
<div class="sub">SHUTTER 设计语言 · Line Icon Set v1.0</div>
<div class="spec">
<span><b>网格</b> 24×24 · 安全区内缩 2px</span>
<span><b>描边</b> 1.8px · 圆头圆角（check 2.4 / width 阶梯）</span>
<span><b>默认色</b> #1C2430</span>
<span><b>选中态</b> #2F6DF6</span>
<span><b>反白态</b> #FFFFFF</span>
<span><b>导出</b> SVG 源文件 + 透明 PNG（20/40/64px × 3 色）</span>
</div>
{rows}
</body></html>'''
    with open(os.path.join(BASE, "icons", "图标总览.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    for d in (SVG_DIR, PNG_DIR):
        os.makedirs(d, exist_ok=True)
        for c in COLORS:
            os.makedirs(os.path.join(PNG_DIR, c), exist_ok=True)

    # 1) SVG 源文件（ink 色直出，改色改 stroke 即可）
    for name, (_cn, _u, body) in ICONS.items():
        with open(os.path.join(SVG_DIR, f"{name}.svg"), "w", encoding="utf-8") as f:
            f.write(svg_markup(INK, body))

    # 2) PNG 变体：3 色 × 20/40/64px，透明底（Chrome 渲染 + Pillow 缩放）
    render_pngs()

    # 3) 图标总览页
    write_icon_sheet()

    n = len(ICONS)
    print(f"OK: {n} 个图标 · SVG {n} 份 + PNG {n * len(COLORS) * len(SIZES)} 张（3 色 × 3 尺寸），总览页已生成")


if __name__ == "__main__":
    main()
