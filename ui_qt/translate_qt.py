# -*- coding: utf-8 -*-
"""翻译后端（P10）：把 OCR 识别到的文字翻成目标语言。

支持服务商（都走标准 HTTP，不额外依赖第三方 SDK）：
    baidu    百度翻译开放平台（国内直连，个人实名后每月免费额度）
    azure    Azure AI Translator（F0 免费层 200 万字符/月）
    deepl    DeepL API Free（50 万字符/月）
    libre    LibreTranslate（可指向自建/局域网实例，完全离线）
    mymemory MyMemory 公共接口（零配置，无需 Key，量小、质量一般）

凭据来源（优先级从高到低）：
    1. 设置页里手填的 AppID / Key
    2. 本机环境变量（config.json 里 translate_env_enabled=True 时生效）
    3. MyMemory 兜底（无需任何 Key，仅当所选服务商是 mymemory 或未配置时手动触发）

环境变量名：
    百度      SCREENSNAP_BAIDU_APPID / SCREENSNAP_BAIDU_KEY
              或  BAIDU_TRANSLATE_APPID / BAIDU_TRANSLATE_KEY
    Azure     SCREENSNAP_AZURE_KEY / SCREENSNAP_AZURE_REGION
              或  AZURE_TRANSLATOR_KEY / AZURE_TRANSLATOR_REGION
    DeepL     SCREENSNAP_DEEPL_KEY 或 DEEPL_AUTH_KEY
    Libre     SCREENSNAP_LIBRE_URL 或 LIBRETRANSLATE_URL
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image
from PySide6.QtCore import QThread, Signal

import config as _cfg

# ---------------------------------------------------------------- 服务商目录

PROVIDERS = (
    ("baidu", "百度翻译（国内直连）"),
    ("baidu_llm", "百度大模型翻译"),
    ("azure", "Azure 翻译（F0 免费层）"),
    ("deepl", "DeepL（API Free）"),
    ("libre", "LibreTranslate（可离线自建）"),
    ("mymemory", "MyMemory（免 Key，量小）"),
)
PROVIDER_IDS = tuple(pid for pid, _ in PROVIDERS)

# 目标语言选项：auto = 中文翻英文、其它语言翻中文
TARGETS = (
    ("auto", "自动（中文→英文，其它→中文）"),
    ("zh", "中文"),
    ("en", "英文"),
    ("ja", "日文"),
    ("ko", "韩文"),
)
TARGET_IDS = tuple(t for t, _ in TARGETS)

# 各服务商的语言代码差异（只覆盖常用几种）
_AZURE_LANG = {"zh": "zh-Hans", "en": "en", "ja": "ja", "ko": "ko"}
_DEEPL_LANG = {"zh": "ZH", "en": "EN", "ja": "JA", "ko": "KO"}
_BAIDU_LANG = {"zh": "zh", "en": "en", "ja": "jp", "ko": "kor",
               "cht": "cht", "wyw": "wyw", "yue": "yue"}
# 通用文本翻译（NMT）
_BAIDU_NMT_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"
# 大模型文本翻译（支持 model_type=llm/nmt、翻译指令 reference）
_BAIDU_LLM_URL = "https://fanyi-api.baidu.com/ait/api/aiTextTranslate"
# 图片翻译（截图内的文字识别 + 翻译，返回抠图区域与替换位置）
PICTURE_URL = "https://fanyi-api.baidu.com/ait/api/picture/translate"
_PIC_MAX_BYTES = 5 * 1024 * 1024      # 接口限制：原图不超过 5MB
_PIC_LONG_EDGE = 2000                 # 超限时等比缩到的长边像素

_CHUNK_CHARS = 1200          # 单次请求的最大字符数（各家有 5000 字节级限制，留余量）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_HAS_LETTER_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u0400-\u04ff\u3040-\u30ff\uac00-\ud7af]")


class TranslateError(RuntimeError):
    """翻译失败（未配置 / 额度 / 网络 / 接口报错）。"""


# ---------------------------------------------------------------- 语言判定

def has_cjk(text: str) -> bool:
    """文本里中文占比是否足够（简单判定，够用即可）。"""
    s = (text or "").strip()
    if not s:
        return False
    cjk = len(_CJK_RE.findall(s))
    letters = cjk + len(_HAS_LETTER_RE.findall(s))
    return cjk > 0 and (letters == 0 or cjk / max(1, letters) >= 0.2)


def lang_pair(text: str, target: str | None = None) -> tuple[str, str]:
    """返回 (源语言, 目标语言)。target=auto 时按内容自动决定方向。"""
    tgt = (target or "auto").strip().lower()
    if tgt not in TARGET_IDS or tgt == "auto":
        return ("zh", "en") if has_cjk(text) else ("auto", "zh")
    return ("auto", tgt)


def target_label(target: str | None) -> str:
    tgt = (target or "auto").strip().lower()
    for tid, label in TARGETS:
        if tid == tgt:
            return label
    return TARGETS[0][1]


def provider_label(provider: str | None) -> str:
    pid = (provider or "").strip().lower()
    for p, label in PROVIDERS:
        if p == pid:
            return label
    return pid or "未配置"


# ---------------------------------------------------------------- 凭据解析

def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _cfg_str(cfg: dict, key: str) -> str:
    try:
        return str((cfg or {}).get(key, "") or "").strip()
    except Exception:
        return ""


def credentials(provider: str, cfg: dict | None = None) -> dict:
    """解析某个服务商的凭据。

    返回 ``{"source": "settings"|"env"|"", "ok": bool, "fields": {...}, "hint": str}``。
    设置页填写优先；留空的项才落到环境变量。
    """
    cfg = cfg if cfg is not None else _cfg.load_config()
    env_on = bool((cfg or {}).get("translate_env_enabled", True))
    pid = (provider or "baidu").strip().lower()
    fields: dict = {}
    source = ""

    def pick(cfg_key: str, env_names: tuple[str, ...]) -> str:
        nonlocal source
        manual = _cfg_str(cfg, cfg_key)
        if manual:
            source = source or "settings"
            return manual
        if env_on:
            v = _env(*env_names)
            if v:
                source = source or "env"
                return v
        return ""

    if pid in ("baidu", "baidu_llm"):
        fields["appid"] = pick("translate_baidu_appid",
                               ("SCREENSNAP_BAIDU_APPID", "BAIDU_TRANSLATE_APPID"))
        fields["key"] = pick("translate_baidu_key",
                             ("SCREENSNAP_BAIDU_KEY", "BAIDU_TRANSLATE_KEY"))
        fields["apikey"] = pick("translate_baidu_apikey",
                                ("SCREENSNAP_BAIDU_APIKEY", "BAIDU_TRANSLATE_APIKEY"))
        _model = (_cfg_str(cfg, "translate_baidu_model") or "llm").strip().lower()
        fields["model"] = _model if _model in ("llm", "nmt") else "llm"
        fields["reference"] = (_cfg_str(cfg, "translate_baidu_reference") or "").strip()[:500]
        if pid == "baidu_llm":
            ok = bool(fields["apikey"] or (fields["appid"] and fields["key"]))
            hint = "" if ok else ("填写「API Key」（管理控制台 → API Key 管理，走 Bearer 鉴权），"
                                 "或填写 APPID + 密钥（走 sign 鉴权）")
        else:
            ok = bool(fields["appid"] and fields["key"])
            hint = "" if ok else "填写百度翻译的 APPID 与密钥（密钥在百度翻译开放平台「管理控制台」查看）"
    elif pid == "azure":
        fields["key"] = pick("translate_azure_key",
                             ("SCREENSNAP_AZURE_KEY", "AZURE_TRANSLATOR_KEY"))
        fields["region"] = pick("translate_azure_region",
                                ("SCREENSNAP_AZURE_REGION", "AZURE_TRANSLATOR_REGION"))
        ok = bool(fields["key"])
        hint = "" if ok else "填写 Azure 翻译资源的密钥（区域按资源所在区域填，如 eastasia；全球资源可留空）"
    elif pid == "deepl":
        fields["key"] = pick("translate_deepl_key",
                             ("SCREENSNAP_DEEPL_KEY", "DEEPL_AUTH_KEY"))
        ok = bool(fields["key"])
        hint = "" if ok else "填写 DeepL API 的 Authentication Key（免费版以 :fx 结尾）"
    elif pid == "libre":
        fields["url"] = pick("translate_libre_url",
                             ("SCREENSNAP_LIBRE_URL", "LIBRETRANSLATE_URL"))
        ok = bool(fields["url"])
        hint = "" if ok else "填写 LibreTranslate 服务地址，例如 http://127.0.0.1:5000"
    elif pid == "mymemory":
        ok = True
        hint = ""
    else:
        ok = False
        hint = "未选择翻译服务商"

    return {"source": source, "ok": ok, "fields": fields, "hint": hint}


def source_label(source: str) -> str:
    return {"settings": "设置页填写", "env": "本机环境变量", "": "未配置"}.get(source or "", "未配置")


def config_ready(cfg: dict | None = None) -> bool:
    """当前配置下能否翻译（MyMemory 永远可用）。"""
    cfg = cfg if cfg is not None else _cfg.load_config()
    pid = _cfg_str(cfg, "translate_provider") or "baidu"
    return bool(credentials(pid, cfg)["ok"])


# ---------------------------------------------------------------- HTTP 小工具

def _post(url: str, data: bytes, headers: dict, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return _open(req, timeout)


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ScreenSnap/%s" % _cfg.APP_VERSION})
    return _open(req, timeout)


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise TranslateError("接口返回 HTTP %s %s" % (e.code, body.strip()))
    except urllib.error.URLError as e:
        raise TranslateError("网络不通：%s" % getattr(e, "reason", e))
    except TimeoutError:
        raise TranslateError("请求超时")


def _split_chunks(text: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """按行聚合分块，尽量保留原文换行结构。"""
    out, buf = [], ""
    for line in (text or "").split("\n"):
        while len(line) > limit:                     # 单行超长时硬切
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit and buf:
            out.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        out.append(buf)
    return out or [text]


# ---------------------------------------------------------------- 各服务商实现
# 百度翻译错误码 → 中文提示（通用文本翻译与大模型翻译接口通用）
_BAIDU_ERRORS = {
    "52001": "请求超时，请稍后重试",
    "52002": "系统错误，请稍后重试",
    "52003": "未授权用户：请检查 APPID 是否正确、对应服务是否已开通",
    "54000": "必填参数为空：请检查是否漏传必需参数",
    "54001": "签名错误：APPID/密钥是否正确；sign 需为 appid+q+salt+密钥 的 MD5（32 位小写）；"
             "若使用 Bearer 方式请检查 API Key",
    "54003": "访问频率受限：请降低调用频率，或升级为高级版/尊享版",
    "54004": "账户余额不足：请前往管理控制台充值",
    "54005": "长文本请求过于频繁：请降低长 query 发送频率，3 秒后重试",
    "58000": "客户端 IP 非法：请检查开发者信息页填写的服务器 IP（动态 IP 建议留空）",
    "58001": "译文语言方向不支持：请检查源/目标语种是否在该服务支持范围内",
    "58002": "服务当前已关闭：请前往管理控制台开启对应服务",
    "58003": "该 IP 已被封禁：同一 IP 当日使用多个 APPID 会被封禁，次日自动解封",
    "58004": "模型参数错误：model_type 仅支持 llm（大模型翻译）或 nmt（机器翻译）",
    "59001": "译文语言方向不支持：请检查 from/to 语种代码，to 不支持 auto",
    "59002": "翻译指令（reference）过长：上限 500 字符",
    "59003": "请求文本过长：单次 q 不可超过 6000 字符",
    "59004": "QPS 超限：当前接口并发已达上限，请限流后重试",
    "59005": "tag_handling 参数非法：仅支持 0 或 1",
    "90107": "认证未通过或未生效：请在百度翻译平台查看认证进度",
}


def _baidu_error(data: dict) -> str:
    """把百度接口返回的 error_code 转成中文可读提示。"""
    code = str(data.get("error_code") or "")
    msg = (data.get("error_msg") or "").strip()
    zh = _BAIDU_ERRORS.get(code)
    if zh:
        return "百度翻译报错 %s：%s%s" % (code, zh, ("（原始信息：%s）" % msg) if msg else "")
    return "百度翻译报错 %s：%s" % (code, msg or "未知错误，请查阅百度翻译错误码文档")


# 图片翻译接口专有错误码（部分与文本翻译重叠，文案按图片场景重写）
_BAIDU_PICTURE_ERRORS = {
    "53001": "图片翻译服务未开通或已到期：请到百度翻译开放平台「管理控制台」开通图片翻译服务",
    "54000": "必填参数为空：请检查 APPID / content 等参数是否漏传",
    "54001": "鉴权失败：请检查百度 APPID 与 API Key 是否匹配、API Key 是否已生效",
    "54004": "账户余额不足：请前往管理控制台充值",
    "55001": "图片格式不支持：请使用 JPG / PNG / BMP 等常见格式",
    "55002": "图片超出大小限制：单张图片不能超过 5MB",
    "55003": "图片尺寸不符合要求：请调整图片长宽后重试",
    "55004": "图片解码失败：请确认图片未损坏",
    "55005": "未从图片中检测到可翻译的文字：请更换图片或提高清晰度",
    "55006": "图片翻译请求过于频繁：请稍后重试",
    "55007": "图片翻译处理失败：请稍后重试或更换图片",
    "59004": "QPS 超限：当前接口并发已达上限，请稍后重试",
    "20003": "API Key 无效或鉴权失败：请检查设置 → 翻译 → 百度 API Key",
}


def _baidu_picture_error(data: dict) -> str:
    """图片翻译接口报错 → 中文提示。"""
    code = str(data.get("error_code") or "").strip()
    msg = str(data.get("error_msg") or "").strip()
    zh = _BAIDU_PICTURE_ERRORS.get(code) or _BAIDU_ERRORS.get(code)
    if zh:
        return "百度图片翻译报错 %s：%s%s" % (code, zh, ("（原始信息：%s）" % msg) if msg else "")
    return "百度图片翻译报错 %s：%s" % (code, msg or "未知错误，请查阅百度翻译错误码文档")


def _pic_rect(value) -> tuple[int, int, int, int]:
    """把 "x,y,w,h" 解析成整数四元组，解析不出来时回退 (0,0,0,0)。"""
    nums: list[float] = []
    items = list(value) if isinstance(value, (list, tuple)) else \
        [p for p in re.split(r"[,\s]+", str(value or "").strip()) if p]
    for item in items:
        try:
            nums.append(float(item))
        except (TypeError, ValueError):
            nums.append(0.0)
    while len(nums) < 4:
        nums.append(0.0)
    return (int(round(nums[0])), int(round(nums[1])), int(round(nums[2])), int(round(nums[3])))


def _pic_points(value) -> list[tuple[int, int]]:
    """解析多边形顶点：兼容 [{"x":..,"y":..}] 与 [[x,y],..] 两种形态。"""
    out: list[tuple[int, int]] = []
    for item in value or []:
        try:
            if isinstance(item, dict):
                out.append((int(round(float(item.get("x", 0)))), int(round(float(item.get("y", 0))))))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((int(round(float(item[0]))), int(round(float(item[1])))))
        except (TypeError, ValueError):
            continue
    return out


def _pic_paste(value) -> bytes:
    """paste_img 是 base64（可能带 data: 前缀），解码成图片字节。"""
    raw = str(value or "").strip()
    if not raw:
        return b""
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


def _shrink_picture(raw: bytes) -> bytes:
    """原图超过 5MB 时等比缩到长边 2000px；仍超限再降尺寸并转 JPEG。"""
    if len(raw) <= _PIC_MAX_BYTES:
        return raw
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:
        raise TranslateError("图片读取失败：%s" % e)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    scale = min(1.0, _PIC_LONG_EDGE / float(max(im.size) or 1))
    if scale < 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    out = buf.getvalue()
    if len(out) <= _PIC_MAX_BYTES:
        return out
    for side in (1600, 1200, 900):
        scale = min(1.0, side / float(max(im.size) or 1))
        cur = im
        if scale < 1.0:
            cur = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        cur.save(buf, format="JPEG", quality=85, optimize=True)
        out = buf.getvalue()
        if len(out) <= _PIC_MAX_BYTES:
            break
    return out


def _parse_picture(data: dict) -> dict:
    """把接口返回整理成 UI 可直接消费的结构。"""
    contents = []
    for item in (data.get("contents") or []):
        if not isinstance(item, dict):
            continue
        contents.append({
            "src": str(item.get("src") or ""),
            "dst": str(item.get("dst") or ""),
            "rect": _pic_rect(item.get("rect")),
            "line_count": int(item.get("line_count") or 0),
            "points": _pic_points(item.get("points")),
            "paste_img": _pic_paste(item.get("paste_img")),
        })
    return {
        "from": str(data.get("from") or ""),
        "to": str(data.get("to") or ""),
        "src": str(data.get("src") or ""),
        "dst": str(data.get("dst") or ""),
        "contents": contents,
    }


def picture_translate(image_bytes: bytes, from_lang: str = "auto", to_lang: str = "zh",
                      cfg: dict | None = None, paste: int = 2, view_type: int = 1,
                      timeout: float = 30.0) -> dict:
    """百度图片翻译：翻译截图里的文字，并返回译文与贴图区域。

    ``paste``：0=不贴图 1=整图 2=逐条贴图（默认）3=逐条贴图+原图；
    ``view_type``：0=原文 1=译文。
    凭据复用「百度大模型翻译」里的 APPID 与 API Key（Bearer 鉴权）。
    """
    cfg = cfg if cfg is not None else _cfg.load_config()
    cred = credentials("baidu_llm", cfg)
    fields = cred.get("fields") or {}
    appid = str(fields.get("appid") or "").strip()
    apikey = str(fields.get("apikey") or "").strip()
    if not apikey:
        raise TranslateError("图片翻译需在设置→翻译→百度 API Key 中填写 API Key")
    if not appid:
        raise TranslateError("图片翻译需在设置→翻译→百度 APPID 中填写 APPID")
    if not image_bytes:
        raise TranslateError("没有可翻译的图片")

    payload = {
        "from": from_lang or "auto",
        "to": to_lang or "zh",
        "appid": appid,
        "content": base64.b64encode(_shrink_picture(image_bytes)).decode("ascii"),
        "paste": int(paste),
        "need_intervene": 0,
        "view_type": int(view_type),
        "model_type": "nmt",
    }
    raw = _post(PICTURE_URL, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json", "Authorization": "Bearer %s" % apikey},
                timeout)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise TranslateError("接口返回无法解析：%s" % raw.decode("utf-8", "replace")[:200])
    if not isinstance(data, dict):
        raise TranslateError("接口返回格式异常")
    if data.get("error_code"):
        raise TranslateError(_baidu_picture_error(data))
    return _parse_picture(data)


def _tr_baidu(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    appid, key = cred["appid"], cred["key"]
    out = []
    for chunk in _split_chunks(text):
        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5((appid + chunk + salt + key).encode("utf-8")).hexdigest()
        payload = urllib.parse.urlencode({
            "q": chunk,
            "from": _BAIDU_LANG.get(src, "auto"),
            "to": _BAIDU_LANG.get(dst, dst),
            "appid": appid,
            "salt": salt,
            "sign": sign,
        }).encode("utf-8")
        raw = _post(_BAIDU_NMT_URL, payload,
                    {"Content-Type": "application/x-www-form-urlencoded"}, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
        if data.get("error_code"):
            raise TranslateError(_baidu_error(data))
        out.append("\n".join(r.get("dst", "") for r in (data.get("trans_result") or [])))
    return "\n".join(out)


def _tr_baidu_llm(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    """百度大模型文本翻译（/ait/api/aiTextTranslate）。

    - 填了 API Key：走 Authorization: Bearer，不再传 salt/sign
    - 只填 APPID + 密钥：退化为 sign 鉴权（appid+q+salt+密钥 的 MD5）
    """
    appid = cred.get("appid", "")
    key = cred.get("key", "")
    apikey = cred.get("apikey", "")
    model = (cred.get("model") or "llm").strip().lower()
    if model not in ("llm", "nmt"):
        model = "llm"
    reference = (cred.get("reference") or "").strip()[:500]
    out = []
    for chunk in _split_chunks(text):
        form = {
            "q": chunk,
            "from": _BAIDU_LANG.get(src, "auto"),
            "to": _BAIDU_LANG.get(dst, dst),
            "appid": appid,
            "model_type": model,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if apikey:
            headers["Authorization"] = "Bearer " + apikey
        else:
            salt = str(random.randint(32768, 65536))
            form["salt"] = salt
            form["sign"] = hashlib.md5((appid + chunk + salt + key).encode("utf-8")).hexdigest()
        if model == "llm" and reference:
            form["reference"] = reference
        raw = _post(_BAIDU_LLM_URL, urllib.parse.urlencode(form).encode("utf-8"),
                    headers, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
        if data.get("error_code"):
            raise TranslateError(_baidu_error(data))
        translated = "\n".join(r.get("dst", "") for r in (data.get("trans_result") or []))
        out.append(translated or str(data.get("dst") or ""))
    return "\n".join(out)


def _tr_azure(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    out = []
    params = {"api-version": "3.0", "to": _AZURE_LANG.get(dst, dst)}
    if src and src != "auto":
        params["from"] = _AZURE_LANG.get(src, src)
    url = "https://api.cognitive.microsofttranslator.com/translate?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json",
               "Ocp-Apim-Subscription-Key": cred["key"]}
    if cred.get("region"):
        headers["Ocp-Apim-Subscription-Region"] = cred["region"]
    for chunk in _split_chunks(text):
        body = json.dumps([{"Text": chunk}], ensure_ascii=False).encode("utf-8")
        raw = _post(url, body, headers, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            raise TranslateError("Azure 报错：%s" % (err.get("message") or err))
        out.append("\n".join(t.get("text", "") for item in data for t in item.get("translations", [])))
    return "\n".join(out)


def _tr_deepl(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    key = cred["key"]
    host = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
    out = []
    for chunk in _split_chunks(text):
        form = {"text": chunk, "target_lang": _DEEPL_LANG.get(dst, dst.upper())}
        if src and src != "auto":
            form["source_lang"] = _DEEPL_LANG.get(src, src.upper())
        raw = _post(host + "/v2/translate", urllib.parse.urlencode(form).encode("utf-8"),
                    {"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": "DeepL-Auth-Key " + key}, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
        out.append("\n".join(t.get("text", "") for t in (data.get("translations") or [])))
    return "\n".join(out)


def _tr_libre(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    base = (cred.get("url") or "").rstrip("/")
    if not base.startswith("http"):
        raise TranslateError("LibreTranslate 地址需要以 http:// 或 https:// 开头")
    out = []
    for chunk in _split_chunks(text):
        body = json.dumps({"q": chunk, "source": "auto" if src == "auto" else src,
                           "target": dst, "format": "text"}, ensure_ascii=False).encode("utf-8")
        raw = _post(base + "/translate", body, {"Content-Type": "application/json"}, timeout)
        data = json.loads(raw.decode("utf-8", "replace"))
        if data.get("error"):
            raise TranslateError("LibreTranslate 报错：%s" % data.get("error"))
        out.append(str(data.get("translatedText") or ""))
    return "\n".join(out)


_MYMEM_TAG = re.compile(r"</?g\b[^>]*>", re.I)


def _clean_mymemory(text: str) -> str:
    """MyMemory 会用 <g id="n">…</g> 标记分段，还带回 HTML 实体，去掉。"""
    s = _MYMEM_TAG.sub("", text or "")
    s = html.unescape(s)
    return s.strip()


def _lang_key(code: str) -> str:
    """zh-CN / zh-TW / zh 视为同一种，用于判断源语言与目标语言是否撞车。"""
    return (code or "").strip().lower().replace("_", "-").split("-")[0]


def _tr_mymemory(text: str, src: str, dst: str, cred: dict, timeout: float) -> str:
    # MyMemory 没有源语言自动检测：中英混排时必须逐段判断，否则会把英文当中文翻
    def pair_for(q: str) -> str:
        if src in ("zh", "en", "ja", "ko"):
            s = src
        else:
            s = "zh" if has_cjk(q) else "en"
        if _lang_key(s) == _lang_key(dst):
            s = "en" if _lang_key(dst) != "en" else "zh"
        return "%s|%s" % ("zh-CN" if s == "zh" else s, dst)

    def ask(q: str) -> str:
        url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(
            {"q": q, "langpair": pair_for(q)})
        data = json.loads(_get(url, timeout).decode("utf-8", "replace"))
        if str(data.get("responseStatus")) not in ("200", "OK"):
            raise TranslateError(
                "MyMemory 报错：%s" % (data.get("responseDetails") or data.get("responseStatus")))
        return _clean_mymemory(str((data.get("responseData") or {}).get("translatedText") or ""))

    out = []
    for chunk in _split_chunks(text, 450):
        if "\n" not in chunk:
            out.append(ask(chunk))
            continue
        # 免费接口把多行塞进一次请求会乱匹配（返回不相干记忆库句子），逐行翻更稳
        parts = []
        for line in chunk.split("\n"):
            if not line.strip():
                parts.append("")
                continue
            try:
                parts.append(ask(line.strip()))
            except TranslateError:
                parts.append(line)
        out.append("\n".join(parts))
    return "\n".join(out)


_IMPL = {
    "baidu": _tr_baidu,
    "baidu_llm": _tr_baidu_llm,
    "azure": _tr_azure,
    "deepl": _tr_deepl,
    "libre": _tr_libre,
    "mymemory": _tr_mymemory,
}


# ---------------------------------------------------------------- 对外接口

def translate_text(text: str, provider: str | None = None, cfg: dict | None = None,
                   target: str | None = None, timeout: float = 12.0) -> tuple[str, str]:
    """同步翻译（请在子线程里调用，别卡 UI）。

    返回 ``(译文, 方向说明)``，如 ``("Hello", "中文 → 英文 · 百度翻译（国内直连）")``。
    """
    cfg = cfg if cfg is not None else _cfg.load_config()
    body = (text or "").strip()
    if not body:
        raise TranslateError("没有可翻译的文字")
    pid = (provider or _cfg_str(cfg, "translate_provider") or "baidu").strip().lower()
    if pid not in _IMPL:
        raise TranslateError("不支持的翻译服务：%s" % pid)
    cred = credentials(pid, cfg)
    if not cred["ok"]:
        raise TranslateError("尚未配置翻译接口：%s" % (cred["hint"] or "请在设置里填写 Key"))
    src, dst = lang_pair(body, target if target is not None else _cfg_str(cfg, "translate_target"))
    result = _IMPL[pid](body, src, dst, cred["fields"], timeout)
    result = (result or "").strip()
    if not result:
        raise TranslateError("接口没有返回译文")
    src_name = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文"}.get(src, "自动检测")
    dst_name = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文"}.get(dst, dst)
    return result, "%s → %s · %s" % (src_name, dst_name, provider_label(pid))


def translate_quick(text: str, cfg: dict | None = None, timeout: float = 10.0) -> str:
    """给「测试连接」用：返回一行结果，失败抛 TranslateError。"""
    result, _ = translate_text(text, cfg=cfg, target="auto", timeout=timeout)
    return result


class TranslateTask(QThread):
    """后台翻译线程：done(译文, 方向说明) / failed(错误文案)。"""

    done = Signal(str, str)
    failed = Signal(str)

    def __init__(self, text: str, cfg: dict | None = None, provider: str | None = None,
                 target: str | None = None, parent=None):
        super().__init__(parent)
        self._text = text
        self._cfg = dict(cfg or _cfg.load_config())
        self._provider = provider
        self._target = target

    def run(self) -> None:
        try:
            result, meta = translate_text(self._text, self._provider, self._cfg, self._target)
        except TranslateError as e:
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))
            return
        except Exception as e:                       # 兜底：JSON 结构变化等
            if not self.isInterruptionRequested():
                self.failed.emit("翻译失败：%s" % e)
            return
        if not self.isInterruptionRequested():
            self.done.emit(result, meta)


class PictureTranslateTask(QThread):
    """后台图片翻译线程：done(结果字典) / failed(错误文案)。"""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, image_bytes: bytes, cfg: dict | None = None,
                 from_lang: str = "auto", to_lang: str = "zh",
                 paste: int = 2, view_type: int = 1, parent=None):
        super().__init__(parent)
        self._image_bytes = image_bytes
        self._cfg = dict(cfg or _cfg.load_config())
        self._from = from_lang
        self._to = to_lang
        self._paste = paste
        self._view = view_type

    def run(self) -> None:
        try:
            result = picture_translate(self._image_bytes, self._from, self._to,
                                       self._cfg, self._paste, self._view)
        except TranslateError as e:
            if not self.isInterruptionRequested():
                self.failed.emit(str(e))
            return
        except Exception as e:                       # 兜底：异常响应结构等
            if not self.isInterruptionRequested():
                self.failed.emit("图片翻译失败：%s" % e)
            return
        if not self.isInterruptionRequested():
            self.done.emit(result)


def env_status(cfg: dict | None = None) -> list[tuple[str, str]]:
    """列出当前能读到的翻译相关环境变量，供设置页展示。"""
    cfg = cfg if cfg is not None else _cfg.load_config()
    if not (cfg or {}).get("translate_env_enabled", True):
        return []
    pairs = (
        ("百度 APPID", ("SCREENSNAP_BAIDU_APPID", "BAIDU_TRANSLATE_APPID")),
        ("百度密钥", ("SCREENSNAP_BAIDU_KEY", "BAIDU_TRANSLATE_KEY")),
        ("Azure 密钥", ("SCREENSNAP_AZURE_KEY", "AZURE_TRANSLATOR_KEY")),
        ("Azure 区域", ("SCREENSNAP_AZURE_REGION", "AZURE_TRANSLATOR_REGION")),
        ("DeepL Key", ("SCREENSNAP_DEEPL_KEY", "DEEPL_AUTH_KEY")),
        ("LibreTranslate 地址", ("SCREENSNAP_LIBRE_URL", "LIBRETRANSLATE_URL")),
    )
    out = []
    for label, names in pairs:
        v = _env(*names)
        if v:
            shown = v if label.endswith("区域") or "地址" in label else (v[:4] + "****" + v[-2:] if len(v) > 8 else "****")
            out.append((label, shown))
    return out
