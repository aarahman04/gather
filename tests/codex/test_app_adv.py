import ctypes

import json

import subprocess

import sys

import threading

import time

from datetime import datetime

from types import SimpleNamespace

import pytest

from docx import Document

from gather import win32, writers

from gather.__main__ import App

from gather.session import Session, mask_secrets

from common import T, SECRET, TRICKY, child, external_text, wait_for


def fake_app(tmp_path):
    app = App.__new__(App)
    app.session = Session(T)
    app.session.add("unsaved synthetic copy", "codex.exe", "synthetic", T)
    app.cfg = SimpleNamespace(format="md", mask=True)
    app.folder = tmp_path
    app.last_path = app.last_text = None
    app.notifications = []
    app.watcher = SimpleNamespace(set_capturing=lambda _: None, stop=lambda: None)
    app.tray = SimpleNamespace(set_recording=lambda *args: None, stop=lambda: None,
                               notify=lambda *args: app.notifications.append(args))
    app.pill = SimpleNamespace(hide=lambda: None, show=lambda _: None, update=lambda _: None)
    app.root = SimpleNamespace(destroy=lambda: None)
    return app


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F1: finish clears session before export")
def test_failed_export_retains_retryable_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    saved = app.session

    def fail(*args):
        raise PermissionError("synthetic disk failure")

    monkeypatch.setattr(writers, "write", fail)
    with pytest.raises(PermissionError):
        app.finish()
    assert app.session is saved
    assert not list(tmp_path.iterdir())


def test_quit_saves_nonempty_session(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    paths = []
    monkeypatch.setattr(win32, "copy_files_to_clipboard", lambda p: paths.extend(p))
    app.on_quit()
    assert app.session is None
    assert len(paths) == 1
    assert "unsaved synthetic copy" in app.last_path.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F6: tray mutates config outside Tk queue")
def test_settings_change_during_export_keeps_copy_consistent(tmp_path, monkeypatch):
    app = fake_app(tmp_path)
    app.session.chunks[0].text = SECRET
    original = writers.write

    def interleave(*args):
        path = original(*args)
        app.cfg.mask = False  # pystray currently mutates this outside the Tk queue
        return path

    monkeypatch.setattr(writers, "write", interleave)
    monkeypatch.setattr(win32, "copy_files_to_clipboard", lambda _: None)
    app.finish()
    assert app.last_text == app.last_path.read_text(encoding="utf-8")


