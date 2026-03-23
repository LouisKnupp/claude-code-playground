"""Zoom transcript connector.

Reads .vtt and .txt transcript files from a local directory.
- Strips WebVTT formatting (headers, cue timestamps, cue indices)
- Retains speaker labels and first-cue timestamp in metadata
- Derives meeting title and date from the parent folder name
  (format: "YYYY-MM-DD HH.MM.SS Meeting Name")
- Skips chat log files (*chat*.txt) — sidebar chat exports, not transcripts
- deep_link is a file:// URI clickable in macOS terminal and Finder
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from playground.connectors.base import DataConnector
from playground.connectors import registry
from playground.core.exceptions import ConnectorError
from playground.core.models import Document

# Matches lines like: 00:01:23.456 --> 00:01:45.789
_TIMESTAMP_LINE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}")
# Matches speaker labels like: "John Smith: " or "SPEAKER 1: "
_SPEAKER_LINE = re.compile(r"^([A-Za-z][^:\n]{0,60}):\s+(.+)")
# Matches bare cue index lines (digits only)
_CUE_INDEX = re.compile(r"^\d+$")
# Matches Zoom folder names: "YYYY-MM-DD HH.MM.SS Meeting Name"
_FOLDER_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2}) (.+)$")


def _parse_vtt(text: str) -> tuple[str, dict]:
    """Parse a .vtt file into (clean_text, metadata).

    Returns:
        clean_text: plain prose suitable for FTS indexing
        metadata: {speakers: list[str], first_timestamp: str}
    """
    lines = text.splitlines()
    clean: list[str] = []
    speakers: set[str] = set()
    first_ts: str = ""

    for line in lines:
        line = line.strip()
        if not line or line == "WEBVTT" or _CUE_INDEX.match(line):
            continue
        if _TIMESTAMP_LINE.match(line):
            if not first_ts:
                first_ts = line.split("-->")[0].strip()
            continue
        m = _SPEAKER_LINE.match(line)
        if m:
            speaker, rest = m.group(1).strip(), m.group(2).strip()
            speakers.add(speaker)
            clean.append(f"{speaker}: {rest}")
        else:
            clean.append(line)

    return "\n".join(clean), {"speakers": sorted(speakers), "first_timestamp": first_ts}


def _parse_txt(text: str) -> tuple[str, dict]:
    """Plain .txt files — return as-is, extract speaker labels if present."""
    clean: list[str] = []
    speakers: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _SPEAKER_LINE.match(line)
        if m:
            speakers.add(m.group(1).strip())
        clean.append(line)
    return "\n".join(clean), {"speakers": sorted(speakers), "first_timestamp": ""}


def _meeting_title_and_date(path: Path, transcripts_dir: Path) -> tuple[str, str]:
    """Derive a human-readable title and ISO date string from the Zoom folder name.

    Zoom saves recordings in folders named "YYYY-MM-DD HH.MM.SS Meeting Name".
    Falls back to the filename stem for files not in a dated subfolder.
    """
    try:
        rel = path.relative_to(transcripts_dir)
        folder = rel.parts[0] if len(rel.parts) > 1 else None
    except ValueError:
        folder = None

    if folder:
        m = _FOLDER_DATE_RE.match(folder)
        if m:
            date_str, hh, mm, ss, name = m.groups()
            meeting_date = f"{date_str} {hh}:{mm}:{ss}"
            return name.strip(), meeting_date

    # Fallback for files directly in the transcripts root
    mtime = path.stat().st_mtime
    meeting_date = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    return path.stem.replace("_", " ").replace("-", " "), meeting_date


def _is_chat_log(path: Path) -> bool:
    """Return True for Zoom chat export files, which are not transcripts."""
    return path.suffix.lower() == ".txt" and "chat" in path.stem.lower()


class ZoomConnector:
    source_type = "zoom"
    display_name = "Zoom Transcripts"

    def __init__(self, transcripts_dir: Path) -> None:
        self._dir = Path(transcripts_dir).expanduser()

    def _load_file(self, path: Path) -> Document:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".vtt":
            content_text, meta = _parse_vtt(raw)
        else:
            content_text, meta = _parse_txt(raw)

        title, meeting_date = _meeting_title_and_date(path, self._dir)
        meta["filename"] = path.name
        meta["meeting_date"] = meeting_date

        source_id = str(path.resolve())
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()
        doc_id = hashlib.sha256(f"{source_id}{content_hash}".encode()).hexdigest()

        return Document(
            id=doc_id,
            source_type=self.source_type,
            title=title,
            content_text=content_text,
            metadata=meta,
            deep_link=f"file://{path.resolve()}",
            content_hash=content_hash,
            indexed_at=datetime.utcnow(),
        )

    def _glob_files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        return sorted(
            p for p in self._dir.rglob("*")
            if p.suffix.lower() in {".vtt", ".txt"} and not _is_chat_log(p)
        )

    def fetch_all(self) -> list[Document]:
        try:
            return [self._load_file(p) for p in self._glob_files()]
        except OSError as exc:
            raise ConnectorError(self.source_type, str(exc)) from exc

    def fetch_updated(self, since: datetime) -> list[Document]:
        try:
            return [
                self._load_file(p)
                for p in self._glob_files()
                if datetime.utcfromtimestamp(p.stat().st_mtime) > since
            ]
        except OSError as exc:
            raise ConnectorError(self.source_type, str(exc)) from exc


# Self-register
def _factory(transcripts_dir: Path, **_: object) -> DataConnector:
    return ZoomConnector(transcripts_dir=transcripts_dir)


registry.register("zoom", _factory)
