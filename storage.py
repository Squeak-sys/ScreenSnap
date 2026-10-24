# -*- coding: utf-8 -*-
"""存储管理：截图入库、缩略图缓存、3 天回收站、录屏视频登记。"""
import json
import os
import re
import shutil
import subprocess
import time

GALLERY_JSON = "gallery.json"
TRASH_DIR = ".trash"
TRASH_JSON = "trash.json"
THUMB_SUFFIX = "_thumb"
RECORD_DIR = "录屏"          # 录屏保存根目录（storage.save_root/录屏/YYYY-MM-DD）
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now_ts():
    return int(time.time())


def date_dir_name(ts=None):
    ts = ts if ts is not None else time.time()
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class Storage:
    def __init__(self, save_root, trash_days=3):
        self.save_root = save_root
        self.trash_days = trash_days
        os.makedirs(save_root, exist_ok=True)
        self.gallery_path = os.path.join(save_root, GALLERY_JSON)
        self.trash_path = os.path.join(save_root, TRASH_DIR, TRASH_JSON)
        self.cleanup_trash()

    # ---------- 基础读写 ----------
    def _read_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def load_gallery(self):
        return self._read_json(self.gallery_path, [])

    def save_gallery(self, items):
        self._write_json(self.gallery_path, items)

    # ---------- 录屏视频登记 ----------
    def scan_videos(self):
        """扫描 save_root/录屏/YYYY-MM-DD/*.mp4，将未登记的视频登记入历史库
        （kind=video，含 ffmpeg 抽帧缩略图）。返回本次新登记数量。"""
        gallery = self.load_gallery()
        known = {it.get("path") for it in gallery}
        rec_root = os.path.join(self.save_root, RECORD_DIR)
        if not os.path.isdir(rec_root):
            return 0
        new_items = []
        for day in sorted(os.listdir(rec_root)):
            day_dir = os.path.join(rec_root, day)
            if not os.path.isdir(day_dir) or not _DAY_RE.match(day):
                continue
            for fn in sorted(os.listdir(day_dir)):
                if not fn.lower().endswith(".mp4"):
                    continue
                p = os.path.join(day_dir, fn)
                if p in known:
                    continue
                thumb = os.path.join(day_dir, os.path.splitext(fn)[0]
                                     + THUMB_SUFFIX + ".png")
                self._make_video_thumb(p, thumb)
                try:
                    ts = int(os.path.getmtime(p))
                except Exception:
                    ts = _now_ts()
                new_items.append({
                    "name": fn, "date": day, "path": p,
                    "thumb": thumb if os.path.exists(thumb) else p,
                    "ts": ts, "order": 0, "kind": "video",
                })
        if not new_items:
            return 0
        # 按日期归位：视频条目插入到对应（或更早）日期分组之后，保证日期顺序
        for it in new_items:
            pos = len(gallery)
            for i in range(len(gallery) - 1, -1, -1):
                if gallery[i].get("date", "") <= it["date"]:
                    pos = i + 1
                    break
            gallery.insert(pos, it)
        for i, g in enumerate(gallery):
            g["order"] = i
        self.save_gallery(gallery)
        return len(new_items)

    @staticmethod
    def _make_video_thumb(video_path, thumb_path, max_w=200, max_h=140):
        """用 ffmpeg 抽取视频首帧生成缩略图（已存在则跳过）。"""
        if os.path.exists(thumb_path):
            return thumb_path
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
            cmd = [exe, "-y", "-ss", "0.1", "-i", video_path,
                   "-frames:v", "1",
                   "-vf", f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease",
                   "-q:v", "3", thumb_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        except Exception:
            pass
        return thumb_path if os.path.exists(thumb_path) else None

    # ---------- 截图入库 ----------
    def save_image(self, img, ts=None, fmt="png"):
        """保存截图 -> 返回条目 dict。img 为 PIL Image（建议 RGB）。"""
        ts = ts if ts is not None else time.time()
        date_str = date_dir_name(ts)
        day_dir = os.path.join(self.save_root, date_str)
        os.makedirs(day_dir, exist_ok=True)
        name = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        # 避免同秒冲突
        idx = 1
        while True:
            fname = f"{name}_{idx}.{fmt}" if idx > 1 else f"{name}.{fmt}"
            full = os.path.join(day_dir, fname)
            if not os.path.exists(full):
                break
            idx += 1
        img.save(full, "JPEG" if fmt.lower() == "jpg" else "PNG")
        # 缩略图
        thumb_path = os.path.join(day_dir, os.path.splitext(fname)[0] + THUMB_SUFFIX + ".png")
        self.make_thumb(img, thumb_path)
        item = {
            "name": fname,
            "date": date_str,
            "path": full,
            "thumb": thumb_path,
            "ts": int(ts),
            "order": 0,
        }
        items = self.load_gallery()
        item["order"] = len(items)
        items.append(item)
        self.save_gallery(items)
        return item

    @staticmethod
    def make_thumb(img, thumb_path, max_w=200, max_h=140):
        """生成缩略图并缓存到磁盘。"""
        if os.path.exists(thumb_path):
            return thumb_path
        im = img.copy()
        im.thumbnail((max_w, max_h), 3)  # LANCZOS
        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
        im.save(thumb_path, "PNG")
        return thumb_path

    def thumb_for(self, item):
        p = item.get("thumb")
        if p and os.path.exists(p):
            return p
        # 兜底：重新生成
        try:
            from PIL import Image
            src = Image.open(item["path"])
            return self.make_thumb(src, item["path"] + ".thumb.png")
        except Exception:
            return None

    # ---------- 回收站（3 天） ----------
    def delete_to_trash(self, items):
        """把条目移入回收站，记录删除时间。"""
        trash_dir = os.path.join(self.save_root, TRASH_DIR)
        os.makedirs(trash_dir, exist_ok=True)
        trash = self._read_json(self.trash_path, [])
        gallery = self.load_gallery()
        gallery_keys = set()
        for it in items:
            p = it.get("path", "")
            if not p or not os.path.exists(p):
                continue
            rel = os.path.relpath(p, self.save_root)
            # 移动文件到回收站（保留相对路径，避免重名）
            dest = os.path.join(trash_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.move(p, dest)
            except Exception:
                continue
            # 缩略图一并移动
            tp = it.get("thumb")
            if tp and os.path.exists(tp):
                try:
                    shutil.move(tp, os.path.join(trash_dir, os.path.relpath(tp, self.save_root)))
                except Exception:
                    pass
            trash.append({"path": dest, "deleted_ts": _now_ts(), "src": p})
            gallery_keys.add(p)
        if trash:
            self._write_json(self.trash_path, trash)
        if gallery_keys:
            gallery = [g for g in gallery if g.get("path") not in gallery_keys]
            # 重排 order
            for i, g in enumerate(gallery):
                g["order"] = i
            self.save_gallery(gallery)
        return len(gallery_keys)

    def cleanup_trash(self):
        """清理超过 trash_days 的回收站文件。启动/打开历史库时调用。"""
        trash_dir = os.path.join(self.save_root, TRASH_DIR)
        if not os.path.exists(trash_dir):
            return 0
        trash = self._read_json(self.trash_path, [])
        if not trash:
            return 0
        deadline = _now_ts() - self.trash_days * 86400
        keep = []
        removed = 0
        for it in trash:
            if it.get("deleted_ts", 0) < deadline:
                try:
                    if os.path.exists(it["path"]):
                        if os.path.isdir(it["path"]):
                            shutil.rmtree(it["path"])
                        else:
                            os.remove(it["path"])
                    removed += 1
                except Exception:
                    keep.append(it)
            else:
                keep.append(it)
        if keep:
            self._write_json(self.trash_path, keep)
        else:
            try:
                os.remove(self.trash_path)
            except Exception:
                pass
        return removed

    def trash_count(self):
        trash = self._read_json(self.trash_path, [])
        return len(trash)
