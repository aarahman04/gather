from datetime import datetime

import pytest
from docx import Document

from gather.session import Session
from gather.writers import FORMATS, render_markdown, write

T0 = datetime(2026, 10, 13, 14, 30)


def sess():
    s = Session(T0)
    s.add("hello\n  world", "chrome.exe", "Claude", datetime(2026, 10, 13, 14, 31))
    s.add("---\nkey sk-" + "a" * 24, "WindowsTerminal.exe", "", datetime(2026, 10, 13, 14, 33))
    return s


def test_markdown_exact():
    assert render_markdown(sess(), ended=datetime(2026, 10, 13, 14, 45)) == (
        "# Gather — 2026-10-13 14:30–14:45 · 2 items\n\n"
        "## 1 · chrome — Claude · 14:31\n\nhello\n  world\n\n---\n\n"
        "## 2 · WindowsTerminal · 14:33\n\n---\nkey [masked]\n")
    assert "sk-" in render_markdown(sess(), mask=False)


def test_markdown_labels():
    s = Session(T0)
    s.add("x", "", "", T0)
    s.add("y", "a.exe", "T" * 80, T0)
    md = render_markdown(s)
    assert "1 · unknown · 14:30" in md and "a — " + "T" * 59 + "… · 14:30" in md
    s.undo()
    assert "1 item\n" in render_markdown(s)


def test_filename_and_collision(tmp_path):
    e = datetime(2026, 10, 13, 14, 45)
    f = tmp_path / "sub"
    p1, p2, p3 = (write(sess(), "md", f, ended=e) for _ in range(3))
    assert [p.name for p in (p1, p2, p3)] == [
        "gather-2026-10-13-1445.md", "gather-2026-10-13-1445-2.md", "gather-2026-10-13-1445-3.md"]
    assert not p1.read_bytes().startswith(b"\xef\xbb\xbf") and b"\r" not in p1.read_bytes()
    with pytest.raises(ValueError):
        write(sess(), "txt", f)


def test_docx_roundtrip(tmp_path):
    p = write(sess(), "docx", tmp_path)
    paras = [x.text for x in Document(str(p)).paragraphs]
    assert paras[0].startswith("Gather — ") and "hello" in paras and "  world" in paras
    assert "key [masked]" in paras


def test_pdf_basic(tmp_path):
    p = write(sess(), "pdf", tmp_path)
    b = p.read_bytes()
    assert b.startswith(b"%PDF") and len(b) > 2000


@pytest.mark.parametrize("fmt", FORMATS)
def test_stress(tmp_path, fmt):
    s = Session(T0)
    for t in ["emoji 😀👍🏽 é ü", "CJK 你好 日本語 한국어", "a\tb\t\tc", "x" * 5000,
              "word " * 40000, "ctrl \x00\x01\x08\x0b\x1b\x7f end", "→ ─│┌┐ ✓", "\ud800 lone"]:
        s.add(t, "a.exe", "t")
    assert write(s, fmt, tmp_path).stat().st_size > 0
