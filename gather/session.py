"""Session model. Owner: Wave 1 agent B. Signatures are the contract."""
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
        raise NotImplementedError

    def undo(self) -> Chunk | None:
        """Remove and return the last chunk, or None."""
        raise NotImplementedError

    def __len__(self) -> int:
        return len(self.chunks)


def mask_secrets(text: str) -> str:
    """Replace high-confidence secrets with '[masked]'. Conservative: no false
    positives on hashes, UUIDs, base64 blobs."""
    raise NotImplementedError
