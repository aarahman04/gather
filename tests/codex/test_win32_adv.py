import ctypes

import json

import subprocess

import sys

import threading

import time

from datetime import datetime

from types import SimpleNamespace

import pytest

from docx import Document

from gather import win32, writers

from gather.__main__ import App

from gather.session import Session, mask_secrets

from common import T, SECRET, TRICKY, child, external_text, wait_for


@pytest.fixture
def capture():
    session = Session(T)
    event = threading.Event()

    def receive(text, app, title):
        session.add(text, app, title)
        event.set()

    watcher = win32.ClipboardWatcher(lambda: None, receive, mouse_trigger="Off")
    watcher.start()
    watcher.set_capturing(True)
    try:
        yield watcher, session, event
    finally:
        watcher.stop()
        assert watcher._hwnd is None


@pytest.mark.desktop
def test_5mb_clipboard_is_captured_exactly(capture):
    _, session, event = capture
    text = "0123456789abcdef" * (5 * 1024 * 1024 // 16)
    assert len(text.encode("utf-8")) == 5 * 1024 * 1024
    external_text(text)
    assert event.wait(5)
    assert [c.text for c in session.chunks] == [text]


@pytest.mark.desktop
def test_50_copies_in_two_seconds_keep_order(capture):
    _, session, _ = capture
    result = child("""
import time
from gather import win32 as w
start = time.monotonic()
for i in range(50):
    time.sleep(max(0, start + i * 0.04 - time.monotonic()))
    w.copy_text_to_clipboard(f'codex-rapid-{i:02d}')
print(time.monotonic() - start)
"""
    )
    elapsed = float(result.stdout)
    assert elapsed < 2, f"Test producer was too slow: {elapsed}s"
    assert wait_for(lambda: len(session) >= 50)
    assert [c.text for c in session.chunks] == [f"codex-rapid-{i:02d}" for i in range(50)]


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F7 deferred: Windows keeps only the latest clipboard state, so copies made while the listener is stalled are unrecoverable by design; a separate metadata thread would add complexity for an artificial stall. Text is now read before the metadata lookup, which shrinks the window.")
@pytest.mark.desktop
def test_rapid_copies_survive_a_slow_foreground_query(capture, monkeypatch):
    _, session, _ = capture
    entered, release = threading.Event(), threading.Event()
    original = win32._foreground_info

    def slow_foreground():
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return original()

    monkeypatch.setattr(win32, "_foreground_info", slow_foreground)
    external_text("codex-delayed-0")
    assert entered.wait(2)
    try:
        child("""
import time
from gather import win32 as w
for i in range(1, 6):
    w.copy_text_to_clipboard(f'codex-delayed-{i}')
    time.sleep(0.04)
""")
    finally:
        release.set()
    assert wait_for(lambda: bool(session.chunks))
    time.sleep(0.1)
    assert [c.text for c in session.chunks] == [f"codex-delayed-{i}" for i in range(6)]


@pytest.mark.parametrize("fmt", ["image", "files"])
@pytest.mark.desktop
def test_nontext_is_ignored(capture, fmt, tmp_path):
    _, session, event = capture
    path = tmp_path / "nontext.txt"
    path.write_text("synthetic")
    child(r"""
import sys, struct
from gather import win32 as w
if sys.argv[1] == 'files':
    w.copy_files_to_clipboard([sys.argv[2]])
else:
    # BITMAPINFOHEADER and one BGR pixel padded to a DWORD: valid 1x1 CF_DIB.
    dib = struct.pack('<IiiHHIIiiII',40,1,1,1,24,0,4,0,0,0,0) + b'\x00\x00\xff\x00'
    w._open_clipboard()
    try:
        w.user32.EmptyClipboard()
        w._set_global(8,dib)
    finally:
        w.user32.CloseClipboard()
""", fmt, str(path))
    assert not event.wait(0.25)
    assert not session.chunks


@pytest.mark.parametrize("name", ["surrogates", "rtl", "zero_width", "crlf", "emoji_cjk"])
@pytest.mark.desktop
def test_unicode_clipboard_capture(capture, name):
    _, session, event = capture
    external_text(TRICKY[name])
    assert event.wait(3)
    assert [c.text for c in session.chunks] == [TRICKY[name].replace("\r\n", "\n")]


# Was xfail (F4) in Codex's review; fixed by raising the _open_clipboard retry budget to ~1 s.
@pytest.mark.parametrize("kind", ["text", "files"])
@pytest.mark.desktop
def test_write_waits_for_500ms_clipboard_lock(kind, tmp_path):
    holder = subprocess.Popen([sys.executable, "-c", """
import time
from gather import win32 as w
w._register_class()
hwnd = w.user32.CreateWindowExW(0,w._CLASS_NAME,'Codex clipboard lock',0,0,0,0,0,
                              w.HWND_MESSAGE,None,w.kernel32.GetModuleHandleW(None),None)
assert hwnd
w._open_clipboard(hwnd)
try:
    print('locked', flush=True)
    time.sleep(0.5)
finally:
    w.user32.CloseClipboard()
    w.user32.DestroyWindow(hwnd)
"""], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        if kind == "text":
            win32.copy_text_to_clipboard("codex-lock")
            assert win32._get_clipboard_text() == "codex-lock"
        else:
            path = tmp_path / "lock.md"
            path.write_text("synthetic")
            win32.copy_files_to_clipboard([str(path)])
            assert win32.user32.IsClipboardFormatAvailable(win32.CF_HDROP)
    finally:
        holder.communicate(timeout=5)
        assert holder.returncode == 0


# Was xfail (F9) in Codex's review; fixed by encoding payloads before opening/emptying the clipboard.
@pytest.mark.desktop
def test_encoding_failure_preserves_previous_clipboard():
    win32.copy_text_to_clipboard("codex-before-failure")
    with pytest.raises(UnicodeEncodeError):
        win32.copy_text_to_clipboard("\ud800")
    assert win32._get_clipboard_text() == "codex-before-failure"


# Was xfail (F5) in Codex's review; fixed by re-checking the owner while the clipboard is open, before the read.
@pytest.mark.desktop
def test_owner_change_during_read_does_not_capture_self(capture, monkeypatch):
    watcher, _, _ = capture
    watcher.set_capturing(False)
    external_text("codex-external-before-race")
    captured = []
    probe = win32.ClipboardWatcher(lambda: None, lambda t, a, ti: captured.append(t), mouse_trigger="Off")
    probe.set_capturing(True)

    real_owner = win32.user32.GetClipboardOwner
    fired = []

    def interleave():
        # Metadata now runs after the read, so inject the own write right after the early
        # (unlocked) owner check returns a stale external owner, i.e. before the read.
        owner = real_owner()
        if not fired:
            fired.append(True)
            win32.copy_text_to_clipboard("codex-own-write-during-read")
        return owner

    monkeypatch.setattr(win32.user32, "GetClipboardOwner", interleave)
    probe._handle_update()
    assert fired and captured == []


@pytest.mark.parametrize("failure", ["alloc", "lock", "set"])
def test_global_memory_failure_frees_owned_handle_and_closes(failure, monkeypatch):
    freed, closed = [], []
    buf = ctypes.create_string_buffer(100)
    monkeypatch.setattr(win32.user32, "OpenClipboard", lambda _: True)
    monkeypatch.setattr(win32.user32, "EmptyClipboard", lambda: True)
    monkeypatch.setattr(win32.user32, "CloseClipboard", lambda: closed.append(True))
    monkeypatch.setattr(win32.kernel32, "GlobalAlloc", lambda *args: 0 if failure == "alloc" else 123)
    monkeypatch.setattr(win32.kernel32, "GlobalLock", lambda _: 0 if failure == "lock" else ctypes.addressof(buf))
    monkeypatch.setattr(win32.kernel32, "GlobalUnlock", lambda _: True)
    monkeypatch.setattr(win32.kernel32, "GlobalFree", lambda h: freed.append(h))
    monkeypatch.setattr(win32.user32, "SetClipboardData", lambda *args: 0)
    with pytest.raises(OSError):
        win32.copy_text_to_clipboard("synthetic")
    assert closed == [True]
    assert freed == ([] if failure == "alloc" else [123])


