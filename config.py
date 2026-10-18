# -*- coding: utf-8 -*-
"""配置管理：快捷键、保存格式、OCR、开机自启等。"""
import json
import os
import sys

APP_NAME = "ScreenSnap"
APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DEFAULT_SAVE_ROOT = os.path.join(os.path.expanduser("~"), "Pictures", APP_NAME)

# 修饰键与 VK 码
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

DEFAULT_CONFIG = {
    "hotkey": "Ctrl+Alt+A",        # 全局截图快捷键
    "save_format": "png",          # png / jpg
    "save_root": DEFAULT_SAVE_ROOT,
    "ocr_enabled": False,          # OCR 开关
    "auto_start": True,            # 开机自启
    "trash_days": 3,               # 回收站保留天数
    "column_capacity": 10,         # 历史库每列最多张数
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------- 热键字符串解析 ----------------

_VK_BASE = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "ESC": 0x1B,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDN": 0x22, "INSERT": 0x2D,
}

_MOD_NAMES = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL,
              "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN,
              "WINDOWS": MOD_WIN, "META": MOD_WIN}


def parse_hotkey(text):
    """解析 'Ctrl+Alt+A' 之类字符串 -> (modifiers, vk, key_name)
    失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    main_key = None
    for p in parts:
        up = p.upper()
        if up in _MOD_NAMES:
            mods |= _MOD_NAMES[up]
        elif up in _VK_BASE:
            main_key = up
        else:
            return None
    if main_key is None:
        return None
    if mods == 0:
        return None  # 必须带至少一个修饰键，避免误触
    return (mods, _VK_BASE[main_key], main_key)
