"""Recording-only undo, crash durability and a count-only pill."""
import ctypes
import subprocess
import sys
import threading

import pytest

from gather import ui, win32
from gather.session import Journal
from common import T, SECRET
from exe_check import hotkey
from test_app_adv import fake_app, settle
from test_mouse import target  # owned native focus target for injected keys


@pytest.mark.parametrize("text,expected", [
    ("first\nsecond\r\nthird\tend", "first second third end"),
    ("x" * 40, "x" * 40), ("x" * 41, "x" * 40 + "…"),
    (SECRET, "[masked]"), ("prefix " + SECRET + " end", "prefix [masked] end"),
    ("sk-" + "A1" * 100, "[masked]"),
    ("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----", "[masked]"),
    ("lone \ud800 and \x00", "lone   and  "),
])
def test_preview_text(text, expected):
    assert ui.preview_text(text) == expected


def test_repeated_undo_dedupe_empty_and_clipboard(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.on_clip("two\nlines", "a.exe", "test", T)
    app.on_clip("three", "a.exe", "test", T)
    app.on_clip("three", "a.exe", "test", T)  # duplicate ignored
    assert len(app.session) == 3
    before = win32._get_clipboard_text()
    for count in (2, 1, 0):
        app.on_undo()
        assert len(app.session) == count
        assert app.pill_state == f"recording {count}"
    app.on_undo()
    assert app.notifications[-1] == "Nothing to undo." and len(app.session) == 0
    app.on_clip("unsaved synthetic copy", "a.exe", "test", T)
    assert len(app.session) == 1  # recopy after undo must be captured
    assert win32._get_clipboard_text() == before
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert [c.text for c in recovered.chunks] == ["unsaved synthetic copy"]


def test_capture_and_removal_never_send_copied_text_to_pill(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.cfg.mask = False  # export preference must not expose a key on screen
    app.on_toggle()
    app.on_clip(SECRET, "a.exe", "test", T)
    assert app.pill_state == "recording 1" and app.session.chunks[0].text == SECRET
    app.on_undo()
    assert app.pill_state == "recording 0"
    app.on_cancel()
    app.on_undo()  # idle no-op
    assert app.pill_state == "hidden" and app.session is None


def test_recopy_duplicate_filtered_item_after_undo(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    for _ in range(2):
        app.on_clip("wrong copy", "a.exe", "test", T)
    assert len(app.session) == 2
    app.on_undo()
    app.on_clip("wrong copy", "a.exe", "test", T)
    assert [c.text for c in app.session.chunks] == ["unsaved synthetic copy", "wrong copy"]


def test_undo_persists_before_memory_removal(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    pop = app.session.undo

    def crash_boundary():
        [(_, recovered)] = Journal.recover(app.journal_dir)
        assert len(recovered) == 0  # disk already reflects this undo before pop
        return pop()

    monkeypatch.setattr(app.session, "undo", crash_boundary)
    app.on_undo()
    assert len(app.session) == 0


def test_process_crash_right_after_undo_replays_without_removed_item(tmp_path):
    code = """
import os, sys
from types import SimpleNamespace
from pathlib import Path
from gather.__main__ import App
from gather.session import Session, Journal
app = App.__new__(App)
app.session = Session()
app.session.add('kept', 'test.exe', 'test')
app.session.add('removed', 'test.exe', 'test')
app.journal = Journal.create(Path(sys.argv[1]), app.session)
app.tray = SimpleNamespace(set_recording=lambda *a: None)
app.pill = SimpleNamespace(update=lambda *a: None, set_items=lambda *a: None)
app.on_undo()
os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], timeout=20)
    assert result.returncode == 23
    [(_, recovered)] = Journal.recover(tmp_path)
    assert [c.text for c in recovered.chunks] == ["kept"]
    assert recovered.add("removed", "test.exe", "test")


def test_journal_undo_failure_keeps_the_item_and_retry_is_replayable(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.on_clip("wrong", "a.exe", "test", T)
    original = app.journal.undo

    def partially_failed_write(*args):
        with app.journal.path.open("a", encoding="utf-8") as file:
            file.write('{"undo":')
        raise OSError("synthetic partial disk write")

    monkeypatch.setattr(app.journal, "undo", partially_failed_write)
    app.on_undo()
    assert len(app.session) == 2 and "Item kept" in app.notifications[-1]
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert len(recovered) == 2
    monkeypatch.setattr(app.journal, "undo", original)
    app.on_undo()
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert [c.text for c in recovered.chunks] == ["unsaved synthetic copy"]


def test_undo_works_on_resumed_failed_export(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.on_clip("wrong copy", "a.exe", "test", T)

    def denied(*args):
        raise PermissionError("synthetic export failure")

    monkeypatch.setattr("gather.writers.write", denied)
    app.finish()
    settle(app)
    assert app.session is not None and app.capturing and app.tray.undo_ok
    app.on_undo()
    assert [c.text for c in app.session.chunks] == ["unsaved synthetic copy"]
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert [c.text for c in recovered.chunks] == ["unsaved synthetic copy"]


def test_undo_hotkey_lifetime_and_repeated_input(target):
    _, pump = target
    hits = []
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None, mouse_trigger="Off",
                                     on_undo=lambda: hits.append(True))
    watcher.start()

    def probe():
        free = win32.user32.RegisterHotKey(None, 9431, win32.MOD_ALT, ord("Z"))
        if free:
            win32.user32.UnregisterHotKey(None, 9431)
        return bool(free)

    try:
        assert probe(), "Alt+Z already used by another app"
        assert not watcher.undo_hotkey_ok
        assert watcher.set_capturing(True)
        assert watcher.undo_hotkey_ok and not probe()
        before = win32._get_clipboard_text()
        for _ in range(3):
            hotkey((0x12, ord("Z")))
            pump()
        assert hits == [True] * 3
        assert win32._get_clipboard_text() == before
        assert watcher.set_capturing(False)
        assert not watcher.undo_hotkey_ok and probe()
        # An old queued WM_HOTKEY cannot undo while idle.
        win32.user32.PostMessageW(watcher._hwnd, win32.WM_HOTKEY,
                                 win32.UNDO_HOTKEY_ID, ord("Z") << 16 | win32.MOD_ALT)
        pump()
        assert hits == [True] * 3
        assert watcher.set_capturing(True)
    finally:
        watcher.stop()
    assert probe() and not watcher.undo_hotkey_ok


def test_undo_registration_failure_keeps_recording_and_tray(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.watcher.set_capturing = lambda on: setattr(app, "capturing", on) or False
    app.start_capturing()
    assert app.capturing and not app.tray.undo_ok
    assert "Alt+Z" in app.notifications[-1] and "Remove last item" in app.notifications[-1]
    app.on_undo()
    assert len(app.session) == 0 and app.pill_state == "recording 0"


def test_actual_alt_z_conflict_keeps_clipboard_listener():
    ready, release = threading.Event(), threading.Event()

    def holder():
        ready.ok = win32.user32.RegisterHotKey(None, 9432, win32.MOD_ALT, ord("Z"))
        ready.set()
        release.wait(10)
        if ready.ok:
            win32.user32.UnregisterHotKey(None, 9432)

    thread = threading.Thread(target=holder)
    thread.start()
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None, mouse_trigger="Off", on_undo=lambda: None)
    try:
        assert ready.wait(3) and ready.ok
        watcher.start()
        assert not watcher.set_capturing(True)
        assert watcher._capturing and watcher._hwnd and not watcher.undo_hotkey_ok
        assert watcher.set_capturing(False)
    finally:
        watcher.stop()
        release.set()
        thread.join(3)


def test_count_only_pill_focus_and_click_through(target):
    # A fresh interpreter avoids the local Anaconda repeated-Tk-init failure.
    subprocess.run([sys.executable, "-c", '''
import tkinter as tk
from gather import ui, win32
root = tk.Tk(); root.withdraw()
foreground = win32.user32.GetForegroundWindow()
pill = ui.Pill(root)
pill.show(0); pill.update(1)
assert pill.lbl.cget('text') == 'Gather  1'
pill.update(0)
assert pill.lbl.cget('text') == 'Gather  0'
pill.saving()
assert pill.lbl.cget('text') == 'Saving…'
pill.show(0); pill.update(1); pill.hide(); root.update()
assert win32.user32.GetForegroundWindow() == foreground
ex = ui._user32.GetWindowLongPtrW(pill.hwnd, ui._GWL_EXSTYLE)
assert ex & ui._WS_EX_NOACTIVATE and ex & ui._WS_EX_TRANSPARENT
root.destroy()
'''], check=True, timeout=20)
