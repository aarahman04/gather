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


T = datetime(2026, 10, 13, 14, 30)
SECRET = "sk-" + "a" * 24  # synthetic, never a real credential
TRICKY = {
    "surrogates": "high \ud800 low \udfff end",
    "rtl": "English العربية עברית \u202eRTL\u202c",
    "zero_width": "a\u200bb\u200cc\u200dd\ufeffe",
    "crlf": "first\r\nsecond\nthird\rbare",
    "controls": "x\x00\x01\x08\x0b\x1b\ufffe\uffff end",
    "emoji_cjk": "😀👍🏽 你好 日本語 한국어 é e\u0301",
    "unbroken": "x" * 10000,
}


def child(code, *args, **kwargs):
    return subprocess.run([sys.executable, "-c", code, *args], check=True,
                          timeout=30, capture_output=True, text=True, **kwargs)


def external_text(text):
    child("import sys,json; from gather import win32 as w; "
          "t=json.load(sys.stdin); w._open_clipboard(); "
          "w.user32.EmptyClipboard(); "
          "w._set_global(w.CF_UNICODETEXT,(t+'\\0').encode('utf-16-le','surrogatepass')); "
          "w.user32.CloseClipboard()", input=json.dumps(text))


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def capture():
    session = Session(T)
    event = threading.Event()

    def receive(text, app, title):
        session.add(text, app, title)
        event.set()

    watcher = win32.ClipboardWatcher(lambda: None, receive)
    watcher.start()
    watcher.set_capturing(True)
    try:
        yield watcher, session, event
    finally:
        watcher.stop()
        assert watcher._hwnd is None


def test_5mb_clipboard_is_captured_exactly(capture):
    _, session, event = capture
    text = "0123456789abcdef" * (5 * 1024 * 1024 // 16)
    assert len(text.encode("utf-8")) == 5 * 1024 * 1024
    external_text(text)
    assert event.wait(5)
    assert [c.text for c in session.chunks] == [text]


@pytest.mark.parametrize("fmt", ["md", "docx", pytest.param("pdf", marks=pytest.mark.xfail(
    strict=True, raises=subprocess.TimeoutExpired, reason="F10: unbroken 5MB PDF exceeded 120s"))])
def test_5mb_writes_every_format(tmp_path, fmt):
    # Bound the large PDF/font workload in a child process, away from the desktop app.
    subprocess.run([sys.executable, "-c", """
import sys
from datetime import datetime
from pathlib import Path
from gather.session import Session
from gather.writers import write
s = Session(datetime(2026,10,13,14,30))
s.add('0123456789abcdef' * (5*1024*1024//16),'codex.exe','synthetic')
write(s, sys.argv[1], Path(sys.argv[2]))
""", fmt, str(tmp_path)], check=True, capture_output=True, timeout=30 if fmt == "pdf" else 120)
    paths = list(tmp_path.glob(f"*.{fmt}"))
    assert len(paths) == 1 and paths[0].stat().st_size > 0
    if fmt == "md":
        assert "0123456789abcdef" * (5 * 1024 * 1024 // 16) in paths[0].read_text(encoding="utf-8")
    elif fmt == "docx":
        assert any(len(p.text) == 5 * 1024 * 1024 for p in Document(str(paths[0])).paragraphs)
    else:
        assert paths[0].read_bytes().startswith(b"%PDF")


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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F7: metadata blocks clipboard snapshots")
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
def test_unicode_clipboard_capture(capture, name):
    _, session, event = capture
    external_text(TRICKY[name])
    assert event.wait(3)
    assert [c.text for c in session.chunks] == [TRICKY[name].replace("\r\n", "\n")]


@pytest.mark.parametrize("kind", ["text", "files"])
@pytest.mark.xfail(strict=True, raises=OSError, reason="F4: retry budget is only 200 ms")
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


MASK_TABLE = [
    ("openai", SECRET, "[masked]"),
    ("anthropic", "sk-ant-api03-" + "Ab1_-" * 10, "[masked]"),
    ("project", "sk-proj-" + "A_1-" * 20, "[masked]"),
    ("github", "ghp_" + "A1" * 18, "[masked]"),
    ("github_pat", "github_pat_" + "Z9_" * 20, "[masked]"),
    ("aws", "AKIA" + "ABCDEFGH12345678", "[masked]"),
    ("slack", "xoxb-1234567890-abcdef", "[masked]"),
    ("google", "AIza" + "a" * 35, "[masked]"),
    ("pem", "-----BEGIN OPENSSH PRIVATE KEY-----\nMIIabc\ndef==\n-----END OPENSSH PRIVATE KEY-----", "[masked]"),
    ("git_sha", "9fceb02d0ae598e95dc970b74767f19372d61af8", None),
    ("uuid", "123e4567-e89b-12d3-a456-426614174000", None),
    ("sha256", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", None),
    ("base64", "TWFuIGlzIGRpc3Rpbmd1aXNoZWQsIG5vdCBvbmx5IGJ5IGhpcyByZWFzb24=", None),
    ("task", "task-management-system-for-everyone-today", None),
    ("sk_learn", "pip install sk-learn", None),
]


@pytest.mark.parametrize("name,text,expected", MASK_TABLE, ids=[r[0] for r in MASK_TABLE])
def test_masking_table(name, text, expected):
    assert mask_secrets(text) == (text if expected is None else expected)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F8: long sk-learn slug is masked")
def test_public_sk_learn_slug_is_not_masked():
    slug = "sk-learn-feature-selection-guide"
    assert mask_secrets(slug) == slug


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.parametrize("name", TRICKY)
def test_tricky_input_writes_every_format(tmp_path, name, fmt):
    session = Session(T)
    session.add(TRICKY[name], "codex.exe", "synthetic", T)
    path = writers.write(session, fmt, tmp_path, ended=T)
    assert path.stat().st_size > 0
    clean = TRICKY[name].replace("\r\n", "\n").encode("utf-8", "replace").decode()
    if fmt == "md":
        assert clean in path.read_bytes().decode("utf-8")
    elif fmt == "docx":
        text = "\n".join(p.text for p in Document(str(path)).paragraphs)
        # Word represents a bare CR as a paragraph/run line break.
        assert writers._ILLEGAL.sub("", clean).replace("\r", "\n") in text
    else:
        assert path.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("fmt", writers.FORMATS)
def test_sequential_filename_collisions_preserve_files(tmp_path, fmt):
    paths = []
    for i in range(3):
        session = Session(T)
        session.add(f"copy-{i}", "codex.exe", "synthetic", T)
        paths.append(writers.write(session, fmt, tmp_path, ended=T))
    assert len(set(paths)) == 3
    assert [p.name for p in paths] == [f"gather-2026-10-13-1430{s}.{fmt}" for s in ("", "-2", "-3")]
    if fmt == "md":
        assert all(f"copy-{i}" in p.read_text(encoding="utf-8") for i, p in enumerate(paths))
    elif fmt == "docx":
        assert all(f"copy-{i}" in "\n".join(x.text for x in Document(str(p)).paragraphs)
                   for i, p in enumerate(paths))
    else:
        assert all(p.read_bytes().startswith(b"%PDF") for p in paths)


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F2: title bypasses masking")
def test_masking_also_covers_window_titles(tmp_path, fmt, monkeypatch):
    session = Session(T)
    session.add("ordinary text", "codex.exe", f"key={SECRET}", T)
    pdf_text = []
    if fmt == "pdf":
        from fpdf import FPDF
        original = FPDF.multi_cell

        def record(pdf, width, height, text, **kwargs):
            pdf_text.append(text)
            return original(pdf, width, height, text, **kwargs)

        monkeypatch.setattr(FPDF, "multi_cell", record)
    path = writers.write(session, fmt, tmp_path, mask=True, ended=T)
    if fmt == "md":
        text = path.read_text(encoding="utf-8")
    elif fmt == "docx":
        text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    else:
        text = "\n".join(pdf_text)
        assert path.read_bytes().startswith(b"%PDF")
    assert SECRET not in text


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.parametrize("title", ["surrogate", "control"])
def test_illegal_title_does_not_break_export(tmp_path, fmt, title, request):
    if title == "surrogate" and fmt in ("md", "docx"):
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=UnicodeEncodeError,
                                                reason="F3: title bypasses surrogate cleanup"))
    if title == "control" and fmt == "docx":
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=ValueError,
                                                reason="F3: title bypasses XML cleanup"))
    session = Session(T)
    session.add("ordinary text", "codex.exe", "title " + ("\ud800" if title == "surrogate" else "\x01"), T)
    path = writers.write(session, fmt, tmp_path, ended=T)
    assert path.stat().st_size > 0
    if fmt == "docx":
        Document(str(path))
    elif fmt == "pdf":
        assert path.read_bytes().startswith(b"%PDF")


def fake_app(tmp_path):
    app = App.__new__(App)
    app.session = Session(T)
    app.session.add("unsaved synthetic copy", "codex.exe", "synthetic", T)
    app.cfg = SimpleNamespace(format="md", mask=True)
    app.folder = tmp_path
    app.last_path = app.last_text = None
    app.notifications = []
    app.watcher = SimpleNamespace(set_capturing=lambda _: None, stop=lambda: None)
    app.tray = SimpleNamespace(set_recording=lambda *args: None, stop=lambda: None,
                               notify=lambda *args: app.notifications.append(args))
    app.pill = SimpleNamespace(hide=lambda: None, show=lambda _: None, update=lambda _: None)
    app.root = SimpleNamespace(destroy=lambda: None)
    return app


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F1: finish clears session before export")
def test_failed_export_retains_retryable_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    saved = app.session

    def fail(*args):
        raise PermissionError("synthetic disk failure")

    monkeypatch.setattr(writers, "write", fail)
    with pytest.raises(PermissionError):
        app.finish()
    assert app.session is saved
    assert not list(tmp_path.iterdir())


def test_quit_saves_nonempty_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    paths = []
    monkeypatch.setattr(win32, "copy_files_to_clipboard", lambda p: paths.extend(p))
    app.on_quit()
    assert app.session is None
    assert len(paths) == 1
    assert "unsaved synthetic copy" in app.last_path.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F6: tray mutates config outside Tk queue")
def test_settings_change_during_export_keeps_copy_consistent(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    app.session.chunks[0].text = SECRET
    original = writers.write

    def interleave(*args):
        path = original(*args)
        app.cfg.mask = False  # pystray currently mutates this outside the Tk queue
        return path

    monkeypatch.setattr(writers, "write", interleave)
    monkeypatch.setattr(win32, "copy_files_to_clipboard", lambda _: None)
    app.finish()
    assert app.last_text == app.last_path.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F9: encoding occurs after EmptyClipboard")
def test_encoding_failure_preserves_previous_clipboard():
    win32.copy_text_to_clipboard("codex-before-failure")
    with pytest.raises(UnicodeEncodeError):
        win32.copy_text_to_clipboard("\ud800")
    assert win32._get_clipboard_text() == "codex-before-failure"


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F5: owner checked before clipboard lock")
def test_owner_change_during_read_does_not_capture_self(capture, monkeypatch):
    watcher, _, _ = capture
    watcher.set_capturing(False)
    external_text("codex-external-before-race")
    captured = []
    probe = win32.ClipboardWatcher(lambda: None, lambda t, a, ti: captured.append(t))
    probe.set_capturing(True)

    def interleave():
        win32.copy_text_to_clipboard("codex-own-write-during-read")
        return "codex.exe", "synthetic"

    monkeypatch.setattr(win32, "_foreground_info", interleave)
    probe._handle_update()
    assert captured == []


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
