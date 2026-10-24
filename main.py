# -*- coding: utf-8 -*-
"""ScreenSnap 主入口：托盘常驻 + 全局热键截图 + 编辑 + 自动复制/入库/贴图。"""
import os
import sys
import threading
import tkinter as tk
import winreg
import ctypes
from ctypes import wintypes

import pystray
from PIL import Image

import dialogs
from config import load_config, save_config
from storage import Storage
from hotkey import HotkeyManager

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(APP_DIR, "assets", "icon.png")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ScreenSnap"

# ---------- 单实例互斥锁 ----------
SINGLE_INSTANCE_MUTEX = "Local\\ScreenSnap_SingleInstance"
_single_instance_handle = None  # 持有句柄，进程存续期间保持互斥体存在


def ensure_single_instance():
    """单实例检查：已有 ScreenSnap 进程时返回 False（调用方应退出）。"""
    global _single_instance_handle
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _single_instance_handle = handle  # 保持引用，避免句柄被回收
    return True


def build_launch_cmd():
    """开发期：pythonw.exe + 本脚本；打包后由 PyInstaller 替换。"""
    exe = sys.executable
    d = os.path.dirname(exe)
    pyw = os.path.join(d, "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    script = os.path.abspath(__file__)
    return f'"{exe}" "{script}"'


def set_auto_start(enabled):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, build_launch_cmd())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        finally:
            key.Close()
        return True
    except Exception:
        return False


class ScreenSnapApp:
    def __init__(self):
        self.config = load_config()
        self.storage = Storage(self.config.get("save_root"),
                               self.config.get("trash_days", 3))
        self.root = tk.Tk()
        dialogs.patch()   # 弹窗定位补丁：messagebox/filedialog 跟随鼠标所在屏幕弹出
        self.root.withdraw()
        self.root.title("ScreenSnap")

        self.hotkey = HotkeyManager()
        self.hotkey.set_root_getter(lambda: self.root)
        self.tray = None

        # 开机自启同步
        self.apply_auto_start()

        # 注册热键
        err = self.hotkey.register(self.config.get("hotkey", "Ctrl+Alt+A"),
                                   self.start_capture)
        if err:
            self._notify(err)

        # 启动托盘
        self._start_tray()

    # ---------- 托盘 ----------
    def _start_tray(self):
        try:
            icon_img = Image.open(ICON_PATH).resize((64, 64), 3)
        except Exception:
            icon_img = Image.new("RGB", (64, 64), (37, 99, 235))
        menu = pystray.Menu(
            pystray.MenuItem("截图 (Ctrl+Alt+A)", self._tray_capture, default=True),
            pystray.MenuItem("截图历史库", self._tray_gallery),
            pystray.MenuItem("设置", self._tray_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._tray_quit),
        )
        self.tray = pystray.Icon("ScreenSnap", icon_img, "ScreenSnap 截图工具", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_capture(self, icon=None, item=None):
        self._run_in_tk(self.start_capture)

    def _tray_gallery(self, icon=None, item=None):
        self._run_in_tk(self.open_gallery)

    def _tray_settings(self, icon=None, item=None):
        self._log("tray_settings clicked")
        self._run_in_tk(self.open_settings)

    def _tray_quit(self, icon=None, item=None):
        self.hotkey.unregister()
        if self.tray:
            self.tray.stop()
        self.root.after(50, self.root.destroy)

    def _run_in_tk(self, fn):
        self._log(f"_run_in_tk schedule {getattr(fn, '__name__', fn)}")
        try:
            self.root.after(0, fn)
        except Exception as e:
            self._log(f"_run_in_tk FAIL {e!r}")

    # ---------- 截图流程 ----------
    def start_capture(self):
        self._log("start_capture triggered")
        # 防止重复触发：已有捕获/编辑窗口时不重复开
        if hasattr(self, "_busy") and self._busy:
            return
        self._busy = True
        try:
            from capture import CaptureWindow
            CaptureWindow(self.root, on_result=self._on_captured,
                          on_cancel=self._on_cancel_capture)
        except Exception as e:
            self._busy = False
            self._notify(f"截图失败：{e}")

    def _on_cancel_capture(self):
        self._busy = False

    def _on_captured(self, img, offset_x=0, offset_y=0,
                     full_image=None, sel_box=None):
        try:
            from editor import EditorWindow
            EditorWindow(self.root, img, on_done=self._on_done,
                         on_cancel=self._on_cancel_capture,
                         on_pin=self._on_pin,
                         on_record=self.start_record,
                         offset_x=offset_x, offset_y=offset_y,
                         full_image=full_image, sel_box=sel_box)
        except Exception as e:
            self._busy = False
            self._notify(f"打开编辑失败：{e}")

    def _on_done(self, img, x=0, y=0):
        self._busy = False
        # 1) 复制到剪贴板（按设置：截图后自动复制）
        if self.config.get("auto_copy", True):
            from clipboard import copy_image_to_clipboard
            copy_image_to_clipboard(img)
        # 2) 保存到历史库（按当前设置格式）
        fmt = self.config.get("save_format", "png")
        img = img.convert("RGB")
        self.storage.save_image(img, None, fmt)
        # 3) 完成即关闭：不残留贴图；如需钉桌面请用「盯住」

    def _on_pin(self, img, x=0, y=0):
        """盯住：钉在屏幕原位，同时保存历史库（不复制剪贴板）。"""
        self._busy = False
        try:
            fmt = self.config.get("save_format", "png")
            self.storage.save_image(img.convert("RGB"), None, fmt)
        except Exception:
            pass
        try:
            from sticker import StickerWindow
            StickerWindow(self.root, img, x, y)
        except Exception:
            pass

    # ---------- 录制流程 ----------
    def start_record(self, rect=None):
        """工具栏「录制」入口：rect 为截图选区时跳过框选直接录制，否则走区域选择流程。"""
        self._log("start_record triggered")
        if hasattr(self, "_busy") and self._busy:
            return
        self._busy = True
        try:
            from recorder import start_record as _start_record
            _start_record(self.root, self.storage, rect=rect,
                          on_finish=self._on_record_finished)
        except Exception as e:
            self._busy = False
            self._notify(f"启动录制失败：{e}")

    def _on_record_finished(self, path):
        self._busy = False
        if path:
            from tkinter import messagebox
            self.root.after(0, lambda: messagebox.showinfo(
                "ScreenSnap", f"录制完成，已保存：\n{path}"))

    # ---------- 窗口 ----------
    def _log(self, msg):
        try:
            import datetime
            p = os.path.join(os.environ.get("APPDATA", "."), "ScreenSnap", "debug_tray.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")
        except Exception:
            pass

    def open_gallery(self):
        self._log("open_gallery enter")
        try:
            from gallery import GalleryWindow
            GalleryWindow(self.root, self.storage, self.config)
            self._log("open_gallery ok")
        except Exception as e:
            self._log(f"open_gallery FAIL {e!r}")
            self._notify(f"打开历史库失败：{e}")

    def open_settings(self):
        self._log("open_settings enter")
        try:
            from settings import SettingsWindow
            SettingsWindow(self.root, self)
            self._log("open_settings ok")
        except Exception as e:
            self._log(f"open_settings FAIL {e!r}")
            self._notify(f"打开设置失败：{e}")

    # ---------- 设置联动 ----------
    def change_hotkey(self, text):
        """尝试切换热键，成功返回 (True, None)，失败 (False, 错误信息)。"""
        old = self.config.get("hotkey", "Ctrl+Alt+A")
        self.hotkey.unregister()
        err = self.hotkey.register(text, self.start_capture)
        if err:
            # 回滚旧热键
            self.hotkey.register(old, self.start_capture)
            return False, err
        self.config["hotkey"] = text
        save_config(self.config)
        return True, None

    def apply_auto_start(self):
        if self.config.get("auto_start", True):
            set_auto_start(True)

    def _notify(self, msg):
        try:
            from tkinter import messagebox
            self.root.after(0, lambda: messagebox.showwarning("ScreenSnap", msg))
        except Exception:
            print(msg)


def main():
    if not ensure_single_instance():
        # 已有 ScreenSnap 实例在运行，直接退出，避免多开
        return
    app = ScreenSnapApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
