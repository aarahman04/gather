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


MASK_TABLE = [
    ("openai", SECRET, "[masked]"),
    ("anthropic", "sk-ant-api03-" + "Ab1_-" * 10, "[masked]"),
    ("project", "sk-proj-" + "A_1-" * 20, "[masked]"),
    ("github", "ghp_" + "A1" * 18, "[masked]"),
    ("github_pat", "github_pat_" + "Z9_" * 20, "[masked]"),
    ("aws", "AKIA" + "ABCDEFGH12345678", "[masked]"),
    ("slack", "xoxb-1234567890-abcdef", "[masked]"),
    ("google", "AIza" + "a" * 35, "[masked]"),
    ("pem", "-----BEGIN OPENSSH PRIVATE KEY-----\nMIIabc\ndef==\n-----END OPENSSH PRIVATE KEY-----", "[masked]"),
    ("git_sha", "9fceb02d0ae598e95dc970b74767f19372d61af8", None),
    ("uuid", "123e4567-e89b-12d3-a456-426614174000", None),
    ("sha256", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", None),
    ("base64", "TWFuIGlzIGRpc3Rpbmd1aXNoZWQsIG5vdCBvbmx5IGJ5IGhpcyByZWFzb24=", None),
    ("task", "task-management-system-for-everyone-today", None),
    ("sk_learn", "pip install sk-learn", None),
]


@pytest.mark.parametrize("fmt", ["md", "docx", pytest.param("pdf", marks=pytest.mark.xfail(
    strict=True, raises=subprocess.TimeoutExpired, reason="F10: unbroken 5MB PDF exceeded 120s"))])
def test_5mb_writes_every_format(tmp_path, fmt):
    # Bound the large PDF/font workload in a child process, away from the desktop app.
    subprocess.run([sys.executable, "-c", """
import sys
from datetime import datetime
from pathlib import Path
from gather.session import Session
from gather.writers import write
s = Session(datetime(2026,10,13,14,30))
s.add('0123456789abcdef' * (5*1024*1024//16),'codex.exe','synthetic')
write(s, sys.argv[1], Path(sys.argv[2]))
""", fmt, str(tmp_path)], check=True, capture_output=True, timeout=30 if fmt == "pdf" else 120)
    paths = list(tmp_path.glob(f"*.{fmt}"))
    assert len(paths) == 1 and paths[0].stat().st_size > 0
    if fmt == "md":
        assert "0123456789abcdef" * (5 * 1024 * 1024 // 16) in paths[0].read_text(encoding="utf-8")
    elif fmt == "docx":
        assert any(len(p.text) == 5 * 1024 * 1024 for p in Document(str(paths[0])).paragraphs)
    else:
        assert paths[0].read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("name,text,expected", MASK_TABLE, ids=[r[0] for r in MASK_TABLE])
def test_masking_table(name, text, expected):
    assert mask_secrets(text) == (text if expected is None else expected)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F8: long sk-learn slug is masked")
def test_public_sk_learn_slug_is_not_masked():
    slug = "sk-learn-feature-selection-guide"
    assert mask_secrets(slug) == slug


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.parametrize("name", TRICKY)
def test_tricky_input_writes_every_format(tmp_path, name, fmt):
    session = Session(T)
    session.add(TRICKY[name], "codex.exe", "synthetic", T)
    path = writers.write(session, fmt, tmp_path, ended=T)
    assert path.stat().st_size > 0
    clean = TRICKY[name].replace("\r\n", "\n").encode("utf-8", "replace").decode()
    if fmt == "md":
        assert clean in path.read_bytes().decode("utf-8")
    elif fmt == "docx":
        text = "\n".join(p.text for p in Document(str(path)).paragraphs)
        # Word represents a bare CR as a paragraph/run line break.
        assert writers._ILLEGAL.sub("", clean).replace("\r", "\n") in text
    else:
        assert path.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("fmt", writers.FORMATS)
def test_sequential_filename_collisions_preserve_files(tmp_path, fmt):
    paths = []
    for i in range(3):
        session = Session(T)
        session.add(f"copy-{i}", "codex.exe", "synthetic", T)
        paths.append(writers.write(session, fmt, tmp_path, ended=T))
    assert len(set(paths)) == 3
    assert [p.name for p in paths] == [f"gather-2026-10-13-1430{s}.{fmt}" for s in ("", "-2", "-3")]
    if fmt == "md":
        assert all(f"copy-{i}" in p.read_text(encoding="utf-8") for i, p in enumerate(paths))
    elif fmt == "docx":
        assert all(f"copy-{i}" in "\n".join(x.text for x in Document(str(p)).paragraphs)
                   for i, p in enumerate(paths))
    else:
        assert all(p.read_bytes().startswith(b"%PDF") for p in paths)


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="F2: title bypasses masking")
def test_masking_also_covers_window_titles(tmp_path, fmt, monkeypatch):
    session = Session(T)
    session.add("ordinary text", "codex.exe", f"key={SECRET}", T)
    pdf_text = []
    if fmt == "pdf":
        from fpdf import FPDF
        original = FPDF.multi_cell

        def record(pdf, width, height, text, **kwargs):
            pdf_text.append(text)
            return original(pdf, width, height, text, **kwargs)

        monkeypatch.setattr(FPDF, "multi_cell", record)
    path = writers.write(session, fmt, tmp_path, mask=True, ended=T)
    if fmt == "md":
        text = path.read_text(encoding="utf-8")
    elif fmt == "docx":
        text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    else:
        text = "\n".join(pdf_text)
        assert path.read_bytes().startswith(b"%PDF")
    assert SECRET not in text


@pytest.mark.parametrize("fmt", writers.FORMATS)
@pytest.mark.parametrize("title", ["surrogate", "control"])
def test_illegal_title_does_not_break_export(tmp_path, fmt, title, request):
    if title == "surrogate" and fmt in ("md", "docx"):
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=UnicodeEncodeError,
                                                reason="F3: title bypasses surrogate cleanup"))
    if title == "control" and fmt == "docx":
        request.node.add_marker(pytest.mark.xfail(strict=True, raises=ValueError,
                                                reason="F3: title bypasses XML cleanup"))
    session = Session(T)
    session.add("ordinary text", "codex.exe", "title " + ("\ud800" if title == "surrogate" else "\x01"), T)
    path = writers.write(session, fmt, tmp_path, ended=T)
    assert path.stat().st_size > 0
    if fmt == "docx":
        Document(str(path))
    elif fmt == "pdf":
        assert path.read_bytes().startswith(b"%PDF")


