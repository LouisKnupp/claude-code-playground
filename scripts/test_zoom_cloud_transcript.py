#!/usr/bin/env python3
"""Fetch a single Zoom cloud transcript without indexing anything.

This is a smoke-test utility for validating:
1. Zoom OAuth tokens are working
2. The recordings API returns meetings for the configured user
3. A transcript file can be downloaded successfully

Examples:
    PYTHONPATH=src .venv/bin/python scripts/test_zoom_cloud_transcript.py
    PYTHONPATH=src .venv/bin/python scripts/test_zoom_cloud_transcript.py --days 14
    PYTHONPATH=src .venv/bin/python scripts/test_zoom_cloud_transcript.py --topic "standup"
    PYTHONPATH=src .venv/bin/python scripts/test_zoom_cloud_transcript.py --meeting-id 123456789
    PYTHONPATH=src .venv/bin/python scripts/test_zoom_cloud_transcript.py --output /tmp/zoom-test.vtt
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

from playground.connectors.zoom import (
    ZoomCloudClient,
    _is_cloud_transcript_file,
    _parse_cloud_meeting_title_and_date,
    _parse_cloud_transcript_text,
    _utcnow_naive,
)
from playground.core.config import load_settings, save_config_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch one Zoom cloud transcript without running full sync."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="How far back to search for recordings. Default: 30",
    )
    parser.add_argument(
        "--meeting-id",
        help="Optional Zoom meeting ID to target exactly.",
    )
    parser.add_argument(
        "--topic",
        help="Optional case-insensitive topic substring filter.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional file path to save the raw downloaded transcript.",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="Print the full raw transcript instead of just a preview.",
    )
    return parser


def _token_updater_factory(config_path: Path):
    def _update(tokens: dict[str, str]) -> None:
        save_config_values(tokens, config_path)

    return _update


def _meeting_matches(meeting: dict, meeting_id: str | None, topic: str | None) -> bool:
    if meeting_id and str(meeting.get("id", "")) != meeting_id:
        return False
    if topic:
        meeting_topic = str(meeting.get("topic") or "")
        if topic.lower() not in meeting_topic.lower():
            return False
    return True


def main() -> int:
    args = _build_parser().parse_args()
    settings = load_settings()

    client = ZoomCloudClient(
        client_id=settings.zoom_api_client_id,
        client_secret=settings.zoom_api_client_secret,
        redirect_uri=settings.zoom_api_redirect_uri,
        user_id=settings.zoom_api_user_id,
        access_token=settings.zoom_api_access_token,
        refresh_token=settings.zoom_api_refresh_token,
        token_expires_at=settings.zoom_api_token_expires_at,
        token_updater=_token_updater_factory(settings.config_path),
    )

    end = _utcnow_naive()
    start = end - timedelta(days=args.days)
    meetings = client.list_recordings(start=start, end=end)

    if not meetings:
        print("No Zoom recordings found in the requested date range.", file=sys.stderr)
        return 1

    transcript_match: tuple[dict, dict] | None = None
    for meeting in meetings:
        if not _meeting_matches(meeting, args.meeting_id, args.topic):
            continue
        for recording_file in meeting.get("recording_files", []):
            if _is_cloud_transcript_file(recording_file):
                transcript_match = (meeting, recording_file)
                break
        if transcript_match:
            break

    if transcript_match is None:
        print("No matching transcript file found.", file=sys.stderr)
        return 1

    meeting, recording_file = transcript_match
    raw_text = client.download_text(str(recording_file.get("download_url") or ""))
    parsed_text, meta = _parse_cloud_transcript_text(raw_text)
    title, meeting_date = _parse_cloud_meeting_title_and_date(meeting)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw_text, encoding="utf-8")

    print(f"Meeting title: {title}")
    print(f"Meeting id: {meeting.get('id', '')}")
    print(f"Meeting uuid: {meeting.get('uuid', '')}")
    print(f"Meeting date: {meeting_date}")
    print(f"Recording file id: {recording_file.get('id', '')}")
    print(f"Recording type: {recording_file.get('recording_type', '')}")
    print(f"Download URL: {recording_file.get('download_url', '')}")
    print(f"Play URL: {recording_file.get('play_url', '')}")
    print(f"Speakers: {', '.join(meta.get('speakers', [])) or '(none detected)'}")
    print(f"First timestamp: {meta.get('first_timestamp', '') or '(not available)'}")
    print(f"Parsed transcript length: {len(parsed_text)} chars")

    if args.output:
        print(f"Saved raw transcript to: {args.output}")

    print()
    print("Transcript preview:")
    print("-" * 80)
    if args.show_raw:
        print(raw_text)
    else:
        preview = parsed_text[:2000]
        print(preview)
        if len(parsed_text) > len(preview):
            print("\n[truncated]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
