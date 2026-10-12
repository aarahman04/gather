"""Win32 layer (ctypes only, no pywin32).

Owner: Wave 1 agent A. Signatures below are the contract; do not change them.
"""
from pathlib import Path
from typing import Callable

# Ctrl+Alt+G
HOTKEY_LABEL = "Ctrl+Alt+G"


class ClipboardWatcher:
    """Hidden message-only window on its own daemon thread.

    Receives WM_HOTKEY (Ctrl+Alt+G) and WM_CLIPBOARDUPDATE.
    Callbacks run on the watcher thread; keep them cheap (caller enqueues).
    """

    def __init__(self, on_hotkey: Callable[[], None],
                 on_text: Callable[[str, str, str], None]) -> None:
        """on_text(text, app, title): app = foreground process exe name
        (e.g. 'chrome.exe'), title = foreground window title at copy time."""
        raise NotImplementedError

    def start(self) -> bool:
        """Start thread, block until window exists. Return True if the
        hotkey registered, False if taken (listener still runs)."""
        raise NotImplementedError

    def set_capturing(self, on: bool) -> None:
        """When False, clipboard updates are ignored (clipboard not read)."""
        raise NotImplementedError

    def stop(self) -> None:
        """Unregister hotkey + listener, destroy window, end thread."""
        raise NotImplementedError


def copy_files_to_clipboard(paths: list[str], text: str | None = None) -> None:
    """Put files on clipboard as CF_HDROP + 'Preferred DropEffect' = COPY.
    If text is given, also set CF_UNICODETEXT. Must never be seen by
    ClipboardWatcher as a capture."""
    raise NotImplementedError


def copy_text_to_clipboard(text: str) -> None:
    """Set CF_UNICODETEXT. Must never be seen by ClipboardWatcher as a capture."""
    raise NotImplementedError


def documents_dir() -> Path:
    """Real Documents folder via SHGetKnownFolderPath (handles OneDrive redirect)."""
    raise NotImplementedError


def single_instance(name: str = "Gather") -> bool:
    """Named mutex. False if another instance already holds it."""
    raise NotImplementedError
