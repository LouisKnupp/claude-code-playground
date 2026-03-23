"""Zoom transcript connector.

Reads .vtt and .txt transcript files from a local directory.
- Strips WebVTT formatting (headers, cue timestamps, cue indices)
- Retains speaker labels and first-cue timestamp in metadata
- deep_link is a file:// URI clickable in macOS terminal and Finder
"""

from __future__ import annotations

import hashlib
import json
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

        meta["filename"] = path.name
        meta["meeting_date"] = path.stat().st_mtime

        source_id = str(path.resolve())
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()
        doc_id = hashlib.sha256(f"{source_id}{content_hash}".encode()).hexdigest()

        return Document(
            id=doc_id,
            source_type=self.source_type,
            title=path.stem.replace("_", " ").replace("-", " "),
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
            p for p in self._dir.rglob("*") if p.suffix.lower() in {".vtt", ".txt"}
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
