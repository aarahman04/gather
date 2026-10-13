import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

from gather import session as session_mod, ui, win32, writers
from gather.__main__ import App
from gather.session import Chunk, Journal, Session

from common import T, SECRET


def fake_app(tmp_path, monkeypatch, recording=True):
    """App without Tk/tray/watcher; output and journal folders under tmp_path."""
    app = App.__new__(App)
    app.q = queue.Queue()
    app.cfg = SimpleNamespace(format="md", mask=True, hotkey="Alt+C", mouse_trigger="Middle-click",
                              save=lambda: setattr(app, "saved", app.cfg.hotkey))
    app.saved = None
    app.folder = tmp_path / "out"
    app.journal_dir = tmp_path / "unsaved"
    app.journal_warned = False
    app.exports = ThreadPoolExecutor(1)
    app.pending, app.quitting, app.closed = 0, False, False
    app.session = app.journal = app.last_path = app.last_text = None
    app.notifications, app.clipboard = [], []
    app.capturing = app.destroyed = app.watcher_stopped = False
    app.pill_state = "hidden"
    app.watcher = SimpleNamespace(set_capturing=lambda on: setattr(app, "capturing", on) or True,
                                  stop=lambda: setattr(app, "watcher_stopped", True),
                                  hotkey="Alt+C", mouse_trigger="Middle-click")
    app.tray = SimpleNamespace(set_recording=lambda *args: None, stop=lambda: None, hotkey_ok=True,
                               notify=lambda *args: app.notifications.append(args[1]))
    app.pill = SimpleNamespace(hide=lambda: setattr(app, "pill_state", "hidden"),
                               set_items=lambda items: setattr(app, "panel_items", list(items)),
                               show=lambda n: setattr(app, "pill_state", f"recording {n}"),
                               update=lambda n, message="": (setattr(app, "pill_state", f"recording {n}"),
                                                             setattr(app, "preview", message)),
                               saving=lambda: setattr(app, "pill_state", "saving"))
    app.root = SimpleNamespace(destroy=lambda: setattr(app, "destroyed", True),
                               after=lambda *args: None)
    monkeypatch.setattr(win32, "copy_files_to_clipboard", lambda p: app.clipboard.extend(p))
    if recording:
        app.session = Session(T)
        app.journal = Journal.create(app.journal_dir, app.session)
        app.on_clip("unsaved synthetic copy", "codex.exe", "synthetic", T)
    return app


def settle(app, timeout=30):
    """Run the real drain() until every pending export has been handled."""
    deadline = time.monotonic() + timeout
    while app.pending:
        assert time.monotonic() < deadline, "export never reported back"
        app.drain()
        time.sleep(0.01)


def test_failed_export_retains_retryable_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    saved, journal = app.session, app.journal
    original, broken = writers.write, [True]

    def maybe_fail(*args):
        if broken[0]:
            raise PermissionError("synthetic disk failure")
        return original(*args)

    monkeypatch.setattr(writers, "write", maybe_fail)
    app.finish()
    settle(app)
    assert app.session == saved and app.session is not saved  # stop exports a snapshot
    assert app.journal is journal and journal.path.exists()
    assert app.capturing and app.pill_state == "recording 1"
    assert not list(app.folder.glob("*"))
    assert "still recording" in app.notifications[-1]

    broken[0] = False  # retry with the hotkey once the disk is fine
    app.on_toggle()
    settle(app)
    assert app.session is None
    assert "unsaved synthetic copy" in app.last_path.read_text(encoding="utf-8")
    assert not journal.path.exists()


def test_failed_export_while_new_session_records_keeps_journal(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    journal = app.journal
    gate = threading.Event()

    def fail(*args):
        gate.wait(10)
        raise PermissionError("synthetic disk failure")

    monkeypatch.setattr(writers, "write", fail)
    app.finish()
    app.on_toggle()  # new session starts while the first is still saving
    new = app.session
    gate.set()
    settle(app)
    assert app.session is new and journal.path.exists()
    assert "next time Gather starts" in app.notifications[-1]


def test_quit_saves_nonempty_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.on_quit()
    settle(app)
    assert app.session is None and app.destroyed
    assert len(app.clipboard) == 1
    assert "unsaved synthetic copy" in app.last_path.read_text(encoding="utf-8")


def test_settings_change_during_export_keeps_copy_consistent(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    app.session.chunks[0].text = SECRET
    original = writers.write

    def interleave(*args):
        path = original(*args)
        app.cfg.mask = False  # pystray mutates cfg outside the Tk queue
        return path

    monkeypatch.setattr(writers, "write", interleave)
    app.finish()
    settle(app)
    assert app.last_text == app.last_path.read_text(encoding="utf-8")
    assert SECRET not in app.last_text


def test_recovery_exports_leftover_journal_without_clipboard(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    j = Journal.create(app.journal_dir, Session(T))
    j.append(Chunk("first", "codex.exe", "synthetic", T))
    j.append(Chunk("dropped", "codex.exe", "synthetic", T))
    j.undo()
    j.append(Chunk("second", "codex.exe", "synthetic", T))
    empty = Journal.create(app.journal_dir, Session(datetime(2026, 1, 1)))
    app.recover()
    settle(app)
    assert not j.path.exists() and not empty.path.exists()
    assert app.clipboard == []
    body = app.last_path.read_text(encoding="utf-8")
    assert "first" in body and "second" in body and "dropped" not in body
    assert app.last_text == body
    assert app.notifications == [f"Recovered 2 items from a session that didn't finish: {app.last_path.name}"]


def test_journal_deleted_after_export_and_on_cancel(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    journal = app.journal
    app.finish()
    settle(app)
    assert not journal.path.exists() and app.last_path.exists()

    app.on_toggle()
    journal = app.journal
    app.on_clip("to be cancelled", "codex.exe", "synthetic", T)
    assert journal.path.exists()
    app.on_cancel()
    assert app.session is None and not journal.path.exists()
    assert len(list(app.folder.glob("*"))) == 1


def test_quit_waits_for_pending_export(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    journal, gate, original = app.journal, threading.Event(), writers.write

    def held(*args):
        gate.wait(10)
        return original(*args)

    monkeypatch.setattr(writers, "write", held)
    app.finish()
    app.on_quit()
    app.drain()
    assert app.quitting and not app.destroyed and app.pill_state == "saving"
    app.on_toggle()  # no new session while quitting
    assert app.session is None
    gate.set()
    settle(app)
    assert app.destroyed and app.watcher_stopped
    assert app.last_path.exists() and not journal.path.exists()


def test_slow_export_keeps_queue_responsive(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    original = writers.write

    def slow(*args):
        time.sleep(2)
        return original(*args)

    monkeypatch.setattr(writers, "write", slow)
    t0 = time.monotonic()
    app.finish()
    assert app.pill_state == "saving"
    app.q.put(("toggle",))
    app.drain()
    assert time.monotonic() - t0 < 1
    assert app.session is not None and app.pending == 1  # toggle handled mid-export
    assert app.pill_state == "recording 0"  # recording display wins over saving
    app.q.put(("clip", "second session", "codex.exe", "synthetic", T))
    settle(app)
    assert app.pill_state == "recording 1" and app.last_path.exists()
    app.on_toggle()
    settle(app)
    assert app.pill_state == "hidden"
    assert len(list(app.folder.glob("*.md"))) == 2


def test_clipboard_busy_names_saved_file(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)

    def busy(paths):
        raise OSError("clipboard locked")

    monkeypatch.setattr(win32, "copy_files_to_clipboard", busy)
    app.finish()
    settle(app)
    assert app.notifications[-1].startswith(f"Saved {app.last_path.name}, but the clipboard was busy")


def test_journal_write_failure_keeps_recording(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)

    def full(self, chunk):
        raise OSError("disk full")

    monkeypatch.setattr(session_mod.Journal, "append", full)
    app.on_clip("a", "codex.exe", "synthetic", T)
    app.on_clip("b", "codex.exe", "synthetic", T)
    assert len(app.session) == 3 and app.journal is None
    assert sum("backup copy" in n for n in app.notifications) == 1
    app.finish()
    settle(app)
    assert "\nb\n" in app.last_path.read_text(encoding="utf-8")


def test_pill_saving():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")  # Tk init can transiently fail on a busy desktop
    root.withdraw()
    try:
        p = ui.Pill(root)
        p.saving()
        root.update()
        assert p.lbl.cget("text") == "Saving…" and p.win.winfo_viewable()
        p.show(2)
        assert p.lbl.cget("text") == "Gather  2" and p.dot.cget("fg") == "#ff3b30"
        p.hide()
    finally:
        root.destroy()


def test_hotkey_change_success_saves_cfg(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.set_hotkey = lambda name: setattr(app.watcher, "hotkey", name) or True
    app.on_hotkey_change("Alt+C")  # already active: no-op
    assert app.saved is None and not app.notifications
    app.on_hotkey_change("Pause")
    assert app.cfg.hotkey == app.saved == "Pause" and app.tray.hotkey_ok
    assert app.notifications[-1] == "Hotkey is now Pause."


def test_hotkey_change_failure_keeps_cfg(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.set_hotkey = lambda name: False
    app.on_hotkey_change("Scroll Lock")
    assert app.cfg.hotkey == "Alt+C" and app.saved is None and app.tray.hotkey_ok
    assert "Couldn't register Scroll Lock" in app.notifications[-1]
    assert "still using Alt+C" in app.notifications[-1]


def test_startup_hotkey_conflict_keeps_tray_toggle(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.hotkey = None
    app.watcher.start = lambda: False
    app.tray.run_detached = app.root.mainloop = lambda: None
    app.run()
    assert not app.tray.hotkey_ok
    assert "Alt+C is used by another app" in app.notifications[-1]
    assert "click the tray icon" in app.notifications[-1]
    app.on_toggle()
    assert app.session is not None and app.capturing


def test_retry_hotkey_after_startup_conflict(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.hotkey = None
    app.tray.hotkey_ok = False
    app.watcher.set_hotkey = lambda name: setattr(app.watcher, "hotkey", name) or True
    app.on_hotkey_change("Alt+C")
    assert app.watcher.hotkey == app.cfg.hotkey == app.saved == "Alt+C"
    assert app.tray.hotkey_ok


def test_hotkey_save_failure_still_updates_active_tray(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.hotkey = None
    app.tray.hotkey_ok = False
    app.watcher.set_hotkey = lambda name: setattr(app.watcher, "hotkey", name) or True

    def denied():
        raise PermissionError("config locked")

    app.cfg.save = denied
    app.q.put(("hotkey_change", "Pause"))
    app.drain()
    assert app.watcher.hotkey == app.cfg.hotkey == "Pause" and app.tray.hotkey_ok
    assert "config locked" in app.notifications[-1]
