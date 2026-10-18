# -*- coding: utf-8 -*-
"""全局热键：基于 Win32 RegisterHotKey + 消息循环，零轮询、自带冲突检测。

2026-10-18 两处修复：
1) 线程队列修复（关键）：RegisterHotKey 会把 WM_HOTKEY 投递到【调用线程】的
   消息队列，而 GetMessageW 只能取出【本线程】队列的消息。旧实现在主线程注册、
   子线程收消息，子线程永远收不到 → 热键注册成功却按下无反应。
   现改为在工作线程内完成注册 + 消息循环（同一线程），register() 用事件同步
   等待注册结果。
2) 固件污染兼容：磁轴键盘（TITAN 68 Turbo MAC 模式）存在修饰键状态污染时，
   注册主组合键的同时补注册带 Alt 的冗余变体，两路共用同一回调。
"""
import ctypes
import threading
from ctypes import wintypes

from config import parse_hotkey

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
ERROR_INVALID_PARAMETER = 87


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT)]


class HotkeyManager:
    """全局热键：注册与消息循环同线程，回调调度到主线程（tkinter 安全）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._registered = False
        self._has_variant = False
        self._thread = None
        self._tid = 0
        self._ready = None
        self._err = None
        self._callback = None
        self._mods = 0
        self._vk = 0
        self._key_name = ""
        self._root_getter = None  # callable -> tk root

    def set_root_getter(self, fn):
        self._root_getter = fn

    def register(self, hotkey_text, callback):
        """注册热键。成功返回 None；冲突/非法返回错误信息字符串。"""
        parsed = parse_hotkey(hotkey_text)
        if parsed is None:
            return "快捷键格式无效，示例：Ctrl+Alt+A"
        mods, vk, key_name = parsed
        with self._lock:
            if self._registered or (self._thread and self._thread.is_alive()):
                self._stop_locked()
            self._mods, self._vk, self._key_name = mods, vk, key_name
            self._callback = callback
            self._registered = False
            self._has_variant = False
            self._err = None
            self._ready = threading.Event()
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()
        # 等待工作线程完成注册（RegisterHotKey 即时返回，2s 上限足够）
        self._ready.wait(2.0)
        err = self._err
        if err:
            return err
        return None

    def unregister(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """停止热键（须持有 self._lock）。注销由工作线程兜底执行。"""
        if not self._registered and not (self._thread and self._thread.is_alive()):
            return
        self._registered = False
        # 投递 WM_QUIT 唤醒工作线程的 GetMessageW，线程退出时兜底注销
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        self._thread = None
        self._tid = 0

    def _worker(self):
        self._tid = kernel32.GetCurrentThreadId()
        try:
            ok = user32.RegisterHotKey(None, 1, self._mods | MOD_NOREPEAT, self._vk)
            if not ok:
                err = kernel32.GetLastError()
                if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                    self._err = f"热键 {self._key_name} 组合已被其他程序占用，请换一个"
                else:
                    self._err = f"热键 {self._key_name} 注册失败（错误码 {err}）"
                self._ready.set()
                return
            # 冗余变体：主组合不含 Alt 时补注册带 Alt 版本（兼容固件污染）
            self._has_variant = False
            if not (self._mods & 0x0001):  # MOD_ALT
                if user32.RegisterHotKey(None, 2,
                                         (self._mods | 0x0001) | MOD_NOREPEAT,
                                         self._vk):
                    self._has_variant = True
            self._registered = True
            self._ready.set()
        except Exception as e:
            self._err = f"热键注册异常：{e!r}"
            self._ready.set()
            return
        # 消息循环：与注册同线程，能收到 WM_HOTKEY
        msg = MSG()
        while self._registered:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            if msg.message == WM_HOTKEY and msg.wParam in (1, 2):
                self._dispatch()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 线程退出兜底注销
        user32.UnregisterHotKey(None, 1)
        if self._has_variant:
            user32.UnregisterHotKey(None, 2)
        self._registered = False

    def _dispatch(self):
        cb = self._callback
        if not cb:
            return
        root = self._root_getter() if self._root_getter else None
        if root is not None:
            try:
                root.after(0, cb)
            except Exception:
                pass
        else:
            try:
                cb()
            except Exception:
                pass
