import ctypes
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes as w

import pytest

from gather import win32

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.DragQueryFileW.restype = w.UINT
shell32.DragQueryFileW.argtypes = [w.HANDLE, w.UINT, w.LPWSTR, w.UINT]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.keybd_event.restype = None
user32.keybd_event.argtypes = [w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t]


@pytest.fixture(scope="module", autouse=True)
def restore_clipboard():
    """It's the user's live machine: put their clipboard text back afterwards."""
    try:
        saved = win32._get_clipboard_text()
    except OSError:
        saved = None
    yield
    if saved is not None:
        win32.copy_text_to_clipboard(saved)


def external_copy(text: str) -> None:
    # separate process (so the owner isn't us) using Gather's ~1 s lock retry;
    # PowerShell's Set-Clipboard fails outright if the clipboard is briefly locked
    subprocess.run([sys.executable, "-c", "import sys; from gather import win32; "
                    "win32.copy_text_to_clipboard(sys.argv[1])", text], check=True, timeout=30)


def wait_for(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_text_roundtrip():
    win32.copy_text_to_clipboard("gather ✓ ünïcode")
    assert win32._get_clipboard_text() == "gather ✓ ünïcode"


def test_files_roundtrip(tmp_path):
    files = [tmp_path / "a.txt", tmp_path / "b ü.md"]
    for f in files:
        f.write_text("x")
    win32.copy_files_to_clipboard([str(f) for f in files], text="hi")
    win32._open_clipboard()
    try:
        h = win32.user32.GetClipboardData(win32.CF_HDROP)
        assert h
        n = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        got = []
        for i in range(n):
            buf = ctypes.create_unicode_buffer(1024)
            shell32.DragQueryFileW(h, i, buf, 1024)
            got.append(buf.value)
        hd = win32.user32.GetClipboardData(win32.CF_PREFERRED_DROPEFFECT)
        p = win32.kernel32.GlobalLock(hd)
        effect = ctypes.c_uint32.from_address(p).value
        win32.kernel32.GlobalUnlock(hd)
    finally:
        win32.user32.CloseClipboard()
    assert got == [str(f) for f in files]
    assert effect == 1
    assert win32._get_clipboard_text() == "hi"


@pytest.fixture
def watcher():
    hits, texts = threading.Event(), []
    wt = win32.ClipboardWatcher(hits.set, lambda t, a, ti: texts.append((t, a, ti)))
    ok = wt.start()
    yield wt, ok, hits, texts
    wt.stop()
    assert wt._thread is None


def test_watcher_captures_external_only(watcher):
    wt, _, _, texts = watcher
    wt.set_capturing(True)
    external_copy("hello-gather")
    assert wait_for(lambda: any(t == "hello-gather" for t, _, _ in texts), 3)
    _, app, title = next(x for x in texts if x[0] == "hello-gather")
    assert isinstance(app, str) and isinstance(title, str)

    texts.clear()
    win32.copy_text_to_clipboard("self-write")
    win32.copy_files_to_clipboard([__file__])
    time.sleep(1)
    assert texts == []

    wt.set_capturing(False)
    external_copy("not-captured")
    time.sleep(1)
    assert texts == []


def test_password_manager_copy_ignored(watcher):
    wt, _, _, texts = watcher
    wt.set_capturing(True)
    # another process copies text flagged the way password managers flag it
    script = ("from gather import win32 as m\n"
              "m._open_clipboard()\n"
              "m.user32.EmptyClipboard()\n"
              "m._set_global(m.CF_UNICODETEXT, m._text_bytes('hunter2'))\n"
              "m._set_global(m.CF_EXCLUDE, b'\\0\\0\\0\\0')\n"
              "m.user32.CloseClipboard()\n")
    subprocess.run([sys.executable, "-c", script], check=True, timeout=30)
    time.sleep(1)
    assert texts == []


def test_hotkey(watcher):
    _, ok, hits, _ = watcher
    if not ok:
        pytest.skip("Ctrl+Alt+G already registered by another app")
    VK_CONTROL, VK_MENU, KEYUP = 0x11, 0x12, 0x2
    for vk in (VK_CONTROL, VK_MENU, ord("G")):
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (ord("G"), VK_MENU, VK_CONTROL):
        user32.keybd_event(vk, 0, KEYUP, 0)
    assert hits.wait(2)


def test_documents_dir():
    assert win32.documents_dir().is_dir()


def test_single_instance():
    name = f"GatherTest-{uuid.uuid4().hex}"
    assert win32.single_instance(name) is True
    code = f"from gather import win32; print(win32.single_instance({name!r}))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "False"
