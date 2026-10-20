"""Copy received files using Windows' standard file-copy clipboard format."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import time


def file_drop_payload(paths: list[Path]) -> bytes:
    if not paths:
        raise ValueError("没有可复制的文件")
    names = "\0".join(str(path.resolve()) for path in paths) + "\0\0"
    # DROPFILES: pFiles, POINT.x/y, fNC, fWide. Unicode paths, double NUL terminator.
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + names.encode("utf-16-le")


def copy_files(paths: list[Path]) -> None:
    if not paths:
        return
    if os.name != "nt":
        raise OSError("文件剪贴板只支持 Windows")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    import ctypes as c
    from ctypes import wintypes as w
    user = c.WinDLL("user32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [w.UINT, c.c_size_t]
    kernel.GlobalAlloc.restype = w.HANDLE
    kernel.GlobalLock.argtypes = [w.HANDLE]
    kernel.GlobalLock.restype = c.c_void_p
    kernel.GlobalUnlock.argtypes = [w.HANDLE]
    kernel.GlobalFree.argtypes = [w.HANDLE]
    kernel.GlobalFree.restype = w.HANDLE
    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = w.HINSTANCE
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes = [w.HWND]
    user.OpenClipboard.argtypes = [w.HWND]
    user.OpenClipboard.restype = w.BOOL
    user.CloseClipboard.argtypes = []
    user.EmptyClipboard.argtypes = []
    user.EmptyClipboard.restype = w.BOOL
    user.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
    user.SetClipboardData.restype = w.HANDLE
    user.RegisterClipboardFormatW.argtypes = [w.LPCWSTR]
    user.RegisterClipboardFormatW.restype = w.UINT

    effect_format = user.RegisterClipboardFormatW("Preferred DropEffect")
    if not effect_format:
        raise c.WinError(c.get_last_error())
    allocated = []
    window = None
    try:
        # Prepare all memory before replacing the clipboard's current content.
        formats = []
        for format_id, payload in ((15, file_drop_payload(paths)), (effect_format, struct.pack("<I", 1))):
            handle = kernel.GlobalAlloc(0x42, len(payload))  # GMEM_MOVEABLE | GMEM_ZEROINIT
            if not handle:
                raise MemoryError("剪贴板内存分配失败")
            allocated.append(handle)
            address = kernel.GlobalLock(handle)
            if not address:
                raise c.WinError(c.get_last_error())
            try:
                c.memmove(address, payload, len(payload))
            finally:
                kernel.GlobalUnlock(handle)
            formats.append((format_id, handle))
        # pythonw has no console window. Use a hidden owner window so that
        # EmptyClipboard does not leave a NULL owner (which breaks SetClipboardData).
        window = user.CreateWindowExW(0, "STATIC", "EasyDrop Clipboard", 0, 0, 0, 0, 0, None, None, kernel.GetModuleHandleW(None), None)
        if not window:
            raise c.WinError(c.get_last_error())
        for _ in range(20):
            if user.OpenClipboard(window):
                break
            time.sleep(0.05)
        else:
            raise OSError("剪贴板正被其他程序占用")
        try:
            if not user.EmptyClipboard():
                raise c.WinError(c.get_last_error())
            for format_id, handle in formats:
                if not user.SetClipboardData(format_id, handle):
                    raise c.WinError(c.get_last_error())
                allocated.remove(handle)  # Windows now owns this memory.
        finally:
            user.CloseClipboard()
    finally:
        for handle in allocated:
            kernel.GlobalFree(handle)
        if window:
            user.DestroyWindow(window)
