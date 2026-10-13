"""Mouse hook behavior, including actual SendInput delivery to an owned blank window."""
import ctypes
import threading
import time
import uuid
import subprocess
import sys
from ctypes import wintypes as w

import pytest

from gather import ui, win32
from common import wait_for
from exe_check import INPUT, KEYBDINPUT, MOUSEINPUT
from live_safety import foreground
from test_app_adv import fake_app


win32._sig(win32.user32, "SetForegroundWindow", w.BOOL, w.HWND)
win32._sig(win32.user32, "GetCursorPos", w.BOOL, ctypes.POINTER(w.POINT))
win32._sig(win32.user32, "SetCursorPos", w.BOOL, ctypes.c_int, ctypes.c_int)
win32._sig(win32.user32, "AttachThreadInput", w.BOOL, w.DWORD, w.DWORD, w.BOOL)
win32._sig(win32.kernel32, "GetCurrentThreadId", w.DWORD)
win32._sig(win32.user32, "PeekMessageW", w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
win32._sig(win32.user32, "UnregisterClassW", w.BOOL, w.LPCWSTR, w.HINSTANCE)
win32._sig(win32.user32, "GetWindowRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
win32._sig(win32.user32, "SetProcessDPIAware", w.BOOL)
win32._sig(win32.user32, "WindowFromPoint", w.HWND, w.POINT)
win32._sig(win32.user32, "ShowWindow", w.BOOL, w.HWND, ctypes.c_int)
win32._sig(win32.user32, "SetWindowPos", w.BOOL, w.HWND, w.HWND, ctypes.c_int,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT)
_held = set()


def focus(hwnd):
    win32.user32.ShowWindow(hwnd, 9)  # restore a test window minimized by another app
    for _ in range(20):
        before = win32.user32.GetForegroundWindow()
        thread = win32.user32.GetWindowThreadProcessId(before, None)
        current = win32.kernel32.GetCurrentThreadId()
        attached = thread != current and win32.user32.AttachThreadInput(current, thread, True)
        try:
            win32.user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32.user32.AttachThreadInput(current, thread, False)
        if win32.user32.GetForegroundWindow() == hwnd:
            break
        time.sleep(0.05)
    assert foreground() == hwnd


def send(*events):
    # A consumed press still needs its release during an unexpected focus transition.
    hwnd = foreground()
    inputs = (INPUT * len(events))(*events)
    assert win32.user32.GetForegroundWindow() == hwnd
    sent = win32.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
    for event in inputs[:sent]:
        key = "middle" if event.type == 0 else event.ki.wVk
        up = bool(event.mi.dwFlags & 0x40) if event.type == 0 else bool(event.ki.dwFlags & 2)
        if up:
            _held.discard(key)
        else:
            _held.add(key)
    assert sent == len(inputs)


def middle(up=False, tag=0):
    return INPUT(type=0, mi=MOUSEINPUT(dwFlags=0x40 if up else 0x20, dwExtraInfo=tag))


def alt(up=False):
    return INPUT(type=1, ki=KEYBDINPUT(wVk=0x12, dwFlags=2 if up else 0))


@pytest.fixture
def target():
    win32.user32.SetProcessDPIAware()
    previous = win32.user32.GetForegroundWindow()
    cursor = w.POINT()
    assert win32.user32.GetCursorPos(ctypes.byref(cursor))
    events = []

    @win32.WNDPROC
    def wndproc(hwnd, msg, wp, lp):
        if msg in (win32.WM_MBUTTONDOWN, win32.WM_MBUTTONUP):
            events.append("down" if msg == win32.WM_MBUTTONDOWN else "up")
            return 0
        return win32.user32.DefWindowProcW(hwnd, msg, wp, lp)

    name = "GatherMouseTest" + uuid.uuid4().hex
    module = win32.kernel32.GetModuleHandleW(None)
    wc = win32.WNDCLASSW(lpfnWndProc=wndproc, hInstance=module, lpszClassName=name,
                         hbrBackground=w.HBRUSH(6))
    assert win32.user32.RegisterClassW(ctypes.byref(wc))
    hwnd = win32.user32.CreateWindowExW(0, name, "Gather mouse test — blank owned window",
                                       0x10CF0000, 100, 120, 450, 250, None, None, module, None)
    assert hwnd
    # The CLI runner's hidden STARTUPINFO overrides the first ShowWindow call.
    win32.user32.ShowWindow(hwnd, 5)
    win32.user32.ShowWindow(hwnd, 5)
    assert win32.user32.SetWindowPos(hwnd, w.HWND(-1), 0, 0, 0, 0, 0x43)

    def pump():
        for _ in range(10):
            msg = w.MSG()
            while win32.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                win32.user32.TranslateMessage(ctypes.byref(msg))
                win32.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)
        assert foreground() == hwnd, "Test window lost focus"

    try:
        focus(hwnd)
        assert not any(win32.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (4, 0x12)), "Mouse/Alt already held"
        rect = w.RECT()
        assert win32.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        assert win32.user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        pump()
        point = w.POINT()
        win32.user32.GetCursorPos(ctypes.byref(point))
        assert win32.user32.WindowFromPoint(point) == hwnd, "Pointer is outside the owned window"
        events.clear()
        yield events, pump
    finally:
        focus(hwnd)
        if "middle" in _held:
            send(middle(True))
        if 0x12 in _held:
            send(alt(True))
        pump()
        win32.user32.SetCursorPos(cursor.x, cursor.y)
        win32.user32.DestroyWindow(hwnd)
        win32.user32.UnregisterClassW(name, module)
        if previous:
            win32.user32.SetForegroundWindow(previous)


@pytest.mark.parametrize("mode,with_alt,swallow", [
    ("Off", False, False), ("Off", True, False),
    ("Middle-click", False, True), ("Middle-click", True, True),
    ("Alt + middle-click", False, False), ("Alt + middle-click", True, True),
])
def test_injected_modes(target, mode, with_alt, swallow):
    events, pump = target
    hits = []
    watcher = win32.ClipboardWatcher(lambda: hits.append(threading.get_ident()), lambda *a: None,
                                     mouse_trigger=mode)
    watcher.start()
    thread = watcher._thread
    try:
        assert bool(watcher._mouse_hook) == (mode != "Off")
        if with_alt:
            send(alt())
        try:
            send(middle())
            pump()
        finally:
            # Release Alt first: pairing must depend on the consumed down, not Alt state.
            if with_alt:
                send(alt(True))
            send(middle(True))
        pump()
        if not swallow:
            assert wait_for(lambda: (pump(), events == ["down", "up"])[1], timeout=2)
        assert events == ([] if swallow else ["down", "up"])
        assert hits == ([thread.ident] if swallow else [])
    finally:
        watcher.stop()
    assert not thread.is_alive() and watcher._mouse_hook is None
    assert watcher._hwnd is None and watcher.mouse_trigger == "Off"


def test_setting_change_waits_for_consumed_release(target):
    events, pump = target
    hits, result = [], []
    watcher = win32.ClipboardWatcher(lambda: hits.append(True), lambda *a: None)
    watcher.start()
    change = None
    try:
        send(middle())
        assert wait_for(lambda: hits == [True])
        change = threading.Thread(target=lambda: result.append(watcher.set_mouse_trigger("Off")))
        change.start()
        assert wait_for(lambda: watcher._mouse_req is not None)
        assert watcher._mouse_hook and watcher.mouse_trigger == "Middle-click"
    finally:
        send(middle(True))
        if change:
            change.join(3)
    try:
        assert result == [True] and watcher._mouse_hook is None
        pump()
        assert events == []
        send(middle(), middle(True))
        pump()
        assert events == ["down", "up"] and hits == [True]
        assert watcher.set_mouse_trigger("Alt + middle-click")
        assert watcher.mouse_trigger == "Alt + middle-click" and watcher._mouse_hook
        assert watcher.set_mouse_trigger("Middle-click")
        assert watcher.mouse_trigger == "Middle-click" and watcher._mouse_hook
    finally:
        watcher.stop()


def test_own_injected_mouse_input_passes_through(target):
    events, pump = target
    hits = []
    watcher = win32.ClipboardWatcher(lambda: hits.append(True), lambda *a: None)
    watcher.start()
    try:
        send(middle(tag=win32.OWN_INPUT_TAG), middle(True, win32.OWN_INPUT_TAG))
        pump()
        assert events == ["down", "up"] and hits == []
    finally:
        watcher.stop()


def test_orphan_release_and_negative_hook_code_pass_through(monkeypatch):
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None)
    calls = []
    monkeypatch.setattr(win32.user32, "CallNextHookEx", lambda *a: calls.append(a) or 37)
    event = win32.MSLLHOOKSTRUCT()
    ptr = ctypes.addressof(event)
    assert watcher._mouse_callback(-1, win32.WM_MBUTTONDOWN, 0) == 37
    assert watcher._mouse_callback(0, win32.WM_MBUTTONUP, ptr) == 37
    assert len(calls) == 2 and not watcher._mouse_swallowed


@pytest.mark.parametrize("initial", ["Off", "Middle-click"])
def test_install_failure_leaves_keyboard_and_tray_working(monkeypatch, initial):
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None, mouse_trigger=initial)
    watcher.start()
    key = watcher.hotkey
    monkeypatch.setattr(win32.user32, "SetWindowsHookExW", lambda *a: None)
    try:
        assert not watcher.set_mouse_trigger("Alt + middle-click")
        assert watcher.mouse_trigger == "Off" and watcher._mouse_hook is None
        assert watcher.hotkey == key and watcher._hwnd
    finally:
        watcher.stop()


def test_startup_hook_failure_and_cleanup(monkeypatch):
    monkeypatch.setattr(win32.user32, "SetWindowsHookExW", lambda *a: None)
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None)
    watcher.start()
    try:
        assert watcher._hwnd and watcher.mouse_trigger == "Off"
    finally:
        watcher.stop()
    assert watcher._hwnd is None and watcher._mouse_hook is None


def test_stop_during_consumed_press_does_not_leave_a_hook(target):
    events, pump = target
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None)
    watcher.start()
    thread = watcher._thread
    try:
        send(middle())
        assert wait_for(lambda: watcher._mouse_swallowed)
    finally:
        watcher.stop()
        send(middle(True))
    pump()
    assert not thread.is_alive() and watcher._mouse_hook is None
    events.clear()
    send(middle(), middle(True))
    pump()
    assert events == ["down", "up"]


def test_hooks_replaced_and_removed_on_the_watcher_thread(monkeypatch):
    calls = []
    install, remove = win32.user32.SetWindowsHookExW, win32.user32.UnhookWindowsHookEx

    def install_hook(*args):
        handle = install(*args)
        calls.append(("install", handle, threading.get_ident()))
        return handle

    def remove_hook(handle):
        calls.append(("remove", handle, threading.get_ident()))
        return remove(handle)

    monkeypatch.setattr(win32.user32, "SetWindowsHookExW", install_hook)
    monkeypatch.setattr(win32.user32, "UnhookWindowsHookEx", remove_hook)
    watcher = win32.ClipboardWatcher(lambda: None, lambda *a: None)
    watcher.start()
    thread = watcher._thread
    try:
        assert watcher.set_mouse_trigger("Alt + middle-click")
        assert watcher.set_mouse_trigger("Off")
        assert watcher.set_mouse_trigger("Middle-click")
        with pytest.raises(ValueError):
            watcher.set_mouse_trigger("Right-click")
    finally:
        watcher.stop()
    assert [c[0] for c in calls] == ["install", "remove"] * 3
    assert [c[1] for c in calls[::2]] == [c[1] for c in calls[1::2]]
    assert {c[2] for c in calls} == {thread.ident} and not thread.is_alive()


def test_mouse_callback_only_posts_and_failed_post_passes_through(monkeypatch):
    calls = []
    watcher = win32.ClipboardWatcher(lambda: calls.append("toggle"), lambda *a: None)
    watcher._hwnd, watcher.mouse_trigger = 123, "Middle-click"
    monkeypatch.setattr(win32.user32, "GetAsyncKeyState", lambda vk: 0)
    monkeypatch.setattr(win32.user32, "CallNextHookEx", lambda *a: 0)
    event = win32.MSLLHOOKSTRUCT()
    pointer = ctypes.addressof(event)
    monkeypatch.setattr(win32.user32, "PostMessageW", lambda *a: False)
    assert watcher._mouse_callback(0, win32.WM_MBUTTONDOWN, pointer) == 0
    assert not watcher._mouse_swallowed
    monkeypatch.setattr(win32.user32, "PostMessageW", lambda *a: calls.append(a[1]) or True)
    assert watcher._mouse_callback(0, win32.WM_MBUTTONDOWN, pointer) == 1
    assert watcher._mouse_callback(0, win32.WM_MBUTTONDOWN, pointer) == 1
    assert calls == [win32.WM_MOUSE_TRIGGER]  # duplicate down never toggles twice
    assert watcher._mouse_callback(0, win32.WM_MBUTTONUP, pointer) == 1
    assert not watcher._mouse_swallowed


def test_locked_clipboard_does_not_stall_mouse_hook(target):
    events, pump = target
    hits, texts = threading.Event(), []
    watcher = win32.ClipboardWatcher(hits.set, lambda text, *a: texts.append(text))
    watcher.start()
    watcher.set_capturing(True)
    holder = None
    try:
        holder = subprocess.Popen([sys.executable, "-c", """
import time
from gather import win32 as w
w._register_class()
hwnd = w.user32.CreateWindowExW(0, w._CLASS_NAME, 'Gather test lock', 0, 0, 0, 0, 0,
                               w.HWND_MESSAGE, None, w.kernel32.GetModuleHandleW(None), None)
w._open_clipboard(hwnd)
try:
    w.user32.EmptyClipboard()
    w._set_global(w.CF_UNICODETEXT, w._text_bytes('synthetic-mouse-lock'))
    print('locked', flush=True)
    time.sleep(0.8)
finally:
    w.user32.CloseClipboard()
    w.user32.DestroyWindow(hwnd)
"""], stdout=subprocess.PIPE, text=True)
        assert holder.stdout.readline().strip() == "locked"
        assert win32.user32.PostMessageW(watcher._hwnd, win32.WM_CLIPBOARDUPDATE, 0, 0)
        assert wait_for(lambda: watcher._clipboard_retries > 0)
        try:
            send(middle())
            assert hits.wait(0.4), "Locked clipboard blocked hook delivery"
        finally:
            send(middle(True))
        holder.wait(5)
        # GetWindowText for our own native target needs its UI thread to dispatch.
        assert wait_for(lambda: (pump(), "synthetic-mouse-lock" in texts)[1])
        pump()
        assert events == []
        hits.clear()
        send(middle(), middle(True))
        assert hits.wait(1), "Hook was silently removed"
    finally:
        if holder and holder.poll() is None:
            holder.kill()
            holder.wait(5)
        watcher.stop()


@pytest.mark.parametrize("mode", list(win32.MOUSE_TRIGGERS))
def test_mouse_config_persistence(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = ui.Config("pdf", False, "Pause", mode)
    cfg.save()
    assert ui.Config.load() == cfg


@pytest.mark.parametrize("setting", [None, "invalid", 2, []])
def test_old_or_invalid_mouse_setting_gets_default(tmp_path, monkeypatch, setting):
    import json
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = ui.Config.path()
    path.parent.mkdir()
    data = {"hotkey": "Pause", "mask": False}
    if setting is not None:
        data["mouse_trigger"] = setting
    path.write_text(json.dumps(data))
    assert ui.Config.load() == ui.Config("md", False, "Pause", "Middle-click")


def test_app_mouse_changes_and_failure_save_off(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.cfg.save = lambda: setattr(app, "saved", app.cfg.mouse_trigger)
    app.watcher.set_mouse_trigger = lambda name: setattr(app.watcher, "mouse_trigger", name) or True
    app.on_mouse_trigger_change("Middle-click")
    assert app.saved is None
    app.on_mouse_trigger_change("Alt + middle-click")
    assert app.cfg.mouse_trigger == app.saved == "Alt + middle-click"
    app.watcher.mouse_trigger = "Off"
    app.watcher.set_mouse_trigger = lambda name: False
    app.on_mouse_trigger_change("Middle-click")
    assert app.cfg.mouse_trigger == app.saved == "Off"
    assert "Couldn't enable" in app.notifications[-1]
    app.on_toggle()
    assert app.session is not None and app.capturing


def test_app_startup_failure_saves_off(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.mouse_trigger = "Off"
    app.watcher.start = lambda: True
    app.cfg.save = lambda: setattr(app, "saved", app.cfg.mouse_trigger)
    app.tray.run_detached = app.root.mainloop = lambda: None
    app.run()
    assert app.cfg.mouse_trigger == app.saved == "Off" and app.tray.hotkey_ok
    assert "keyboard hotkey and tray still work" in app.notifications[-1]


def test_startup_config_save_failure_keeps_app_alive(tmp_path, monkeypatch):
    app = fake_app(tmp_path, monkeypatch, recording=False)
    app.watcher.mouse_trigger = "Off"
    app.watcher.start = lambda: True
    app.tray.run_detached = app.root.mainloop = lambda: None

    def denied():
        raise PermissionError("config locked")

    app.cfg.save = denied
    app.run()
    assert app.cfg.mouse_trigger == "Off" and "couldn't save" in app.notifications[-1]
