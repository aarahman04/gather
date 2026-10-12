from datetime import datetime

import pytest

from gather.session import Session, mask_secrets

T = datetime(2026, 10, 13, 14, 30)


def test_add_dedupe_blank_undo_crlf():
    s = Session(T)
    assert s.add("a\r\nb", "x.exe", "t")
    assert s.chunks[0].text == "a\nb"
    assert not s.add("a\nb", "x.exe", "t")  # consecutive dup
    assert not s.add("  \n\t", "x.exe", "t")
    assert s.add("c", "x", "t") and s.add("a\nb", "x", "t")  # non-consecutive ok
    assert len(s) == 3
    assert s.undo().text == "a\nb" and len(s) == 2
    assert Session(T).undo() is None


def test_add_preserves_whitespace_and_ts():
    s = Session(T)
    s.add("  indented\n", "a", "b", T)
    assert s.chunks[0].text == "  indented\n" and s.chunks[0].ts == T


@pytest.mark.parametrize("secret", [
    "sk-" + "a" * 24, "sk-ant-api03-" + "Ab1_-" * 5, "sk-proj-" + "x" * 30,
    "ghp_" + "A1" * 18, "gho_" + "b" * 36, "github_pat_" + "Z9_" * 10,
    "AKIA" + "ABCDEFGH12345678", "xoxb-1234567890-abcdef", "AIza" + "a" * 35,
])
def test_masks_secrets(secret):
    assert mask_secrets(f"key={secret} end") == "key=[masked] end"


def test_masks_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\ndef==\n-----END RSA PRIVATE KEY-----"
    assert mask_secrets(f"a\n{pem}\nb") == "a\n[masked]\nb"


@pytest.mark.parametrize("safe", [
    "9fceb02d0ae598e95dc970b74767f19372d61af8",
    "123e4567-e89b-12d3-a456-426614174000",
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "TWFuIGlzIGRpc3Rpbmd1aXNoZWQsIG5vdCBvbmx5IGJ5IGhpcyByZWFzb24=",
    "pip install sk-learn", "task-management-system-for-everyone-today",
    "ghp_short", "AKIAshort",
])
def test_keeps_non_secrets(safe):
    assert mask_secrets(safe) == safe
