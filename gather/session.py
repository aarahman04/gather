"""Session model."""
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Chunk:
    text: str
    app: str      # e.g. 'chrome.exe'
    title: str    # foreground window title
    ts: datetime


@dataclass
class Session:
    started: datetime = field(default_factory=datetime.now)
    chunks: list[Chunk] = field(default_factory=list)

    def add(self, text: str, app: str, title: str, ts: datetime | None = None) -> bool:
        """Append. Return False (ignored) if text is blank or identical to the last chunk."""
        text = text.replace("\r\n", "\n")
        if not text.strip() or (self.chunks and self.chunks[-1].text == text):
            return False
        self.chunks.append(Chunk(text, app, title, ts or datetime.now()))
        return True

    def undo(self) -> Chunk | None:
        """Remove and return the last chunk, or None."""
        return self.chunks.pop() if self.chunks else None

    def __len__(self) -> int:
        return len(self.chunks)


_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bxox[abposr]-[A-Za-z0-9-]{10,}"
    r"|\bAIza[0-9A-Za-z_-]{35}",
    re.S,
)


def mask_secrets(text: str) -> str:
    """Replace high-confidence secrets with '[masked]'. Conservative: no false
    positives on hashes, UUIDs, base64 blobs."""
    return _SECRET.sub("[masked]", text)
