"""pytest plugin for tests on the user's desktop; also used by build.ps1."""
import ctypes
import time

from gather import win32


_saved = None
_saved_ok = False


def foreground():
    hwnd = win32.user32.GetForegroundWindow()
    app, title = win32._foreground_info()
    if not hwnd or not title:
        raise RuntimeError("Cannot verify the foreground window; no keys sent")
    print(f"Foreground verified: hwnd={hwnd}, app={app}, title={ascii(title)}", flush=True)
    return hwnd


def restore_text(saved):
    # Restore raw UTF-16 too, in case the original text has lone surrogates.
    for attempt in range(10):
        try:
            win32._open_clipboard(win32._owner_hwnd)
            try:
                if not win32.user32.EmptyClipboard():
                    raise ctypes.WinError(ctypes.get_last_error())
                if saved is not None:
                    win32._set_global(win32.CF_UNICODETEXT,
                                      (saved + "\0").encode("utf-16-le", "surrogatepass"))
            finally:
                win32.user32.CloseClipboard()
            return
        except OSError:
            if attempt == 9:
                raise
            time.sleep(0.1)


def pytest_sessionstart(session):
    global _saved, _saved_ok
    # Fail before any input/clipboard changes when the user's utility is running.
    from exe_check import gather_processes
    if gather_processes():
        raise RuntimeError("Quit the existing Gather instance before live tests")
    _saved = win32._get_clipboard_text()
    _saved_ok = True
    original = win32.user32.keybd_event

    def guarded_key(vk, scan, flags, extra):
        if not flags & 2:
            foreground()
        return original(vk, scan, flags, extra)

    win32.user32.keybd_event = guarded_key


def pytest_sessionfinish(session, exitstatus):
    if _saved_ok:
        restore_text(_saved)
        assert win32._get_clipboard_text() == _saved, "Clipboard text restoration failed"
        print("Original clipboard text restored and verified", flush=True)
