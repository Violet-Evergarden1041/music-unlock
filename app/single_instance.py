"""单实例保护：保证同一时间只跑一个窗口，重复启动时把已有窗口叫到前面。

为什么不用 QLocalServer：在 Windows 上它不是互斥的——两个进程对同一个名字
调用 listen() 都会返回 True，于是每个实例都以为自己是第一个。这里改用
Win32 命名互斥体（真正互斥）+ 注册窗口消息（跨进程唤醒）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

#: Local\ 前缀表示"每个登录会话一个"，不会干扰其他用户
_MUTEX_NAME = "Local\\MusicUnlock.SingleInstance.v2"
_MESSAGE_NAME = "MusicUnlock_ShowWindow_v2"

_HWND_BROADCAST = 0xFFFF
_ERROR_ALREADY_EXISTS = 183

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL


class _MessageFilter(QAbstractNativeEventFilter):
    """接住别的实例广播过来的"显示窗口"消息。"""

    def __init__(self) -> None:
        super().__init__()
        self.message_id = 0
        self.on_activate = None

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if self.message_id and event_type in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except (TypeError, ValueError):
                return False, 0
            if msg.message == self.message_id:
                if self.on_activate is not None:
                    self.on_activate()
                return True, 0
        return False, 0


class SingleInstance:
    """创建即抢锁；被别的实例占着就广播消息把它叫出来。"""

    def __init__(self) -> None:
        self._mutex = None
        self._filter = None
        self.message_id = 0

    def already_running(self) -> bool:
        """返回 True 表示已经有实例在跑（此时应当就此退出）。"""
        handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        error = ctypes.get_last_error()
        self._mutex = handle
        if not handle:
            # 创建失败就别卡住用户，当作没有其他实例
            return False
        return error == _ERROR_ALREADY_EXISTS

    def _ensure_message(self) -> int:
        if not self.message_id:
            self.message_id = _user32.RegisterWindowMessageW(_MESSAGE_NAME)
        return self.message_id

    def broadcast_activate(self) -> bool:
        """让已有实例把窗口显示出来。"""
        message_id = self._ensure_message()
        if not message_id:
            return False
        _user32.PostMessageW(_HWND_BROADCAST, message_id, 0, 0)
        return True

    def listen(self, app, on_activate) -> None:
        """主实例调用：装一个过滤器，收到广播就执行 on_activate。"""
        message_id = self._ensure_message()
        if not message_id:
            return
        self._filter = _MessageFilter()
        self._filter.message_id = message_id
        self._filter.on_activate = on_activate
        app.installNativeEventFilter(self._filter)
