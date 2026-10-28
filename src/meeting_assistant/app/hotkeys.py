"""System-wide hotkeys on Windows via RegisterHotKey, so notes can be added from inside Zoom or Teams.

RegisterHotKey delivers WM_HOTKEY to the thread that registered it, so registration and
the message loop both live in this QThread.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
VK_SPACE = 0x20


class HotkeyThread(QThread):
    triggered = Signal(int)
    failed = Signal(str)

    def __init__(self, hotkeys: dict[int, tuple[int, int]]):
        """hotkeys maps an id of your choice to (modifiers, virtual key code)."""
        super().__init__()
        self.hotkeys = hotkeys
        self._thread_id: int | None = None

    def run(self) -> None:
        if sys.platform != "win32":
            self.failed.emit("Global hotkeys are only supported on Windows.")
            return
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = []
        for hotkey_id, (mods, vk) in self.hotkeys.items():
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                registered.append(hotkey_id)
            else:
                self.failed.emit("A shortcut is already used by another app.")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.triggered.emit(msg.wParam)

        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.wait(2000)
