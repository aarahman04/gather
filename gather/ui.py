"""Tray, recording pill, sounds, config, autostart. Owner: Wave 1 agent C.
Signatures are the contract."""
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Config:
    format: str = "md"      # 'md' | 'docx' | 'pdf'
    mask: bool = True

    @staticmethod
    def path() -> Path:
        """%APPDATA%\Gather\config.json"""
        raise NotImplementedError

    @classmethod
    def load(cls) -> "Config":
        """Missing/corrupt file -> defaults."""
        raise NotImplementedError

    def save(self) -> None:
        raise NotImplementedError


def get_autostart() -> bool:
    raise NotImplementedError


def set_autostart(on: bool, command: str) -> None:
    """HKCU\Software\Microsoft\Windows\CurrentVersion\Run value 'Gather'."""
    raise NotImplementedError


def play(kind: str) -> None:
    """kind: 'start' | 'stop' | 'error'. Non-blocking."""
    raise NotImplementedError


class Tray:
    """pystray icon. Callbacks fire on pystray's thread."""

    def __init__(self, cfg: Config, actions: dict[str, Callable[[], None]]) -> None:
        """actions keys: 'toggle', 'undo', 'cancel', 'copy_text', 'copy_path',
        'open_folder', 'quit', 'autostart'. Left-click = 'toggle'.
        Format radio + mask checkbox edit cfg and cfg.save() directly."""
        raise NotImplementedError

    def run_detached(self) -> None:
        raise NotImplementedError

    def set_recording(self, on: bool, count: int = 0) -> None:
        """Grey icon idle / red recording; tooltip shows count."""
        raise NotImplementedError

    def notify(self, title: str, message: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class Pill:
    """Small always-on-top, no-focus, click-through overlay at top-right:
    '● Gather 4'. Lives on the Tk main thread."""

    def __init__(self, root: tk.Tk) -> None:
        raise NotImplementedError

    def show(self, count: int) -> None:
        raise NotImplementedError

    def update(self, count: int) -> None:
        """Set count and briefly pulse."""
        raise NotImplementedError

    def hide(self) -> None:
        raise NotImplementedError
