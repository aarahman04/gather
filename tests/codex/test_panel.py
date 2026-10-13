"""Ordered items, durable edits and a clickable overlay that leaves the source focused."""
import ctypes
import json
import subprocess
import sys
import tkinter as tk
from ctypes import wintypes as w

import pytest
from docx import Document

from gather import ui, win32, writers
from gather.session import Journal, Session
from common import T, SECRET
from exe_check import INPUT, MOUSEINPUT, hotkey
from live_safety import foreground
from test_app_adv import fake_app, settle
from test_mouse import target, middle, send


def sample(n=3):
    session = Session(T)
    for i in range(n):
        session.add(f"item-{i}", f"app-{i}.exe", f"title-{i}", T.replace(minute=30 + i))
    return session


def texts(session):
    return [c.text for c in session.chunks]


@pytest.mark.parametrize("n,index", [(3, 0), (3, 1), (3, 2), (1, 0)])
def test_remove_by_stable_id(n, index):
    session = sample(n)
    chunk = session.chunks[index]
    ids = [c.id for c in session.chunks]
    assert len(set(ids)) == n
    assert session.remove(chunk.id) is chunk
    assert [c.id for c in session.chunks] == ids[:index] + ids[index + 1:]
    assert session.remove(chunk.id) is None
    assert session.remove("invalid") is None


@pytest.mark.parametrize("origin,destination", [(i, j) for i in range(3) for j in range(3)])
def test_move_first_middle_last(origin, destination):
    session = sample()
    expected = session.chunks.copy()
    chunk = expected.pop(origin)
    expected.insert(destination, chunk)
    assert session.move(chunk.id, destination)
    assert session.chunks == expected
    assert session.latest().text == "item-2"
    assert session.undo().text == "item-2"


def test_only_item_and_invalid_moves_do_not_change_order():
    session = sample(1)
    snapshot = session.snapshot()
    assert session.move(session.chunks[0].id, 0)
    for item_id, position in (("invalid", 0), (session.chunks[0].id, -1),
                              (session.chunks[0].id, 1), (session.chunks[0].id, "0")):
        assert not session.move(item_id, position)
    assert session == snapshot


def test_dedupe_and_undo_follow_capture_order_after_move_and_removal():
    session = sample()
    latest = session.latest()
    session.move(latest.id, 0)
    assert not session.add(latest.text, "a", "b")
    session.remove(latest.id)
    assert not session.add("item-1", "a", "b")
    assert session.add(latest.text, "a", "b")
    assert session.latest().id != latest.id
    assert session.undo().text == latest.text
    assert session.undo().text == "item-1"
    assert session.undo().text == "item-0" and session.undo() is None


@pytest.mark.parametrize("fmt", writers.FORMATS)
def test_exports_follow_positions_with_original_source_and_time(tmp_path, monkeypatch, fmt):
    session = sample()
    session.move(session.chunks[2].id, 0)
    drawn = []
    if fmt == "pdf":
        from fpdf import FPDF
        for name in ("text", "multi_cell"):
            original = getattr(FPDF, name)
            def record(pdf, *args, _original=original, **kwargs):
                drawn.append(args[2])
                return _original(pdf, *args, **kwargs)
            monkeypatch.setattr(FPDF, name, record)
    path = writers.write(session, fmt, tmp_path, ended=T)
    if fmt == "md":
        body = path.read_text(encoding="utf-8")
    elif fmt == "docx":
        body = "\n".join(p.text for p in Document(path).paragraphs)
    else:
        assert path.read_bytes().startswith(b"%PDF")
        body = "\n".join(drawn)
    assert body.index("item-2") < body.index("item-0") < body.index("item-1")
    for position, index in enumerate((2, 0, 1), 1):
        assert f"{position} · app-{index} — title-{index} · 14:{30 + index}" in body


def test_journal_mixed_edits_preserve_ids_order_and_capture_order(tmp_path):
    session = sample()
    journal = Journal.create(tmp_path, session)
    a, b, c = session.chunks
    journal.move(c.id, 0); session.move(c.id, 0)
    journal.remove(b.id); session.remove(b.id)
    journal.undo(); session.undo()  # c, even though c is first on screen
    session.add("recopied", "other.exe", "source", T)
    journal.append(session.latest())
    journal.move(a.id, 1); session.move(a.id, 1)
    [(_, recovered)] = Journal.recover(tmp_path)
    assert recovered == session
    assert recovered.undo().text == "recopied"
    # Recreating a journal from a reordered/resumed session retains capture chronology.
    journal.delete()
    session.move(session.latest().id, 0)
    Journal.create(tmp_path, session)
    [(_, recovered)] = Journal.recover(tmp_path)
    assert recovered == session and recovered.latest().id == session.latest().id


def test_legacy_journal_and_unicode_line_separators(tmp_path):
    path = tmp_path / "old.jsonl"
    text = "literal \u0085 \u2028 \u2029 and lone \ud800"
    records = [{"started": T.isoformat()}, {"text": text, "app": "old.exe", "title": "title", "ts": T.isoformat()},
               {"text": "removed", "app": "old.exe", "title": "title", "ts": T.isoformat()}, {"undo": True}]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                    encoding="utf-8", errors="surrogatepass")
    [(_, recovered)] = Journal.recover(tmp_path)
    assert texts(recovered) == [text] and recovered.chunks[0].id
    assert recovered.add("removed", "old.exe", "title", T)


@pytest.mark.parametrize("operation,before_memory", [(op, boundary) for op in ("remove", "move") for boundary in (False, True)])
def test_crash_immediately_after_edit_or_journal_write(tmp_path, operation, before_memory):
    code = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
from gather.__main__ import App
from gather.session import Session, Journal
app = App.__new__(App)
app.session = Session()
for t in ('first', 'middle', 'last'): app.session.add(t, 'test.exe', 'source')
app.journal = Journal.create(Path(sys.argv[1]), app.session)
app.tray = SimpleNamespace(set_recording=lambda *a: None)
app.pill = SimpleNamespace(update=lambda *a: None, set_items=lambda *a: None)
operation = sys.argv[2]
item_id = app.session.chunks[1 if operation == 'remove' else 2].id
if sys.argv[3] == 'True':
    setattr(app.session, operation, lambda *a: os._exit(24))
getattr(app, 'on_' + operation)(item_id, *([0] if operation == 'move' else []))
os._exit(24)
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), operation, str(before_memory)], timeout=20)
    assert result.returncode == 24
    [(_, recovered)] = Journal.recover(tmp_path)
    assert texts(recovered) == (["first", "last"] if operation == "remove" else ["last", "first", "middle"])
    assert recovered.latest().text == "last"


@pytest.mark.parametrize("op", ["remove", "move"])
def test_edit_write_failure_keeps_memory_and_disk_then_retry(tmp_path, monkeypatch, op):
    app = fake_app(tmp_path, monkeypatch)
    app.on_clip("last", "test.exe", "source", T)
    before = app.session.snapshot()
    item_id = app.session.latest().id
    write = app.journal._write
    def partial(*args):
        with app.journal.path.open("a", encoding="utf-8") as file:
            file.write('{"' + op + '":')
        raise OSError("synthetic full disk")
    monkeypatch.setattr(app.journal, "_write", partial)
    args = (item_id,) + ((0,) if op == "move" else ())
    getattr(app, "on_" + op)(*args)
    assert app.session == before and "order unchanged" in app.notifications[-1]
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert recovered == before
    monkeypatch.setattr(app.journal, "_write", write)
    getattr(app, "on_" + op)(*args)
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert recovered == app.session and recovered != before


def test_stop_snapshot_rejects_stale_edits_and_failed_export_keeps_order(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch)
    for text in ("middle", "last"):
        app.on_clip(text, "test.exe", "source", T)
    item_id = app.session.latest().id
    app.q.put(("move", item_id, 0)); app.drain()
    before = app.session.snapshot()
    saved = app.session
    def fail(*args):
        assert args[0] is not saved and args[0] == before
        raise OSError("synthetic denied export")
    monkeypatch.setattr(writers, "write", fail)
    clipboard = win32._get_clipboard_text()
    app.finish()
    app.on_remove(item_id); app.on_move(item_id, 1); app.on_undo()
    settle(app)
    assert app.session == before and app.panel_items == before.chunks
    app.on_undo()
    assert texts(app.session) == ["unsaved synthetic copy", "middle"]
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert recovered == app.session
    assert win32._get_clipboard_text() == clipboard
    app.on_cancel()


@pytest.mark.parametrize("op", ["undo", "remove", "move"])
def test_missing_backup_is_rebuilt_before_edit_and_failure_keeps_items(tmp_path, monkeypatch, op):
    app = fake_app(tmp_path, monkeypatch)
    app.on_clip("last", "test.exe", "source", T)
    app.journal.delete(); app.journal = None
    before = app.session.snapshot()
    original = Journal.append
    def fail(journal, chunk):
        original(journal, chunk)
        raise OSError("synthetic failure rebuilding backup")
    monkeypatch.setattr(Journal, "append", fail)
    args = () if op == "undo" else (app.session.latest().id,) + ((0,) if op == "move" else ())
    getattr(app, "on_" + op)(*args)
    assert app.session == before and app.journal is None
    assert Journal.recover(app.journal_dir) == []  # no partially published backup
    monkeypatch.setattr(Journal, "append", original)
    getattr(app, "on_" + op)(*args)
    [(_, recovered)] = Journal.recover(app.journal_dir)
    assert recovered == app.session and recovered != before


@pytest.fixture(scope="module")
def tk_root():
    # One Tk interpreter, as in Gather; each test destroys its own overlays.
    root = tk.Tk(); root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def panel_app(tmp_path, monkeypatch, target, tk_root):
    root = tk_root
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.root = root
    app.pill = ui.Pill(root, lambda item_id: app.q.put(("remove", item_id)),
                       lambda item_id, pos: app.q.put(("move", item_id, pos)))
    app.on_toggle()
    root.update()
    try:
        yield app
    finally:
        app.closed = True
        app.exports.shutdown(wait=True)
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        for window in (app.pill.win, app.pill.chevron_win, app.pill.panel):
            window.destroy()


def click_point(x, y, pump):
    hwnd = foreground()
    assert win32.user32.SetCursorPos(x, y)
    assert win32.user32.GetForegroundWindow() == hwnd
    inputs = (INPUT * 2)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)), INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
    assert win32.user32.SendInput(2, inputs, ctypes.sizeof(INPUT)) == 2
    pump()
    assert win32.user32.GetForegroundWindow() == hwnd


def drag_points(start, finish, pump):
    hwnd = foreground()
    assert win32.user32.SetCursorPos(*start)
    down = INPUT(type=0, mi=MOUSEINPUT(dwFlags=2))
    up = INPUT(type=0, mi=MOUSEINPUT(dwFlags=4))
    assert win32.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT)) == 1
    try:
        pump()
        assert foreground() == hwnd
        assert win32.user32.SetCursorPos(*finish)
        pump()
    finally:
        assert win32.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT)) == 1
    pump()
    assert foreground() == hwnd


def widget_click(widget, pump):
    click_point(widget.winfo_rootx() + widget.winfo_width() // 2,
                widget.winfo_rooty() + widget.winfo_height() // 2, pump)


def test_native_drag_and_keyboard_focus_stay_in_source(panel_app, target):
    app, pill = panel_app, panel_app.pill
    _, pump = target
    def update():
        app.root.update(); app.drain(); app.root.update(); pump()
    for text in ("first", "middle", "last"):
        app.on_clip(text, "test.exe", "source", T)
    win32._sig(win32.user32, "GetFocus", w.HWND)
    source_focus = win32.user32.GetFocus()
    assert source_focus
    widget_click(pill.chevron, update)
    assert win32.user32.GetFocus() == source_focus
    handle = pill.rows[app.session.latest().id]["handle"]
    drag_points((handle.winfo_rootx() + 4, handle.winfo_rooty() + 4),
                (handle.winfo_rootx() + 4, pill.canvas.winfo_rooty() + 2), update)
    assert texts(app.session) == ["last", "first", "middle"]
    assert win32.user32.GetFocus() == source_focus
    widget_click(pill.rows[app.session.chunks[1].id]["cross"], update)
    assert texts(app.session) == ["last", "middle"]
    assert win32.user32.GetFocus() == source_focus


def test_pill_body_passes_actual_left_click_to_source(panel_app, target):
    pill, root = panel_app.pill, panel_app.root
    _, pump = target
    source = foreground()
    hits = []
    @ui._SUBCLASSPROC
    def clicked(hwnd, msg, wp, lp, subclass_id, data):
        if msg == 0x201:
            hits.append(True)
        return ui._comctl32.DefSubclassProc(hwnd, msg, wp, lp)
    assert ui._comctl32.SetWindowSubclass(source, clicked, 42, 0)
    try:
        # Put only our blank target underneath the overlay before clicking its body.
        assert win32.user32.SetWindowPos(source, None, pill.win.winfo_x() - 20, -60,
                                        700, 250, 0x14)
        for hwnd in (pill.hwnd, pill.chevron_hwnd):
            assert win32.user32.SetWindowPos(hwnd, w.HWND(-1), 0, 0, 0, 0, 0x13)
        root.update(); pump()
        widget_click(pill.lbl, lambda: (root.update(), pump()))
        assert hits == [True] and foreground() == source
    finally:
        assert ui._comctl32.RemoveWindowSubclass(source, clicked, 42)


def test_destroy_removes_all_overlay_windows_and_native_subclasses(target):
    # A fresh process tests actual quit, with the single Tk interpreter used by Gather.
    subprocess.run([sys.executable, "-c", '''
import tkinter as tk
from gather import ui, win32
from ctypes import wintypes as w
root = tk.Tk(); root.withdraw()
pill = ui.Pill(root)
pill.show(0); pill.toggle_panel(); root.update()
handles = [pill.hwnd, pill.win.winfo_id(), pill.chevron_hwnd,
           pill.chevron_win.winfo_id(), pill.panel_hwnd, pill.panel.winfo_id()]
root.destroy()
win32._sig(win32.user32, 'IsWindow', w.BOOL, w.HWND)
assert not any(win32.user32.IsWindow(hwnd) for hwnd in handles)
'''], check=True, timeout=20)


def test_chevron_cross_rows_live_updates_and_no_activation(panel_app, target):
    app, pill = panel_app, panel_app.pill
    _, pump = target
    source = foreground()
    def update():
        app.root.update(); app.drain(); app.root.update(); pump()
    widget_click(pill.chevron, update)
    assert pill.expanded and pill.chevron.cget("text") == "⌃" and pill.empty.winfo_ismapped()
    app.cfg.mask = False
    app.on_clip(SECRET + "\n\ud800\x00", "test.exe", SECRET + "\ud800", T)
    update()
    row = pill.rows[app.session.chunks[0].id]
    assert row["preview"].cget("text") == "[masked]   "
    assert SECRET not in row["source"].cget("text") and "\ud800" not in row["source"].cget("text")
    assert row["number"].cget("text") == "1" and row["time"].cget("text") == "14:30:00"
    widget_click(row["preview"], update)
    assert len(app.session) == 1 and foreground() == source
    app.on_clip("second", "chrome.exe", "page", T); update()
    assert len(pill.rows) == 2
    before = win32._get_clipboard_text()
    widget_click(row["cross"], update)
    assert texts(app.session) == ["second"] and len(pill.rows) == 1
    assert next(iter(pill.rows.values()))["number"].cget("text") == "1"
    assert win32._get_clipboard_text() == before
    widget_click(pill.chevron, update)
    assert not pill.expanded and not pill.panel.winfo_ismapped()
    assert foreground() == source
    for hwnd in (pill.hwnd, pill.chevron_hwnd, pill.panel_hwnd):
        ex = ui._user32.GetWindowLongPtrW(hwnd, ui._GWL_EXSTYLE)
        assert ex & ui._WS_EX_NOACTIVATE and ex & ui._WS_EX_TOOLWINDOW
        assert bool(ex & ui._WS_EX_TRANSPARENT) == (hwnd == pill.hwnd)
        assert win32.user32.SendMessageW(hwnd, 0x21, hwnd, 0x02010001) == 3


def test_simulated_widget_drag_marker_reorders_and_alt_z_uses_capture_order(panel_app):
    app, pill = panel_app, panel_app.pill
    for text in ("first", "middle", "last"):
        app.on_clip(text, "test.exe", "source", T)
    pill.chevron.invoke(); app.root.update()
    handle = pill.rows[app.session.latest().id]["handle"]
    handle.event_generate("<ButtonPress-1>", x=2, y=2)
    handle.event_generate("<B1-Motion>", x=2, y=2, rooty=pill.canvas.winfo_rooty() + 2)
    app.root.update()
    assert pill.marker.winfo_ismapped()
    handle.event_generate("<ButtonRelease-1>", x=2, y=2, rooty=pill.canvas.winfo_rooty() + 2)
    assert texts(app.session) == ["first", "middle", "last"]  # queued, never edited in a widget callback
    app.drain(); app.root.update()
    assert texts(app.session) == ["last", "first", "middle"]
    assert [pill.rows[c.id]["number"].cget("text") for c in app.session.chunks] == ["1", "2", "3"]
    app.on_undo(); app.root.update()
    assert texts(app.session) == ["first", "middle"] and len(pill.rows) == 2


def test_scroll_many_items_and_collapse_on_saving_stop_cancel(panel_app):
    app, pill = panel_app, panel_app.pill
    for i in range(10):
        app.on_clip(f"item-{i}", "test.exe", "source", T)
    pill.chevron.invoke(); app.root.update()
    assert len(pill.rows) == 10 and pill.canvas.winfo_height() <= 8 * pill.row_height
    assert pill.scrollbar.winfo_ismapped()
    start = pill.canvas.yview()
    pill.panel.event_generate("<MouseWheel>", delta=-120); app.root.update()
    assert pill.canvas.yview()[0] > start[0]
    # A row's wheel events also reach the panel, with no keyboard focus required.
    next(iter(pill.rows.values()))["preview"].event_generate("<MouseWheel>", delta=120)
    app.root.update()
    assert pill.canvas.yview()[0] == start[0]
    pill.saving(); app.root.update()
    assert not pill.expanded and not pill.chevron_win.winfo_ismapped()
    pill.show(10); pill.chevron.invoke(); app.root.update()
    exported, _ = app.end_recording(); app.root.update()
    assert not pill.expanded and not pill.recording and len(exported) == 10
    pill.chevron.invoke()
    assert not pill.expanded
    app.on_toggle(); pill.chevron.invoke(); app.on_cancel(); app.root.update()
    assert not pill.expanded and not pill.panel.winfo_ismapped()


def test_keyboard_and_middle_trigger_with_panel_open(panel_app, target):
    app, pill = panel_app, panel_app.pill
    _, pump = target
    toggles = []
    watcher = win32.ClipboardWatcher(lambda: toggles.append(True), lambda *args: None,
                                     on_undo=lambda: app.q.put(("undo",)))
    watcher.start()
    try:
        assert watcher.set_capturing(True)
        for text in ("first", "last"):
            app.on_clip(text, "test.exe", "source", T)
        pill.chevron.invoke(); app.root.update()
        hotkey((0x12, ord("Z"))); pump(); app.drain(); app.root.update()
        assert texts(app.session) == ["first"] and len(pill.rows) == 1
        hotkey(); pump()
        send(middle(), middle(True)); pump()
        assert toggles == [True, True] and pill.expanded
    finally:
        watcher.stop()


@pytest.mark.parametrize("factor,area", [(1.25, (0, 0, 1280, 720)), (1.5, (0, 0, 640, 480))])
def test_layout_clamps_scaled_panel_to_small_work_area(panel_app, monkeypatch, factor, area):
    pill = panel_app.pill
    pill.scale = factor
    pill.row_height = round(52 * factor)
    def workarea(action, param, pointer, flags):
        rect = ctypes.cast(pointer, ctypes.POINTER(w.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = area
        return True
    monkeypatch.setattr(ui._user32, "SystemParametersInfoW", workarea)
    pill.set_items(sample(10).chunks); pill.chevron.invoke(); panel_app.root.update()
    x, y = pill.panel.winfo_x(), pill.panel.winfo_y()
    assert x >= area[0] and y >= area[1]
    assert x + pill.panel.winfo_width() <= area[2] and y + pill.panel.winfo_height() <= area[3]
