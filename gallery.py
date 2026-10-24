# -*- coding: utf-8 -*-
"""历史库设置页：真无限画布（视口裁剪渲染）+ 日期分组（日期从左到右，组内每列 7 张满则右移）。
交互：滚轮缩放、空白拖动平移画布、单击选中(Shift多选)、Delete 删除（3天回收站）、
拖拽调整顺序、双击查看大图。
渲染机制：维护虚拟布局（世界坐标），仅创建视口（含安全边距）内的图片/日期 item，
视野外销毁；平移/缩放直接更新视图偏移 (_origin_x/_origin_y) 与 _scale，无边界限制
（可突破 ±80000，坐标无限扩展）。"""
import os
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import filedialog, messagebox

import math

import editor
from PIL import Image, ImageDraw, ImageTk

THUMB_W, THUMB_H = 180, 130


class GalleryWindow:
    # ---------- 多屏定位（与 settings.py 同一套逻辑） ----------
    @staticmethod
    def _monitor_rects():
        """枚举所有显示器物理边界（虚拟屏坐标，含负值区域）。"""
        u = ctypes.windll.user32
        rects = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                            ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def _cb(_hmon, _hdc, lprc, _lparam):
            r = lprc.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return True

        u.EnumDisplayMonitors(None, None, _cb, 0)
        return rects

    @staticmethod
    def _place_on_pointer(win, w, h, margin=24):
        """把窗口放到鼠标所在屏幕内（优先居中，并 clamp 到可见区域）。"""
        try:
            u = ctypes.windll.user32

            class _PT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = _PT()
            u.GetCursorPos(ctypes.byref(pt))
            for l, t, r, b in GalleryWindow._monitor_rects():
                if l <= pt.x < r and t <= pt.y < b:
                    x = l + (r - l - w) // 2
                    y = t + (b - t - h) // 2
                    x = max(l + margin, min(x, r - w - margin))
                    y = max(t + margin, min(y, b - h - margin))
                    win.geometry(f"{w}x{h}+{x}+{y}")
                    return
        except Exception:
            pass
        win.geometry(f"{w}x{h}")  # 兜底：交给系统默认定位

    def __init__(self, root, storage, config):
        self.root = root
        self.storage = storage
        self.cfg = config
        self.capacity = int(self.cfg.get("column_capacity", 10))  # 竖列最多张数（config 缺失默认 10）
        self.cell_w, self.cell_h = 200, 170
        self.margin = 26
        self.group_h = 40

        self.storage.cleanup_trash()
        # 先把录屏视频登记进历史库（含 ffmpeg 抽帧缩略图），再统一加载
        try:
            self.storage.scan_videos()
        except Exception:
            pass
        self.items = self.storage.load_gallery()
        self.items.sort(key=lambda it: it.get("order", 0))

        self.win = tk.Toplevel(root)
        self.win.title("ScreenSnap - 截图历史库")
        self.win.geometry("1100x720")
        self.win.minsize(760, 520)

        top = tk.Frame(self.win, bg="#1f1f1f")
        top.pack(side="top", fill="x")
        tip = ("滚轮=缩放  |  空白处拖动=平移画布  |  单击选中(Shift多选)  |  "
               "按住图片拖动=调整顺序  |  Delete=删除(3天回收站)  |  双击=查看大图")
        tk.Label(top, text=tip, bg="#1f1f1f", fg="#cccccc",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=12, pady=8)
        n_video = sum(1 for it in self.items if it.get("kind") == "video")
        self.status_var = tk.StringVar(
            value=f"共 {len(self.items)} 项（截图 {len(self.items) - n_video} · 录屏 {n_video}）")
        tk.Label(top, textvariable=self.status_var, bg="#1f1f1f", fg="#ffb800",
                 font=("Microsoft YaHei UI", 9)).pack(side="right", padx=12)

        body = tk.Frame(self.win)
        body.pack(fill="both", expand=True)
        # 真无限画布：无滚动条、无 scrollregion 边界；滚轮缩放 + 空白拖动平移
        self.canvas = tk.Canvas(body, bg="#262626", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # ---------- 虚拟布局与视口裁剪渲染状态 ----------
        self._groups = []          # [{date,x,y,cols,count}, ...]（世界坐标）
        self._item_meta = {}       # path -> {"x":..,"y":..,"g":group_idx}（世界坐标）
        self._max_y = 0            # 布局最大 y（世界坐标，仅统计用）
        self._scale = 1.0          # 画布缩放比例（滚轮）
        self._origin_x = 0.0       # 世界原点(0,0)在画布坐标系中的位置
        self._origin_y = 0.0
        self._view_scale = None    # 上次渲染的 scale；变化时强制全量重建
        self._canvas_items = {}    # path -> canvas id（仅可视区内图片 item）
        self._canvas_photos = {}   # path -> PhotoImage（防 GC）
        self._date_cids = {}       # group_idx -> canvas id（仅可视区内日期标题）
        self._thumb_imgs = {}      # thumb path -> 基础缩略图 PIL Image（缩放二次采样）

        self.selected = set()      # path set
        self._sel_rects = {}       # path -> canvas id（选中高亮框）
        self._drag_path = None
        self._drag_start_pos = None
        self._dragging = False
        self._drag_marker = None
        self._drag_floating = None
        self._drag_floating_photo = None
        self._drag_orig_id = None  # 拖拽时被暂时隐藏的原图 canvas id
        self._panning = False
        self._pan_last = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.win.bind("<Delete>", lambda e: self.delete_selected())
        self.win.bind("<Escape>", lambda e: self.win.destroy())

        self._layout()

    def _on_canvas_configure(self, event):
        """画布尺寸变化（窗口 resize / 首次显示）时同步视口。"""
        self._sync_viewport()

    # ---------- 布局（虚拟） ----------
    def _get_thumb_im(self, item):
        """取基础缩略图（THUMB_WxTHUMB_H 的 PIL Image），渲染时按 _scale 二次采样。
        录屏视频条目叠加播放按钮标记（右下角深色圆底 + 白色三角）。"""
        p = item.get("thumb")
        if p not in self._thumb_imgs:
            try:
                im = Image.open(p).resize((THUMB_W, THUMB_H), 3)
            except Exception:
                # 缩略图缺失时生成
                try:
                    src = Image.open(item["path"])
                    tp = self.storage.thumb_for(item)
                    im = Image.open(tp).resize((THUMB_W, THUMB_H), 3)
                except Exception:
                    im = Image.new("RGB", (THUMB_W, THUMB_H), (60, 60, 60))
            if item.get("kind") == "video":
                d = ImageDraw.Draw(im)
                cx, cy = THUMB_W - 28, THUMB_H - 28
                r = 15
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(26, 26, 26))
                d.ellipse([cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2],
                          outline=(255, 255, 255), width=2)
                d.polygon([(cx - 5, cy - 8), (cx - 5, cy + 8), (cx + 9, cy)],
                          fill=(255, 255, 255))
            self._thumb_imgs[p] = im
        return self._thumb_imgs[p]

    def _build_plan(self):
        """重建虚拟布局：按日期分组、列排布，计算每项世界坐标与命中矩形（以 scale=1 为基准）。"""
        self._item_meta.clear()
        self._groups = []
        groups = []
        for it in self.items:
            d = it.get("date", "")
            if groups and groups[-1][0] == d:
                groups[-1][1].append(it)
            else:
                groups.append((d, [it]))
        x = self.margin
        max_y = 0
        for gi, (date, grp) in enumerate(groups):
            gx = x
            gy = self.margin
            col, row = 0, 0
            for it in grp:
                if row >= self.capacity:
                    row = 0
                    col += 1
                ix = gx + col * self.cell_w
                iy = gy + self.group_h + row * self.cell_h
                self._item_meta[it.get("path")] = {"x": ix, "y": iy, "g": gi}
                row += 1
            x += (col + 1) * self.cell_w
            max_y = max(max_y, gy + self.group_h + self.capacity * self.cell_h)
            self._groups.append({"date": date, "x": gx, "y": gy,
                                 "cols": col + 1, "count": len(grp)})
        self._max_y = max_y

    # ---------- 视口裁剪渲染 ----------
    def _viewport_rect(self):
        """视口（画布像素坐标）+ 安全边距。"""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        pad = max(150, int(300 * self._scale))
        return (-pad, -pad, w + pad, h + pad)

    def _item_screen_rect(self, meta):
        """item 图片实际缩略图边界（画布坐标）。"""
        s = self._scale
        ox, oy = self._origin_x, self._origin_y
        tw = max(4, int(THUMB_W * s))
        th = max(4, int(THUMB_H * s))
        sx1 = meta["x"] * s + ox
        sy1 = meta["y"] * s + oy
        return (sx1, sy1, sx1 + tw, sy1 + th)

    def _group_screen_rect(self, g):
        """日期分组区域（画布坐标）。"""
        s = self._scale
        ox, oy = self._origin_x, self._origin_y
        sx1 = g["x"] * s + ox
        sy1 = g["y"] * s + oy
        sx2 = (g["x"] + g["cols"] * self.cell_w) * s + ox
        sy2 = (g["y"] + self.group_h) * s + oy
        return (sx1, sy1, sx2, sy2)

    def _refresh_viewport(self, force=False):
        """全量/强制重建可视集（缩放、数据变化时调用）。"""
        if force or self._view_scale != self._scale:
            for cid in self._canvas_items.values():
                try:
                    self.canvas.delete(cid)
                except Exception:
                    pass
            self._canvas_items.clear()
            self._canvas_photos.clear()
            for cid in self._date_cids.values():
                try:
                    self.canvas.delete(cid)
                except Exception:
                    pass
            self._date_cids.clear()
            for cid in self._sel_rects.values():
                try:
                    self.canvas.delete(cid)
                except Exception:
                    pass
            self._sel_rects.clear()
            self._view_scale = self._scale
        self._sync_viewport()

    def _sync_viewport(self):
        """增量同步：只创建进入视口的 item、销毁离开视口的 item。"""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 0 or h <= 0:
            return
        x0, y0, x1, y1 = self._viewport_rect()
        s = self._scale
        tw = max(4, int(THUMB_W * s))
        th = max(4, int(THUMB_H * s))
        font_size = max(9, min(22, int(13 * s)))

        # 1) 销毁离开可视区的图片 item
        for path, cid in list(self._canvas_items.items()):
            meta = self._item_meta.get(path)
            if meta is None:
                self.canvas.delete(cid)
                del self._canvas_items[path]
                self._canvas_photos.pop(path, None)
                continue
            sx1, sy1, sx2, sy2 = self._item_screen_rect(meta)
            if sx2 < x0 or sx1 > x1 or sy2 < y0 or sy1 > y1:
                self.canvas.delete(cid)
                del self._canvas_items[path]
                self._canvas_photos.pop(path, None)

        # 2) 创建进入可视区的图片 item
        for path, meta in self._item_meta.items():
            if path in self._canvas_items:
                continue
            sx1, sy1, sx2, sy2 = self._item_screen_rect(meta)
            if sx2 < x0 or sx1 > x1 or sy2 < y0 or sy1 > y1:
                continue
            it = self._item_by_path(path)
            if it is None:
                continue
            photo = ImageTk.PhotoImage(
                self._get_thumb_im(it).resize((tw, th), 3))
            self._canvas_photos[path] = photo
            cid = self.canvas.create_image(sx1, sy1, image=photo, anchor="nw")
            self._canvas_items[path] = cid

        # 3) 日期标题（按组区域裁剪）
        for gi, g in enumerate(self._groups):
            sx1, sy1, sx2, sy2 = self._group_screen_rect(g)
            visible = not (sx2 < x0 or sx1 > x1 or sy2 < y0 or sy1 > y1)
            cid = self._date_cids.get(gi)
            if visible and cid is None:
                self._date_cids[gi] = self.canvas.create_text(
                    sx1, sy1 + 10, text=g["date"], anchor="nw",
                    fill="#ffb800",
                    font=("Microsoft YaHei UI", font_size, "bold"))
            elif not visible and cid is not None:
                self.canvas.delete(cid)
                del self._date_cids[gi]

        # 4) 选中框：仅画在可视 item 上，贴合图片实际显示边界
        self._redraw_selection()

    def _redraw_selection(self):
        for cid in self._sel_rects.values():
            try:
                self.canvas.delete(cid)
            except Exception:
                pass
        self._sel_rects = {}
        for path, cid in self._canvas_items.items():
            if path in self.selected:
                b = self.canvas.bbox(cid)
                if not b:
                    continue
                r = self.canvas.create_rectangle(
                    b[0] - 3, b[1] - 3, b[2] + 3, b[3] + 3,
                    outline="#ffb800", width=2)
                self._sel_rects[path] = r

    # ---------- 交互 ----------
    def _hit_item(self, x, y):
        """精确命中：基于虚拟布局矩形（图片实际缩略图边界换算到画布坐标），
        不依赖 canvas item 的 bbox。"""
        s = self._scale
        ox, oy = self._origin_x, self._origin_y
        tw = max(4, int(THUMB_W * s))
        th = max(4, int(THUMB_H * s))
        for path, meta in self._item_meta.items():
            sx1 = meta["x"] * s + ox
            sy1 = meta["y"] * s + oy
            if sx1 <= x < sx1 + tw and sy1 <= y < sy1 + th:
                return self._item_by_path(path)
        return None

    def _on_hover(self, event):
        """光标随悬停对象即时切换（与 editor 调整选区光标逻辑对齐）：
        缩略图=手型(hand2)，日期标题/空白=箭头(arrow)；拖动/平移中保持按下时
        设定的光标（grabbing/fleur），不随位置闪跳。"""
        if self._panning or self._dragging or self._drag_path is not None:
            return  # 拖动/平移进行中：保持当前光标，不做悬停切换
        if self._hit_item(event.x, event.y) is not None:
            self.canvas.configure(cursor="hand2")
            return
        self.canvas.configure(cursor="arrow")

    def _on_press(self, event):
        x, y = event.x, event.y     # 画布坐标 = 窗口坐标（无 scrollregion）
        it = self._hit_item(x, y)
        if it is not None:
            # 按下缩略图：记录起点，release 时判断单击/拖拽排序
            self._drag_path = it.get("path")
            self._drag_start_pos = (x, y)
            self._dragging = False
            self.canvas.configure(cursor="hand2")
            return
        # 空白处：开始平移画布（等价 scan 方式，方向体感一致）
        self._panning = True
        self._pan_last = (event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _pan_to(self, event):
        """平移画布：直接更新视图偏移并增量重绘。"""
        if self._pan_last is None:
            return
        dx = event.x - self._pan_last[0]
        dy = event.y - self._pan_last[1]
        self._pan_last = (event.x, event.y)
        if dx == 0 and dy == 0:
            return
        self._origin_x += dx
        self._origin_y += dy
        # 移动现有 item（复用，不重新生成位图），再增删边缘
        for cid in self._canvas_items.values():
            self.canvas.move(cid, dx, dy)
        for cid in self._date_cids.values():
            self.canvas.move(cid, dx, dy)
        for cid in self._sel_rects.values():
            self.canvas.move(cid, dx, dy)
        self._sync_viewport()

    def _on_drag(self, event):
        if self._panning:
            self.canvas.configure(cursor="fleur")
            self._pan_to(event)
            return
        x, y = event.x, event.y
        if self._drag_path is not None:
            # 移动超过阈值判定为拖拽排序
            if self._drag_start_pos and (
                    abs(x - self._drag_start_pos[0]) > 8 or abs(y - self._drag_start_pos[1]) > 8):
                self._dragging = True
            if self._dragging:
                self.canvas.configure(cursor="fleur")  # Windows Tk 无 grabbing，用 fleur（需求允许 fleur/grabbing）
                # 浮动图跟随鼠标（创建一次，拖动中移动坐标）
                if self._drag_floating is None:
                    it = self._item_by_path(self._drag_path)
                    if it is not None:
                        # 原图暂时隐藏，落定新位置后再恢复/重排
                        cid = self._canvas_items.get(self._drag_path)
                        if cid is not None:
                            self._drag_orig_id = cid
                            self.canvas.itemconfigure(cid, state="hidden")
                            # 同步移除该图选中框，避免原位残留黄框
                            rcid = self._sel_rects.pop(self._drag_path, None)
                            if rcid:
                                try:
                                    self.canvas.delete(rcid)
                                except Exception:
                                    pass
                        tw = max(30, int(THUMB_W * self._scale))
                        th = max(22, int(THUMB_H * self._scale))
                        self._drag_floating_photo = ImageTk.PhotoImage(
                            self._get_thumb_im(it).resize((tw, th), 3))
                        self._drag_floating = self.canvas.create_image(
                            x, y, image=self._drag_floating_photo, anchor="center")
                else:
                    self.canvas.coords(self._drag_floating, x, y)
                if self._drag_floating:
                    self.canvas.tag_raise(self._drag_floating)
                if self._drag_marker:
                    self.canvas.delete(self._drag_marker)
                w = max(30, int(THUMB_W * self._scale))
                h = max(22, int(THUMB_H * self._scale))
                # 虚线框以浮动图中心为基准，精确套住浮动图
                self._drag_marker = self.canvas.create_rectangle(
                    x - w / 2, y - h / 2, x + w / 2, y + h / 2,
                    outline="#ffb800", width=2, dash=(4, 2))

    def _on_release(self, event):
        if self._panning:
            self._panning = False
            self._pan_last = None
            self._on_hover(event)  # 释放后恢复悬停光标（空白=arrow）
            return
        x, y = event.x, event.y
        if self._drag_path is not None:
            target = self._hit_item(x, y)
            drag_path = self._drag_path
            was_dragging = self._dragging
            self._drag_path = None
            self._dragging = False
            self._drag_start_pos = None
            if self._drag_marker:
                self.canvas.delete(self._drag_marker)
                self._drag_marker = None
            if self._drag_floating:
                self.canvas.delete(self._drag_floating)
                self._drag_floating = None
                self._drag_floating_photo = None
            # 恢复被暂时隐藏的原图（若已重排则此 id 已失效，忽略异常）
            if self._drag_orig_id:
                try:
                    self.canvas.itemconfigure(self._drag_orig_id, state="normal")
                except Exception:
                    pass
                self._drag_orig_id = None
            if was_dragging:
                # 拖拽排序：交换 order
                if target is not None and target.get("path") != drag_path:
                    self._swap_order(drag_path, target.get("path"))
                elif self.selected:
                    self._redraw_selection()
            else:
                # 单击：选中 / 取消
                shift = (event.state & 0x0001) != 0
                if shift:
                    if drag_path in self.selected:
                        self.selected.discard(drag_path)
                    else:
                        self.selected.add(drag_path)
                else:
                    self.selected = {drag_path}
                self._redraw_selection()
                self._update_status()
        self._on_hover(event)  # 拖拽/单击结束后恢复悬停光标（缩略图=hand2，空白=arrow）

    def _on_wheel(self, event):
        """滚轮缩放：以鼠标位置为锚点整体缩放（真无限画布）。
        只更新 _scale 与 _origin（锚点内容不动），位图按新尺寸二次采样重渲染。"""
        if not self.items:
            return
        old = self._scale
        k = 1.1 if event.delta > 0 else 1 / 1.1
        new = max(0.05, min(12.0, old * k))
        if abs(new - old) < 1e-9:
            return
        cx, cy = event.x, event.y   # 画布坐标（无 scrollregion，等于窗口坐标）
        ratio = new / old
        self._scale = new
        # 锚点缩放等价于线性缩放 + 平移：累积平移保证锚点下内容不动
        self._origin_x = cx + (self._origin_x - cx) * ratio
        self._origin_y = cy + (self._origin_y - cy) * ratio
        self._refresh_viewport(force=True)

    def _on_double(self, event):
        it = self._hit_item(event.x, event.y)
        if it:
            self._view_large(it)

    def _item_by_path(self, path):
        for it in self.items:
            if it.get("path") == path:
                return it
        return None

    def _swap_order(self, path_a, path_b):
        it_a = next((it for it in self.items if it.get("path") == path_a), None)
        it_b = next((it for it in self.items if it.get("path") == path_b), None)
        if not it_a or not it_b:
            return
        # 只允许同一天内调整顺序，禁止跨日期移动（保证按日期存放）
        if it_a.get("date") != it_b.get("date"):
            return
        oa, ob = it_a.get("order", 0), it_b.get("order", 0)
        it_a["order"], it_b["order"] = ob, oa
        self.items.sort(key=lambda it: it.get("order", 0))
        self.storage.save_gallery(self.items)
        self._layout(keep_view=True)
        self._update_status()

    # ---------- 删除 ----------
    def delete_selected(self):
        if not self.selected:
            return
        targets = [it for it in self.items if it.get("path") in self.selected]
        n = self.storage.delete_to_trash(targets)
        self.selected.clear()
        self.items = self.storage.load_gallery()
        self.items.sort(key=lambda it: it.get("order", 0))
        self._layout(keep_view=True)
        self._update_status()
        if n:
            messagebox.showinfo("ScreenSnap", f"已移入回收站 {n} 张，3 天后自动清除。")

    def _update_status(self):
        sel = len(self.selected)
        n_video = sum(1 for it in self.items if it.get("kind") == "video")
        txt = f"共 {len(self.items)} 项（截图 {len(self.items) - n_video} · 录屏 {n_video}）"
        if sel:
            txt += f"  |  已选 {sel} 项"
        self.status_var.set(txt)

    # ---------- 布局入口 ----------
    def _layout(self, keep_view=False):
        """重建虚拟布局与可视集。真无限画布下视口=窗口，origin/scale 不变即视图保持；
        keep_view=True 表示沿用当前 origin/scale（排序/删除后不跳回左上角）。"""
        if not keep_view:
            self._origin_x = 0.0
            self._origin_y = 0.0
            self._scale = 1.0
        self._build_plan()
        self._refresh_viewport(force=True)
        self._update_status()

    # ---------- 查看大图 ----------
    def _view_large(self, item):
        path = item.get("path")
        if not path or not os.path.exists(path):
            messagebox.showwarning("ScreenSnap", "文件不存在，可能已被清理。")
            return
        if item.get("kind") == "video":
            # 录屏视频：打开视频播放窗口并自动开始播放
            vw = _VideoViewer(self.win, path,
                              on_close=lambda: self._after_view_close(item))
            vw.win.update_idletasks()
            GalleryWindow._place_on_pointer(vw.win, vw.win.winfo_width(),
                                            vw.win.winfo_height())
            return vw
        img = Image.open(path).convert("RGB")
        vw = _LargeViewer(
            self.win, img,
            storage=self.storage,
            save_fmt=self.cfg.get("save_format", "png"),
            on_delete=lambda: self._delete_one(item, None),
            on_saved=self._reload_after_save,
        )
        # 多屏定位：大图窗口放到鼠标所在屏幕（双击处），避免跑到其他显示器
        vw.win.update_idletasks()
        GalleryWindow._place_on_pointer(vw.win, vw.win.winfo_width(),
                                        vw.win.winfo_height())
        return vw

    def _after_view_close(self, item):
        """视频播放窗口关闭后：若视频已被删除则刷新画布。"""
        if not os.path.exists(item.get("path", "")):
            self.items = self.storage.load_gallery()
            self.items.sort(key=lambda it: it.get("order", 0))
            self._layout(keep_view=True)

    def _reload_after_save(self):
        """保存到历史库后刷新画布：新增条目立即可见。"""
        self.items = self.storage.load_gallery()
        self.items.sort(key=lambda it: it.get("order", 0))
        self._layout(keep_view=True)

    def _copy_img(self, item):
        from clipboard import copy_image_to_clipboard
        try:
            img = Image.open(item["path"])
            ok = copy_image_to_clipboard(img)
            messagebox.showinfo("ScreenSnap", "已复制到剪贴板" if ok else "复制失败")
        except Exception as e:
            messagebox.showerror("ScreenSnap", f"复制失败：{e}")

    def _save_as(self, img):
        p = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg")],
            initialfile="截图.png")
        if p:
            try:
                img.save(p)
                messagebox.showinfo("ScreenSnap", f"已保存：{p}")
            except Exception as e:
                messagebox.showerror("ScreenSnap", f"保存失败：{e}")

    def _delete_one(self, item, view=None):
        self.selected = {item.get("path")}
        self.delete_selected()
        if view is not None:
            # 优先走 _close：一并销毁大图窗口与其悬浮工具栏，避免残留
            try:
                view._close()
            except Exception:
                try:
                    view.destroy()
                except Exception:
                    pass


class _EmbeddedToolbar(tk.Frame):
    """大图查看器内嵌工具栏：直接嵌入 _LargeViewer 主窗口底部的 Frame 子控件，
    与大图窗口同窗口同图层（无独立 Toplevel），天然跟随窗口移动、随窗口销毁，
    彻底消除独立窗口带来的置顶/浮动/跟随/残留问题。
    提供与 editor.ToolbarWindow 一致的接口（_highlight_tool / set_color / set_width），
    保证 _LargeViewer 复用 EditorWindow 继承来的样式方法（set_color/set_width/undo）可用。"""

    def __init__(self, master, ed, bg="#f0f0f0"):
        super().__init__(master, bg=bg)
        self.editor = ed
        self._tool_btns = {}
        self._normal_bg = {}

        def _mk_btn(parent, **kw):
            kw.setdefault("relief", "flat")
            kw.setdefault("bd", 0)
            kw.setdefault("cursor", "hand2")
            kw.setdefault("bg", "#f7f7f7")
            kw.setdefault("activebackground", "#e8e8e8")
            kw.setdefault("highlightthickness", 1)
            kw.setdefault("highlightbackground", "#f7f7f7")
            kw.setdefault("highlightcolor", "#f7f7f7")
            kw.setdefault("font", ("Microsoft YaHei UI", 9))
            return tk.Button(parent, **kw)

        # 第一行：标注工具（7 个）+ 颜色 + 线宽 + 撤销
        row1 = tk.Frame(self, bg=bg)
        row1.pack(side="top", fill="x", padx=6, pady=(4, 1))

        self._icons = editor._make_tool_icons()
        for tool in editor.TOOLS:
            b = _mk_btn(row1, image=self._icons[tool],
                        command=lambda t=tool: self.editor.set_tool(t))
            b.pack(side="left", padx=2)
            self._tool_btns[tool] = b
            self._normal_bg[b] = "#f7f7f7"

        # 颜色：色块按钮 + 下拉菜单（Menu 弹出，不创建独立窗口）
        self._color_btn = _mk_btn(row1, text="", bg="#e62117",
                                  activebackground="#e62117",
                                  command=self._open_color_menu)
        self._color_btn.pack(side="left", padx=(6, 2))
        self._normal_bg[self._color_btn] = "#e62117"

        # 线宽：下拉菜单
        self._width_btn = _mk_btn(row1, text="线宽·中", fg="#333333",
                                  command=self._open_width_menu)
        self._width_btn.pack(side="left", padx=2)
        self._normal_bg[self._width_btn] = "#f7f7f7"

        undo_b = _mk_btn(row1, text="撤销", fg="#333333",
                         command=self.editor.undo)
        undo_b.pack(side="left", padx=2)
        self._normal_bg[undo_b] = "#f7f7f7"

        # 第二行：常用操作（复制/另存/保存/删除/盯住/关闭）
        row2 = tk.Frame(self, bg=bg)
        row2.pack(side="bottom", fill="x", padx=6, pady=(0, 4))

        for text, cmd in (("复制图片", self.editor._copy_img),
                          ("另存为...", self.editor._save_as),
                          ("保存到历史库", self.editor._save_to_gallery),
                          ("删除(回收站)", self.editor._delete_btn),
                          ("盯住", self.editor.pin),
                          ("关闭", self.editor.done)):
            b = _mk_btn(row2, text=text, fg="#333333", command=cmd)
            b.pack(side="left", padx=2)

    def _open_color_menu(self):
        menu = tk.Menu(self, tearoff=0)
        for name, rgb in editor.COLORS.items():
            hexc = "#{:02x}{:02x}{:02x}".format(*rgb)
            menu.add_command(label=name,
                             command=lambda c=rgb: self.editor.set_color(c))
        try:
            menu.tk_popup(self._color_btn.winfo_rootx(),
                          self._color_btn.winfo_rooty() + self._color_btn.winfo_height())
        finally:
            menu.grab_release()

    def _open_width_menu(self):
        menu = tk.Menu(self, tearoff=0)
        for name, w in editor.WIDTHS.items():
            menu.add_command(label=f"{name}（{w}px）",
                             command=lambda v=w, n=name: self.editor.set_width(v, n))
        try:
            menu.tk_popup(self._width_btn.winfo_rootx(),
                          self._width_btn.winfo_rooty() + self._width_btn.winfo_height())
        finally:
            menu.grab_release()

    def _highlight_tool(self):
        """当前工具高亮（None 表示浏览/平移模式，无高亮）。"""
        cur = self.editor.tool
        for t, b in self._tool_btns.items():
            if t == cur:
                b.configure(bg="#d7e3f4", activebackground="#c4d6ef",
                            highlightbackground="#c4d6ef",
                            highlightcolor="#c4d6ef")
            else:
                b.configure(bg="#f7f7f7", activebackground="#e8e8e8",
                            highlightbackground="#f7f7f7",
                            highlightcolor="#f7f7f7")

    def set_color(self, rgb):
        hexc = "#{:02x}{:02x}{:02x}".format(*rgb)
        self._color_btn.configure(bg=hexc, activebackground=hexc,
                                  highlightbackground="#ffb800",
                                  highlightcolor="#ffb800")

    def set_width(self, w, name=None):
        if name is None:
            name = next((n for n, v in editor.WIDTHS.items() if v == w), str(w))
        self._width_btn.configure(text=f"线宽·{name}")


class _LargeViewer(editor.EditorWindow):
    """历史库大图查看+标注窗口：复用 editor.py 的七种标注工具；工具栏为内嵌
    Frame 子控件（与大图窗口同窗口同图层，无独立窗口，彻底消除置顶/浮动/残留），
    标注以 PIL 内存图绘制（不改动原图文件）；
    支持滚轮锚点缩放（20%~200%）、空白拖动平移画布；超大图片打开时按 50% 显示。"""

    # 打开尺寸规则：图片宽或高超过该阈值（px）时，打开按实际尺寸的 50% 显示
    OPEN_HALF_THRESHOLD = 1000

    def __init__(self, root, image, on_close=None, on_delete=None,
                 storage=None, save_fmt="png", on_saved=None):
        # 直接初始化标注状态（字段与 EditorWindow 对齐，走 MODE_DRAW 标注模式）
        self.root = root
        self.on_close = on_close
        self.on_delete = on_delete
        self.storage = storage
        self.save_fmt = save_fmt
        self.on_saved = on_saved
        self.undo_stack = []
        self.tool = None  # 默认浏览/平移模式：不激活任何绘制工具，工具栏无高亮
        self.color = editor.COLORS["红"]
        self.width = editor.WIDTHS["中"]
        self._start = None
        self._preview = None
        self._pen_points = []
        self._text_entry = None
        self._text_objs = []
        self._drag_text = None
        self._drag_off = (0, 0)
        self.mode = editor.MODE_DRAW
        self.full_img = image.convert("RGB")
        self.sel = [0, 0, self.full_img.width, self.full_img.height]
        self.img = self.full_img.copy()
        self.w = self.img.width
        self.h = self.img.height
        self.tool_h = 32

        # 真无限画布缩放/平移状态（20%~200%）
        # 打开尺寸规则：宽或高超过 OPEN_HALF_THRESHOLD 时按 50% 显示
        self._scale = 0.5 if (self.w > _LargeViewer.OPEN_HALF_THRESHOLD
                              or self.h > _LargeViewer.OPEN_HALF_THRESHOLD) else 1.0
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._photo = None
        self._photo_id = None
        self._panning = False
        self._pan_last = None

        self.win = tk.Toplevel(root)
        self.win.title("ScreenSnap - 大图查看与标注")
        dw = max(1, int(self.w * self._scale))
        dh = max(1, int(self.h * self._scale))
        # 底部为两行内嵌工具栏，窗口高度相应增加
        self.win.geometry(f"{dw + 16}x{dh + 96}")
        self.win.minsize(480, 320)
        try:
            self.win.transient(root)
        except Exception:
            pass

        body = tk.Frame(self.win)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#262626", highlightthickness=0,
                                cursor="fleur")
        self.canvas.pack(fill="both", expand=True)

        # 内嵌工具栏：直接作为大图窗口底部 Frame 子控件（同窗口同图层），
        # 天然跟随窗口移动、随窗口销毁，无独立 Toplevel 的置顶/浮动/残留问题
        self.toolbar = _EmbeddedToolbar(self.win, self)
        self.toolbar.pack(fill="x", side="bottom")
        self._center_view()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<MouseWheel>", self._on_wheel_large)
        self.canvas.bind("<Configure>", self._on_resize)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.bind("<Return>", lambda e: self.done())
        self.win.protocol("WM_DELETE_WINDOW", self._close)  # 点窗口 X：一并销毁悬浮工具栏
        self.win.bind("<Control-z>", lambda e: self.undo())
        self.win.bind("<Control-Z>", lambda e: self.undo())
        for i, tool in enumerate(editor.TOOLS, 1):
            self.win.bind(f"<Key-{i}>",
                          lambda e, t=tool: self._on_tool_key(t))
        self.win.focus_force()
        self.toolbar._highlight_tool()
        self._refresh_display()

    # ---------- 视图：居中 / 平移 / 缩放（20%~200% 锚点缩放） ----------
    def _center_view(self):
        """初始视图：图片小于画布时居中，大于画布时左上角对齐（可拖动查看）。"""
        self.win.update_idletasks()
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        dw = self.w * self._scale
        dh = self.h * self._scale
        self._origin_x = max(0.0, (cw - dw) / 2)
        self._origin_y = max(0.0, (ch - dh) / 2)

    def _on_wheel_large(self, event):
        """滚轮缩放：以鼠标为锚点，范围 20%~200%（复用真无限画布锚点公式）。"""
        old = self._scale
        k = 1.1 if event.delta > 0 else 1 / 1.1
        new = max(0.2, min(2.0, old * k))
        if abs(new - old) < 1e-9:
            return
        cx, cy = event.x, event.y
        ratio = new / old
        self._scale = new
        self._origin_x = cx + (self._origin_x - cx) * ratio
        self._origin_y = cy + (self._origin_y - cy) * ratio
        self._refresh_display()

    def _pan_to(self, event):
        """空白拖动平移：直接更新视图偏移并移动既有 canvas item。"""
        if self._pan_last is None:
            return
        dx = event.x - self._pan_last[0]
        dy = event.y - self._pan_last[1]
        self._pan_last = (event.x, event.y)
        if dx == 0 and dy == 0:
            return
        self._origin_x += dx
        self._origin_y += dy
        for cid in (self._photo_id,):
            if cid is not None:
                self.canvas.move(cid, dx, dy)
        for obj in self._text_objs:
            if obj.get("item") is not None:
                self.canvas.move(obj["item"], dx, dy)

    # ---------- 坐标换算 ----------
    def _img_coords(self, event):
        """画布可视坐标 -> 图片坐标（世界坐标 = (画布 - origin) / scale）。"""
        return (int((event.x - self._origin_x) / self._scale),
                int((event.y - self._origin_y) / self._scale))

    def _text_at(self, x, y):
        """命中检测（x,y 为图片坐标，内部换算画布坐标后按 bbox 判定）。"""
        cx = x * self._scale + self._origin_x
        cy = y * self._scale + self._origin_y
        for obj in reversed(self._text_objs):
            item = obj.get("item")
            if item is None:
                continue
            bbox = self.canvas.bbox(item)
            if bbox and bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                return obj
        return None

    # ---------- 显示 ----------
    def _display_img(self):
        s = self._scale
        if s == 1.0:
            return self.img
        return self.img.resize((max(1, int(self.w * s)),
                                max(1, int(self.h * s))), 3)

    def _refresh_display(self):
        self.canvas.delete("all")
        self._photo = ImageTk.PhotoImage(self._display_img())
        self._photo_id = self.canvas.create_image(
            self._origin_x, self._origin_y, image=self._photo, anchor="nw")
        s = self._scale
        for obj in self._text_objs:
            layer, off = self._render_text_img(obj)
            if s != 1.0:
                layer = layer.resize((max(1, int(layer.width * s)),
                                      max(1, int(layer.height * s))), 3)
            obj["photo"] = ImageTk.PhotoImage(layer)
            obj["item"] = self.canvas.create_image(
                obj["x"] * s + self._origin_x - off * s,
                obj["y"] * s + self._origin_y - off * s,
                image=obj["photo"], anchor="nw")
        # 大图查看窗口不绘制工作区红框（浏览/编辑均不显示）

    # ---------- 鼠标交互（重写：空白平移 + 画布坐标换算） ----------
    def _on_press(self, event):
        if self._text_entry is not None:
            self._commit_text()
            return
        # Ctrl+左键：任意绘制工具下均可拖动平移画布（不必切回浏览工具）
        if event.state & 0x4:  # ControlMask
            self._panning = True
            self._pan_last = (event.x, event.y)
            self.canvas.configure(cursor="fleur")
            return
        if self.tool is None:
            # 浏览/平移模式：按下即拖动整幅图
            self._panning = True
            self._pan_last = (event.x, event.y)
            self.canvas.configure(cursor="fleur")
            return
        x, y = self._img_coords(event)
        # 图片外空白：开始平移画布（光标即时切手型，拖动过程中保持）
        if not (0 <= x < self.w and 0 <= y < self.h):
            self._panning = True
            self._pan_last = (event.x, event.y)
            self.canvas.configure(cursor="fleur")
            return
        hit = self._text_at(x, y)
        if hit is not None:
            self._drag_text = hit
            bbox = self.canvas.bbox(hit["item"])
            cx = x * self._scale + self._origin_x
            cy = y * self._scale + self._origin_y
            self._drag_off = (cx - bbox[0], cy - bbox[1])
            self.canvas.configure(cursor="fleur")
            return
        self._push_undo()
        if self.tool == editor.TOOL_TEXT:
            # 文字输入：光标切文本光标（xterm），提交/取消后恢复工具光标
            self.canvas.configure(cursor="xterm")
            self._open_text_entry(x, y, event.x, event.y)
            return
        self._start = (x, y)
        self._pen_points = [(x, y)]
        if self.tool == editor.TOOL_PEN:
            sx, sy = self._scale, self._scale
            self._preview = self.canvas.create_line(
                x * sx + self._origin_x, y * sy + self._origin_y,
                x * sx + self._origin_x + 1, y * sy + self._origin_y + 1,
                fill=self._hex(self.color), width=max(1, int(self.width * sx)))

    def _on_drag(self, event):
        if self._panning:
            self._pan_to(event)
            return
        # 拖动文字对象：实时移动（画布偏移 = 图片坐标差 * scale）
        if self._drag_text is not None:
            x, y = event.x, event.y
            obj = self._drag_text
            nx, ny = x - self._drag_off[0], y - self._drag_off[1]
            new_px = (nx - self._origin_x) / self._scale
            new_py = (ny - self._origin_y) / self._scale
            dx = (new_px - obj["x"]) * self._scale
            dy = (new_py - obj["y"]) * self._scale
            obj["x"], obj["y"] = new_px, new_py
            if obj["item"] is not None:
                self.canvas.move(obj["item"], dx, dy)
            return
        if not self._start:
            return
        x, y = self._img_coords(event)
        x0, y0 = self._start
        sx, sy = self._scale, self._scale
        ox, oy = self._origin_x, self._origin_y
        if self.tool == editor.TOOL_PEN:
            self._pen_points.append((x, y))
            pts = [(px * sx + ox, py * sy + oy) for px, py in self._pen_points]
            self.canvas.coords(self._preview, *[v for p in pts for v in p])
            return
        if self._preview:
            self.canvas.delete(self._preview)
        self.canvas.delete("preview")
        ax0, ay0 = x0 * sx + ox, y0 * sy + oy
        ax1, ay1 = x * sx + ox, y * sy + oy
        w = max(1, int(self.width * sx))
        col = self._hex(self.color)
        if self.tool in (editor.TOOL_RECT, editor.TOOL_MOSAIC):
            self._preview = self.canvas.create_rectangle(
                ax0, ay0, ax1, ay1, outline=col, width=w)
        elif self.tool == editor.TOOL_ELLIPSE:
            self._preview = self.canvas.create_oval(
                ax0, ay0, ax1, ay1, outline=col, width=w)
        elif self.tool == editor.TOOL_LINE:
            self._preview = self.canvas.create_line(
                ax0, ay0, ax1, ay1, fill=col, width=w)
        elif self.tool == editor.TOOL_ARROW:
            self._preview = self.canvas.create_line(
                ax0, ay0, ax1, ay1, fill=col, width=w)
            length = math.hypot(ax1 - ax0, ay1 - ay0)
            if length >= 8:
                ux, uy = (ax1 - ax0) / length, (ay1 - ay0) / length
                px, py = -uy, ux
                hl = max(10, w * 3)
                self.canvas.create_polygon(
                    [(ax1, ay1),
                     (ax1 - ux * hl + px * hl * 0.55, ay1 - uy * hl + py * hl * 0.55),
                     (ax1 - ux * hl - px * hl * 0.55, ay1 - uy * hl - py * hl * 0.55)],
                    fill=col, tags=("preview",))

    def _on_release(self, event):
        if self._panning:
            self._panning = False
            self._pan_last = None
            self._restore_tool_cursor()
            return
        if self._drag_text is not None:
            self._drag_text = None
            self._restore_tool_cursor()
            return
        if not self._start:
            return
        x, y = self._img_coords(event)
        x0, y0 = self._start
        self._start = None
        self._clear_preview()
        draw = ImageDraw.Draw(self.img)
        w = self.width
        if self.tool == editor.TOOL_RECT:
            draw.rectangle([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                           outline=self.color, width=w)
        elif self.tool == editor.TOOL_ELLIPSE:
            draw.ellipse([min(x0, x), min(y0, y), max(x0, x), max(y0, y)],
                         outline=self.color, width=w)
        elif self.tool == editor.TOOL_LINE:
            draw.line([(x0, y0), (x, y)], fill=self.color, width=w)
        elif self.tool == editor.TOOL_ARROW:
            self._draw_arrow(draw, x0, y0, x, y)
        elif self.tool == editor.TOOL_PEN:
            if len(self._pen_points) >= 2:
                draw.line(self._pen_points, fill=self.color, width=w,
                          joint="curve")
        elif self.tool == editor.TOOL_MOSAIC:
            self._apply_mosaic(min(x0, x), min(y0, y), max(x0, x), max(y0, y))
        self._refresh_display()

    def set_tool(self, tool):
        """切换绘制工具；None 表示浏览/平移模式（工具栏无高亮）。"""
        self.tool = tool
        self.toolbar._highlight_tool()
        self.canvas.configure(cursor="fleur" if tool is None else "crosshair")
        self._refresh_display()  # 刷新画布（大图窗口无工作区红框）

    def _restore_tool_cursor(self):
        """按当前工具恢复画布光标：浏览/平移=手型(fleur)，绘制工具=十字线(crosshair)。"""
        self.canvas.configure(cursor="fleur" if self.tool is None else "crosshair")

    def _commit_text(self):
        """提交文字输入后恢复工具光标（十字线/手型）。"""
        super()._commit_text()
        self._restore_tool_cursor()

    def _finish_text_entry(self, cancel=False):
        """结束文字输入（提交或 Esc 取消）后恢复工具光标。"""
        super()._finish_text_entry(cancel=cancel)
        self._restore_tool_cursor()

    def _edit_text_obj(self, obj, vx, vy):
        """双击编辑已有文字：输入时光标切文本光标(xterm)。"""
        super()._edit_text_obj(obj, vx, vy)
        self.canvas.configure(cursor="xterm")

    def _on_resize(self, event):
        """窗口/画布尺寸变化：图片重新适配居中，绘制/缩放/标注坐标随
        _origin/_scale 动态换算，天然保持正确。"""
        cw, ch = event.width, event.height
        if cw <= 0 or ch <= 0:
            return
        if (getattr(self, "_last_cw", None),
                getattr(self, "_last_ch", None)) == (cw, ch):
            return
        self._last_cw, self._last_ch = cw, ch
        dw = self.w * self._scale
        dh = self.h * self._scale
        self._origin_x = max(0.0, (cw - dw) / 2)
        self._origin_y = max(0.0, (ch - dh) / 2)
        self._refresh_display()
        # 内嵌工具栏为窗口子控件，随窗口布局自动贴底，无需手动排布

    def _save_to_gallery(self):
        """保存：标注合成图存为新文件并自动加入历史库，成功后刷新历史库画布。
        返回 True=保存成功；False=失败（已提示，调用方可决定是否关闭窗口）。"""
        try:
            if self.storage is None:
                messagebox.showwarning("ScreenSnap", "当前无法访问历史库存储。")
                return False
            self._finish_text_entry()
            self._flatten_texts()
            item = self.storage.save_image(self.img.convert("RGB"), None,
                                           self.save_fmt)
            if self.on_saved:
                try:
                    self.on_saved()
                except Exception:
                    pass
            messagebox.showinfo("ScreenSnap", f"已保存到历史库：{item['path']}")
            return True
        except Exception as e:
            messagebox.showerror("ScreenSnap", f"保存失败：{e}")
            return False

    # ---------- 完成 / 取消 / 盯住（大图场景均=关闭窗口，标注不落盘） ----------
    def done(self):
        """确认/完成：与「保存到历史库」相同——合成标注并保存为历史库新条目，
        成功后刷新历史库画布并提示，再关闭窗口；保存失败则提示并保持窗口。"""
        if self._save_to_gallery():
            self._close()

    def cancel(self):
        self._close()

    def pin(self):
        """盯住：与截图后盯住行为一致——合成标注图保存到历史库，
        并作为贴图（StickerWindow）钉在屏幕上（位置取大图窗口当前位置）。"""
        try:
            self._finish_text_entry()
            self._flatten_texts()
            if self.storage is not None:
                self.storage.save_image(self.img.convert("RGB"), None,
                                        self.save_fmt)
        except Exception:
            pass
        try:
            from sticker import StickerWindow
            StickerWindow(self.root, self.img.convert("RGB"),
                          self.win.winfo_x(), self.win.winfo_y())
        except Exception:
            pass
        self._close()

    def _close(self):
        try:
            self._finish_text_entry()
        except Exception:
            pass
        # 内嵌工具栏是窗口子控件，随 win.destroy() 一并销毁，无残留
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass

    # ---------- 底部按钮 ----------
    def _copy_img(self):
        from clipboard import copy_image_to_clipboard
        try:
            ok = copy_image_to_clipboard(self.img.convert("RGB"))
            messagebox.showinfo("ScreenSnap", "已复制到剪贴板" if ok else "复制失败")
        except Exception as e:
            messagebox.showerror("ScreenSnap", f"复制失败：{e}")

    def _save_as(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg")],
            initialfile="截图.png")
        if p:
            try:
                self.img.convert("RGB").save(p)
                messagebox.showinfo("ScreenSnap", f"已保存：{p}")
            except Exception as e:
                messagebox.showerror("ScreenSnap", f"保存失败：{e}")

    def _delete_btn(self):
        if self.on_delete:
            try:
                self.on_delete()
            except Exception:
                pass
        self._close()


class _VideoViewer:
    """历史库录屏视频播放窗口：双击视频条目自动打开并开始播放。
    使用 imageio_ffmpeg.read_frames 逐帧解码（rgb24 bytes -> PIL），
    画布显示 + 暂停/继续 + 重新播放 + 关闭。"""

    def __init__(self, root, path, on_close=None):
        self.root = root
        self.path = path
        self.on_close = on_close
        self._gen = None
        self._vw = 320
        self._vh = 180
        self._fps = 15.0
        self._playing = False
        self._stopped = False
        self._frame_idx = 0
        self._total = 0
        self._photo = None

        try:
            import imageio_ffmpeg
            self._gen = imageio_ffmpeg.read_frames(path, pix_fmt="rgb24")
            meta = next(self._gen)  # 首项为 meta dict
            self._vw = int(meta.get("size", (320, 180))[0])
            self._vh = int(meta.get("size", (320, 180))[1])
            self._fps = float(meta.get("fps", 15.0) or 15.0)
        except Exception:
            self._gen = None
        try:
            import imageio_ffmpeg as _f
            n, _ = _f.count_frames_and_secs(path)
            self._total = int(n or 0)
        except Exception:
            self._total = 0

        # 显示尺寸：视频不超过屏幕 90%
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        self._disp_scale = min(1.0, (sw * 0.9) / max(1, self._vw),
                               (sh * 0.9) / max(1, self._vh))
        dw = max(1, int(self._vw * self._disp_scale))
        dh = max(1, int(self._vh * self._disp_scale))

        self.win = tk.Toplevel(root)
        self.win.title(os.path.basename(path))
        self.win.geometry(f"{dw + 16}x{dh + 96}")
        self.win.minsize(360, 220)
        try:
            self.win.transient(root)
        except Exception:
            pass
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        top = tk.Frame(self.win, bg="#1f1f1f")
        top.pack(fill="x")
        tk.Label(top, text=os.path.basename(path), bg="#1f1f1f", fg="#ffb800",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=10, pady=6)
        self.info_var = tk.StringVar(value="")
        tk.Label(top, textvariable=self.info_var, bg="#1f1f1f", fg="#cccccc",
                 font=("Microsoft YaHei UI", 9)).pack(side="right", padx=10)

        self.canvas = tk.Canvas(self.win, bg="#000000",
                                highlightthickness=0, width=dw, height=dh)
        self.canvas.pack(fill="both", expand=True)

        bar = tk.Frame(self.win, bg="#2a2a2a")
        bar.pack(fill="x")
        self._btn_play = tk.Button(bar, text="暂停", command=self._toggle_play,
                                   font=("Microsoft YaHei UI", 9), width=6)
        self._btn_play.pack(side="left", padx=8, pady=5)
        tk.Button(bar, text="重新播放", command=self._restart,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=4, pady=5)
        tk.Button(bar, text="关闭", command=self._close,
                  font=("Microsoft YaHei UI", 9)).pack(side="right", padx=8, pady=5)

        if self._gen is None:
            self.info_var.set("无法解码视频")
            return
        # 打开即自动开始播放
        self._playing = True
        self.win.after(50, self._step)

    # ---------- 播放 ----------
    def _step(self):
        if self._stopped:
            return
        if not self._playing:
            self.win.after(int(1000 / max(1.0, self._fps)), self._step)
            return
        try:
            data = next(self._gen)
        except StopIteration:
            self._playing = False
            self._btn_play.configure(text="播放")
            self.info_var.set(f"播放完成" + (f" · {self._total} 帧" if self._total else ""))
            return
        except Exception:
            self._playing = False
            self.info_var.set("解码中断")
            return
        try:
            img = Image.frombytes("RGB", (self._vw, self._vh), data)
            if self._disp_scale < 0.999:
                img = img.resize((max(1, int(img.width * self._disp_scale)),
                                  max(1, int(img.height * self._disp_scale))), 2)
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
            self._frame_idx += 1
            if self._total:
                self.info_var.set(f"{self._frame_idx} / {self._total} 帧")
        except Exception:
            self._playing = False
            self.info_var.set("显示失败")
            return
        self.win.after(int(1000 / max(1.0, self._fps)), self._step)

    def _toggle_play(self):
        if self._stopped:
            return
        if self._playing:
            self._playing = False
            self._btn_play.configure(text="播放")
        else:
            if self._gen is None:
                return
            self._playing = True
            self._btn_play.configure(text="暂停")
            self.win.after(int(1000 / max(1.0, self._fps)), self._step)

    def _restart(self):
        try:
            if self._gen is not None:
                self._gen.close()
        except Exception:
            pass
        self._gen = None
        try:
            import imageio_ffmpeg
            self._gen = imageio_ffmpeg.read_frames(self.path, pix_fmt="rgb24")
            next(self._gen)
        except Exception:
            self._gen = None
            self.info_var.set("无法重新解码")
            return
        self._frame_idx = 0
        self._playing = True
        self._btn_play.configure(text="暂停")
        self.win.after(50, self._step)

    def _close(self):
        self._stopped = True
        try:
            if self._gen is not None:
                self._gen.close()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
