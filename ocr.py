# -*- coding: utf-8 -*-
"""OCR：可选功能，懒加载 pytesseract，识别图中文字并复制。"""
import os
import threading

import pytesseract


def is_available():
    """tesseract 引擎是否可用。"""
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_text(img, lang="chi_sim+eng"):
    """识别图片文字。失败抛异常由调用方处理。"""
    return pytesseract.image_to_string(img, lang=lang)


def ocr_async(img, on_result, on_error=None):
    """后台线程识别，避免卡界面。"""

    def job():
        try:
            text = ocr_text(img)
            on_result(text)
        except Exception as e:
            if on_error:
                on_error(str(e))

    threading.Thread(target=job, daemon=True).start()
