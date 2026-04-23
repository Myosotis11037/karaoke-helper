from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable


if sys.platform == "win32":
    WM_DROPFILES = 0x0233
    GWL_WNDPROC = -4
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    shcore = getattr(ctypes.windll, "shcore", None)

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        SetWindowLongPtr = user32.SetWindowLongPtrW
        GetWindowLongPtr = user32.GetWindowLongPtrW
    else:
        SetWindowLongPtr = user32.SetWindowLongW
        GetWindowLongPtr = user32.GetWindowLongW

    SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROC]
    SetWindowLongPtr.restype = LRESULT
    GetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int]
    GetWindowLongPtr.restype = LRESULT

    CallWindowProc = user32.CallWindowProcW
    CallWindowProc.argtypes = [LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    CallWindowProc.restype = LRESULT

    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryPoint.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.POINT)]
    shell32.DragQueryPoint.restype = wintypes.BOOL
    shell32.DragFinish.argtypes = [wintypes.HANDLE]

    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetDpiForSystem.restype = wintypes.UINT


def enable_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def set_explicit_app_user_model_id(app_id: str) -> None:
    if sys.platform != "win32":
        return

    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        pass


def apply_tk_scaling(root) -> None:
    if sys.platform != "win32":
        return

    try:
        dpi = user32.GetDpiForWindow(root.winfo_id())
    except Exception:
        try:
            dpi = user32.GetDpiForSystem()
        except Exception:
            dpi = 96

    root.tk.call("tk", "scaling", dpi / 72.0)


class WindowsFileDropHandler:
    def __init__(self, root, on_drop: Callable[[list[str], int, int], None]) -> None:
        self.root = root
        self.on_drop = on_drop
        self._installed = False
        self._original_proc = None
        self._new_proc = None
        self._hwnd = None

    def install(self) -> None:
        if sys.platform != "win32" or self._installed:
            return

        self.root.update_idletasks()
        self._hwnd = self.root.winfo_id()
        shell32.DragAcceptFiles(self._hwnd, True)

        self._new_proc = WNDPROC(self._window_proc)
        self._original_proc = GetWindowLongPtr(self._hwnd, GWL_WNDPROC)
        SetWindowLongPtr(self._hwnd, GWL_WNDPROC, self._new_proc)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self._installed = True

    def _on_destroy(self, event) -> None:
        if event.widget is not self.root or not self._installed:
            return
        try:
            shell32.DragAcceptFiles(self._hwnd, False)
        except Exception:
            pass
        self._installed = False

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            dropped_files = self._extract_files(wparam)
            point = wintypes.POINT()
            shell32.DragQueryPoint(wparam, ctypes.byref(point))
            shell32.DragFinish(wparam)
            user32.ClientToScreen(hwnd, ctypes.byref(point))
            self.on_drop(dropped_files, point.x, point.y)
            return 0

        return CallWindowProc(self._original_proc, hwnd, msg, wparam, lparam)

    def _extract_files(self, handle) -> list[str]:
        file_count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        files: list[str] = []
        for index in range(file_count):
            length = shell32.DragQueryFileW(handle, index, None, 0)
            buffer = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(handle, index, buffer, len(buffer))
            files.append(buffer.value)
        return files
