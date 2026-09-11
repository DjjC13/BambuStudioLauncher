#!/usr/bin/env python3
"""
A Windows notification-area (system tray) icon, using only the standard library.

tkinter has no tray support, and the usual answer is the third-party ``pystray``
package. This module talks to ``Shell_NotifyIconW`` through ctypes instead, so
the launcher keeps its "no pip installs" property and the bundled executable
stays small.

How it works
------------
A tray icon needs a window to receive its callbacks, and that window needs a
message loop. Tk owns the main thread's loop, so this creates its own hidden
window on a dedicated thread and pumps messages there. Callbacks therefore
arrive on *that* thread - the owner is responsible for marshalling them back to
the UI thread (the launcher uses ``Tk.after``).

Usage::

    tray = TrayIcon("Bambu Studio running", icon_path,
                    on_activate=..., on_quit=...)
    tray.show()
    tray.notify("Bambu Studio crashed", "Access violation")
    tray.close()
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- constants ------------------------------------------------------------

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000
WM_APP = 0x8000
TRAY_CALLBACK = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO, NIIF_WARNING, NIIF_ERROR = 0x01, 0x02, 0x03

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
SM_CXSMICON, SM_CYSMICON = 49, 50

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

IDM_SHOW, IDM_QUIT = 1, 2

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.restype = wintypes.HANDLE
user32.TrackPopupMenu.restype = ctypes.c_int
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]


class TrayIcon:
    """A notification-area icon with a Show / Quit context menu."""

    _seq = 0

    def __init__(self, tooltip: str, icon_path: Path | None = None,
                 on_activate=None, on_quit=None) -> None:
        self.tooltip = tooltip[:127]
        self.icon_path = icon_path
        self.on_activate = on_activate
        self.on_quit = on_quit

        self.hwnd = None
        self._hicon = None
        self._nid = None
        self._thread = None
        self._ready = threading.Event()
        self._added = False
        self.failed = False

        TrayIcon._seq += 1
        self._class_name = "BambuLauncherTray{}".format(TrayIcon._seq)
        # Held on the instance: ctypes callbacks are garbage collected
        # otherwise, and Windows would call into freed memory.
        self._wndproc = WNDPROC(self._on_message)
        self._wndclass = None

    # ------------------------------------------------------------- lifecycle

    def show(self) -> bool:
        """Create the icon. Returns False if the tray is unavailable."""
        if self._thread is not None:
            return not self.failed
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        return not self.failed

    def close(self) -> None:
        """Remove the icon and stop the message loop."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
        self._remove_icon()

    def notify(self, title: str, message: str, level: str = "error") -> None:
        """Raise a balloon notification from the icon."""
        if not self._added or self._nid is None:
            return
        flags = {"info": NIIF_INFO, "warning": NIIF_WARNING,
                 "error": NIIF_ERROR}.get(level, NIIF_INFO)
        self._nid.uFlags = NIF_INFO
        self._nid.szInfoTitle = title[:63]
        self._nid.szInfo = message[:255]
        self._nid.dwInfoFlags = flags
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    # --------------------------------------------------------------- internals

    def _run(self) -> None:
        try:
            self._create_window()
            self._add_icon()
        except Exception:
            self.failed = True
            self._ready.set()
            return

        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _create_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        self._wndclass = wc                      # keep alive
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())

        self.hwnd = user32.CreateWindowExW(
            0, self._class_name, self._class_name, 0, 0, 0, 0, 0,
            None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _load_icon(self):
        if self.icon_path and Path(self.icon_path).exists():
            hicon = user32.LoadImageW(
                None, str(self.icon_path), IMAGE_ICON,
                user32.GetSystemMetrics(SM_CXSMICON),
                user32.GetSystemMetrics(SM_CYSMICON),
                LR_LOADFROMFILE)
            if hicon:
                return hicon
        # Fall back to the generic application icon rather than no icon at all.
        return user32.LoadIconW(None, wintypes.LPCWSTR(32512))  # IDI_APPLICATION

    def _add_icon(self) -> None:
        self._hicon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = TRAY_CALLBACK
        nid.hIcon = self._hicon
        nid.szTip = self.tooltip
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._nid = nid
        self._added = True

    def _remove_icon(self) -> None:
        if self._added and self._nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._added = False

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._fire(self.on_activate)
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == WM_DESTROY:
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, IDM_SHOW, "Show launcher")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "Quit")

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # Required so the menu closes when the user clicks elsewhere.
        user32.SetForegroundWindow(self.hwnd)
        choice = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

        if choice == IDM_SHOW:
            self._fire(self.on_activate)
        elif choice == IDM_QUIT:
            self._fire(self.on_quit)

    @staticmethod
    def _fire(callback) -> None:
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
