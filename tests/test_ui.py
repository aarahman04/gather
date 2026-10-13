import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gather import ui


def test_config_roundtrip_and_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert ui.Config.load() == ui.Config()
    ui.Config("pdf", False).save()
    assert ui.Config.load() == ui.Config("pdf", False)
    ui.Config.path().write_text("{nope")
    assert ui.Config.load() == ui.Config()
    ui.Config.path().write_text('{"format": "exe", "mask": "x", "hotkey": "F13"}')
    assert ui.Config.load() == ui.Config()
    ui.Config(hotkey="Pause").save()
    assert ui.Config.load().hotkey == "Pause"


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
    actions = {k: (lambda k=k: calls.append(k)) for k in keys}
    actions["hotkey"] = lambda name: calls.append(("hotkey", name))
    actions["mouse_trigger"] = lambda name: calls.append(("mouse_trigger", name))
    t = ui.Tray(ui.Config(), actions)
    items = list(t.menu.items)
    assert items[0].text == "Start recording (Alt+C)"
    assert t._title() == "Gather — idle (Alt+C / Middle-click to start)"
    assert items[0].default
    undo, cancel = items[1], items[2]
    assert undo.text == "Remove last item (Alt+Z)"
    assert not undo.enabled and not cancel.enabled
    t.recording, t.count = True, 2
    assert items[0].text == "Stop recording (Alt+C)"
    assert undo.enabled and cancel.enabled
    assert "Alt+Z undo" in t._title()
    t.count = 0
    assert undo.enabled  # allow the visible "Nothing to undo" response
    t.undo_ok = False
    assert undo.text == "Remove last item (Alt+Z unavailable)" and "tray undo" in t._title()
    t.undo_ok, t.count = True, 2
    items[0](t.icon)
    assert calls == ["toggle"]
    fmt = [i for i in items if i.text == "Format"][0]
    pdf = list(fmt.submenu.items)[2]
    pdf(t.icon)
    assert t.cfg.format == "pdf" and ui.Config.load().format == "pdf"
    assert pdf.checked

    names = [i.text for i in items]
    assert names.index("Hotkey") == names.index("Format") + 1
    hk = list(items[names.index("Hotkey")].submenu.items)
    assert [i.text for i in hk] == ["Alt+C", "Win+Y", "Pause", "Scroll Lock", "Ctrl+Alt+G"]
    assert [i.checked for i in hk] == [True, False, False, False, False]
    calls.clear()
    hk[2](t.icon)
    assert calls == [("hotkey", "Pause")]
    assert t.cfg.hotkey == "Alt+C"  # the app changes cfg only once it registers
    t.cfg.hotkey = "Pause"
    t.recording = False
    assert [i.checked for i in hk] == [False, False, True, False, False]
    assert items[0].text == "Start recording (Pause)"
    t.hotkey_ok = False
    assert items[0].text == "Start recording (no hotkey)"
    assert t._title() == "Gather — idle (Middle-click to start)"
    mouse = list(items[names.index("Mouse trigger")].submenu.items)
    assert [i.text for i in mouse] == ["Middle-click (replaces normal middle-click)", "Alt + middle-click", "Off"]
    assert [i.checked for i in mouse] == [True, False, False]
    calls.clear()
    mouse[1](t.icon)
    assert calls == [("mouse_trigger", "Alt + middle-click")]
    assert t.cfg.mouse_trigger == "Middle-click"
    t.cfg.mouse_trigger = "Off"
    assert [i.checked for i in mouse] == [False, False, True]
    assert t._title() == "Gather — idle (tray click to start)"
    calls.clear()
    items[0](t.icon)
    assert calls == ["toggle"]


@pytest.mark.parametrize("preset", ["Alt+C", "Win+Y", "Pause", "Scroll Lock", "Ctrl+Alt+G"])
def test_hotkey_config_persistence(tmp_path, monkeypatch, preset):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ui.Config("docx", False, preset).save()
    assert ui.Config.load() == ui.Config("docx", False, preset)


def test_old_config_defaults_to_alt_c(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = ui.Config.path()
    path.parent.mkdir(parents=True)
    path.write_text('{"format":"pdf","mask":false}')
    assert ui.Config.load() == ui.Config("pdf", False, "Alt+C")


def test_concurrent_config_saves_keep_valid_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    entered, release, second_started, second_write = (threading.Event() for _ in range(4))
    original = Path.write_text

    def blocked_write(path, data, *args, **kwargs):
        if not entered.is_set():
            result = original(path, data, *args, **kwargs)
            entered.set()
            assert release.wait(3)
            return result
        second_write.set()
        return original(path, data, *args, **kwargs)

    def save_second():
        second_started.set()
        ui.Config("pdf", False, "Pause").save()

    monkeypatch.setattr(Path, "write_text", blocked_write)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(ui.Config(hotkey="Alt+C").save)
        try:
            assert entered.wait(2)
            second = pool.submit(save_second)
            assert second_started.wait(2)
            assert not second_write.wait(0.1)
        finally:
            release.set()
        first.result(2)
        second.result(2)
    assert ui.Config.load() == ui.Config("pdf", False, "Pause")


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
