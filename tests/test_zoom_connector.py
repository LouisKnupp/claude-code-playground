"""Smoke tests for Zoom connector parsing — no DB, no LLM, no real network."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from playground.connectors.zoom import (
    ZoomCloudClient,
    ZoomConnector,
    _chunk_date_ranges,
    _deduplicate_by_folder,
    _is_chat_log,
    _is_cloud_transcript_file,
    _meeting_title_and_date,
    _parse_cloud_meeting_title_and_date,
    _parse_txt,
    _parse_vtt,
)


# ---------------------------------------------------------------------------
# _parse_txt — bracket format (real Zoom closed-caption export)
# ---------------------------------------------------------------------------

TXT_BRACKET = """\
[Abbie Paul] 11:20:49
Awesome. Hi, everyone! I'm Abby Paul, I'm the Manager of Catalog Development.

[Abbie Paul] 11:20:54
Just want to give you a quick update.

[Louis Knupp] 11:21:10
Thanks Abbie, really helpful.

[Louis Knupp] 11:21:15
We'll follow up after the meeting.
"""

def test_txt_bracket_extracts_speakers():
    _, meta = _parse_txt(TXT_BRACKET)
    assert meta["speakers"] == ["Abbie Paul", "Louis Knupp"]

def test_txt_bracket_speaker_lines_excluded_from_text():
    text, _ = _parse_txt(TXT_BRACKET)
    assert "[Abbie Paul]" not in text
    assert "[Louis Knupp]" not in text

def test_txt_bracket_speech_preserved():
    text, _ = _parse_txt(TXT_BRACKET)
    assert "I'm the Manager of Catalog Development" in text
    assert "We'll follow up after the meeting." in text

def test_txt_bracket_deduplicates_repeated_speaker():
    _, meta = _parse_txt(TXT_BRACKET)
    assert meta["speakers"].count("Abbie Paul") == 1
    assert meta["speakers"].count("Louis Knupp") == 1

def test_txt_bracket_speakers_sorted():
    _, meta = _parse_txt(TXT_BRACKET)
    assert meta["speakers"] == sorted(meta["speakers"])


# ---------------------------------------------------------------------------
# _parse_txt — colon format (fallback for other .txt transcript styles)
# ---------------------------------------------------------------------------

TXT_COLON = """\
John Smith: Hello everyone.
Jane Doe: Hi John, good to see you.
John Smith: Let's get started.
"""

def test_txt_colon_extracts_speakers():
    _, meta = _parse_txt(TXT_COLON)
    assert "John Smith" in meta["speakers"]
    assert "Jane Doe" in meta["speakers"]

def test_txt_colon_speech_preserved():
    text, _ = _parse_txt(TXT_COLON)
    assert "Hello everyone." in text


# ---------------------------------------------------------------------------
# _parse_txt — empty / no speakers
# ---------------------------------------------------------------------------

def test_txt_no_speakers_returns_empty_list():
    _, meta = _parse_txt("Just some plain notes with no speaker labels.\n")
    assert meta["speakers"] == []

def test_txt_empty_string():
    text, meta = _parse_txt("")
    assert text == ""
    assert meta["speakers"] == []


# ---------------------------------------------------------------------------
# _parse_vtt
# ---------------------------------------------------------------------------

VTT_BASIC = """\
WEBVTT

1
00:00:13.321 --> 00:00:15.321
Find one here…

2
00:00:26.929 --> 00:00:28.929
So, like, here's a good example.

3
00:00:29.850 --> 00:00:31.850
That came over from Ultimate.
"""

VTT_WITH_SPEAKERS = """\
WEBVTT

1
00:00:05.000 --> 00:00:08.000
Alice Johnson: Welcome to the call.

2
00:00:10.000 --> 00:00:14.000
Bob Smith: Thanks for having me.
"""

def test_vtt_strips_header_and_cues():
    text, _ = _parse_vtt(VTT_BASIC)
    assert "WEBVTT" not in text
    assert "-->" not in text

def test_vtt_speech_preserved():
    text, _ = _parse_vtt(VTT_BASIC)
    assert "Find one here" in text
    assert "came over from Ultimate" in text

def test_vtt_first_timestamp_captured():
    _, meta = _parse_vtt(VTT_BASIC)
    assert meta["first_timestamp"] == "00:00:13.321"

def test_vtt_with_speakers_extracts_them():
    _, meta = _parse_vtt(VTT_WITH_SPEAKERS)
    assert "Alice Johnson" in meta["speakers"]
    assert "Bob Smith" in meta["speakers"]

def test_vtt_no_speakers_returns_empty_list():
    _, meta = _parse_vtt(VTT_BASIC)
    assert meta["speakers"] == []


# ---------------------------------------------------------------------------
# _deduplicate_by_folder
# ---------------------------------------------------------------------------

def _make_paths(tmp_path: Path, folder: str, names: list[str]) -> list[Path]:
    d = tmp_path / folder
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        p = d / name
        p.write_text("")
        paths.append(p)
    return paths

def test_dedup_prefers_saved_over_closed(tmp_path):
    paths = _make_paths(tmp_path, "meeting1", [
        "meeting_saved_closed_caption.txt",
        "closed_caption.txt",
        "video123.vtt",
    ])
    result = _deduplicate_by_folder(paths)
    assert len(result) == 1
    assert result[0].name == "meeting_saved_closed_caption.txt"

def test_dedup_prefers_closed_caption_over_vtt(tmp_path):
    paths = _make_paths(tmp_path, "meeting1", [
        "closed_caption.txt",
        "video123.vtt",
    ])
    result = _deduplicate_by_folder(paths)
    assert len(result) == 1
    assert result[0].name == "closed_caption.txt"

def test_dedup_falls_back_to_vtt(tmp_path):
    paths = _make_paths(tmp_path, "meeting1", ["video123.vtt"])
    result = _deduplicate_by_folder(paths)
    assert len(result) == 1
    assert result[0].name == "video123.vtt"

def test_dedup_separate_folders_both_kept(tmp_path):
    paths = (
        _make_paths(tmp_path, "meeting1", ["meeting_saved_closed_caption.txt"])
        + _make_paths(tmp_path, "meeting2", ["meeting_saved_closed_caption.txt"])
    )
    result = _deduplicate_by_folder(paths)
    assert len(result) == 2

def test_dedup_root_level_file_kept(tmp_path):
    f = tmp_path / "custom_transcript.txt"
    f.write_text("")
    result = _deduplicate_by_folder([f])
    assert len(result) == 1
    assert result[0].name == "custom_transcript.txt"


# ---------------------------------------------------------------------------
# _is_chat_log
# ---------------------------------------------------------------------------

def test_chat_log_detected():
    assert _is_chat_log(Path("meeting_chat.txt")) is True
    assert _is_chat_log(Path("GMT20240101_chat_file.txt")) is True

def test_transcript_not_chat_log():
    assert _is_chat_log(Path("meeting_saved_closed_caption.txt")) is False
    assert _is_chat_log(Path("closed_caption.txt")) is False
    assert _is_chat_log(Path("video123.vtt")) is False


# ---------------------------------------------------------------------------
# _meeting_title_and_date
# ---------------------------------------------------------------------------

def test_folder_name_parsed(tmp_path):
    folder = tmp_path / "2025-11-21 11.48.14 GTM Meeting"
    folder.mkdir()
    f = folder / "meeting_saved_closed_caption.txt"
    f.write_text("")
    title, date = _meeting_title_and_date(f, tmp_path)
    assert title == "GTM Meeting"
    assert date == "2025-11-21 11:48:14"

def test_folder_name_with_special_chars(tmp_path):
    folder = tmp_path / "2026-03-19 12.41.19 DataOps morning check-in"
    folder.mkdir()
    f = folder / "meeting_saved_closed_caption.txt"
    f.write_text("")
    title, date = _meeting_title_and_date(f, tmp_path)
    assert title == "DataOps morning check-in"
    assert date == "2026-03-19 12:41:19"

def test_file_directly_in_root_falls_back_to_stem(tmp_path):
    f = tmp_path / "my_meeting_notes.txt"
    f.write_text("")
    title, date = _meeting_title_and_date(f, tmp_path)
    assert title == "my meeting notes"
    assert date  # some non-empty date string


# ---------------------------------------------------------------------------
# Zoom cloud helpers
# ---------------------------------------------------------------------------

def test_cloud_transcript_file_detected_by_file_type():
    assert _is_cloud_transcript_file({"file_type": "TRANSCRIPT"}) is True


def test_cloud_transcript_file_detected_by_recording_type():
    assert _is_cloud_transcript_file({"recording_type": "audio_transcript"}) is True


def test_cloud_non_transcript_file_rejected():
    assert _is_cloud_transcript_file({"file_type": "MP4", "download_url": "https://example.com/video.mp4"}) is False


def test_cloud_meeting_title_and_date():
    title, meeting_date = _parse_cloud_meeting_title_and_date({
        "topic": "Customer Kickoff",
        "start_time": "2026-03-01T15:30:00Z",
    })
    assert title == "Customer Kickoff"
    assert meeting_date == "2026-03-01 15:30:00"


def test_chunk_date_ranges_splits_long_range():
    ranges = _chunk_date_ranges(
        datetime(2026, 1, 1),
        datetime(2026, 3, 15),
        days_per_chunk=30,
    )
    assert ranges == [
        ("2026-01-01", "2026-01-30"),
        ("2026-01-31", "2026-03-01"),
        ("2026-03-02", "2026-03-15"),
    ]


def test_build_authorize_url():
    client = ZoomCloudClient(
        client_id="client123",
        client_secret="secret456",
        redirect_uri="http://localhost/callback",
    )
    url = client.build_authorize_url()
    assert "response_type=code" in url
    assert "client_id=client123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcallback" in url


class _FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_cloud_fetch_all_downloads_transcripts(monkeypatch, tmp_path):
    requests = []
    recordings_calls = 0

    def fake_urlopen(req, timeout=0):
        nonlocal recordings_calls
        requests.append(req.full_url)
        if req.full_url.startswith("https://zoom.us/oauth/token"):
            assert req.get_method() == "POST"
            return _FakeResponse(json.dumps({
                "access_token": "token-123",
                "refresh_token": "refresh-123",
                "expires_in": 3600,
            }))
        if "/users/me/recordings?" in req.full_url:
            recordings_calls += 1
            if recordings_calls > 1:
                return _FakeResponse(json.dumps({"meetings": [], "next_page_token": ""}))
            return _FakeResponse(json.dumps({
                "meetings": [
                    {
                        "uuid": "meeting-uuid",
                        "id": 12345,
                        "topic": "Weekly Standup",
                        "start_time": "2026-03-10T14:00:00Z",
                        "recording_files": [
                            {
                                "id": "transcript-1",
                                "file_type": "TRANSCRIPT",
                                "recording_type": "audio_transcript",
                                "download_url": "https://file.zoom.us/transcript-1.vtt",
                                "play_url": "https://play.zoom.us/recording/transcript-1",
                            },
                            {
                                "id": "video-1",
                                "file_type": "MP4",
                                "download_url": "https://file.zoom.us/video-1.mp4",
                            },
                        ],
                    }
                ],
                "next_page_token": "",
            }))
        if req.full_url == "https://file.zoom.us/transcript-1.vtt":
            return _FakeResponse(VTT_WITH_SPEAKERS)
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr("playground.connectors.zoom.urlopen", fake_urlopen)

    connector = ZoomConnector(
        transcripts_dir=tmp_path,
        source_mode="cloud",
        api_client_id="client",
        api_client_secret="secret",
        api_redirect_uri="http://localhost",
        api_refresh_token="refresh-123",
    )

    docs = connector.fetch_all()

    assert len(docs) == 1
    assert docs[0].title == "Weekly Standup"
    assert docs[0].metadata["recording_file_id"] == "transcript-1"
    assert docs[0].metadata["speakers"] == ["Alice Johnson", "Bob Smith"]
    assert docs[0].deep_link == "https://play.zoom.us/recording/transcript-1"
    assert "video-1.mp4" not in "".join(requests)


def test_cloud_fetch_updated_filters_old_meetings(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout=0):
        if req.full_url.startswith("https://zoom.us/oauth/token"):
            assert req.get_method() == "POST"
            return _FakeResponse(json.dumps({
                "access_token": "token-123",
                "refresh_token": "refresh-123",
                "expires_in": 3600,
            }))
        if "/users/me/recordings?" in req.full_url:
            return _FakeResponse(json.dumps({
                "meetings": [
                    {
                        "uuid": "older-meeting",
                        "topic": "Too Old",
                        "start_time": "2026-03-01T10:00:00Z",
                        "recording_files": [
                            {
                                "id": "transcript-old",
                                "file_type": "TRANSCRIPT",
                                "download_url": "https://file.zoom.us/old.vtt",
                            }
                        ],
                    },
                    {
                        "uuid": "newer-meeting",
                        "topic": "Fresh Meeting",
                        "start_time": "2026-03-20T10:00:00Z",
                        "recording_files": [
                            {
                                "id": "transcript-new",
                                "file_type": "TRANSCRIPT",
                                "download_url": "https://file.zoom.us/new.vtt",
                            }
                        ],
                    },
                ],
                "next_page_token": "",
            }))
        if req.full_url == "https://file.zoom.us/new.vtt":
            return _FakeResponse(VTT_BASIC)
        if req.full_url == "https://file.zoom.us/old.vtt":
            raise AssertionError("Old transcript should not be downloaded")
        raise AssertionError(f"Unexpected URL: {req.full_url}")

    monkeypatch.setattr("playground.connectors.zoom.urlopen", fake_urlopen)

    connector = ZoomConnector(
        transcripts_dir=tmp_path,
        source_mode="cloud",
        api_client_id="client",
        api_client_secret="secret",
        api_redirect_uri="http://localhost",
        api_refresh_token="refresh-123",
    )

    docs = connector.fetch_updated(datetime(2026, 3, 15, 0, 0, 0))

    assert len(docs) == 1
    assert docs[0].title == "Fresh Meeting"


def test_exchange_code_updates_tokens(monkeypatch):
    saved = {}

    def fake_urlopen(req, timeout=0):
        assert req.get_method() == "POST"
        assert "grant_type=authorization_code" in req.full_url
        assert "code=abc123" in req.full_url
        return _FakeResponse(json.dumps({
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        }))

    monkeypatch.setattr("playground.connectors.zoom.urlopen", fake_urlopen)

    client = ZoomCloudClient(
        client_id="client",
        client_secret="secret",
        redirect_uri="http://localhost",
        token_updater=saved.update,
    )
    tokens = client.exchange_code("abc123")

    assert tokens["access_token"] == "access-1"
    assert tokens["refresh_token"] == "refresh-1"
    assert saved["zoom_api_access_token"] == "access-1"
    assert saved["zoom_api_refresh_token"] == "refresh-1"


def test_refresh_access_token_updates_tokens(monkeypatch):
    saved = {}

    def fake_urlopen(req, timeout=0):
        assert req.get_method() == "POST"
        assert "grant_type=refresh_token" in req.full_url
        assert "refresh_token=refresh-1" in req.full_url
        return _FakeResponse(json.dumps({
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        }))

    monkeypatch.setattr("playground.connectors.zoom.urlopen", fake_urlopen)

    client = ZoomCloudClient(
        client_id="client",
        client_secret="secret",
        redirect_uri="http://localhost",
        refresh_token="refresh-1",
        token_updater=saved.update,
    )

    assert client.get_access_token() == "access-2"
    assert saved["zoom_api_refresh_token"] == "refresh-2"
