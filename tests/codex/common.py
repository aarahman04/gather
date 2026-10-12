"""Shared fixtures/helpers for Codex adversarial tests."""
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



T = datetime(2026, 10, 13, 14, 30)


SECRET = "sk-" + "a" * 24  # synthetic, never a real credential


TRICKY = {
    "surrogates": "high \ud800 low \udfff end",
    "rtl": "English العربية עברית \u202eRTL\u202c",
    "zero_width": "a\u200bb\u200cc\u200dd\ufeffe",
    "crlf": "first\r\nsecond\nthird\rbare",
    "controls": "x\x00\x01\x08\x0b\x1b\ufffe\uffff end",
    "emoji_cjk": "😀👍🏽 你好 日本語 한국어 é e\u0301",
    "unbroken": "x" * 10000,
}


def child(code, *args, **kwargs):
    return subprocess.run([sys.executable, "-c", code, *args], check=True,
                          timeout=30, capture_output=True, text=True, **kwargs)


def external_text(text):
    child("import sys,json; from gather import win32 as w; "
          "t=json.load(sys.stdin); w._open_clipboard(); "
          "w.user32.EmptyClipboard(); "
          "w._set_global(w.CF_UNICODETEXT,(t+'\\0').encode('utf-16-le','surrogatepass')); "
          "w.user32.CloseClipboard()", input=json.dumps(text))


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


