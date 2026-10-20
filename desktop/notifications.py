"""Native Windows receipt banners, without extra packages or shell commands."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import queue
import threading


def receipt_text(paths: list[Path], clipboard_copied: bool = False) -> tuple[str, str]:
    title = f"EasyDrop · 收到 {len(paths)} 个文件"
    names = "、".join(path.name for path in paths[:3])
    if len(paths) > 3:
        names += "等"
    copied = "\n已复制，可直接 Ctrl+V 粘贴" if clipboard_copied else ""
    return title, f"{names[:160]}\n已保存到桌面“来自vivo”{copied}\n点击打开文件夹"


class DesktopNotifier:
    def __init__(self, destination: Path):
        self.destination = destination
        self.messages: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.hwnd = None
        self.thread = None

    def __call__(self, paths: list[Path], *, clipboard_copied: bool = False) -> None:
        if not paths or os.name != "nt":
            return
        self.messages.put(receipt_text(paths, clipboard_copied))
        if not self.thread or not self.thread.is_alive():
            self.ready.clear()
            self.thread = threading.Thread(target=self._run, name="EasyDrop-Notifications", daemon=True)
            self.thread.start()
        if not self.ready.wait(3) or not self.hwnd:
            raise RuntimeError("Windows 通知组件未能启动")
        import ctypes
        from ctypes import wintypes as w
        post = ctypes.windll.user32.PostMessageW
        post.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        post.restype = w.BOOL
        if not post(self.hwnd, 0x8001, 0, 0):
            raise ctypes.WinError()

    def _run(self) -> None:
        import ctypes as c
        from ctypes import wintypes as w
        user = c.WinDLL("user32", use_last_error=True)
        shell = c.WinDLL("shell32", use_last_error=True)
        kernel = c.WinDLL("kernel32", use_last_error=True)
        callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

        class WindowClass(c.Structure):
            _fields_ = [("style", w.UINT), ("procedure", callback_type), ("class_extra", c.c_int),
                        ("window_extra", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

        class Guid(c.Structure):
            _fields_ = [("a", w.DWORD), ("b", w.WORD), ("d", w.WORD), ("e", w.BYTE * 8)]

        class IconData(c.Structure):
            _fields_ = [("size", w.DWORD), ("window", w.HWND), ("id", w.UINT), ("flags", w.UINT),
                        ("message", w.UINT), ("icon", w.HICON), ("tip", w.WCHAR * 128),
                        ("state", w.DWORD), ("state_mask", w.DWORD), ("text", w.WCHAR * 256),
                        ("version", w.UINT), ("title", w.WCHAR * 64), ("info_flags", w.DWORD),
                        ("guid", Guid), ("balloon_icon", w.HICON)]

        kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel.GetModuleHandleW.restype = w.HINSTANCE
        user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        user.DefWindowProcW.restype = c.c_ssize_t
        user.RegisterClassW.argtypes = [c.POINTER(WindowClass)]
        user.RegisterClassW.restype = w.ATOM
        user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        user.CreateWindowExW.restype = w.HWND
        user.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
        user.LoadIconW.restype = w.HICON
        user.GetMessageW.argtypes = [c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        user.GetMessageW.restype = w.BOOL
        user.TranslateMessage.argtypes = [c.POINTER(w.MSG)]
        user.DispatchMessageW.argtypes = [c.POINTER(w.MSG)]
        user.DispatchMessageW.restype = c.c_ssize_t
        user.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
        user.RegisterWindowMessageW.restype = w.UINT
        shell.Shell_NotifyIconW.argtypes = [w.DWORD, c.POINTER(IconData)]
        shell.Shell_NotifyIconW.restype = w.BOOL
        data = IconData()
        data.size = c.sizeof(data)
        data.id = 1
        data.message = 0x8002
        data.tip = "EasyDrop · 点击打开来自vivo"
        data.icon = user.LoadIconW(None, c.cast(c.c_void_p(32516), w.LPCWSTR))
        taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")

        def add_icon():
            data.flags = 1 | 2 | 4  # NIF_MESSAGE | NIF_ICON | NIF_TIP
            if not shell.Shell_NotifyIconW(0, c.byref(data)):
                raise RuntimeError("Windows 托盘图标创建失败")
            data.version = 4
            shell.Shell_NotifyIconW(4, c.byref(data))

        @callback_type
        def procedure(hwnd, message, wp, lp):
            try:
                if message == 0x8001:
                    while True:
                        try:
                            title, text = self.messages.get_nowait()
                        except queue.Empty:
                            break
                        data.flags = 16  # NIF_INFO; Windows supplies the notification sound.
                        data.title = title[:63]
                        data.text = text[:255]
                        data.info_flags = 1  # NIIF_INFO
                        if not shell.Shell_NotifyIconW(1, c.byref(data)):
                            raise RuntimeError("Windows 拒绝显示接收通知")
                        logging.info("接收通知已提交：%s", title)
                    return 0
                if message == 0x8002:
                    event = lp & 0xFFFF
                    if event in (0x405, 0x400, 0x401, 0x203):
                        os.startfile(str(self.destination))
                    elif event == 0x402:
                        logging.info("接收通知已显示")
                    return 0
                if message == taskbar_created:
                    add_icon()
                    return 0
            except Exception:
                logging.exception("通知显示失败，已保存的文件不受影响")
            return user.DefWindowProcW(hwnd, message, wp, lp)

        try:
            instance = kernel.GetModuleHandleW(None)
            cls = WindowClass()
            cls.procedure, cls.instance, cls.name = procedure, instance, f"EasyDropReceipt-{os.getpid()}"
            if not user.RegisterClassW(c.byref(cls)):
                raise c.WinError(c.get_last_error())
            self.hwnd = user.CreateWindowExW(0, cls.name, "EasyDrop 接收通知", 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            data.window = self.hwnd
            add_icon()
            self.ready.set()
            message = w.MSG()
            while user.GetMessageW(c.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(c.byref(message))
                user.DispatchMessageW(c.byref(message))
        except Exception:
            logging.exception("Windows 通知组件启动失败")
        finally:
            if self.hwnd:
                shell.Shell_NotifyIconW(2, c.byref(data))
            self.hwnd = None
            self.ready.set()
