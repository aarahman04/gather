"""Win32 clipboard, hotkeys and mouse hook (ctypes only, no pywin32)."""
import ctypes
import os
import threading
import time
from ctypes import wintypes as w
from pathlib import Path
from typing import Callable


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, WPARAM, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = w.HWND(-3)
WM_APP = 0x8000
WM_SET_HOTKEY = WM_APP + 1  # private: swap the hotkey on the watcher thread
WM_SET_MOUSE_TRIGGER = WM_APP + 2
WM_MOUSE_TRIGGER = WM_APP + 3
WM_SET_CAPTURING = WM_APP + 4
WH_MOUSE_LL = 14
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x207, 0x208
WM_TIMER, CLIPBOARD_RETRY_TIMER = 0x113, 1
OWN_INPUT_TAG = 0x47415448
MOD_ALT, MOD_CONTROL, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x8, 0x4000
CF_UNICODETEXT, CF_HDROP = 13, 15
GMEM_MOVEABLE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ALREADY_EXISTS = 183
HOTKEY_ID = 1
UNDO_HOTKEY_ID = 3  # toggle presets alternate between IDs 1 and 2

# Tray presets, name -> (modifiers, virtual key). MOD_NOREPEAT is added on registration.
HOTKEYS: dict[str, tuple[int, int]] = {
    "Alt+C": (MOD_ALT, ord("C")),
    "Win+Y": (MOD_WIN, ord("Y")),
    "Pause": (0, 0x13),        # VK_PAUSE
    "Scroll Lock": (0, 0x91),  # VK_SCROLL
    "Ctrl+Alt+G": (MOD_CONTROL | MOD_ALT, ord("G")),
}
DEFAULT_HOTKEY = "Alt+C"
MOUSE_TRIGGERS = {
    "Middle-click": "Middle-click (replaces normal middle-click)",
    "Alt + middle-click": "Alt + middle-click",
    "Off": "Off",
}
DEFAULT_MOUSE_TRIGGER = "Middle-click"


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", w.POINT), ("mouseData", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


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
_sig(user32, "SetWindowsHookExW", w.HANDLE, ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD)
_sig(user32, "UnhookWindowsHookEx", w.BOOL, w.HANDLE)
_sig(user32, "CallNextHookEx", LRESULT, w.HANDLE, ctypes.c_int, WPARAM, LPARAM)
_sig(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
_sig(user32, "keybd_event", None, w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t)
_sig(user32, "SetTimer", WPARAM, w.HWND, WPARAM, w.UINT, w.LPVOID)
_sig(user32, "KillTimer", w.BOOL, w.HWND, WPARAM)
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
    # Capture must yield to the message loop while locked, so mouse hooks don't time out.
    if for_capture:
        if not user32.OpenClipboard(hwnd):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
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
            if (msg == WM_HOTKEY and wparam == UNDO_HOTKEY_ID and wt._capturing
                    and wt.undo_hotkey_ok and (lparam >> 16) & 0xFFFF == ord("Z")):
                wt._on_undo()
                return 0
            # Old queued hotkeys can survive a swap, including reuse of their ID.
            if (msg == WM_HOTKEY and wparam == wt._hotkey_id and wt.hotkey is not None
                    and (lparam >> 16) & 0xFFFF == HOTKEYS[wt.hotkey][1]):
                wt._on_hotkey()
                return 0
            if msg == WM_CLIPBOARDUPDATE:
                wt._clipboard_retries = 0
                wt._handle_update()
                return 0
            if msg == WM_TIMER and wparam == CLIPBOARD_RETRY_TIMER:
                user32.KillTimer(hwnd, CLIPBOARD_RETRY_TIMER)
                wt._handle_update()
                return 0
            if msg == WM_SET_HOTKEY:
                wt._swap_hotkey()
                return 0
            if msg == WM_SET_MOUSE_TRIGGER:
                wt._swap_mouse_trigger()
                return 0
            if msg == WM_SET_CAPTURING:
                wt._set_undo_hotkey()
                return 0
            if msg == WM_MOUSE_TRIGGER:
                if wparam:
                    # An unassigned key masks Alt's menu activation without a real shortcut.
                    user32.keybd_event(0xE8, 0, 0, OWN_INPUT_TAG)
                    user32.keybd_event(0xE8, 0, 2, OWN_INPUT_TAG)
                wt._on_hotkey()
                return 0
            if msg == WM_DESTROY:
                try:
                    wt._unhook_mouse()
                finally:
                    user32.UnregisterHotKey(hwnd, wt._hotkey_id)
                    user32.UnregisterHotKey(hwnd, UNDO_HOTKEY_ID)
                    wt.undo_hotkey_ok = False
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

    Receives WM_HOTKEY, WH_MOUSE_LL callbacks and WM_CLIPBOARDUPDATE.
    Callbacks run on the watcher thread; keep them cheap (caller enqueues).
    """

    def __init__(self, on_hotkey: Callable[[], None],
                 on_text: Callable[[str, str, str], None],
                 hotkey: str = DEFAULT_HOTKEY,
                 mouse_trigger: str = DEFAULT_MOUSE_TRIGGER,
                 on_undo: Callable[[], None] | None = None) -> None:
        """on_text(text, app, title): app = foreground process exe name
        (e.g. 'chrome.exe'), title = foreground window title at copy time.
        hotkey: a HOTKEYS name, registered by start()."""
        if hotkey not in HOTKEYS:
            raise ValueError(f"unknown hotkey {hotkey!r}")
        if mouse_trigger not in MOUSE_TRIGGERS:
            raise ValueError(f"unknown mouse trigger {mouse_trigger!r}")
        self._want_mouse = mouse_trigger
        self.mouse_trigger = "Off"
        self._mouse_hook = None
        self._mouse_proc = HOOKPROC(self._mouse_callback)  # keep alive until unhooked
        self._mouse_swallowed = False
        self._mouse_req = None
        self._want = hotkey
        self.hotkey: str | None = None  # name currently registered, None if none
        self._hotkey_id = HOTKEY_ID
        self._req = None  # (name, done Event, result list) for _swap_hotkey
        self._lock = threading.Lock()
        self._req_lock = threading.Lock()
        self._on_hotkey = on_hotkey
        self._on_undo = on_undo
        self.undo_hotkey_ok = False
        self._capture_done = threading.Event()
        self._on_text = on_text
        self._capturing = False
        self._clipboard_retries = 0
        self._hwnd = None
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
        return self.hotkey is not None

    def set_hotkey(self, name: str) -> bool:
        """Switch presets on the watcher thread. False keeps the working key;
        cancel requests that haven't started within 2 s."""
        if name not in HOTKEYS:
            raise ValueError(f"unknown hotkey {name!r}")
        # RegisterHotKey must run on the thread that owns the window
        with self._lock:
            if not self._hwnd:
                return False
            if name == self.hotkey:
                return True
            done, result = threading.Event(), []
            with self._req_lock:
                self._req = (name, done, result)
                if not user32.PostMessageW(self._hwnd, WM_SET_HOTKEY, 0, 0):
                    self._req = None
                    return False
            done.wait(2)
            # If registration has started, wait for its atomic swap to finish.
            with self._req_lock:
                self._req = None
                return bool(result and result[0])

    def _register(self, name: str, key_id: int) -> bool:
        mods, vk = HOTKEYS[name]
        return bool(user32.RegisterHotKey(self._hwnd, key_id, mods | MOD_NOREPEAT, vk))

    def _swap_hotkey(self) -> None:
        with self._req_lock:
            if self._req is None:
                return
            (name, done, result), self._req = self._req, None
            # Register first: a failed choice must never release the working key.
            new_id = HOTKEY_ID + 1 if self._hotkey_id == HOTKEY_ID else HOTKEY_ID
            ok = self._register(name, new_id)
            if ok:
                user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
                self._hotkey_id, self.hotkey = new_id, name
            result.append(ok)
            done.set()

    def set_capturing(self, on: bool) -> bool:
        """Enable capture and its recording-only undo key. Return whether Alt+Z is available."""
        with self._lock:
            self._capturing = bool(on)
            if self._on_undo is None:
                return True
            self._capture_done.clear()
            if not self._hwnd or not user32.PostMessageW(self._hwnd, WM_SET_CAPTURING, 0, 0):
                return False
            self._capture_done.wait(2)
            return self._capture_done.is_set() and (not on or self.undo_hotkey_ok)

    def _set_undo_hotkey(self) -> None:
        # Read current intent: even a delayed start message must not register while idle.
        try:
            if self._capturing and not self.undo_hotkey_ok:
                self.undo_hotkey_ok = bool(user32.RegisterHotKey(
                    self._hwnd, UNDO_HOTKEY_ID, MOD_ALT | MOD_NOREPEAT, ord("Z")))
            elif not self._capturing and self.undo_hotkey_ok:
                user32.UnregisterHotKey(self._hwnd, UNDO_HOTKEY_ID)
                self.undo_hotkey_ok = False
        finally:
            self._capture_done.set()

    def set_mouse_trigger(self, name: str) -> bool:
        if name not in MOUSE_TRIGGERS:
            raise ValueError(f"unknown mouse trigger {name!r}")
        with self._lock:
            if not self._hwnd:
                return False
            if name == self.mouse_trigger:
                return True
            done, result = threading.Event(), []
            with self._req_lock:
                self._mouse_req = (name, done, result)
                if not user32.PostMessageW(self._hwnd, WM_SET_MOUSE_TRIGGER, 0, 0):
                    self._mouse_req = None
                    return False
            done.wait(2)
            with self._req_lock:
                self._mouse_req = None
                return bool(result and result[0])

    def _unhook_mouse(self) -> None:
        if self._mouse_hook:
            if not user32.UnhookWindowsHookEx(self._mouse_hook):
                raise ctypes.WinError(ctypes.get_last_error())
            self._mouse_hook = None
        self.mouse_trigger = "Off"

    def _install_mouse(self, name: str) -> bool:
        if name == "Off":
            return True
        self._mouse_hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._mouse_proc, kernel32.GetModuleHandleW(None), 0)
        if not self._mouse_hook:
            return False
        self.mouse_trigger = name
        return True

    def _swap_mouse_trigger(self) -> None:
        with self._req_lock:
            # Finish a consumed click before removing its hook; never orphan its release.
            if self._mouse_req is None or self._mouse_swallowed:
                return
            (name, done, result), self._mouse_req = self._mouse_req, None
            try:
                self._unhook_mouse()
                result.append(self._install_mouse(name))
            finally:
                done.set()

    def _mouse_callback(self, code, msg, data):
        if code >= 0 and msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            event = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if event.dwExtraInfo != OWN_INPUT_TAG:
                if msg == WM_MBUTTONUP and self._mouse_swallowed:
                    self._mouse_swallowed = False
                    user32.PostMessageW(self._hwnd, WM_SET_MOUSE_TRIGGER, 0, 0)
                    return 1
                if msg == WM_MBUTTONDOWN:
                    if self._mouse_swallowed:
                        return 1
                    alt = bool(user32.GetAsyncKeyState(0x12) & 0x8000)
                    if (self.mouse_trigger == "Middle-click" or
                            self.mouse_trigger == "Alt + middle-click" and alt):
                        if user32.PostMessageW(self._hwnd, WM_MOUSE_TRIGGER, int(alt), 0):
                            self._mouse_swallowed = True
                            return 1
        return user32.CallNextHookEx(self._mouse_hook, code, msg, data)

    def stop(self) -> None:
        """Unregister hotkey + listener, destroy window, end thread."""
        global _owner_hwnd
        if self._hwnd:
            if _owner_hwnd == self._hwnd:
                _owner_hwnd = None
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)  # DefWindowProc -> DestroyWindow
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Gather's watcher did not stop")
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
            if self._register(self._want, self._hotkey_id):
                self.hotkey = self._want
            if not user32.AddClipboardFormatListener(hwnd):
                raise ctypes.WinError(ctypes.get_last_error())
            self._install_mouse(self._want_mouse)
            _owner_hwnd = hwnd
        except BaseException as e:
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)
                _watchers.pop(self._hwnd, None)
                self._hwnd = None
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        msg = w.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                self._unhook_mouse()
            finally:
                _watchers.pop(hwnd, None)
                self._hwnd = None
                self.hotkey = None
                self.undo_hotkey_ok = False
                self._mouse_swallowed = False

    def _handle_update(self) -> None:
        user32.KillTimer(self._hwnd, CLIPBOARD_RETRY_TIMER)
        if not self._capturing:
            return
        owner = user32.GetClipboardOwner()
        if owner and _window_pid(owner) == os.getpid():
            return  # our own write (fast path; re-checked under the clipboard lock)
        try:
            text = _get_clipboard_text(self._hwnd, for_capture=True)
        except OSError:
            if self._clipboard_retries < 50:
                self._clipboard_retries += 1
                user32.SetTimer(self._hwnd, CLIPBOARD_RETRY_TIMER, 20, None)
            return
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
