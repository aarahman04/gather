"""Win32 layer (ctypes only, no pywin32).

Owner: Wave 1 agent A. Signatures below are the contract; do not change them.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes as w
from pathlib import Path
from typing import Callable

# Ctrl+Alt+G
HOTKEY_LABEL = "Ctrl+Alt+G"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = w.HWND(-3)
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
CF_UNICODETEXT, CF_HDROP = 13, 15
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
HOTKEY_ID = 1


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", w.DWORD), ("pt", w.POINT),
                ("fNC", w.BOOL), ("fWide", w.BOOL)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def _sig(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype, f.argtypes = restype, list(argtypes)
    return f


_sig(user32, "DefWindowProcW", LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)
_sig(user32, "RegisterClassW", w.ATOM, ctypes.POINTER(WNDCLASSW))
_sig(user32, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
_sig(user32, "DestroyWindow", w.BOOL, w.HWND)
_sig(user32, "GetMessageW", w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
_sig(user32, "TranslateMessage", w.BOOL, ctypes.POINTER(w.MSG))
_sig(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(w.MSG))
_sig(user32, "PostMessageW", w.BOOL, w.HWND, w.UINT, WPARAM, LPARAM)
_sig(user32, "PostQuitMessage", None, ctypes.c_int)
_sig(user32, "RegisterHotKey", w.BOOL, w.HWND, ctypes.c_int, w.UINT, w.UINT)
_sig(user32, "UnregisterHotKey", w.BOOL, w.HWND, ctypes.c_int)
_sig(user32, "AddClipboardFormatListener", w.BOOL, w.HWND)
_sig(user32, "RemoveClipboardFormatListener", w.BOOL, w.HWND)
_sig(user32, "OpenClipboard", w.BOOL, w.HWND)
_sig(user32, "CloseClipboard", w.BOOL)
_sig(user32, "EmptyClipboard", w.BOOL)
_sig(user32, "SetClipboardData", w.HANDLE, w.UINT, w.HANDLE)
_sig(user32, "GetClipboardData", w.HANDLE, w.UINT)
_sig(user32, "IsClipboardFormatAvailable", w.BOOL, w.UINT)
_sig(user32, "GetClipboardOwner", w.HWND)
_sig(user32, "RegisterClipboardFormatW", w.UINT, w.LPCWSTR)
_sig(user32, "GetForegroundWindow", w.HWND)
_sig(user32, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
_sig(user32, "GetWindowTextLengthW", ctypes.c_int, w.HWND)
_sig(user32, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
_sig(kernel32, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)
_sig(kernel32, "GlobalAlloc", w.HGLOBAL, w.UINT, ctypes.c_size_t)
_sig(kernel32, "GlobalFree", w.HGLOBAL, w.HGLOBAL)
_sig(kernel32, "GlobalLock", w.LPVOID, w.HGLOBAL)
_sig(kernel32, "GlobalUnlock", w.BOOL, w.HGLOBAL)
_sig(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
_sig(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
_sig(kernel32, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD,
     w.LPWSTR, ctypes.POINTER(w.DWORD))
_sig(kernel32, "CreateMutexW", w.HANDLE, w.LPVOID, w.BOOL, w.LPCWSTR)
_sig(shell32, "SHGetKnownFolderPath", ctypes.HRESULT, ctypes.POINTER(GUID),
     w.DWORD, w.HANDLE, ctypes.POINTER(ctypes.c_wchar_p))
_sig(ole32, "CoTaskMemFree", None, w.LPVOID)

CF_PREFERRED_DROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")
# Set by password managers so clipboard monitors skip their copies.
CF_EXCLUDE = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")

# Our clipboard writes use the watcher's hwnd as owner, so the watcher skips
# them via the GetClipboardOwner pid check (works for any window of ours).
_owner_hwnd = None
_watchers: dict[int, "ClipboardWatcher"] = {}
_mutex = None


def _open_clipboard(hwnd=None) -> None:
    # Clipboard is often briefly locked by another app (or a clipboard manager).
    for _ in range(50):  # ~1 s total
        if user32.OpenClipboard(hwnd):
            return
        time.sleep(0.02)
    raise ctypes.WinError(ctypes.get_last_error())


def _set_global(fmt: int, data: bytes) -> None:
    """Copy bytes into GMEM_MOVEABLE memory and hand it to the clipboard
    (clipboard must already be open)."""
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        err = ctypes.get_last_error()
        kernel32.GlobalFree(h)  # ownership only transfers on success
        raise ctypes.WinError(err)


def _text_bytes(text: str) -> bytes:
    return (text + "\0").encode("utf-16-le")


def _get_clipboard_text(hwnd=None, for_capture=False) -> str | None:
    """CF_UNICODETEXT on the clipboard, or None if no text. With for_capture,
    None if the copy is our own or a password manager flagged it (checked while
    the clipboard is open, so the content can't change between check and read)."""
    _open_clipboard(hwnd)
    try:
        if for_capture:
            owner = user32.GetClipboardOwner()
            if owner and _window_pid(owner) == os.getpid():
                return None
            if user32.IsClipboardFormatAvailable(CF_EXCLUDE):
                return None
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def _window_pid(hwnd) -> int:
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _foreground_info() -> tuple[str, str]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    app = ""
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 _window_pid(hwnd))
    if hproc:
        try:
            pbuf = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(len(pbuf))
            if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                app = os.path.basename(pbuf.value)
        finally:
            kernel32.CloseHandle(hproc)
    return app, buf.value


@WNDPROC
def _wndproc(hwnd, msg, wparam, lparam):
    try:
        wt = _watchers.get(hwnd)
        if wt is not None:
            if msg == WM_HOTKEY and wparam == HOTKEY_ID:
                wt._on_hotkey()
                return 0
            if msg == WM_CLIPBOARDUPDATE:
                wt._handle_update()
                return 0
            if msg == WM_DESTROY:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                user32.RemoveClipboardFormatListener(hwnd)
                user32.PostQuitMessage(0)
                return 0
    except Exception:  # never let an exception kill the message loop
        import traceback
        traceback.print_exc()
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_CLASS_NAME = "GatherClipboardWatcher"
_class_registered = False


def _register_class() -> None:
    global _class_registered
    if _class_registered:
        return
    wc = WNDCLASSW()
    wc.lpfnWndProc = _wndproc  # module-level, so never garbage collected
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = _CLASS_NAME
    if not user32.RegisterClassW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())
    _class_registered = True


class ClipboardWatcher:
    """Hidden message-only window on its own daemon thread.

    Receives WM_HOTKEY (Ctrl+Alt+G) and WM_CLIPBOARDUPDATE.
    Callbacks run on the watcher thread; keep them cheap (caller enqueues).
    """

    def __init__(self, on_hotkey: Callable[[], None],
                 on_text: Callable[[str, str, str], None]) -> None:
        """on_text(text, app, title): app = foreground process exe name
        (e.g. 'chrome.exe'), title = foreground window title at copy time."""
        self._on_hotkey = on_hotkey
        self._on_text = on_text
        self._capturing = False
        self._hwnd = None
        self._hotkey_ok = False
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start thread, block until window exists. Return True if the
        hotkey registered, False if taken (listener still runs)."""
        self._thread = threading.Thread(target=self._run, name="gather-win32",
                                        daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error:
            raise self._error
        return self._hotkey_ok

    def set_capturing(self, on: bool) -> None:
        """When False, clipboard updates are ignored (clipboard not read)."""
        self._capturing = bool(on)

    def stop(self) -> None:
        """Unregister hotkey + listener, destroy window, end thread."""
        global _owner_hwnd
        if self._hwnd:
            if _owner_hwnd == self._hwnd:
                _owner_hwnd = None
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)  # DefWindowProc -> DestroyWindow
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        global _owner_hwnd
        try:
            _register_class()
            hwnd = user32.CreateWindowExW(0, _CLASS_NAME, "Gather", 0, 0, 0, 0, 0,
                                          HWND_MESSAGE, None,
                                          kernel32.GetModuleHandleW(None), None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd
            _watchers[hwnd] = self
            self._hotkey_ok = bool(user32.RegisterHotKey(
                hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("G")))
            if not user32.AddClipboardFormatListener(hwnd):
                raise ctypes.WinError(ctypes.get_last_error())
            _owner_hwnd = hwnd
        except BaseException as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        _watchers.pop(hwnd, None)
        self._hwnd = None

    def _handle_update(self) -> None:
        if not self._capturing:
            return
        owner = user32.GetClipboardOwner()
        if owner and _window_pid(owner) == os.getpid():
            return  # our own write (fast path; re-checked under the clipboard lock)
        try:
            text = _get_clipboard_text(self._hwnd, for_capture=True)
        except OSError:
            return  # clipboard stayed locked; drop this update
        if text:
            app, title = _foreground_info()  # after the read: snapshot first
            self._on_text(text, app, title)


def copy_files_to_clipboard(paths: list[str], text: str | None = None) -> None:
    """Put files on clipboard as CF_HDROP + 'Preferred DropEffect' = COPY.
    If text is given, also set CF_UNICODETEXT. Must never be seen by
    ClipboardWatcher as a capture."""
    df = DROPFILES(pFiles=ctypes.sizeof(DROPFILES), fWide=1)
    files = "".join(str(p) + "\0" for p in paths) + "\0"
    # Encode everything before EmptyClipboard so an encoding error keeps the old clipboard.
    hdrop = bytes(df) + files.encode("utf-16-le")
    text_bytes = None if text is None else _text_bytes(text)
    _open_clipboard(_owner_hwnd)
    try:
        user32.EmptyClipboard()
        _set_global(CF_HDROP, hdrop)
        _set_global(CF_PREFERRED_DROPEFFECT, (1).to_bytes(4, "little"))  # DROPEFFECT_COPY
        if text_bytes is not None:
            _set_global(CF_UNICODETEXT, text_bytes)
    finally:
        user32.CloseClipboard()


def copy_text_to_clipboard(text: str) -> None:
    """Set CF_UNICODETEXT. Must never be seen by ClipboardWatcher as a capture."""
    data = _text_bytes(text)  # encode first: an error must not empty the clipboard
    _open_clipboard(_owner_hwnd)
    try:
        user32.EmptyClipboard()
        _set_global(CF_UNICODETEXT, data)
    finally:
        user32.CloseClipboard()


def documents_dir() -> Path:
    """Real Documents folder via SHGetKnownFolderPath (handles OneDrive redirect)."""
    fid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
               (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    p = ctypes.c_wchar_p()
    try:
        shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(p))
        return Path(p.value)
    except OSError:
        return Path.home() / "Documents"
    finally:
        if p:
            ole32.CoTaskMemFree(ctypes.cast(p, ctypes.c_void_p))


def single_instance(name: str = "Gather") -> bool:
    """Named mutex. False if another instance already holds it."""
    global _mutex
    ctypes.set_last_error(0)
    h = kernel32.CreateMutexW(None, False, f"Local\\{name}-singleton")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if h:
            kernel32.CloseHandle(h)
        return False
    _mutex = h  # keep alive for process lifetime
    return True
