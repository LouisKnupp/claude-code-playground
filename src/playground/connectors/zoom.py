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

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playground.connectors.base import DataConnector
from playground.connectors import registry
from playground.core.exceptions import ConnectorError
from playground.core.models import Document

# Matches lines like: 00:01:23.456 --> 00:01:45.789
_TIMESTAMP_LINE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}")
# Matches speaker labels like: "John Smith: " or "SPEAKER 1: "
_SPEAKER_LINE = re.compile(r"^([A-Za-z][^:\n]{0,60}):\s+(.+)")
# Matches Zoom .txt bracket format: "[Speaker Name] HH:MM:SS"
_BRACKET_SPEAKER_LINE = re.compile(r"^\[([^\]]+)\]\s+\d{1,2}:\d{2}:\d{2}$")
# Matches bare cue index lines (digits only)
_CUE_INDEX = re.compile(r"^\d+$")
# Matches Zoom folder names: "YYYY-MM-DD HH.MM.SS Meeting Name"
_FOLDER_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2}) (.+)$")
_ZOOM_API_BASE_URL = "https://api.zoom.us/v2"
_ZOOM_OAUTH_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
_ZOOM_OAUTH_TOKEN_URL = "https://zoom.us/oauth/token"


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
    """Plain .txt files — return as-is, extract speaker labels if present.

    Handles two Zoom .txt formats:
    - Bracket format (closed caption): "[Speaker Name] HH:MM:SS" on its own line
    - Colon format: "Speaker Name: text on same line"
    """
    clean: list[str] = []
    speakers: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        bracket_m = _BRACKET_SPEAKER_LINE.match(line)
        if bracket_m:
            speakers.add(bracket_m.group(1).strip())
            # Skip the timestamp line itself; the speech follows on the next line(s)
            continue
        colon_m = _SPEAKER_LINE.match(line)
        if colon_m:
            speakers.add(colon_m.group(1).strip())
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
    meeting_date = datetime.fromtimestamp(mtime, UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    return path.stem.replace("_", " ").replace("-", " "), meeting_date


def _is_chat_log(path: Path) -> bool:
    """Return True for Zoom chat export files, which are not transcripts."""
    return path.suffix.lower() == ".txt" and "chat" in path.stem.lower()


def _parse_cloud_meeting_title_and_date(meeting: dict) -> tuple[str, str]:
    title = str(meeting.get("topic") or "Zoom Cloud Recording").strip()
    start_time = str(meeting.get("start_time") or "").strip()
    meeting_date = start_time.replace("T", " ").replace("Z", "")
    return title, meeting_date


def _parse_cloud_transcript_text(text: str) -> tuple[str, dict]:
    preview = text.lstrip()[:200]
    if preview.startswith("WEBVTT") or "-->" in preview:
        return _parse_vtt(text)
    return _parse_txt(text)


def _is_cloud_transcript_file(recording_file: dict) -> bool:
    file_type = str(recording_file.get("file_type") or "").upper()
    recording_type = str(recording_file.get("recording_type") or "").lower()
    download_url = str(recording_file.get("download_url") or "").lower()
    return (
        file_type == "TRANSCRIPT"
        or recording_type == "audio_transcript"
        or download_url.endswith(".vtt")
        or download_url.endswith(".txt")
    )


def _chunk_date_ranges(start: datetime, end: datetime, days_per_chunk: int = 30) -> list[tuple[str, str]]:
    if start > end:
        return []

    ranges: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days_per_chunk - 1), end)
        ranges.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_iso_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class ZoomCloudClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        user_id: str = "me",
        access_token: str = "",
        refresh_token: str = "",
        token_expires_at: str = "",
        token_updater: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._redirect_uri = redirect_uri.strip()
        self._user_id = user_id.strip() or "me"
        self._access_token = access_token.strip()
        self._refresh_token = refresh_token.strip()
        self._token_expires_at = token_expires_at.strip()
        self._token_updater = token_updater

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("zoom_api_client_id", self._client_id),
                ("zoom_api_client_secret", self._client_secret),
                ("zoom_api_redirect_uri", self._redirect_uri),
            )
            if not value
        ]
        if missing:
            raise ConnectorError("zoom", f"Zoom cloud mode requires: {', '.join(missing)}")

    def _auth_headers(self) -> dict[str, str]:
        creds = f"{self._client_id}:{self._client_secret}".encode()
        return {
            "Authorization": "Basic " + base64.b64encode(creds).decode(),
        }

    def _request_json(
        self,
        url: str,
        headers: dict[str, str],
        method: str = "GET",
        data: bytes | None = None,
    ) -> dict:
        req = Request(url, headers=headers, method=method, data=data)
        try:
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConnectorError("zoom", f"Zoom API request failed: HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise ConnectorError("zoom", f"Zoom API request failed: {exc}") from exc

    def build_authorize_url(self) -> str:
        self._require_credentials()
        query = urlencode({
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
        })
        return f"{_ZOOM_OAUTH_AUTHORIZE_URL}?{query}"

    def _store_tokens(self, payload: dict) -> str:
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise ConnectorError("zoom", "Zoom OAuth token response did not include access_token")
        refresh_token = str(payload.get("refresh_token") or self._refresh_token).strip()
        expires_in = int(payload.get("expires_in") or 3600)
        expires_at = (_utcnow_naive() + timedelta(seconds=max(expires_in - 60, 0))).isoformat()
        self._access_token = token
        self._refresh_token = refresh_token
        self._token_expires_at = expires_at
        if self._token_updater:
            self._token_updater({
                "zoom_api_access_token": self._access_token,
                "zoom_api_refresh_token": self._refresh_token,
                "zoom_api_token_expires_at": self._token_expires_at,
            })
        return token

    def exchange_code(self, code: str) -> dict[str, str]:
        self._require_credentials()
        query = urlencode({
            "grant_type": "authorization_code",
            "code": code.strip(),
            "redirect_uri": self._redirect_uri,
        })
        url = f"{_ZOOM_OAUTH_TOKEN_URL}?{query}"
        payload = self._request_json(url, headers=self._auth_headers(), method="POST", data=b"")
        self._store_tokens(payload)
        return {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "token_expires_at": self._token_expires_at,
        }

    def refresh_access_token(self) -> str:
        self._require_credentials()
        if not self._refresh_token:
            raise ConnectorError(
                "zoom",
                "Zoom cloud mode requires zoom_api_refresh_token. Run 'playground zoom-auth' first.",
            )
        query = urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        })
        url = f"{_ZOOM_OAUTH_TOKEN_URL}?{query}"
        payload = self._request_json(url, headers=self._auth_headers(), method="POST", data=b"")
        return self._store_tokens(payload)

    def get_access_token(self) -> str:
        expires_at = _parse_iso_datetime(self._token_expires_at)
        if self._access_token and expires_at and expires_at > _utcnow_naive():
            return self._access_token
        if self._access_token and not expires_at:
            return self._access_token
        return self.refresh_access_token()

    def list_recordings(self, start: datetime, end: datetime) -> list[dict]:
        access_token = self.get_access_token()
        meetings: list[dict] = []
        headers = {"Authorization": f"Bearer {access_token}"}

        for date_from, date_to in _chunk_date_ranges(start, end):
            next_page_token = ""
            while True:
                query = {
                    "from": date_from,
                    "to": date_to,
                    "page_size": 300,
                }
                if next_page_token:
                    query["next_page_token"] = next_page_token
                url = f"{_ZOOM_API_BASE_URL}/users/{self._user_id}/recordings?{urlencode(query)}"
                payload = self._request_json(url, headers=headers)
                meetings.extend(payload.get("meetings", []))
                next_page_token = str(payload.get("next_page_token") or "").strip()
                if not next_page_token:
                    break

        return meetings

    def download_text(self, download_url: str) -> str:
        access_token = self.get_access_token()
        req = Request(download_url, headers={"Authorization": f"Bearer {access_token}"})
        try:
            with urlopen(req, timeout=120) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise ConnectorError("zoom", f"Zoom transcript download failed: {exc}") from exc


class ZoomConnector:
    source_type = "zoom"
    display_name = "Zoom Transcripts"

    def __init__(
        self,
        transcripts_dir: Path,
        source_mode: str = "local",
        api_client_id: str = "",
        api_client_secret: str = "",
        api_redirect_uri: str = "http://localhost",
        api_user_id: str = "me",
        api_access_token: str = "",
        api_refresh_token: str = "",
        api_token_expires_at: str = "",
        cloud_lookback_days: int = 365,
        token_updater: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._dir = Path(transcripts_dir).expanduser()
        self._source_mode = source_mode.strip().lower() or "local"
        self._cloud_lookback_days = cloud_lookback_days
        self._cloud_client = ZoomCloudClient(
            client_id=api_client_id,
            client_secret=api_client_secret,
            redirect_uri=api_redirect_uri,
            user_id=api_user_id,
            access_token=api_access_token,
            refresh_token=api_refresh_token,
            token_expires_at=api_token_expires_at,
            token_updater=token_updater,
        )
        if self._source_mode not in {"local", "cloud", "both"}:
            raise ConnectorError("zoom", f"Unsupported zoom_source_mode: {source_mode}")

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
            indexed_at=_utcnow_naive(),
        )

    def _glob_files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        all_files = [
            p for p in self._dir.rglob("*")
            if p.suffix.lower() in {".vtt", ".txt"} and not _is_chat_log(p)
        ]
        return sorted(_deduplicate_by_folder(all_files))

    def _fetch_local_documents(self) -> list[Document]:
        return [self._load_file(p) for p in self._glob_files()]

    def _load_cloud_recording(self, meeting: dict, recording_file: dict) -> Document:
        download_url = str(recording_file.get("download_url") or "").strip()
        if not download_url:
            raise ConnectorError("zoom", "Zoom transcript file is missing download_url")

        raw = self._cloud_client.download_text(download_url)
        content_text, meta = _parse_cloud_transcript_text(raw)
        title, meeting_date = _parse_cloud_meeting_title_and_date(meeting)

        parsed_url = urlparse(download_url)
        filename = Path(parsed_url.path).name or str(recording_file.get("id") or "transcript.vtt")
        meta["filename"] = filename
        meta["meeting_date"] = meeting_date
        meta["meeting_uuid"] = str(meeting.get("uuid") or "")
        meta["meeting_id"] = str(meeting.get("id") or "")
        meta["recording_file_id"] = str(recording_file.get("id") or "")
        meta["recording_type"] = str(recording_file.get("recording_type") or "")
        meta["host_email"] = str(meeting.get("host_email") or "")

        source_id = f"zoom-cloud:{meta['meeting_uuid']}:{meta['recording_file_id'] or filename}"
        content_hash = hashlib.sha256(content_text.encode()).hexdigest()
        doc_id = hashlib.sha256(f"{source_id}{content_hash}".encode()).hexdigest()
        deep_link = (
            str(recording_file.get("play_url") or "").strip()
            or str(meeting.get("share_url") or "").strip()
            or download_url
        )

        return Document(
            id=doc_id,
            source_type=self.source_type,
            title=title,
            content_text=content_text,
            metadata=meta,
            deep_link=deep_link,
            content_hash=content_hash,
            indexed_at=_utcnow_naive(),
        )

    def _fetch_cloud_documents(self, since: datetime | None = None) -> list[Document]:
        end = _utcnow_naive()
        start = since or (end - timedelta(days=self._cloud_lookback_days))
        docs: list[Document] = []

        for meeting in self._cloud_client.list_recordings(start=start, end=end):
            meeting_start_raw = str(meeting.get("start_time") or "").strip()
            if since and meeting_start_raw:
                meeting_start = _parse_iso_datetime(meeting_start_raw)
                if meeting_start and meeting_start <= since:
                    continue

            for recording_file in meeting.get("recording_files", []):
                if not _is_cloud_transcript_file(recording_file):
                    continue
                docs.append(self._load_cloud_recording(meeting, recording_file))

        return docs

    def _fetch_documents(self, since: datetime | None = None) -> list[Document]:
        docs: list[Document] = []
        if self._source_mode in {"local", "both"}:
            if since is None:
                docs.extend(self._fetch_local_documents())
            else:
                docs.extend([
                    self._load_file(p)
                    for p in self._glob_files()
                    if datetime.utcfromtimestamp(p.stat().st_mtime) > since
                ])
        if self._source_mode in {"cloud", "both"}:
            docs.extend(self._fetch_cloud_documents(since=since))
        return docs

    def fetch_all(self) -> list[Document]:
        try:
            return self._fetch_documents()
        except OSError as exc:
            raise ConnectorError(self.source_type, str(exc)) from exc

    def fetch_updated(self, since: datetime) -> list[Document]:
        try:
            return self._fetch_documents(since=since)
        except OSError as exc:
            raise ConnectorError(self.source_type, str(exc)) from exc


def _deduplicate_by_folder(paths: list[Path]) -> list[Path]:
    """Return one file per folder, preferring the highest-quality transcript.

    Priority (highest first):
      1. meeting_saved_closed_caption.txt  — longest, cleaned-up Zoom export
      2. closed_caption.txt                — raw real-time captions
      3. *.vtt                             — WebVTT, same content as closed_caption
      4. any other .txt                    — custom-named exports / manual transcripts

    Files directly in the transcripts root (no subfolder) are kept as-is.
    """
    _PRIORITY = {
        "meeting_saved_closed_caption.txt": 0,
        "closed_caption.txt": 1,
    }

    def _rank(p: Path) -> int:
        name = p.name.lower()
        if name in _PRIORITY:
            return _PRIORITY[name]
        if p.suffix.lower() == ".vtt":
            return 2
        return 3

    by_folder: dict[Path, Path] = {}
    for p in paths:
        folder = p.parent
        if folder not in by_folder or _rank(p) < _rank(by_folder[folder]):
            by_folder[folder] = p
    return list(by_folder.values())


# Self-register
def _factory(
    transcripts_dir: Path,
    source_mode: str = "local",
    api_client_id: str = "",
    api_client_secret: str = "",
    api_redirect_uri: str = "http://localhost",
    api_user_id: str = "me",
    api_access_token: str = "",
    api_refresh_token: str = "",
    api_token_expires_at: str = "",
    cloud_lookback_days: int = 365,
    token_updater: Callable[[dict[str, str]], None] | None = None,
    **_: object,
) -> DataConnector:
    return ZoomConnector(
        transcripts_dir=transcripts_dir,
        source_mode=source_mode,
        api_client_id=api_client_id,
        api_client_secret=api_client_secret,
        api_redirect_uri=api_redirect_uri,
        api_user_id=api_user_id,
        api_access_token=api_access_token,
        api_refresh_token=api_refresh_token,
        api_token_expires_at=api_token_expires_at,
        cloud_lookback_days=cloud_lookback_days,
        token_updater=token_updater,
    )


registry.register("zoom", _factory)
