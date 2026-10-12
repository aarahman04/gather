"""Entry point: wires the Win32 watcher, session, writers and UI together.

Threads: watcher thread and pystray thread only enqueue events; all state
changes happen on the Tk main thread (drained every 50 ms).
"""
import ctypes
import os
import queue
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path

from . import ui, win32, writers
from .session import Session


class App:
    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.cfg = ui.Config.load()
        self.session: Session | None = None
        self.last_path: Path | None = None
        self.last_text: str | None = None
        self.folder = win32.documents_dir() / "Gather"

        self.root = tk.Tk()
        self.root.withdraw()
        self.pill = ui.Pill(self.root)
        actions = {name: (lambda n=name: self.q.put((n,))) for name in
                   ("toggle", "undo", "cancel", "copy_text", "copy_path",
                    "open_folder", "quit", "autostart")}
        self.tray = ui.Tray(self.cfg, actions)
        self.watcher = win32.ClipboardWatcher(
            on_hotkey=lambda: self.q.put(("toggle",)),
            on_text=lambda text, app, title: self.q.put(("clip", text, app, title, datetime.now())))

    def run(self) -> None:
        self.tray.run_detached()
        self.tray.set_recording(False)
        if not self.watcher.start():
            self.tray.notify("Gather", f"{win32.HOTKEY_LABEL} is taken by another app. "
                                       "Click the tray icon to start/stop instead.")
        self.root.after(50, self.drain)
        self.root.mainloop()

    def drain(self) -> None:
        try:
            while True:
                ev = self.q.get_nowait()
                try:
                    getattr(self, "on_" + ev[0])(*ev[1:])
                except Exception as e:  # one bad event must not kill the app
                    ui.play("error")
                    self.tray.notify("Gather error", str(e)[:200])
        except queue.Empty:
            pass
        self.root.after(50, self.drain)

    # --- events -----------------------------------------------------------
    def on_toggle(self) -> None:
        if self.session is None:
            self.session = Session()
            self.watcher.set_capturing(True)
            self.tray.set_recording(True, 0)
            self.pill.show(0)
            ui.play("start")
        else:
            self.finish()

    def on_clip(self, text, app, title, ts) -> None:
        if self.session is not None and self.session.add(text, app, title, ts):
            self.refresh()

    def on_undo(self) -> None:
        if self.session is not None and self.session.undo() is not None:
            self.refresh()

    def on_cancel(self) -> None:
        if self.session is not None:
            self.end_recording()
            self.tray.notify("Gather", "Session cancelled. Nothing saved.")

    def on_copy_text(self) -> None:
        if self.last_text is None:
            return self.tray.notify("Gather", "No session yet.")
        win32.copy_text_to_clipboard(self.last_text)
        self.tray.notify("Gather", "Last session copied as text.")

    def on_copy_path(self) -> None:
        if self.last_path is None:
            return self.tray.notify("Gather", "No session yet.")
        win32.copy_text_to_clipboard(str(self.last_path))
        self.tray.notify("Gather", "File path copied.")

    def on_open_folder(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.startfile(self.folder)

    def on_autostart(self) -> None:
        if getattr(sys, "frozen", False):
            command = f'"{sys.executable}"'
        else:  # running from source: pythonw -m gather
            pyw = Path(sys.executable).with_name("pythonw.exe")
            command = f'"{pyw}" -m gather'
        ui.set_autostart(not ui.get_autostart(), command)
        self.tray.set_recording(self.session is not None, len(self.session or ()))

    def on_quit(self) -> None:
        if self.session is not None and len(self.session):
            self.finish()  # don't lose an in-progress session
        self.watcher.stop()
        self.tray.stop()
        self.root.destroy()

    # --- helpers ----------------------------------------------------------
    def refresh(self) -> None:
        n = len(self.session)
        self.tray.set_recording(True, n)
        self.pill.update(n)

    def end_recording(self) -> Session:
        session, self.session = self.session, None
        self.watcher.set_capturing(False)
        self.tray.set_recording(False)
        self.pill.hide()
        return session

    def finish(self) -> None:
        session = self.end_recording()
        ui.play("stop")
        if not len(session):
            return self.tray.notify("Gather", "Nothing was copied. No file created.")
        ended = datetime.now()
        path = writers.write(session, self.cfg.format, self.folder, self.cfg.mask, ended)
        self.last_text = writers.render_markdown(session, self.cfg.mask, ended)
        self.last_path = path
        win32.copy_files_to_clipboard([str(path)])
        n = len(session)
        self.tray.notify("Gather", f"{path.name} ({n} item{'s' if n != 1 else ''}) is on your "
                                   "clipboard. Ctrl+V to paste the file.")


def main() -> None:
    # windowed exe has no console: library warnings (fpdf/fontTools) need somewhere to go
    if sys.stderr is None:
        sys.stderr = sys.stdout = open(os.devnull, "w")
    if not win32.single_instance():
        ctypes.windll.user32.MessageBoxW(None, "Gather is already running (see the tray).",
                                         "Gather", 0x40)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor: crisp pill/tray
    except Exception:
        pass
    App().run()


if __name__ == "__main__":
    main()
