import pytest

from gather import win32
from live_safety import restore_text


@pytest.fixture(autouse=True)
def preserve_clipboard_text():
    saved = win32._get_clipboard_text()
    try:
        yield
    finally:
        restore_text(saved)
        assert win32._get_clipboard_text() == saved
