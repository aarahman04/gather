"""Session model."""
import json
import os
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Chunk:
    text: str
    app: str      # e.g. 'chrome.exe'
    title: str    # foreground window title
    ts: datetime
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    captured: int = 0  # capture order is independent of export order


@dataclass
class Session:
    started: datetime = field(default_factory=datetime.now)
    chunks: list[Chunk] = field(default_factory=list)
    _next_capture: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        for chunk in self.chunks:
            if not chunk.captured:
                chunk.captured = self._next_capture + 1
            self._next_capture = max(self._next_capture, chunk.captured)

    def latest(self) -> Chunk | None:
        return max(self.chunks, key=lambda c: c.captured, default=None)

    def get(self, item_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == item_id), None)

    def add(self, text: str, app: str, title: str, ts: datetime | None = None) -> bool:
        """Append unless blank or identical to the latest remaining capture."""
        text = text.replace("\r\n", "\n")
        latest = self.latest()
        if not text.strip() or (latest and latest.text == text):
            return False
        self._next_capture += 1
        self.chunks.append(Chunk(text, app, title, ts or datetime.now(), captured=self._next_capture))
        return True

    def undo(self) -> Chunk | None:
        """Remove the latest remaining capture, regardless of display order."""
        latest = self.latest()
        return self.remove(latest.id) if latest else None

    def remove(self, item_id: str) -> Chunk | None:
        chunk = self.get(item_id)
        if chunk is not None:
            self.chunks.remove(chunk)
        return chunk

    def move(self, item_id: str, position: int) -> bool:
        """Move to a final, zero-based position; invalid requests leave the list alone."""
        chunk = self.get(item_id)
        if chunk is None or type(position) is not int or not 0 <= position < len(self.chunks):
            return False
        self.chunks.remove(chunk)
        self.chunks.insert(position, chunk)
        return True

    def snapshot(self) -> "Session":
        return deepcopy(self)

    def __len__(self) -> int:
        return len(self.chunks)


_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
    # any sk- token, except lowercase word-slugs like sk-learn-feature-selection-guide
    r"|\bsk-(?![a-z]{1,15}(?:-[a-z]{1,15})+(?![A-Za-z0-9_-]))[A-Za-z0-9_-]{20,}"
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


class Journal:
    """Append-only on-disk copy of one session, so a crash, forced exit or failed
    export never loses collected text. One JSON line per event; deleted once the
    session's file is safely written."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, folder: Path, session: Session) -> "Journal":
        """New journal file in folder (created if missing), unique per session,
        header line holds session.started."""
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session.started:%Y%m%d-%H%M%S-%f}.jsonl"
        j = cls(path.with_suffix(".tmp"))
        try:
            j._write({"started": session.started.isoformat()}, "w")
            for c in session.chunks:  # a resumed session keeps what it already had
                j.append(c)
            os.replace(j.path, path)  # publish only a complete backup of the resumed list
        except OSError:
            try:
                j.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        j.path = path
        return j

    def _write(self, rec: dict, mode: str = "a") -> None:
        # surrogatepass: clipboard text can hold lone surrogates; keep it byte-exact
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        if mode == "a":
            line = "\n" + line  # isolate a new event from a previous partially failed write
        with open(self.path, mode, encoding="utf-8", errors="surrogatepass") as f:
            f.write(line)  # closing flushes to the OS, which survives a process kill

    def append(self, chunk: Chunk) -> None:
        """Write the chunk as one line and flush (survives process kill)."""
        self._write({"text": chunk.text, "app": chunk.app, "title": chunk.title,
                     "ts": chunk.ts.isoformat(), "id": chunk.id, "captured": chunk.captured})

    def undo(self, item_id: str | None = None) -> None:
        """Record removal of the latest remaining capture (also reads old journals)."""
        self._write({"undo": True, **({"id": item_id} if item_id is not None else {})})

    def remove(self, item_id: str) -> None:
        self._write({"remove": item_id})

    def move(self, item_id: str, position: int) -> None:
        self._write({"move": item_id, "position": position})

    def delete(self) -> None:
        """Remove the file; missing file is fine."""
        self.path.unlink(missing_ok=True)

    @staticmethod
    def recover(folder: Path) -> list[tuple["Journal", Session]]:
        """Every journal left in folder, replayed into a Session (undo applied).
        A truncated/corrupt line is skipped; an unreadable file yields an
        empty Session, never an exception. Missing folder -> []."""
        out = []
        for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
            session = Session()
            try:
                for line in path.read_text(encoding="utf-8", errors="surrogatepass").split("\n"):
                    try:
                        rec = json.loads(line)
                        if not isinstance(rec, dict):
                            continue
                        if "started" in rec:
                            session.started = datetime.fromisoformat(rec["started"])
                        elif rec.get("undo"):
                            session.remove(rec["id"]) if "id" in rec else session.undo()
                        elif "remove" in rec:
                            session.remove(rec["remove"])
                        elif "move" in rec:
                            session.move(rec["move"], rec["position"])
                        else:
                            chunk = Chunk(rec["text"], rec["app"], rec["title"],
                                          datetime.fromisoformat(rec["ts"]))
                            if not all(isinstance(x, str) for x in (chunk.text, chunk.app, chunk.title)):
                                continue
                            chunk.id = rec.get("id", chunk.id)
                            captured = rec.get("captured", 0)
                            if not isinstance(chunk.id, str) or not chunk.id or session.get(chunk.id):
                                continue
                            if type(captured) is not int or captured < 0:
                                continue
                            chunk.captured = captured or session._next_capture + 1
                            session._next_capture = max(session._next_capture, chunk.captured)
                            session.chunks.append(chunk)
                    except (ValueError, KeyError, TypeError):
                        continue  # torn write from a crash
            except OSError:
                pass
            out.append((Journal(path), session))
        return out
