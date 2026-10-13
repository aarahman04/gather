"""Local EXE smoke check: synthetic copies, no pasting or website uploads."""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gather import win32, __version__
from live_safety import foreground, restore_text


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", INPUTUNION)]


win32._sig(win32.user32, "SendInput", w.UINT, w.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
win32._sig(win32.user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
win32._sig(win32.user32, "FindWindowExW", w.HWND, w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR)
win32._sig(win32.shell32, "DragQueryFileW", w.UINT, w.HANDLE, w.UINT, w.LPWSTR, w.UINT)
assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def process_image(pid):
    handle = win32.kernel32.OpenProcess(win32.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    assert handle, f"Cannot inspect process {pid}"
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(buf))
        assert win32.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return Path(buf.value).resolve()
    finally:
        win32.kernel32.CloseHandle(handle)


def gather_processes():
    # Match only executable names and pythonw's actual arguments, never the shell command.
    result = subprocess.run(["powershell", "-NoProfile", "-Command", r"""
@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'Gather.exe' -or
    ($_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -match '(?:^|\s)-m\s+gather(?:\s|$)')
} | Select-Object ProcessId,Name) | ConvertTo-Json -Compress
"""], capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(result.stdout or "[]")
    return data if isinstance(data, list) else [data]


def wait_for(callback, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for EXE state")


def copy_via_powershell(text):
    for _ in range(10):
        result = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 "Set-Clipboard -Value '" + text.replace("'", "''") + "'"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            wait_for(lambda: win32._get_clipboard_text() == text, timeout=3)
            return
        time.sleep(0.1)  # another desktop process can briefly own the clipboard
    raise RuntimeError("PowerShell Set-Clipboard failed: " + result.stderr)


COMBO = (0x12, ord("C"))  # Alt+C; fresh APPDATA means default config
assert win32.DEFAULT_HOTKEY == "Alt+C"


def hotkey(combo=COMBO):
    hwnd = foreground()
    assert not any(win32.user32.GetAsyncKeyState(vk) & 0x8000 for vk in combo), "Keys already held"
    keys = [(vk, 0) for vk in combo] + [(vk, 2) for vk in reversed(combo)]
    inputs = (INPUT * len(keys))(*(INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=flags))
                                  for vk, flags in keys))
    assert win32.user32.GetForegroundWindow() == hwnd, "Focus changed before SendInput"
    sent = win32.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        # Release just our modifiers even if Windows accepted only part of the batch.
        releases = (INPUT * len(combo))(*(INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=2))
                                          for vk in reversed(combo)))
        win32.user32.SendInput(len(combo), releases, ctypes.sizeof(INPUT))
        raise ctypes.WinError(ctypes.get_last_error())


def clipboard_files():
    try:
        win32._open_clipboard()
    except OSError:
        return []
    try:
        handle = win32.user32.GetClipboardData(win32.CF_HDROP)
        if not handle:
            return []
        result = []
        for i in range(win32.shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)):
            n = win32.shell32.DragQueryFileW(handle, i, None, 0)
            buf = ctypes.create_unicode_buffer(n + 1)
            assert win32.shell32.DragQueryFileW(handle, i, buf, n + 1) == n
            result.append(Path(buf.value).resolve())
        return result
    finally:
        win32.user32.CloseClipboard()


def overlay(title, pid):
    hwnd = win32.user32.FindWindowExW(None, None, None, title)
    assert hwnd and win32._window_pid(hwnd) == pid, f"Missing owned overlay: {title}"
    return hwnd


def rectangle(hwnd):
    rect = w.RECT()
    assert win32.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def screenshot(pid, name, expanded=False):
    from PIL import Image, ImageGrab
    body = rectangle(overlay("Gather recording", pid))
    button = rectangle(overlay("Gather expand", pid))
    pill = ImageGrab.grab((body[0], body[1], button[2], body[3]))
    if expanded:
        panel = ImageGrab.grab(rectangle(overlay("Gather items", pid)))
        image = Image.new("RGB", (max(pill.width, panel.width), pill.height + 4 + panel.height), "#151515")
        image.paste(pill, ((image.width - pill.width) // 2, 0))
        image.paste(panel, (0, pill.height + 4))
    else:
        image = pill
    path = ROOT / "handoff" / f"v{__version__}-{name}.png"
    path.parent.mkdir(exist_ok=True)
    image.save(path)
    print(f"Screenshot: {path.name}")


def main():
    exe = (ROOT / "dist" / "Gather.exe").resolve()
    assert exe.is_file()
    assert not gather_processes(), "An existing Gather instance must not be disturbed"
    saved = win32._get_clipboard_text()
    folder = (win32.documents_dir() / "Gather").resolve()
    before = set(folder.glob("gather-*.md"))
    marker = "codex-exe-" + uuid.uuid4().hex
    copies = [f"{label} — sample copied text\n{marker}-{name}"
              for label, name in (("First item", "first"), ("Second item", "second"), ("Third item", "third"))]
    process = None
    owned_pids = set()
    verified_path = None
    # Reuse the blank native target used by the mouse tests; never click a user's app.
    from test_mouse import target, middle, send
    from test_panel import click_point, drag_points
    win32._sig(win32.user32, "IsWindowVisible", w.BOOL, w.HWND)
    win32._sig(win32.user32, "GetFocus", w.HWND)
    window = target.__wrapped__()
    events, pump = next(window)
    with tempfile.TemporaryDirectory(prefix="gather-codex-config-") as settings:
        try:
            env = os.environ.copy()
            env["APPDATA"] = settings  # fresh defaults; preserve the user's config
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen([str(exe)], cwd=ROOT, env=env, startupinfo=startup)
            owned_pids.add(process.pid)
            hwnd = wait_for(lambda: win32.user32.FindWindowExW(win32.HWND_MESSAGE, None,
                                                             win32._CLASS_NAME, "Gather"))
            assert process_image(win32._window_pid(hwnd)) == exe
            owned_pids.add(win32._window_pid(hwnd))
            pid = win32._window_pid(hwnd)
            time.sleep(0.3)
            mods, vk = win32.HOTKEYS[win32.DEFAULT_HOTKEY]
            taken = not win32.user32.RegisterHotKey(None, 9281, mods, vk)
            if not taken:
                win32.user32.UnregisterHotKey(None, 9281)
            assert taken, "EXE has not registered its hotkey"
            def undo_key_free():
                free = win32.user32.RegisterHotKey(None, 9282, win32.MOD_ALT, ord("Z"))
                if free:
                    win32.user32.UnregisterHotKey(None, 9282)
                return bool(free)

            assert undo_key_free(), "Alt+Z is not free while EXE is idle"
            send(middle(), middle(True))
            pump()
            time.sleep(0.3)
            assert not undo_key_free(), "EXE did not register recording-only Alt+Z"
            for text in copies:
                copy_via_powershell(text)
                time.sleep(0.25)
            wrong = marker + "-undo-me"
            copy_via_powershell(wrong)
            time.sleep(0.3)
            hotkey((0x12, ord("Z")))
            time.sleep(0.3)
            assert win32._get_clipboard_text() == wrong, "Undo touched the clipboard"
            source = foreground()
            source_focus = win32.user32.GetFocus()
            screenshot(pid, "pill")
            button = rectangle(overlay("Gather expand", pid))
            click_point((button[0] + button[2]) // 2, (button[1] + button[3]) // 2, pump, pid)
            panel_hwnd = overlay("Gather items", pid)
            wait_for(lambda: win32.user32.IsWindowVisible(panel_hwnd))
            time.sleep(0.2)
            screenshot(pid, "panel-3", True)
            rect = rectangle(panel_hwnd)
            row_height = (rect[3] - rect[1]) // 3
            click_point(rect[2] - 20, rect[1] + row_height * 3 // 2, pump, pid)
            wait_for(lambda: rectangle(panel_hwnd)[3] - rectangle(panel_hwnd)[1] == row_height * 2)
            time.sleep(0.25)  # the remaining row is sliding into its new slot
            rect = rectangle(panel_hwnd)
            from PIL import ImageGrab
            frames = []
            def record_drag():
                pump()
                frames.append(ImageGrab.grab(rectangle(panel_hwnd)))
            drag_points((rect[0] + 15, rect[1] + row_height * 3 // 2),
                        (rect[0] + 15, rect[1] + 2), record_drag, pid, steps=8, hold=0.2)
            animation = ROOT / "handoff" / f"v{__version__}-drag.gif"
            frames[0].save(animation, save_all=True, append_images=frames[1:], duration=100, loop=0)
            print(f"Animation: {animation.name}")
            time.sleep(0.2)
            assert win32.user32.GetForegroundWindow() == source and win32.user32.GetFocus() == source_focus
            assert win32._get_clipboard_text() == wrong, "Panel edits touched the clipboard"
            hotkey()
            paths = wait_for(clipboard_files)
            assert len(paths) == 1
            path = paths[0]
            assert path.parent == folder and path not in before and path.suffix == ".md"
            text = path.read_text(encoding="utf-8")
            assert "2 items" in text.splitlines()[0]
            assert all(text.count(copy) == 1 for copy in (copies[2], copies[0]))
            assert text.index(copies[2]) < text.index(copies[0])
            assert wrong not in text and copies[1] not in text and undo_key_free()
            assert not win32.user32.IsWindowVisible(panel_hwnd), "Panel did not collapse on stop"
            verified_path = path
            assert events == [], "EXE did not consume the middle click"
            print(f"PASS: middle-click start, Alt+Z undo, panel cross + drag without focus/clipboard changes, "
                  f"Alt+C stop, exported third/first order, CF_HDROP={path.name}")
            # The reverse pairing proves both triggers remain available for start and stop.
            win32.copy_text_to_clipboard("")
            hotkey()
            time.sleep(0.3)
            many = [f"Collected item {i:02} — sample text\n{marker}-many-{i:02}" for i in range(1, 11)]
            for i, copy in enumerate(many):
                copy_via_powershell(copy)
                time.sleep(0.15)
                if i == 0:
                    button = rectangle(overlay("Gather expand", pid))
                    click_point((button[0] + button[2]) // 2, (button[1] + button[3]) // 2, pump, pid)
            screenshot(pid, "panel-10", True)
            # Wheel to the bottom, then move the tenth row to the top while holding
            # the edge. The EXE must retain capture and continuously scroll upward.
            rect = rectangle(panel_hwnd)
            x = rect[0] + 15
            y = rect[1] + row_height * 4
            assert foreground() == source
            assert win32.user32.SetCursorPos(x, y)
            assert win32._window_pid(win32.user32.WindowFromPoint(w.POINT(x, y))) == pid
            wheel = INPUT(type=0, mi=MOUSEINPUT(mouseData=(-240) & 0xFFFFFFFF, dwFlags=0x0800))
            assert win32.user32.SendInput(1, ctypes.byref(wheel), ctypes.sizeof(INPUT)) == 1
            pump()
            drag_points((x, rect[1] + row_height * 15 // 2), (x, rect[1] + 2),
                        pump, pid, steps=8, hold=0.3)
            time.sleep(0.2)
            assert win32.user32.GetForegroundWindow() == source and win32.user32.GetFocus() == source_focus
            assert win32._get_clipboard_text() == many[-1], "Reorder touched the clipboard"
            send(middle(), middle(True))
            pump()
            other = wait_for(clipboard_files)
            assert len(other) == 1 and other[0] != path
            body = other[0].read_text(encoding="utf-8")
            assert "10 items" in body.splitlines()[0] and all(copy in body for copy in many)
            ordered = [many[-1], *many[:-1]]
            assert [body.index(copy) for copy in ordered] == sorted(body.index(copy) for copy in ordered)
            assert events == []
            print("PASS: Alt+C start, ten live rows, wheel and continuous edge-scroll reorder, "
                  "middle-click stop; exported tenth/first-through-ninth order")
        finally:
            try:
                for pid in sorted(owned_pids, key=lambda pid: pid != process.pid):
                    if any(row["ProcessId"] == pid for row in gather_processes()):
                        assert process_image(pid) == exe
                        # Also handle the child if the PyInstaller bootloader exited early.
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       check=True, capture_output=True, timeout=15)
                if process is not None:
                    process.wait(timeout=10)
                for candidate in set(folder.glob("gather-*.md")) - before:
                    assert candidate.resolve().parent == folder
                    if candidate == verified_path or marker in candidate.read_text(encoding="utf-8", errors="replace"):
                        candidate.unlink()
                        print(f"Deleted synthetic test file: {candidate.name}")
            finally:
                try:
                    restore_text(saved)
                    assert win32._get_clipboard_text() == saved
                finally:
                    window.close()
            assert not gather_processes(), "Gather process remains after EXE check"
            print("PASS: test EXE stopped; original clipboard text restored and verified")


if __name__ == "__main__":
    sys.modules["exe_check"] = sys.modules[__name__]
    main()
