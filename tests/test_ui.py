import tkinter as tk

import pytest

from gather import ui


def test_config_roundtrip_and_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert ui.Config.load() == ui.Config()
    ui.Config("pdf", False).save()
    assert ui.Config.load() == ui.Config("pdf", False)
    ui.Config.path().write_text("{nope")
    assert ui.Config.load() == ui.Config()
    ui.Config.path().write_text('{"format": "exe", "mask": "x"}')
    assert ui.Config.load() == ui.Config()


def test_autostart():
    name = "GatherTest"
    assert not ui.get_autostart(name)
    try:
        ui.set_autostart(True, "x.exe", name)
        assert ui.get_autostart(name)
    finally:
        ui.set_autostart(False, "", name)
    assert not ui.get_autostart(name)
    ui.set_autostart(False, "", name)  # no-op


def test_play_never_raises():
    for k in ("start", "stop", "error", "bogus"):
        ui.play(k)


def test_icons_differ():
    assert ui._icon_image(False).tobytes() != ui._icon_image(True).tobytes()


def test_tray_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = []
    keys = "toggle undo cancel copy_text copy_path open_folder quit autostart".split()
    t = ui.Tray(ui.Config(), {k: (lambda k=k: calls.append(k)) for k in keys})
    items = list(t.menu.items)
    assert items[0].text == "Start recording (Ctrl+Alt+G)"
    assert items[0].default
    undo, cancel = items[1], items[2]
    assert not undo.enabled and not cancel.enabled
    t.recording, t.count = True, 2
    assert items[0].text == "Stop recording (Ctrl+Alt+G)"
    assert undo.enabled and cancel.enabled
    items[0](t.icon)
    assert calls == ["toggle"]
    fmt = [i for i in items if i.text == "Format"][0]
    pdf = list(fmt.submenu.items)[2]
    pdf(t.icon)
    assert t.cfg.format == "pdf" and ui.Config.load().format == "pdf"
    assert pdf.checked


def test_pill():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        p = ui.Pill(root)
        p.show(0)
        p.update(3)
        root.update()
        ex = ui._user32.GetWindowLongPtrW(p.hwnd, ui._GWL_EXSTYLE)
        assert ex & ui._WS_EX_NOACTIVATE and ex & ui._WS_EX_TRANSPARENT and ex & ui._WS_EX_TOOLWINDOW
        p.hide()
    finally:
        root.destroy()
