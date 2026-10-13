"""Entry point: wires the Win32 watcher, session, writers and UI together.

Threads: watcher thread, pystray thread and the export worker only enqueue
events; all state changes happen on the Tk main thread (drained every 50 ms).
"""
import ctypes
import os
import queue
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import ui, win32, writers
from .session import Journal, Session


class App:
    def __init__(self) -> None:
        self.q: queue.Queue = queue.Queue()
        self.cfg = ui.Config.load()
        self.session: Session | None = None
        self.journal: Journal | None = None  # on-disk copy of self.session
        self.last_path: Path | None = None
        self.last_text: str | None = None
        self.folder = win32.documents_dir() / "Gather"
        self.journal_dir = ui.Config.path().parent / "unsaved"
        self.journal_warned = False
        # one worker: exports run off the Tk thread, one at a time (no output-filename races)
        self.exports = ThreadPoolExecutor(1)
        self.pending = 0  # exports submitted but not yet handled on the Tk thread
        self.quitting = False
        self.closed = False

        self.root = tk.Tk()
        self.root.withdraw()
        self.pill = ui.Pill(self.root,
                           on_remove=lambda item_id: self.q.put(("remove", item_id)),
                           on_move=lambda item_id, position: self.q.put(("move", item_id, position)))
        actions = {name: (lambda n=name: self.q.put((n,))) for name in
                   ("toggle", "undo", "cancel", "copy_text", "copy_path",
                    "open_folder", "quit", "autostart")}
        actions["hotkey"] = lambda name: self.q.put(("hotkey_change", name))
        actions["mouse_trigger"] = lambda name: self.q.put(("mouse_trigger_change", name))
        self.tray = ui.Tray(self.cfg, actions)
        self.watcher = win32.ClipboardWatcher(
            on_hotkey=lambda: self.q.put(("toggle",)),
            on_text=lambda text, app, title: self.q.put(("clip", text, app, title, datetime.now())),
            hotkey=self.cfg.hotkey, mouse_trigger=self.cfg.mouse_trigger,
            on_undo=lambda: self.q.put(("undo",)))

    def run(self) -> None:
        self.tray.run_detached()
        self.tray.set_recording(False)
        if not self.watcher.start():
            self.tray.hotkey_ok = False
            self.tray.set_recording(False)
            self.tray.notify("Gather", f"{self.cfg.hotkey} is used by another app. Pick another in "
                                       "the tray (Hotkey), or click the tray icon to start/stop.")
        if self.watcher.mouse_trigger != self.cfg.mouse_trigger:
            try:
                self.mouse_trigger_failed()
            except OSError as e:
                self.tray.notify("Gather error", f"Mouse trigger is Off, but couldn't save the setting ({e}).")
        self.recover()
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
        if not self.closed:
            self.root.after(50, self.drain)

    # --- events -----------------------------------------------------------
    def on_toggle(self) -> None:
        if self.quitting:
            return
        if self.session is None:
            self.session = Session()
            self.journal = self.new_journal(self.session)
            self.start_capturing()
            self.tray.set_recording(True, 0)
            self.pill.set_items(self.session.chunks)
            self.pill.show(0)
            ui.play("start")
        else:
            self.finish()

    def on_clip(self, text, app, title, ts) -> None:
        if self.session is not None and self.session.add(text, app, title, ts):
            self.jot("append", self.session.chunks[-1])
            self.refresh(ui.preview_text(self.session.chunks[-1].text))

    def on_undo(self) -> None:
        if self.session is None:
            return
        if not self.session.chunks:
            return self.refresh("Nothing to undo")
        # Persist first: a crash immediately after popping must not resurrect this item.
        if not self.persist_edit("undo", self.session.latest().id):
            return
        removed = self.session.undo()
        self.refresh(f"Removed: {ui.preview_text(removed.text)}")

    def on_remove(self, item_id: str) -> None:
        if self.session is None or (chunk := self.session.get(item_id)) is None:
            return
        if not self.persist_edit("remove", item_id):
            return
        self.session.remove(item_id)
        self.refresh(f"Removed: {ui.preview_text(chunk.text)}")

    def on_move(self, item_id: str, position: int) -> None:
        if self.session is None or (chunk := self.session.get(item_id)) is None:
            return
        if type(position) is not int or not 0 <= position < len(self.session):
            return
        if self.session.chunks.index(chunk) == position:
            return
        if not self.persist_edit("move", item_id, position):
            return
        self.session.move(item_id, position)
        self.refresh()

    def persist_edit(self, op: str, *args) -> bool:
        try:
            if self.journal is None:
                self.journal = Journal.create(self.journal_dir, self.session)
            getattr(self.journal, op)(*args)
        except OSError:
            self.tray.notify("Gather", "Couldn't save the edit record. Item kept; order unchanged. Try again.")
            return False
        return True

    def on_cancel(self) -> None:
        if self.session is not None:
            self.drop(self.end_recording()[1])
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

    def on_hotkey_change(self, name: str) -> None:
        if name == self.cfg.hotkey and self.watcher.hotkey == name:
            return
        if self.watcher.set_hotkey(name):
            self.cfg.hotkey = name
            self.tray.hotkey_ok = True
            self.tray.set_recording(self.session is not None, len(self.session or ()))
            self.cfg.save()
            self.tray.notify("Gather", f"Hotkey is now {name}.")
            return
        elif self.watcher.hotkey:
            self.tray.notify("Gather", f"Couldn't register {name} (in use or Gather is busy) — "
                                       f"still using {self.watcher.hotkey}.")
        else:
            self.tray.notify("Gather", f"Couldn't register {name} (in use or Gather is busy). Pick another, "
                                       "or click the tray icon to start/stop.")
        self.tray.hotkey_ok = self.watcher.hotkey is not None
        self.tray.set_recording(self.session is not None, len(self.session or ()))

    def mouse_trigger_failed(self) -> None:
        self.cfg.mouse_trigger = self.watcher.mouse_trigger
        self.tray.set_recording(self.session is not None, len(self.session or ()))
        self.tray.notify("Gather", "Couldn't enable the mouse trigger. Mouse trigger is "
                         f"{self.cfg.mouse_trigger}; the keyboard hotkey and tray still work.")
        self.cfg.save()

    def on_mouse_trigger_change(self, name: str) -> None:
        if name == self.cfg.mouse_trigger and self.watcher.mouse_trigger == name:
            return
        if not self.watcher.set_mouse_trigger(name):
            self.mouse_trigger_failed()
            return
        self.cfg.mouse_trigger = self.watcher.mouse_trigger
        self.tray.set_recording(self.session is not None, len(self.session or ()))
        self.cfg.save()
        self.tray.notify("Gather", f"Mouse trigger is now {ui.MOUSE_TRIGGERS[name]}.")

    def on_quit(self) -> None:
        if self.session is not None:
            if len(self.session):
                self.finish()  # don't lose an in-progress session
            else:
                self.drop(self.end_recording()[1])
        if self.pending:  # quit once the last export has been handled
            self.quitting = True
            self.show_pill()
        else:
            self.close()

    def on_exported(self, session, journal, path, text, recovery) -> None:
        try:
            self.last_path, self.last_text = path, text
            self.drop(journal)
            n = _items(session)
            if recovery:  # at login: don't clobber the user's clipboard
                return self.tray.notify(
                    "Gather", f"Recovered {n} from a session that didn't finish: {path.name}")
            try:
                win32.copy_files_to_clipboard([str(path)])
            except OSError:
                return self.tray.notify("Gather", f"Saved {path.name}, but the clipboard was busy. "
                                                  "Use tray → Copy last file path.")
            self.tray.notify("Gather", f"{path.name} ({n}) is on your clipboard. "
                                       "Ctrl+V to paste the file.")
        finally:
            self.export_done()

    def on_export_failed(self, session, journal, err, recovery) -> None:
        try:
            ui.play("error")
            why, n = (str(err) or type(err).__name__)[:120], _items(session)
            if self.session is None and not recovery and not self.quitting:
                # F1: resume the same session; its journal already holds the chunks
                self.session, self.journal = session, journal or self.new_journal(session)
                self.start_capturing()
                self.tray.set_recording(True, len(session))
                self.pill.set_items(session.chunks)
                self.pill.show(len(session))
                retry = (f"press {self.watcher.hotkey} or click the tray icon"
                         if self.watcher.hotkey else "click the tray icon")
                self.tray.notify("Gather", f"Couldn't save ({why}). Your {n} are kept and Gather is "
                                           f"still recording — {retry} to try "
                                           "again, or change the format in the tray.")
            else:  # the journal stays on disk; recover() exports it next start
                self.tray.notify("Gather", f"Couldn't save ({why}). Your {n} are kept and will be "
                                           "saved next time Gather starts.")
        finally:
            self.export_done()

    # --- helpers ----------------------------------------------------------
    def recover(self) -> None:
        """Export sessions a crash or forced exit left in the journal folder."""
        for journal, session in Journal.recover(self.journal_dir):
            if len(session):
                self.export(session, journal, recovery=True)
            else:
                self.drop(journal)

    def start_capturing(self) -> None:
        self.tray.undo_ok = self.watcher.set_capturing(True)
        if not self.tray.undo_ok:
            self.tray.notify("Gather", "Couldn't register Alt+Z (in use or Gather is busy). "
                                      "Use tray → Remove last item to undo.")

    def refresh(self, message: str = "") -> None:
        n = len(self.session)
        self.tray.set_recording(True, n)
        self.pill.set_items(self.session.chunks)
        self.pill.update(n, message)

    def show_pill(self) -> None:
        if self.session is None:  # a recording's pill wins over "Saving…"
            self.pill.saving() if self.pending else self.pill.hide()

    def end_recording(self) -> tuple[Session, Journal | None]:
        session, journal = self.session.snapshot(), self.journal
        self.session = self.journal = None
        self.watcher.set_capturing(False)
        self.tray.set_recording(False)
        self.show_pill()
        return session, journal

    def finish(self) -> None:
        session, journal = self.end_recording()
        ui.play("stop")
        if not len(session):
            self.drop(journal)
            return self.tray.notify("Gather", "Nothing was copied. No file created.")
        self.export(session, journal)

    def export(self, session: Session, journal: Journal | None, recovery: bool = False) -> None:
        """Write on the worker thread; the result comes back through self.q."""
        # F6: snapshot settings now; the tray may change cfg while the worker runs
        fmt, mask, folder = self.cfg.format, self.cfg.mask, self.folder
        ended = session.latest().ts if recovery else datetime.now()

        def work() -> None:
            try:
                path = writers.write(session, fmt, folder, mask, ended)
                text = writers.render_markdown(session, mask, ended)
            except Exception as e:
                self.q.put(("export_failed", session, journal, e, recovery))
            else:
                self.q.put(("exported", session, journal, path, text, recovery))

        self.pending += 1
        self.show_pill()
        self.exports.submit(work)

    def export_done(self) -> None:
        self.pending -= 1
        self.show_pill()
        if self.quitting and not self.pending:
            self.close()

    def close(self) -> None:
        self.closed = True
        self.watcher.stop()
        self.tray.stop()
        self.root.destroy()

    # Journal disk errors must never break capture: warn once, keep the session in memory.
    def journal_error(self, e: OSError) -> None:
        if not self.journal_warned:
            self.journal_warned = True
            self.tray.notify("Gather", f"Couldn't write the backup copy ({e}). Still recording; "
                                       "this session is only in memory until it's saved.")

    def new_journal(self, session: Session) -> Journal | None:
        try:
            return Journal.create(self.journal_dir, session)
        except OSError as e:
            self.journal_error(e)
            return None

    def jot(self, op: str, *args) -> None:
        if self.journal is None:
            return
        try:
            getattr(self.journal, op)(*args)
        except OSError as e:
            self.journal_error(e)
            self.drop(self.journal)  # a journal with a gap would replay wrong; memory is the copy now
            self.journal = None

    def drop(self, journal: Journal | None) -> None:
        if journal is not None:
            try:
                journal.delete()
            except OSError as e:
                self.journal_error(e)


def _items(session: Session) -> str:
    n = len(session)
    return f"{n} item{'s' if n != 1 else ''}"


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
