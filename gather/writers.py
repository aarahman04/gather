"""Output files. Owner: Wave 1 agent B. Signatures are the contract."""
from datetime import datetime
from pathlib import Path

from .session import Session

FORMATS = ("md", "docx", "pdf")


def render_markdown(session: Session, mask: bool = True, ended: datetime | None = None) -> str:
    """Whole session as Markdown text (also used for 'Copy last as text')."""
    raise NotImplementedError


def write(session: Session, fmt: str, folder: Path, mask: bool = True,
          ended: datetime | None = None) -> Path:
    """Write gather-YYYY-MM-DD-HHMM.<fmt> into folder (created if missing;
    '-2', '-3' suffix on collision). Return the path."""
    raise NotImplementedError
