"""Tray, recording pill, sounds, config, autostart."""
import ctypes
import json
import math
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from time import monotonic
import winreg
import winsound
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw, ImageTk

from .win32 import DEFAULT_HOTKEY, HOTKEYS, DEFAULT_MOUSE_TRIGGER, MOUSE_TRIGGERS
from .session import mask_secrets

_FORMATS = ("md", "docx", "pdf")
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "Gather"
_CONFIG_LOCK = threading.Lock()  # Tk hotkey changes and tray settings share the temp file


@dataclass
class Config:
    format: str = "md"      # 'md' | 'docx' | 'pdf'
    mask: bool = True
    hotkey: str = DEFAULT_HOTKEY  # a win32.HOTKEYS name
    mouse_trigger: str = DEFAULT_MOUSE_TRIGGER
    folder: str = ""        # empty means the Windows Documents\Gather folder

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
            if d.get("hotkey") in HOTKEYS:
                cfg.hotkey = d["hotkey"]
            if d.get("mouse_trigger") in MOUSE_TRIGGERS:
                cfg.mouse_trigger = d["mouse_trigger"]
            folder = d.get("folder")
            if isinstance(folder, str) and "\0" not in folder and Path(folder).is_absolute():
                cfg.folder = folder
        except Exception:
            pass
        return cfg

    def save(self) -> None:
        with _CONFIG_LOCK:
            p = self.path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"format": self.format, "mask": self.mask,
                                       "hotkey": self.hotkey,
                                       "mouse_trigger": self.mouse_trigger,
                                       "folder": self.folder}), encoding="utf-8")
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


def show_help(root: tk.Tk, cfg: Config) -> None:
    mouse = f" or {cfg.mouse_trigger.lower()}" if cfg.mouse_trigger != "Off" else ""
    messagebox.showinfo("Gather — Help",
        f"1. Start recording with {cfg.hotkey}{mouse}, or left-click the tray icon.\n\n"
        "2. Copy text from any app. Gather counts your items.\n\n"
        "3. Click the pill's arrow to see the list. Drag a left handle to reorder; "
        "hold near the top or bottom to scroll. Click × to remove an item.\n\n"
        "4. Alt+Z removes the latest remaining copy, even after reordering. No redo.\n\n"
        f"5. Stop with {cfg.hotkey}{mouse}, or the tray icon. Your file is saved in "
        "your save folder (Documents\\Gather by default) and put on the clipboard. "
        "Ctrl+V pastes or attaches it.\n\n"
        "Right-click the tray for Save folder, format, hotkey, mouse trigger and cancel options. "
        "List previews always mask recognized secrets.\n\n"
        "Middle-click mode replaces normal middle-click in other apps. "
        "Choose Alt + middle-click or Off to keep that behaviour.", parent=root)


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
        'open_folder', 'help', 'quit', 'autostart', 'hotkey' and 'mouse_trigger'
        (take a preset name; the app saves it after applying). Left-click = 'toggle'.
        Format radio + mask checkbox edit cfg and cfg.save() directly."""
        self.cfg, self.actions = cfg, actions
        self.recording, self.count = False, 0
        self.hotkey_ok = True  # False while no hotkey is registered
        self.undo_ok = True
        self._img = {False: _icon_image(False), True: _icon_image(True)}
        self.menu = self._build_menu()
        self.icon = pystray.Icon("Gather", self._img[False], self._title(), self.menu)

    def _title(self) -> str:
        triggers = [self.cfg.hotkey] if self.hotkey_ok else []
        if self.cfg.mouse_trigger != "Off":
            triggers.append(self.cfg.mouse_trigger)
        hint = " / ".join(triggers) or "tray click"
        if self.recording:
            undo = "Alt+Z undo" if self.undo_ok else "tray undo"
            return f"Gather — recording · {self.count} item{'' if self.count == 1 else 's'} ({hint} to stop; {undo}; arrow: items)"
        return f"Gather — idle ({hint} to start)"

    def _hotkey(self) -> str:
        return self.cfg.hotkey if self.hotkey_ok else "no hotkey"

    def _act(self, key: str):
        return lambda icon=None, item=None: self.actions[key]()

    def _build_menu(self) -> pystray.Menu:
        M, I = pystray.Menu, pystray.MenuItem

        def fmt(f):
            def setf(icon, item):
                self.cfg.format = f
                self.cfg.save()
            return setf

        def hk(name):
            return lambda icon, item: self.actions["hotkey"](name)

        def mouse(name):
            return lambda icon, item: self.actions["mouse_trigger"](name)

        def togglemask(icon, item):
            self.cfg.mask = not self.cfg.mask
            self.cfg.save()

        return M(
            I(lambda i: ("Stop" if self.recording else "Start") + f" recording ({self._hotkey()})",
              self._act("toggle"), default=True),
            I(lambda i: "Remove last item (Alt+Z)" if self.undo_ok else "Remove last item (Alt+Z unavailable)",
              self._act("undo"), enabled=lambda i: self.recording),
            I("Cancel session", self._act("cancel"), enabled=lambda i: self.recording),
            M.SEPARATOR,
            I("Copy last as text", self._act("copy_text")),
            I("Copy last file path", self._act("copy_path")),
            I("Open Gather folder", self._act("open_folder")),
            I("Save folder…", self._act("save_folder")),
            M.SEPARATOR,
            I("Format", M(*[
                I(label, fmt(f), checked=lambda i, f=f: self.cfg.format == f, radio=True)
                for f, label in (("md", "Markdown (.md)"), ("docx", "Word (.docx)"), ("pdf", "PDF (.pdf)"))
            ])),
            I("Hotkey", M(*[
                I(name, hk(name), checked=lambda i, n=name: self.cfg.hotkey == n, radio=True)
                for name in HOTKEYS
            ])),
            I("Mouse trigger", M(*[
                I(label, mouse(name),
                  checked=lambda i, n=name: self.cfg.mouse_trigger == n, radio=True)
                for name, label in MOUSE_TRIGGERS.items()
            ])),
            I("Mask secrets", togglemask, checked=lambda i: self.cfg.mask),
            I("Start with Windows", self._act("autostart"), checked=lambda i: get_autostart()),
            M.SEPARATOR,
            I("Help", self._act("help")),
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
_user32.SystemParametersInfoW.restype = wintypes.BOOL
_user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
_comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
_SUBCLASSPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t)
_comctl32.SetWindowSubclass.restype = wintypes.BOOL
_comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
_comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
_comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, ctypes.c_size_t]
_comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
_comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]


@_SUBCLASSPROC
def _noactivate(hwnd, msg, wp, lp, subclass_id, data):
    if msg == 0x21:  # WM_MOUSEACTIVATE: accept the click without activating
        return 3  # MA_NOACTIVATE
    if msg == 0x82:  # WM_NCDESTROY: remove before Tk destroys its native window
        _comctl32.RemoveWindowSubclass(hwnd, _noactivate, subclass_id)
    return _comctl32.DefSubclassProc(hwnd, msg, wp, lp)


def _overlay(root: tk.Tk, title: str, click_through: bool = False) -> tuple[tk.Toplevel, int]:
    w = tk.Toplevel(root, bg=Pill.BG, takefocus=False)
    w.title(title)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.attributes("-alpha", 0.92)
    w.geometry("+-10000+0")
    w.update_idletasks()
    inner = w.winfo_id()
    hwnd = _user32.GetParent(inner) or inner
    ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
    ex |= _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED
    if click_through:
        ex |= _WS_EX_TRANSPARENT
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex)
    for handle in {hwnd, inner}:
        if not _comctl32.SetWindowSubclass(handle, _noactivate, 1, 0):
            w.destroy()
            raise ctypes.WinError(ctypes.get_last_error())
    w.withdraw()
    return w, hwnd


def preview_text(text: str, limit: int = 40) -> str:
    """Always mask before shortening; previews never expose recognized secrets."""
    text = " ".join(mask_secrets(text).splitlines())
    head = "".join(c if c.isprintable() else " " for c in text[:limit])
    return head + ("…" if len(text) > limit else "")


def _fit_text(label: tk.Label, text: str) -> None:
    """Fit already masked text to the actual pixel width, including wide glyphs."""
    shown, body = text, text.removesuffix("…")
    available = label.winfo_width() - 4
    if available > 0:
        while body and int(label.tk.call("font", "measure", label.cget("font"), shown)) > available:
            body = body[:-1]
            shown = body + "…"
    label.config(text=shown)


def _chevron_image(scale: float, fraction: float) -> Image.Image:
    """A rounded vector path, supersampled so the small icon stays crisp."""
    size, sample = round(16 * scale), 4
    image = Image.new("RGBA", (size * sample, size * sample))
    draw = ImageDraw.Draw(image)
    angle = math.pi * fraction
    points = [(8 + x * math.cos(angle) - y * math.sin(angle),
               8 + x * math.sin(angle) + y * math.cos(angle)) for x, y in ((-4, -2), (0, 2), (4, -2))]
    points = [(x * size / 16 * sample, y * size / 16 * sample) for x, y in points]
    width = max(1, round(1.8 * scale * sample))
    draw.line(points, fill="#dddddd", width=width, joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#dddddd")
    return image.resize((size, size), Image.Resampling.LANCZOS)


class Pill:
    """Click-through body, separate clickable chevron and item panel; all never activate.
    Only edit callbacks enqueue work; widgets and list updates live on the Tk thread."""

    BG, FLASH = "#1e1e1e", "#4a4a4a"

    def __init__(self, root: tk.Tk, on_remove=lambda item_id: None,
                 on_move=lambda item_id, position: None) -> None:
        self.root = root
        self.win, self.hwnd = _overlay(root, "Gather recording", True)
        w = self.win
        self.dot = tk.Label(w, text="●", fg="#ff3b30", bg=self.BG, font=("Segoe UI", 10))
        self.dot.pack(side="left", padx=(10, 0), pady=4)
        self.lbl = tk.Label(w, text="", fg="white", bg=self.BG, font=("Segoe UI", 10))
        self.lbl.pack(side="left", padx=(4, 10), pady=4)
        w.withdraw()
        self.scale = float(root.tk.call("tk", "scaling")) * 72 / 96
        motion = wintypes.BOOL(True)
        _user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(motion), 0)
        self._motion = bool(motion.value)
        self._panel_timer = self._rows_timer = self._scroll_timer = None
        self._panel_fraction = 0.0
        self._panel_geometry = None
        self._row_targets = {}
        self._layout_targets = {}
        self.recording = self.expanded = False
        self.items, self.rows = [], {}
        self.on_remove, self.on_move = on_remove, on_move
        self._drag_id = None
        self._drop = 0
        self._drag_pointer = (0, 0)
        self._drag_offset = 0
        self.chevron_win, self.chevron_hwnd = _overlay(root, "Gather expand")
        self._chevrons = [ImageTk.PhotoImage(_chevron_image(self.scale, i / 12), master=root) for i in range(13)]
        self.chevron = tk.Button(self.chevron_win, text="Show items", image=self._chevrons[0], command=self.toggle_panel,
                                fg="white", bg=self.BG, activebackground=self.FLASH,
                                activeforeground="white", relief="flat", borderwidth=0,
                                highlightthickness=0, takefocus=False, font=("Segoe UI", 12))
        self.chevron.pack(fill="both", expand=True)
        self.panel, self.panel_hwnd = _overlay(root, "Gather items")
        self.panel.attributes("-alpha", 1.0)
        self.row_height = round(52 * self.scale)
        self.canvas = tk.Canvas(self.panel, bg=self.BG, highlightthickness=0,
                                takefocus=False, yscrollincrement=self.row_height)
        self.scrollbar = tk.Scrollbar(self.panel, orient="vertical", command=self.canvas.yview,
                                     takefocus=False)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.canvas, bg=self.BG)
        self.content_window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.content_window, width=e.width))
        self.empty = tk.Label(self.content, text="Nothing copied yet.", fg="#aaaaaa", bg=self.BG,
                              font=("Segoe UI", 10), takefocus=False)
        # Canvas drawings sit behind embedded windows; a raised frame keeps the marker visible.
        self.marker = tk.Frame(self.canvas, bg="#ff6b61", height=2, takefocus=False)
        self.panel.bind("<MouseWheel>", self._wheel)
        self.win.bind("<Destroy>", lambda e: self._cleanup() if e.widget == self.win else None)

    def _cancel_timer(self, name: str) -> None:
        timer = getattr(self, name)
        if timer is not None:
            self.root.after_cancel(timer)
            setattr(self, name, None)

    def _animate(self, name, frame, done=lambda: None) -> None:
        self._cancel_timer(name)
        started = monotonic()
        def step():
            t = min(1.0, (monotonic() - started) / 0.18) if self._motion else 1.0
            frame(1 - (1 - t) ** 3)
            if t < 1:
                setattr(self, name, self.root.after(16, step))
            else:
                setattr(self, name, None)
                done()
        step()

    def _reveal(self, fraction):
        self._panel_fraction = fraction
        self.chevron.config(image=self._chevrons[round(fraction * 12)])
        if self._panel_geometry is not None:
            width, height, x, y = self._panel_geometry
            self.panel.geometry(f"{width}x{max(1, round(height * fraction))}+{x}+{y}")

    def _cleanup(self):
        self._cancel_drag()
        self._cancel_timer("_panel_timer")
        self._cancel_timer("_rows_timer")

    def set_items(self, items) -> None:
        self.items = list(items)
        if self.expanded:
            self._render_items()
            self._place()

    def toggle_panel(self) -> None:
        if not self.recording:
            return
        if self.expanded:
            self.collapse(animated=True)
        else:
            self.expanded = True
            self.chevron.config(text="Hide items")
            self._render_items()
            self._place()
            self.panel.deiconify()
            start = self._panel_fraction
            self._animate("_panel_timer", lambda p: self._reveal(start + (1 - start) * p))

    def collapse(self, animated: bool = False) -> None:
        self.expanded = False
        self._cancel_drag()
        self._cancel_timer("_rows_timer")
        self.chevron.config(text="Show items")
        if animated:
            start = self._panel_fraction
            self._animate("_panel_timer", lambda p: self._reveal(start * (1 - p)), self.panel.withdraw)
        else:
            self._cancel_timer("_panel_timer")
            self._reveal(0)
            self.panel.withdraw()

    def _render_items(self) -> None:
        ids = {c.id for c in self.items}
        if self._drag_id not in ids:
            self._cancel_drag(restore=False)
        self._cancel_timer("_rows_timer")
        self._layout_targets = {}
        for item_id in list(self.rows):
            if item_id not in ids:
                self.rows.pop(item_id)["frame"].destroy()
        self.empty.place_forget()
        targets = {}
        for i, chunk in enumerate(self.items, 1):
            y = (i - 1) * self.row_height
            new = chunk.id not in self.rows
            if new:
                self.rows[chunk.id] = self._row(chunk)
                self.rows[chunk.id]["frame"].place(x=0, y=y, relwidth=1, height=self.row_height)
            row = self.rows[chunk.id]
            row["number"].config(text=str(i), width=max(3, len(str(len(self.items)))))
            row["short_preview"] = preview_text(chunk.text, 60)
            _fit_text(row["preview"], row["short_preview"])
            app = chunk.app[:-4] if chunk.app.lower().endswith(".exe") else chunk.app
            source = " · ".join(filter(None, (preview_text(app, 14), preview_text(chunk.title, 28)))) or "unknown"
            row["short_source"] = source
            _fit_text(row["source"], source)
            row["time"].config(text=chunk.ts.strftime("%H:%M:%S"))
            targets[chunk.id] = y
        self._row_targets = targets
        total = max(1, len(self.items)) * self.row_height
        self.content.config(height=total)
        if not self.items:
            self.empty.place(x=0, y=0, relwidth=1, height=self.row_height)
        if len(self.items) > 8:
            self.scrollbar.pack(side="right", fill="y")
        else:
            self.scrollbar.pack_forget()
        self.content.update_idletasks()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), total))
        if self._drag_id is not None:
            self._update_drop()
        else:
            self._slide_rows(targets)

    def _slide_rows(self, targets):
        if targets == self._layout_targets:
            return
        self._layout_targets = targets
        starts = {key: self.rows[key]["frame"].winfo_y() for key in targets}
        def position(p):
            for key, y in targets.items():
                self.rows[key]["frame"].place_configure(y=round(starts[key] + (y - starts[key]) * p))
        self._animate("_rows_timer", position)

    def _row(self, chunk) -> dict:
        row = tk.Frame(self.content, bg=self.BG, height=self.row_height,
                       highlightthickness=1, highlightbackground=self.BG)
        row.pack_propagate(False)
        handle = tk.Label(row, text="⋮⋮", fg="#aaaaaa", bg=self.BG, cursor="fleur",
                          font=("Segoe UI", 12), takefocus=False)
        handle.pack(side="left", padx=(6, 2))
        number = tk.Label(row, width=3, fg="#aaaaaa", bg=self.BG, font=("Segoe UI", 9), takefocus=False)
        number.pack(side="left")
        cross = tk.Button(row, text="×", command=lambda: self._remove(chunk.id),
                          fg="#bbbbbb", bg=self.BG, activebackground=self.FLASH,
                          activeforeground="white", relief="flat", borderwidth=0,
                          highlightthickness=0, takefocus=False, font=("Segoe UI", 13))
        cross.pack(side="right", padx=(4, 6))
        text = tk.Frame(row, bg=self.BG)
        text.pack(side="left", fill="both", expand=True, pady=5)
        preview = tk.Label(text, anchor="w", fg="white", bg=self.BG, font=("Segoe UI", 9), takefocus=False)
        preview.pack(fill="x")
        meta = tk.Frame(text, bg=self.BG)
        meta.pack(fill="x")
        clock = tk.Label(meta, fg="#aaaaaa", bg=self.BG, font=("Segoe UI", 8), takefocus=False)
        clock.pack(side="right")
        source = tk.Label(meta, anchor="w", fg="#aaaaaa", bg=self.BG, font=("Segoe UI", 8), takefocus=False)
        source.pack(side="left", fill="x", expand=True)
        handle.bind("<ButtonPress-1>", lambda e: self._drag_start(e, chunk.id))
        handle.bind("<B1-Motion>", self._drag_motion)
        handle.bind("<ButtonRelease-1>", self._drag_end)
        widgets = dict(frame=row, handle=handle, number=number, cross=cross,
                       preview=preview, source=source, time=clock,
                       short_preview="", short_source="")
        preview.bind("<Configure>", lambda e: _fit_text(preview, widgets["short_preview"]))
        source.bind("<Configure>", lambda e: _fit_text(source, widgets["short_source"]))
        return widgets

    def _remove(self, item_id: str) -> None:
        if self.recording and self.expanded:
            self.on_remove(item_id)

    def _wheel(self, event):
        if self.expanded and event.delta:
            self.canvas.yview_scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1), "units")
            if self._drag_id is not None:
                self._update_drop()
        return "break"

    def _drag_start(self, event, item_id):
        if self.recording and self.expanded:
            self._cancel_drag(restore=False)
            self._cancel_timer("_rows_timer")
            self._drag_id = item_id
            frame = self.rows[item_id]["frame"]
            self._drag_offset = self.canvas.canvasy(event.y_root - self.canvas.winfo_rooty()) - frame.winfo_y()
            frame.config(highlightbackground="#ff6b61")
            frame.lift()
            self.rows[item_id]["handle"].config(fg="#ff6b61")
            self.rows[item_id]["handle"].grab_set()
            self._drag_motion(event)
        return "break"

    def _drag_motion(self, event):
        if self._drag_id is None:
            return "break"
        self._drag_pointer = event.x_root, event.y_root
        direction = self._update_drop()
        if direction and self._scroll_timer is None:
            self._scroll_timer = self.root.after(80, self._drag_scroll)
        elif not direction:
            self._cancel_timer("_scroll_timer")
        return "break"

    def _update_drop(self):
        x, y = self._drag_pointer
        x -= self.canvas.winfo_rootx()
        y -= self.canvas.winfo_rooty()
        height = self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        frame = self.rows[self._drag_id]["frame"]
        # Move the actual masked row with the pointer, keeping the whole row visible.
        frame.place_configure(y=round(max(top, min(self.canvas.canvasy(y) - self._drag_offset,
                                                  top + max(0, height - self.row_height)))))
        frame.lift()
        if not 0 <= x < self.canvas.winfo_width():
            self._drop = None
            self.marker.place_forget()
            self._slide_rows({key: y for key, y in self._row_targets.items() if key != self._drag_id})
            return 0
        clipped = max(0, min(y, height - 1))
        self._drop = max(0, min(len(self.items), int((self.canvas.canvasy(clipped) + self.row_height / 2) // self.row_height)))
        order = [c.id for c in self.items]
        origin = order.index(self._drag_id)
        position = self._drop - (self._drop > origin)
        order.pop(origin)
        order.insert(position, self._drag_id)
        self._slide_rows({key: i * self.row_height for i, key in enumerate(order) if key != self._drag_id})
        line_y = position * self.row_height - top
        self.marker.place(x=0, y=max(0, min(line_y, self.canvas.winfo_height() - 2)), relwidth=1)
        self.marker.lift()
        edge = min(self.row_height // 2, height // 3)
        lo, hi = self.canvas.yview()
        return -1 if y < edge and lo > 0 else (1 if y > height - edge and hi < 1 else 0)

    def _drag_scroll(self):
        self._scroll_timer = None
        if self._drag_id is None:
            return
        direction = self._update_drop()
        if direction:
            self.canvas.yview_scroll(direction, "units")
            self._update_drop()
            self._scroll_timer = self.root.after(80, self._drag_scroll)

    def _cancel_drag(self, restore=True):
        self._cancel_timer("_scroll_timer")
        if self._drag_id in self.rows:
            handle = self.rows[self._drag_id]["handle"]
            if handle.grab_current() is handle:
                handle.grab_release()
            handle.config(fg="#aaaaaa")
            self.rows[self._drag_id]["frame"].config(highlightbackground=self.BG)
        self._drag_id = None
        self.marker.place_forget()
        if restore and self.recording and self.expanded:
            self._slide_rows(self._row_targets)

    def _drag_end(self, event):
        if self._drag_id is not None and self.recording and self.expanded:
            self._drag_pointer = event.x_root, event.y_root
            self._update_drop()  # no extra scroll on release: land exactly at the marker
            item_id = self._drag_id
            origin = next(i for i, c in enumerate(self.items) if c.id == item_id)
            position = self._drop - (self._drop > origin) if self._drop is not None else None
            self._cancel_drag(restore=False)
            if position is not None and origin != position:
                self._slide_rows({**self._layout_targets, item_id: position * self.row_height})
                self.on_move(item_id, position)
            else:
                self._slide_rows(self._row_targets)
        return "break"

    def _set(self, count: int) -> None:
        self.lbl.config(text=f"Gather  {count}")
        self.dot.config(fg="#ff3b30")  # back to recording red after saving()

    def _place(self) -> None:
        r = wintypes.RECT()
        _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA (primary)
        self.win.update_idletasks()
        button = round(28 * self.scale)
        body = min(self.win.winfo_reqwidth(), max(1, r.right - r.left - button - 12))
        height = self.win.winfo_reqheight()
        x, y = (r.left + r.right - body - button) // 2, r.top + 6
        self.win.geometry(f"{body}x{height}+{x}+{y}")
        self.chevron_win.geometry(f"{button}x{height}+{x + body}+{y}")
        if self.expanded:
            width = min(round(420 * self.scale), r.right - r.left - 12)
            panel_x = (r.left + r.right - width) // 2
            panel_y = y + height + 4
            panel_h = min(max(1, min(8, len(self.items))) * self.row_height, r.bottom - panel_y - 6)
            self._panel_geometry = width, max(1, panel_h), panel_x, panel_y
            self._reveal(self._panel_fraction)

    def show(self, count: int) -> None:
        self.recording = True
        self.collapse()
        self._set(count)
        self._place()
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self.chevron_win.deiconify()

    def update(self, count: int) -> None:
        """Keep the pill to its count; copied text belongs in the expanded list."""
        self._set(count)
        self._place()

    def saving(self) -> None:
        """Show 'Saving…' (grey dot, distinct from recording) while an export runs."""
        self.recording = False
        self.collapse()
        self.chevron_win.withdraw()
        self.lbl.config(text="Saving…")
        self.dot.config(fg="#9a9a9a")
        self._place()
        self.win.deiconify()
        self.win.attributes("-topmost", True)

    def hide(self) -> None:
        self.recording = False
        self.collapse()
        self.chevron_win.withdraw()
        self.win.withdraw()
