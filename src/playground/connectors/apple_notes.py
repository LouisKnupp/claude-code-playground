"""Apple Notes connector (macOS only).

Uses AppleScript to read notes from the Notes app. Requires the user to grant
Automation permission in System Settings > Privacy & Security > Automation.

Limitations:
- macOS only
- Practical ceiling ~1000 notes before AppleScript becomes slow
- No native delta API: fetch_updated re-fetches all notes and filters by date

deep_link uses the notes:// URL scheme to open notes directly in the Notes app.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime

from playground.connectors.base import DataConnector
from playground.connectors import registry
from playground.core.exceptions import ConnectorError, PermissionError as PlaygroundPermissionError
from playground.core.models import Document

# AppleScript that returns all notes as tab-separated records
_FETCH_SCRIPT = """
set output to ""
tell application "Notes"
    repeat with aNote in every note
        set noteId to the id of aNote
        set noteTitle to the name of aNote
        set noteBody to the body of aNote
        set modDate to (modification date of aNote) as string
        set output to output & noteId & "\\t" & noteTitle & "\\t" & modDate & "\\t" & noteBody & "\\n---NOTESEP---\\n"
    end repeat
end tell
return output
"""

_PERMISSION_CHECK = 'tell application "Notes" to get the name of the first note'
_SEPARATOR = "\n---NOTESEP---\n"


def _run_applescript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        if "Not authorized" in err or "access" in err.lower():
            raise PlaygroundPermissionError(
                "apple_notes",
                "Automation permission denied. Go to System Settings > Privacy & Security > "
                "Automation and allow your terminal to control Notes.",
            )
        raise ConnectorError("apple_notes", f"AppleScript error: {err}")
    return result.stdout


def _check_permission() -> None:
    """Probe Notes access; raises PlaygroundPermissionError if denied."""
    try:
        _run_applescript(_PERMISSION_CHECK)
    except PlaygroundPermissionError:
        raise
    except ConnectorError:
        pass  # No notes to read — that's fine, not a permission issue


def _parse_note(record: str) -> Document | None:
    """Parse one tab-separated note record into a Document."""
    try:
        import html2text
    except ImportError as exc:
        raise ConnectorError(
            "apple_notes", "html2text not installed. Run: pip install html2text"
        ) from exc

    parts = record.strip().split("\t", 3)
    if len(parts) < 4:
        return None

    note_id, title, mod_date_str, body_html = parts

    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    content_text = h.handle(body_html).strip()

    content_hash = hashlib.sha256(content_text.encode()).hexdigest()
    doc_id = hashlib.sha256(f"apple_notes:{note_id}{content_hash}".encode()).hexdigest()

    # notes:// deep link opens the note directly in Notes.app
    deep_link = f"notes://showNote?identifier={note_id}"

    return Document(
        id=doc_id,
        source_type="apple_notes",
        title=title,
        content_text=content_text,
        metadata={"note_id": note_id, "modified_at": mod_date_str},
        deep_link=deep_link,
        content_hash=content_hash,
        indexed_at=datetime.utcnow(),
    )


def _parse_mod_date(mod_date_str: str) -> datetime:
    """Best-effort parse of AppleScript date strings."""
    for fmt in ("%A, %B %d, %Y at %I:%M:%S %p", "%A, %B %d, %Y"):
        try:
            return datetime.strptime(mod_date_str, fmt)
        except ValueError:
            continue
    return datetime.min


class AppleNotesConnector:
    source_type = "apple_notes"
    display_name = "Apple Notes"

    def __init__(self) -> None:
        _check_permission()

    def _fetch_documents(self) -> list[Document]:
        raw = _run_applescript(_FETCH_SCRIPT)
        records = raw.split(_SEPARATOR)
        docs = []
        for record in records:
            if not record.strip():
                continue
            doc = _parse_note(record)
            if doc:
                docs.append(doc)
        return docs

    def fetch_all(self) -> list[Document]:
        return self._fetch_documents()

    def fetch_updated(self, since: datetime) -> list[Document]:
        all_docs = self._fetch_documents()
        updated = []
        for doc in all_docs:
            mod_str = doc.metadata.get("modified_at", "")
            mod_date = _parse_mod_date(mod_str)
            if mod_date > since:
                updated.append(doc)
        return updated


# Self-register
def _factory(**_: object) -> DataConnector:
    return AppleNotesConnector()


registry.register("apple_notes", _factory)
