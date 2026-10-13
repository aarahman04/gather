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
KEYUP = 0x2
win32._sig(win32.user32, "keybd_event", None, w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t)


def press(*vks):
    """Verify focus before sending only the test watcher's registered keys."""
    hwnd = win32.user32.GetForegroundWindow()
    app, title = win32._foreground_info()
    assert hwnd and title, "Cannot verify foreground window"
    print(f"Foreground verified: {app}, {ascii(title)}")
    assert win32.user32.GetForegroundWindow() == hwnd, "Focus changed before keys"
    try:
        for vk in vks:
            win32.user32.keybd_event(vk, 0, 0, 0)
    finally:
        for vk in reversed(vks):
            win32.user32.keybd_event(vk, 0, KEYUP, 0)


ALT_C, PAUSE = (0x12, ord("C")), (0x13,)


@pytest.fixture(scope="module", autouse=True)
def restore_clipboard(request):
    """It's the user's live machine: put their clipboard text back afterwards."""
    if request.config.getoption("--no-desktop"):
        yield
        return
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


@pytest.mark.desktop
def test_text_roundtrip():
    win32.copy_text_to_clipboard("gather ✓ ünïcode")
    assert win32._get_clipboard_text() == "gather ✓ ünïcode"


@pytest.mark.desktop
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
    wt = win32.ClipboardWatcher(hits.set, lambda t, a, ti: texts.append((t, a, ti)), mouse_trigger="Off")
    ok = wt.start()
    yield wt, ok, hits, texts
    wt.stop()
    assert wt._thread is None


@pytest.mark.desktop
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


@pytest.mark.desktop
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


@pytest.mark.desktop
def test_hotkey(watcher):
    wt, ok, hits, _ = watcher
    if not ok:
        pytest.skip("Alt+C already registered by another app")
    assert wt.hotkey == win32.DEFAULT_HOTKEY == "Alt+C"
    press(*ALT_C)
    assert hits.wait(2)


@pytest.mark.desktop
def test_set_hotkey(watcher):
    wt, ok, hits, _ = watcher
    if not ok:
        pytest.skip("Alt+C already registered by another app")
    with pytest.raises(ValueError):
        wt.set_hotkey("F13")
    try:
        assert wt.set_hotkey("Pause") and wt.hotkey == "Pause"
        press(*PAUSE)
        assert hits.wait(2)
        hits.clear()
        mods, vk = win32.HOTKEYS["Alt+C"]
        assert win32.user32.RegisterHotKey(None, 0x7ABD, mods, vk)
        win32.user32.UnregisterHotKey(None, 0x7ABD)

        # another window/thread holds Scroll Lock: switching fails, Pause still fires
        held, release = threading.Event(), threading.Event()

        def holder():
            mods, vk = win32.HOTKEYS["Scroll Lock"]
            held.ok = win32.user32.RegisterHotKey(None, 0x7ABC, mods | win32.MOD_NOREPEAT, vk)
            held.set()
            release.wait(10)
            if held.ok:
                win32.user32.UnregisterHotKey(None, 0x7ABC)

        th = threading.Thread(target=holder)
        th.start()
        try:
            assert held.wait(5)
            if not held.ok:
                pytest.skip("Scroll Lock already registered by another app")
            assert wt.set_hotkey("Scroll Lock") is False
            assert wt.hotkey == "Pause"
            hits.clear()
            press(*PAUSE)
            assert hits.wait(2)
        finally:
            release.set()
            th.join()
    finally:
        assert wt.set_hotkey("Alt+C") and wt.hotkey == "Alt+C"
    hits.clear()
    press(*ALT_C)
    assert hits.wait(2)


@pytest.mark.parametrize("preset", ["Alt+C", "Win+Y", "Pause", "Scroll Lock", "Ctrl+Alt+G"])
def test_swap_registers_new_preset_before_releasing_previous(monkeypatch, preset):
    wt = win32.ClipboardWatcher(lambda: None, lambda *args: None)
    wt._hwnd = 123
    wt.hotkey = "Pause" if preset == "Alt+C" else "Alt+C"
    calls = []
    monkeypatch.setattr(win32.user32, "RegisterHotKey",
                        lambda hwnd, key_id, mods, vk: calls.append(("register", key_id, mods, vk)) or True)
    monkeypatch.setattr(win32.user32, "UnregisterHotKey",
                        lambda hwnd, key_id: calls.append(("unregister", key_id)) or True)
    done, result = threading.Event(), []
    wt._req = (preset, done, result)
    wt._swap_hotkey()
    mods, vk = win32.HOTKEYS[preset]
    assert calls == [("register", win32.HOTKEY_ID + 1, mods | win32.MOD_NOREPEAT, vk),
                     ("unregister", win32.HOTKEY_ID)]
    assert wt.hotkey == preset and wt._hotkey_id == win32.HOTKEY_ID + 1
    assert done.is_set() and result == [True]


def test_failed_swap_never_releases_previous(monkeypatch):
    wt = win32.ClipboardWatcher(lambda: None, lambda *args: None)
    wt._hwnd, wt.hotkey = 123, "Alt+C"
    released = []
    monkeypatch.setattr(win32.user32, "RegisterHotKey", lambda *args: False)
    monkeypatch.setattr(win32.user32, "UnregisterHotKey", lambda *args: released.append(args))
    done, result = threading.Event(), []
    wt._req = ("Pause", done, result)
    wt._swap_hotkey()
    assert wt.hotkey == "Alt+C" and wt._hotkey_id == win32.HOTKEY_ID
    assert released == [] and done.is_set() and result == [False]


def test_retired_hotkey_message_does_not_toggle(monkeypatch):
    hits = []
    wt = win32.ClipboardWatcher(lambda: hits.append(True), lambda *args: None)
    wt._hwnd, wt.hotkey, wt._hotkey_id = 123, "Pause", win32.HOTKEY_ID + 1
    monkeypatch.setitem(win32._watchers, wt._hwnd, wt)
    monkeypatch.setattr(win32.user32, "DefWindowProcW", lambda *args: 0)
    win32._wndproc(wt._hwnd, win32.WM_HOTKEY, win32.HOTKEY_ID, 0)
    assert hits == []
    win32._wndproc(wt._hwnd, win32.WM_HOTKEY, wt._hotkey_id, ord("C") << 16 | win32.MOD_ALT)
    assert hits == []  # old binding with a reused ID
    win32._wndproc(wt._hwnd, win32.WM_HOTKEY, wt._hotkey_id, 0x13 << 16)
    assert hits == [True]


def test_failed_post_clears_hotkey_request(monkeypatch):
    wt = win32.ClipboardWatcher(lambda: None, lambda *args: None)
    wt._hwnd, wt.hotkey = 123, "Alt+C"
    monkeypatch.setattr(win32.user32, "PostMessageW", lambda *args: False)
    assert wt.set_hotkey("Pause") is False
    assert wt._req is None and wt.hotkey == "Alt+C"


@pytest.mark.desktop
def test_startup_registration_failure(monkeypatch):
    wt = win32.ClipboardWatcher(lambda: None, lambda *args: None, mouse_trigger="Off")
    monkeypatch.setattr(wt, "_register", lambda *args: False)
    try:
        assert wt.start() is False
        assert wt.hotkey is None and wt._hwnd
    finally:
        wt.stop()
    assert wt._hwnd is None


@pytest.mark.desktop
def test_timed_out_hotkey_request_does_not_apply_later(watcher, monkeypatch):
    wt, ok, hits, _ = watcher
    if not ok:
        pytest.skip("Alt+C already registered by another app")
    entered, release, processed = threading.Event(), threading.Event(), threading.Event()
    original_info, original_swap = win32._foreground_info, wt._swap_hotkey

    def stalled_info():
        entered.set()
        assert release.wait(5)
        return original_info()

    def observed_swap():
        original_swap()
        processed.set()

    monkeypatch.setattr(win32, "_foreground_info", stalled_info)
    monkeypatch.setattr(wt, "_swap_hotkey", observed_swap)
    wt.set_capturing(True)
    external_copy("codex-hotkey-timeout")
    assert entered.wait(2)
    try:
        assert wt.set_hotkey("Pause") is False
    finally:
        release.set()
    assert processed.wait(2)
    assert wt.hotkey == "Alt+C"
    press(*ALT_C)
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
