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

from gather import win32
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
    ($_.Name -eq 'pythonw.exe' -and $_.CommandLine -match '(?:^|\s)-m\s+gather(?:\s|$)')
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


COMBO = (0x12, ord("C"))  # Alt+C; fresh APPDATA means default config
assert win32.DEFAULT_HOTKEY == "Alt+C"


def hotkey():
    hwnd = foreground()
    assert not any(win32.user32.GetAsyncKeyState(vk) & 0x8000 for vk in COMBO), "Keys already held"
    keys = [(vk, 0) for vk in COMBO] + [(vk, 2) for vk in reversed(COMBO)]
    inputs = (INPUT * len(keys))(*(INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=flags))
                                  for vk, flags in keys))
    assert win32.user32.GetForegroundWindow() == hwnd, "Focus changed before SendInput"
    sent = win32.user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        # Release just our modifiers even if Windows accepted only part of the batch.
        releases = (INPUT * len(COMBO))(*(INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=2))
                                          for vk in reversed(COMBO)))
        win32.user32.SendInput(len(COMBO), releases, ctypes.sizeof(INPUT))
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


def main():
    exe = (ROOT / "dist" / "Gather.exe").resolve()
    assert exe.is_file()
    assert not gather_processes(), "An existing Gather instance must not be disturbed"
    saved = win32._get_clipboard_text()
    folder = (win32.documents_dir() / "Gather").resolve()
    before = set(folder.glob("gather-*.md"))
    marker = "codex-exe-" + uuid.uuid4().hex
    copies = [marker + "-first", marker + "-second"]
    process = None
    owned_pids = set()
    verified_path = None
    # Reuse the blank native target used by the mouse tests; never click a user's app.
    from test_mouse import target, middle, send
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
            time.sleep(0.3)
            mods, vk = win32.HOTKEYS[win32.DEFAULT_HOTKEY]
            taken = not win32.user32.RegisterHotKey(None, 9281, mods, vk)
            if not taken:
                win32.user32.UnregisterHotKey(None, 9281)
            assert taken, "EXE has not registered its hotkey"
            send(middle(), middle(True))
            pump()
            time.sleep(0.3)
            for text in copies:
                subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"],
                               check=True, timeout=30)
                time.sleep(0.25)
            hotkey()
            paths = wait_for(clipboard_files)
            assert len(paths) == 1
            path = paths[0]
            assert path.parent == folder and path not in before and path.suffix == ".md"
            text = path.read_text(encoding="utf-8")
            assert "2 items" in text.splitlines()[0]
            assert all(text.count(copy) == 1 for copy in copies)
            assert text.index(copies[0]) < text.index(copies[1])
            verified_path = path
            assert events == [], "EXE did not consume the middle click"
            print(f"PASS: middle-click start, Alt+C stop, two ordered copies, CF_HDROP={path.name}")
            # The reverse pairing proves both triggers remain available for start and stop.
            win32.copy_text_to_clipboard("")
            hotkey()
            time.sleep(0.3)
            subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{marker}-third'"],
                           check=True, timeout=30)
            time.sleep(0.3)
            send(middle(), middle(True))
            pump()
            other = wait_for(clipboard_files)
            assert len(other) == 1 and other[0] != path
            assert marker + "-third" in other[0].read_text(encoding="utf-8")
            assert events == []
            print("PASS: Alt+C start, middle-click stop; consumed down/up")
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
