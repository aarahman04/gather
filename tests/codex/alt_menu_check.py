"""Manual local Alt-menu probe. Operates only on a supplied test-owned window."""
import argparse
import ctypes
import json
import subprocess
import time
from pathlib import Path

from PIL import ImageGrab

from exe_check import win32
from live_safety import foreground
from test_mouse import alt, focus, middle, send, _held


def focused():
    result = subprocess.run(["powershell", "-NoProfile", "-Command", """
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$e = [System.Windows.Automation.AutomationElement]::FocusedElement
@{ name=$e.Current.Name; type=$e.Current.ControlType.ProgrammaticName;
   id=$e.Current.AutomationId; pid=$e.Current.ProcessId } | ConvertTo-Json -Compress
"""], check=True, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)


def _probe(hwnd, label):
    win32.user32.SetProcessDPIAware()
    assert win32.user32.GetWindowThreadProcessId(hwnd, None)
    win32.user32.ShowWindow(hwnd, 5)
    win32.user32.ShowWindow(hwnd, 5)
    assert win32.user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x43)
    focus(hwnd)
    rect = win32.w.RECT()
    assert win32.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    assert win32.user32.SetCursorPos((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    before = focused()
    print(label, "before", before, flush=True)
    hits = []
    watcher = win32.ClipboardWatcher(lambda: hits.append(True), lambda *a: None, mouse_trigger="Alt + middle-click")
    watcher.start()
    try:
        assert watcher._mouse_hook
        # Repeat with and without masking to make the side effect observable.
        for mode in ("unmasked", "masked", "masked-batch"):
            mask = mode != "unmasked"
            original = win32.user32.keybd_event
            if not mask:
                win32.user32.keybd_event = lambda *a: None
            try:
                hits.clear()
                assert foreground() == hwnd
                if mode == "masked-batch":
                    send(alt(), middle(), middle(True), alt(True))
                else:
                    send(alt())
                    time.sleep(0.1)
                    send(middle())
                    time.sleep(0.1)
                    send(middle(True))
                    send(alt(True))
                time.sleep(0.2)
                assert hits == [True], "Mouse gesture did not toggle exactly once"
                after = focused()
                print(label, mode, after, flush=True)
                assert after["pid"] == win32._window_pid(hwnd)
                if mask:
                    assert after["type"] not in ("ControlType.MenuItem", "ControlType.Menu")
                ImageGrab.grab().save(Path("handoff") / f"alt-{label}-{mode}.png")
            finally:
                win32.user32.keybd_event = original
            # Escape resets a menu in this blank, owned test window.
            assert foreground() == hwnd
            from exe_check import INPUT, KEYBDINPUT
            send(INPUT(type=1, ki=KEYBDINPUT(wVk=0x1B)),
                 INPUT(type=1, ki=KEYBDINPUT(wVk=0x1B, dwFlags=2)))
            time.sleep(0.1)
    finally:
        watcher.stop()
        win32.user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, 3)


def probe(hwnd, label):
    previous = win32.user32.GetForegroundWindow()
    cursor = win32.w.POINT()
    assert win32.user32.GetCursorPos(ctypes.byref(cursor))
    assert not any(win32.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (4, 0x12))
    try:
        _probe(hwnd, label)
    finally:
        # Release only inputs issued by this harness, including on a failed assertion.
        if "middle" in _held:
            send(middle(True))
        if 0x12 in _held:
            send(alt(True))
        foreground()
        win32.user32.SetCursorPos(cursor.x, cursor.y)
        win32.user32.SetWindowPos(hwnd, ctypes.c_void_p(-2), 0, 0, 0, 0, 3)
        if previous:
            win32.user32.SetForegroundWindow(previous)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("hwnd", type=int)
    parser.add_argument("label")
    args = parser.parse_args()
    probe(args.hwnd, args.label)
