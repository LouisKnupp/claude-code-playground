"""Smoke tests for Zoom connector parsing — no DB, no LLM, no file I/O."""

from playground.connectors.zoom import _parse_txt, _parse_vtt, _is_chat_log, _meeting_title_and_date, _deduplicate_by_folder
from pathlib import Path
import pytest


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
