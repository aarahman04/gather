"""Tray, recording pill, sounds, config, autostart."""
import ctypes
import json
import os
import tkinter as tk
import winreg
import winsound
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw

_FORMATS = ("md", "docx", "pdf")
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "Gather"


@dataclass
class Config:
    format: str = "md"      # 'md' | 'docx' | 'pdf'
    mask: bool = True

    @staticmethod
    def path() -> Path:
        r"""%APPDATA%\Gather\config.json"""
        return Path(os.environ["APPDATA"]) / "Gather" / "config.json"

    @classmethod
    def load(cls) -> "Config":
        """Missing/corrupt file -> defaults."""
        cfg = cls()
        try:
            d = json.loads(cls.path().read_text(encoding="utf-8"))
            if d.get("format") in _FORMATS:
                cfg.format = d["format"]
            if isinstance(d.get("mask"), bool):
                cfg.mask = d["mask"]
        except Exception:
            pass
        return cfg

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"format": self.format, "mask": self.mask}), encoding="utf-8")
        os.replace(tmp, p)


def get_autostart(name: str = _RUN_NAME) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
        return True
    except OSError:
        return False


def set_autostart(on: bool, command: str, name: str = _RUN_NAME) -> None:
    r"""HKCU\Software\Microsoft\Windows\CurrentVersion\Run value 'Gather'."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


_SOUNDS = {"start": winsound.MB_OK, "stop": winsound.MB_ICONASTERISK, "error": winsound.MB_ICONHAND}


def play(kind: str) -> None:
    """kind: 'start' | 'stop' | 'error'. Non-blocking."""
    try:
        winsound.MessageBeep(_SOUNDS.get(kind, winsound.MB_OK))  # MessageBeep is async
    except Exception:
        pass


def _icon_image(recording: bool) -> Image.Image:
    s = 4  # supersample
    im = Image.new("RGBA", (64 * s, 64 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    red, grey, w = (214, 40, 40, 255), (120, 124, 130, 255), (255, 255, 255, 255)
    c = red if recording else grey
    if recording:  # rounded square, different shape from idle circle (colour-blind safe)
        d.rounded_rectangle((2 * s, 2 * s, 62 * s, 62 * s), 12 * s, fill=c)
    else:
        d.ellipse((2 * s, 2 * s, 62 * s, 62 * s), fill=c)
    # stacked pages
    d.rounded_rectangle((20 * s, 12 * s, 44 * s, 40 * s), 3 * s, outline=w, width=3 * s)
    d.rounded_rectangle((14 * s, 20 * s, 38 * s, 48 * s), 3 * s, fill=c, outline=w, width=3 * s)
    for y in (29, 36, 43):
        d.line((20 * s, y * s, 32 * s, y * s), fill=w, width=2 * s)
    if recording:  # white-ringed record dot
        d.ellipse((38 * s, 38 * s, 62 * s, 62 * s), fill=c)
        d.ellipse((42 * s, 42 * s, 58 * s, 58 * s), fill=w)
    return im.resize((64, 64), Image.LANCZOS)


class Tray:
    """pystray icon. Callbacks fire on pystray's thread."""

    def __init__(self, cfg: Config, actions: dict[str, Callable[[], None]]) -> None:
        """actions keys: 'toggle', 'undo', 'cancel', 'copy_text', 'copy_path',
        'open_folder', 'quit', 'autostart'. Left-click = 'toggle'.
        Format radio + mask checkbox edit cfg and cfg.save() directly."""
        self.cfg, self.actions = cfg, actions
        self.recording, self.count = False, 0
        self._img = {False: _icon_image(False), True: _icon_image(True)}
        self.menu = self._build_menu()
        self.icon = pystray.Icon("Gather", self._img[False], self._title(), self.menu)

    def _title(self) -> str:
        if self.recording:
            return f"Gather — recording · {self.count} item{'' if self.count == 1 else 's'}"
        return "Gather — idle (Ctrl+Alt+G to start)"

    def _act(self, key: str):
        return lambda icon=None, item=None: self.actions[key]()

    def _build_menu(self) -> pystray.Menu:
        M, I = pystray.Menu, pystray.MenuItem

        def fmt(f):
            def setf(icon, item):
                self.cfg.format = f
                self.cfg.save()
            return setf

        def togglemask(icon, item):
            self.cfg.mask = not self.cfg.mask
            self.cfg.save()

        return M(
            I(lambda i: ("Stop" if self.recording else "Start") + " recording (Ctrl+Alt+G)",
              self._act("toggle"), default=True),
            I("Remove last item", self._act("undo"), enabled=lambda i: self.recording and self.count > 0),
            I("Cancel session", self._act("cancel"), enabled=lambda i: self.recording),
            M.SEPARATOR,
            I("Copy last as text", self._act("copy_text")),
            I("Copy last file path", self._act("copy_path")),
            I("Open Gather folder", self._act("open_folder")),
            M.SEPARATOR,
            I("Format", M(*[
                I(label, fmt(f), checked=lambda i, f=f: self.cfg.format == f, radio=True)
                for f, label in (("md", "Markdown (.md)"), ("docx", "Word (.docx)"), ("pdf", "PDF (.pdf)"))
            ])),
            I("Mask secrets", togglemask, checked=lambda i: self.cfg.mask),
            I("Start with Windows", self._act("autostart"), checked=lambda i: get_autostart()),
            M.SEPARATOR,
            I("Quit", self._act("quit")),
        )

    def run_detached(self) -> None:
        self.icon.run_detached()

    def set_recording(self, on: bool, count: int = 0) -> None:
        """Grey icon idle / red recording; tooltip shows count."""
        self.recording, self.count = on, count
        self.icon.icon = self._img[on]
        self.icon.title = self._title()
        self.icon.update_menu()

    def notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        self.icon.stop()


_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT, _WS_EX_TOOLWINDOW, _WS_EX_LAYERED, _WS_EX_NOACTIVATE = 0x20, 0x80, 0x80000, 0x08000000
_user32 = ctypes.windll.user32
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.GetParent.restype = wintypes.HWND
_user32.GetParent.argtypes = [wintypes.HWND]


class Pill:
    """Small always-on-top, no-focus, click-through overlay at top-right:
    '● Gather 4'. Lives on the Tk main thread."""

    BG, FLASH = "#1e1e1e", "#4a4a4a"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.win = w = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.92)
        w.configure(bg=self.BG)
        w.geometry("+-10000+0")  # mapped off-screen just long enough to style the real HWND
        w.update_idletasks()
        # winfo_id is Tk's inner child HWND; the real top-level (the one that can take focus) is its parent.
        self.hwnd = _user32.GetParent(w.winfo_id()) or w.winfo_id()
        ex = _user32.GetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE)
        _user32.SetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE, ex | _WS_EX_NOACTIVATE
                                  | _WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT | _WS_EX_LAYERED)
        self.dot = tk.Label(w, text="●", fg="#ff3b30", bg=self.BG, font=("Segoe UI", 10))
        self.dot.pack(side="left", padx=(10, 0), pady=4)
        self.lbl = tk.Label(w, text="", fg="white", bg=self.BG, font=("Segoe UI", 10))
        self.lbl.pack(side="left", padx=(4, 10), pady=4)
        w.withdraw()
        self._pulse = None

    def _set(self, count: int) -> None:
        self.lbl.config(text=f"Gather  {count}")

    def _place(self) -> None:
        r = wintypes.RECT()
        _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA (primary)
        self.win.update_idletasks()
        self.win.geometry(f"+{r.right - self.win.winfo_reqwidth() - 16}+{r.top + 16}")

    def show(self, count: int) -> None:
        self._set(count)
        self._place()
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def update(self, count: int) -> None:
        """Set count and briefly pulse."""
        self._set(count)
        self._place()
        self._paint(self.FLASH)
        if self._pulse:
            self.root.after_cancel(self._pulse)
        self._pulse = self.root.after(150, self._unflash)

    def _paint(self, bg: str) -> None:
        for wd in (self.win, self.dot, self.lbl):
            wd.config(bg=bg)

    def _unflash(self) -> None:
        self._pulse = None
        self._paint(self.BG)

    def hide(self) -> None:
        self.win.withdraw()
