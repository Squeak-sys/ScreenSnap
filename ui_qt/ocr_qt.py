# -*- coding: utf-8 -*-
"""OCR 引擎层（P9-1）。

职责：
    * 提供统一的 ``recognize(image) -> list[OcrLine]`` 接口，屏蔽后端差异；
    * 内置两种后端：
        - ``RapidOcrBackend``  RapidOCR(onnxruntime) + PP-OCRv6 small 模型，精度高、离线；
        - ``WinOcrBackend``    Windows.Media.Ocr（系统语言包），零模型体积、极快、精度中；
    * 提供 ``OcrTask``（QThread）异步包装，避免主线程卡顿；
    * 提供行合并工具（同一视觉行的多个框聚合、中文词间空格清理）。

设计要点：
    * 依赖均为「可选」：未安装 rapidocr / winrt 时各自 ``available()`` 返回 False，
      调用方据此降级或提示，不会因缺包导致程序无法启动。
    * 引擎优先级：装好软件后**默认用本机 Windows 系统 OCR**（零模型、最快）；
      系统 OCR 不可用时才退到用户自行安装的高精度组件包；用户在设置里显式选择
      某个引擎后，该选择被严格采用并在后续启动时自动生效（见 ``engine_prefer``
      与 :func:`auto_candidates`）。
    * 模型懒加载：首次识别时才构建引擎，之后复用；按线程隔离，避免跨线程共享。
    * 返回坐标一律换算回原图尺度（Windows OCR 后端内部做过上采样）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from PIL import Image

from PySide6.QtCore import QThread, Signal

import config as _cfg

# ---------------------------------------------------------------- 可选组件包
# 设计（方案 C 双模式）：主程序只带 Windows 系统 OCR（约 5MB）；RapidOCR 高精度
# 组件以「外挂包」形式安装到 %APPDATA%\ScreenSnap\ocr_pack，运行时注入 sys.path，
# 主程序体积不受影响，安装后完全离线可用。

PACK_META = "pack.json"
PACK_MIN_VERSION = 1


def pack_dir() -> str:
    """组件包安装目录。"""
    return os.path.join(_cfg.APP_DIR, "ocr_pack")


def _read_pack_meta() -> dict | None:
    p = os.path.join(pack_dir(), PACK_META)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def pack_installed() -> bool:
    """组件包是否已安装（只看目录与元信息，不实际 import）。"""
    meta = _read_pack_meta()
    if not meta:
        return False
    return int(meta.get("version", 0)) >= PACK_MIN_VERSION and os.path.isdir(
        os.path.join(pack_dir(), "rapidocr")
    )


def _inject_pack_path() -> bool:
    """把组件包目录挂到 sys.path 末尾（主程序自带的包优先），并登记 DLL 目录。"""
    if not pack_installed():
        return False
    d = pack_dir()
    if d not in sys.path:
        sys.path.append(d)  # append：避免覆盖主程序已有的 numpy 等
    if hasattr(os, "add_dll_directory"):
        for sub in ("", "onnxruntime", "cv2", os.path.join("onnxruntime", "capi")):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                try:
                    os.add_dll_directory(p)
                except OSError:
                    pass
    return True


def pack_info() -> dict:
    """组件包状态，供设置页/结果窗展示。"""
    meta = _read_pack_meta() or {}
    size = 0
    d = pack_dir()
    if os.path.isdir(d):
        for root, _dirs, files in os.walk(d):
            for f in files:
                try:
                    size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    return {
        "installed": pack_installed(),
        "path": d,
        "version": meta.get("version", 0),
        "created": meta.get("created", ""),
        "size_mb": round(size / 1048576, 1),
    }


def install_pack_from_zip(zip_path: str, on_progress: Callable[[int, int], None] | None = None) -> str:
    """从本地 zip 安装组件包（同步执行，调用方应放到后台线程）。

    流程：解压到临时目录 → 校验 rapidocr 目录 → 原子替换旧目录。返回安装目录。
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"组件包不存在：{zip_path}")
    target = pack_dir()
    staging = target + ".new"
    old = target + ".old"
    for p in (staging, old):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        total = sum(m.file_size for m in members) or 1
        done = 0
        for m in members:
            zf.extract(m, staging)
            done += m.file_size
            if on_progress:
                on_progress(done, total)

    # 兼容「压缩包内多一层根目录」的情况
    if not os.path.isdir(os.path.join(staging, "rapidocr")):
        subs = [d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))]
        if len(subs) == 1 and os.path.isdir(os.path.join(staging, subs[0], "rapidocr")):
            nested = os.path.join(staging, subs[0])
            for name in os.listdir(nested):
                shutil.move(os.path.join(nested, name), staging)
            os.rmdir(nested)

    if not os.path.isdir(os.path.join(staging, "rapidocr")):
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("组件包内容不合法：缺少 rapidocr 目录")

    meta_path = os.path.join(staging, PACK_META)
    if not os.path.isfile(meta_path):
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"version": PACK_MIN_VERSION,
                       "created": time.strftime("%Y-%m-%d %H:%M:%S")}, f,
                      ensure_ascii=False, indent=2)

    if os.path.isdir(target):
        os.rename(target, old)
    os.rename(staging, target)
    shutil.rmtree(old, ignore_errors=True)

    _inject_pack_path()
    _reset_backend_cache()
    return target


def download_pack(url: str, on_progress: Callable[[int, int], None] | None = None,
                  timeout: float = 30) -> str:
    """下载组件包到本地并返回文件路径（同步执行，调用方放后台线程）。"""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("下载地址必须以 http(s):// 开头")
    os.makedirs(_cfg.APP_DIR, exist_ok=True)
    out = os.path.join(_cfg.APP_DIR, "ocr_pack_download.zip")
    req = urllib.request.Request(url, headers={"User-Agent": "ScreenSnap/OCR-pack"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(out, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total or done)
    return out


def remove_pack() -> None:
    """卸载组件包（回到仅系统 OCR 模式）。"""
    d = pack_dir()
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    if d in sys.path:
        sys.path.remove(d)
    _reset_backend_cache()

# ---------------------------------------------------------------- 数据结构


@dataclass
class OcrLine:
    """一行识别结果。box 为原图坐标系下的 (x, y, w, h)。"""

    text: str
    box: tuple[int, int, int, int] = (0, 0, 0, 0)
    score: float = 1.0

    @property
    def top(self) -> int:
        return self.box[1]

    @property
    def bottom(self) -> int:
        return self.box[1] + self.box[3]

    @property
    def left(self) -> int:
        return self.box[0]

    @property
    def cx(self) -> float:
        return self.box[0] + self.box[2] / 2

    @property
    def cy(self) -> float:
        return self.box[1] + self.box[3] / 2


class OcrUnavailable(RuntimeError):
    """没有可用后端时抛出。"""


# ---------------------------------------------------------------- 文本工具

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
_RE_CJK_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_RE_SPACES = re.compile(r"[ \t]{2,}")

# 代码/HUD 类文本中容易与代码混淆的标点，仅在 code_mode 下替换
_CODE_PUNCT = {"．": ".", "（": "(", "）": ")", "“": '"', "”": '"', "：": ":"}


def clean_text(text: str, *, code_mode: bool = False) -> str:
    """清理后端输出：合并汉字之间的空格、压缩多余空白、修正常见误识别标点。"""
    if not text:
        return ""
    t = text.replace("\u00a0", " ")
    t = _RE_CJK_GAP.sub("", t)
    if code_mode:
        for k, v in _CODE_PUNCT.items():
            t = t.replace(k, v)
    t = _RE_SPACES.sub(" ", t).strip()
    return t


def group_rows(lines: Sequence[OcrLine], tol_ratio: float = 0.6) -> list[list[OcrLine]]:
    """把零散框按视觉行聚类，返回按「行 → 列」排好序的分组。

    用于表格/多列排版：同一水平带内的框视为一行，行内按 x 排序。
    """
    items = [ln for ln in lines if ln.text.strip()]
    if not items:
        return []
    items.sort(key=lambda l: (l.cy, l.left))
    rows: list[list[OcrLine]] = []
    for ln in items:
        placed = False
        if rows:
            row = rows[-1]
            ref = sum(x.cy for x in row) / len(row)
            ref_h = max(sum(x.box[3] for x in row) / len(row), 1)
            if abs(ln.cy - ref) <= ref_h * tol_ratio:
                row.append(ln)
                placed = True
        if not placed:
            rows.append([ln])
    for row in rows:
        row.sort(key=lambda l: l.left)
    rows.sort(key=lambda r: min(x.top for x in r))
    return rows


def to_text(lines: Iterable[OcrLine], *, layout: str = "auto", code_mode: bool = False) -> str:
    """把识别结果转成文本。

    layout:
        * ``lines``  每个框一行；
        * ``auto``   按视觉行聚类后再拼接（表格/多列更整齐）；
        * ``layout`` 按原图版式还原（所见即所得）：行首按左边距补缩进，
                     行内多列按横向间距补空格，尽量与截图的排布一致。
    """
    lines = list(lines)
    if layout == "lines":
        seq = [[ln] for ln in sorted(lines, key=lambda l: (l.cy, l.left))]
    else:
        seq = group_rows(lines)
    if layout == "layout":
        return to_text_by_layout(seq, code_mode=code_mode)
    out: list[str] = []
    for row in seq:
        if len(row) == 1:
            out.append(clean_text(row[0].text, code_mode=code_mode))
        else:
            parts = [clean_text(x.text, code_mode=code_mode) for x in row]
            out.append("  ".join(p for p in parts if p))
    return "\n".join(x for x in out if x)


def to_text_by_layout(seq: Sequence[Sequence[OcrLine]], *, code_mode: bool = False) -> str:
    """按原图像素坐标还原文本排布：缩进 + 列间距（所见即所得，不做智能重排）。

    seq 为 ``group_rows`` 产出的「行 → 列」分组。以整体最小左边距为基准，
    用「框宽 / 字符数」的中位数估算单字符宽度，把像素偏移换算成前导空格数，
    行内相邻框之间按横向间隔补空格，使结果贴近截图中的版式。
    """
    valid = [ln for row in seq for ln in row if ln.text.strip()]
    if not valid:
        return ""
    base_left = min(ln.left for ln in valid)
    widths: list[float] = []
    for ln in valid:
        t = clean_text(ln.text, code_mode=code_mode)
        n = len(t.replace(" ", ""))
        if n:
            widths.append(ln.box[2] / n)
    widths.sort()
    char_w = widths[len(widths) // 2] if widths else 8.0
    char_w = max(float(char_w), 4.0)

    out: list[str] = []
    for row in seq:
        buf = ""
        prev = None
        for ln in row:
            t = clean_text(ln.text, code_mode=code_mode)
            if not t:
                continue
            if prev is None:
                lead = int(round((ln.left - base_left) / char_w))
                buf = " " * max(0, min(lead, 60)) + t
            else:
                gap = int(round((ln.left - (prev.left + prev.box[2])) / char_w))
                buf += " " * max(2, min(gap, 40)) + t
            prev = ln
        if buf.strip():
            out.append(buf.rstrip())
    return "\n".join(out)


# ---------------------------------------------------------------- 后端基类


class BaseBackend:
    """OCR 后端接口。子类实现 ``available`` 与 ``_run``。"""

    id = "base"
    label = "未知后端"

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @classmethod
    def available(cls) -> bool:
        return False

    def recognize(self, image: Image.Image) -> list[OcrLine]:
        with self._lock:
            return self._run(image)

    def _run(self, image: Image.Image) -> list[OcrLine]:  # pragma: no cover - 抽象
        raise NotImplementedError


# ---------------------------------------------------------------- Windows OCR


class WinOcrBackend(BaseBackend):
    """Windows.Media.Ocr（依赖 pywinrt 模块 + 系统 OCR 语言包）。"""

    id = "winocr"
    label = "Windows 系统 OCR"

    @classmethod
    def status(cls) -> str:
        """细化引擎状态，供设置页展示：

        - ``ok``        中文（zh-CN）OCR 语言包已安装；
        - ``no_zh_pack`` 系统有 OCR 引擎但不含中文语言包（仅能识别其它语种）；
        - ``no_engine``  系统完全没装 OCR 语言包；
        - ``no_module``  运行库缺失（winrt 导入失败）。
        """
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine
        except Exception:
            return "no_module"
        try:
            if OcrEngine.try_create_from_language(Language("zh-CN")) is not None:
                return "ok"
            if OcrEngine.try_create_from_user_profile_languages() is not None:
                return "no_zh_pack"
            return "no_engine"
        except Exception:
            return "no_module"

    @classmethod
    def available(cls) -> bool:
        return cls.status() in ("ok", "no_zh_pack")

    def _run(self, image: Image.Image) -> list[OcrLine]:
        import asyncio
        import os
        import tempfile

        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile

        # 系统 OCR 对行高 < 24px 的小字掉字明显，小图做 2 倍上采样，坐标随后换算回原尺度
        scale = 2.0 if max(image.size) < 1400 else 1.0
        work = image
        if scale != 1.0:
            work = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.LANCZOS,
            )

        engine = OcrEngine.try_create_from_language(Language("zh-CN"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OcrUnavailable("系统未安装 OCR 语言包")

        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            work.convert("RGB").save(tmp, format="PNG")

            async def _recognize():
                f = await StorageFile.get_file_from_path_async(tmp)
                stream = await f.open_async(FileAccessMode.READ)
                try:
                    decoder = await BitmapDecoder.create_async(stream)
                    bmp = await decoder.get_software_bitmap_async()
                    return await engine.recognize_async(bmp)
                finally:
                    stream.close()

            res = asyncio.run(_recognize())
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

        out: list[OcrLine] = []
        for ln in res.lines:
            words = list(ln.words)
            if words:
                x0 = min(w.bounding_rect.x for w in words)
                y0 = min(w.bounding_rect.y for w in words)
                x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
                y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
            else:
                x0 = y0 = x1 = y1 = 0
            box = (
                int(x0 / scale),
                int(y0 / scale),
                int(max(x1 - x0, 1) / scale),
                int(max(y1 - y0, 1) / scale),
            )
            out.append(OcrLine(text=clean_text(ln.text), box=box, score=1.0))
        return out


# ---------------------------------------------------------------- RapidOCR


class RapidOcrBackend(BaseBackend):
    """RapidOCR（onnxruntime）后端，精度优先，模型随包内置。"""

    id = "rapidocr"
    label = "RapidOCR（高精度）"

    _local = threading.local()

    @classmethod
    def available(cls) -> bool:
        if cls._import_ok():
            return True
        # 组件包可能刚装好：注入 sys.path 后清掉半成品模块再试一次
        if _inject_pack_path():
            cls._purge_stale_imports()
            return cls._import_ok()
        return False

    @staticmethod
    def _import_ok() -> bool:
        try:
            import onnxruntime  # noqa: F401
            import rapidocr  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _purge_stale_imports() -> None:
        for name in [m for m in sys.modules if m == "rapidocr" or m.startswith("rapidocr.")]:
            sys.modules.pop(name, None)

    def _engine(self):
        eng = getattr(self._local, "engine", None)
        if eng is None:
            from rapidocr import RapidOCR

            eng = RapidOCR()
            self._local.engine = eng
        return eng

    def _run(self, image: Image.Image) -> list[OcrLine]:
        import numpy as np

        arr = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])  # RGB -> BGR
        res = self._engine()(arr)
        txts = getattr(res, "txts", None) or ()
        boxes = getattr(res, "boxes", None)
        scores = getattr(res, "scores", None)

        out: list[OcrLine] = []
        if boxes is None:
            for i, t in enumerate(txts):
                s = float(scores[i]) if scores is not None and i < len(scores) else 1.0
                out.append(OcrLine(text=clean_text(t), score=s))
            return out

        for i, (pts, t) in enumerate(zip(boxes, txts)):
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            box = (
                int(min(xs)),
                int(min(ys)),
                int(max(max(xs) - min(xs), 1)),
                int(max(max(ys) - min(ys), 1)),
            )
            s = float(scores[i]) if scores is not None and i < len(scores) else 1.0
            out.append(OcrLine(text=clean_text(t), box=box, score=s))
        return out


# ---------------------------------------------------------------- 工厂与调度

_BACKENDS = {RapidOcrBackend.id: RapidOcrBackend, WinOcrBackend.id: WinOcrBackend}
# 展示顺序：本机系统 OCR 在前（默认档），组件包在后（可选增强）
_ORDER = ("winocr", "rapidocr")
_cache: dict[str, BaseBackend] = {}
_cache_lock = threading.Lock()


def auto_candidates() -> list[str]:
    """「自动」档的候选顺序。

    产品口径：装好软件默认就用**本机系统 OCR**；只有系统 OCR 不可用（缺中文
    语言包或运行库）时才退到用户自己安装的高精度组件包；两者都没有时返回空
    列表，由调用方弹出提示引导用户下载组件包或安装系统语言包。
    """
    try:
        win_status = WinOcrBackend.status()
    except Exception:
        win_status = "no_module"
    rapid = False
    try:
        rapid = RapidOcrBackend.available()
    except Exception:
        rapid = False

    if win_status == "ok":
        return ["winocr"] + (["rapidocr"] if rapid else [])
    if rapid:
        return ["rapidocr"]
    if win_status == "no_zh_pack":
        return ["winocr"]  # 只有其它语种引擎，至少能让非中文场景可用
    return []


def _reset_backend_cache() -> None:
    """组件包安装/卸载后清缓存，避免继续持有失效实例。"""
    with _cache_lock:
        _cache.clear()



def available_backends() -> list[dict]:
    """列出后端可用性，供设置页/结果窗展示。

    每项含 ``id`` / ``label`` / ``available`` / ``status``；``status`` 对系统 OCR
    会细分到语言包级别（见 ``WinOcrBackend.status``）。
    """
    info = []
    for bid in _ORDER:
        cls = _BACKENDS[bid]
        try:
            ok = cls.available()
        except Exception:
            ok = False
        status = "ok" if ok else "missing"
        if bid == "winocr":
            try:
                status = cls.status()
            except Exception:
                status = "no_module"
        info.append({"id": bid, "label": cls.label, "available": ok, "status": status})
    return info


def reset_backend_cache() -> None:
    """组件包安装/移除后由 UI 调用，清掉缓存的引擎实例。"""
    _reset_backend_cache()


def engine_prefer(cfg: dict | None = None) -> str | None:
    """读取配置中的默认精度档；``auto`` 归一为 None（交由优先级自动选择）。"""
    try:
        data = cfg if cfg is not None else _cfg.load_config()
        value = str(data.get("ocr_engine") or "auto").strip().lower()
    except Exception:
        return None
    return value if value in _BACKENDS else None


def open_language_settings() -> bool:
    """打开 Windows「语言和区域」设置页，引导安装中文 OCR 语言包。"""
    try:
        os.startfile("ms-settings:regionlanguage")
        return True
    except Exception:
        return False


def get_backend(prefer: str | None = None) -> BaseBackend:
    """取后端实例。

    * ``prefer`` 给了具体引擎（winocr / rapidocr）→ 只用该引擎，不可用即抛错，
      保证用户在设置里「明确指定」的引擎被严格采用；
    * ``prefer`` 为空或 auto → 走 :func:`auto_candidates`：默认本机系统 OCR，
      系统不可用时才退到已安装的组件包。
    """
    if prefer and prefer in _BACKENDS:
        ids = [prefer]
    else:
        ids = auto_candidates()
    for bid in ids:
        cls = _BACKENDS[bid]
        if cls.available():
            with _cache_lock:
                inst = _cache.get(bid)
                if inst is None:
                    inst = cls()
                    _cache[bid] = inst
            return inst
    raise OcrUnavailable(
        "本机未安装 Windows 中文 OCR 语言包，高精度组件包也未安装："
        "请安装系统语言包，或下载高精度组件包后使用。"
    )


def recognize(image: Image.Image, prefer: str | None = None) -> list[OcrLine]:
    """同步识别（调用方须保证不在主线程执行）。"""
    return get_backend(prefer).recognize(image)


class OcrTask(QThread):
    """后台识别线程。

    用法::

        task = OcrTask(image)
        task.done.connect(on_done)     # list[OcrLine]
        task.failed.connect(on_fail)   # str
        task.start()
    """

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, image: Image.Image, prefer: str | None = None, parent=None):
        super().__init__(parent)
        self._image = image.copy()
        self._prefer = prefer

    def run(self) -> None:  # noqa: D102
        try:
            self.progress.emit("正在识别…")
            lines = recognize(self._image, self._prefer)
            self.done.emit(lines)
        except Exception as exc:  # 后端异常统一上报，避免线程静默退出
            self.failed.emit(str(exc))


class PackInstallTask(QThread):
    """后台安装高精度组件包（本地 zip 或在线下载）。

    信号：
        progress(done_bytes, total_bytes)
        done(str 安装目录)
        failed(str 错误信息)
    """

    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, src: str, is_url: bool = False, parent=None):
        super().__init__(parent)
        self._src = src
        self._is_url = is_url

    def _on_prog(self, done: int, total: int) -> None:
        self.progress.emit(int(done), int(total))

    def run(self) -> None:  # noqa: D102
        try:
            zip_path = self._src
            if self._is_url:
                zip_path = download_pack(self._src, self._on_prog)
            target = install_pack_from_zip(zip_path, self._on_prog)
            if self._is_url:
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            self.done.emit(target)
        except Exception as exc:
            self.failed.emit(str(exc))

