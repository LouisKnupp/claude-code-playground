# claude-code-playground

`claude-code-playground` is a local AI work-context assistant for searching and chatting with your own material.

Today the project indexes:

- Zoom transcript exports from a local folder
- Zoom cloud recording transcripts via the Zoom API
- Apple Notes via AppleScript on macOS

It stores normalized documents in SQLite, builds a full-text search index, extracts entities with an LLM, and exposes that data through both a CLI sync workflow and an interactive chat interface.

## What It Does

The project has two main jobs:

1. Ingest your local data into a searchable database
2. Let an LLM answer questions grounded in that indexed data

The current workflow looks like this:

1. A connector fetches raw documents from a source like Zoom or Apple Notes
2. The ingestion pipeline hashes and deduplicates documents
3. Documents are stored in SQLite and indexed with FTS5
4. The configured LLM extracts people/entities from new or changed documents
5. The chat loop uses registered tools to search those indexed documents and answer questions with citations to the source material

## Current Capabilities

- `playground sync` indexes all enabled connectors
- `playground chat` starts an interactive assistant over your indexed data
- `playground history` shows recent chat sessions
- `search_zoom` searches indexed Zoom transcripts
- `search_notes` searches indexed Apple Notes
- `lookup_person` resolves a person or alias across indexed content

## Requirements

- Python 3.11 or newer
- macOS if you want to use the Apple Notes connector
- An OpenAI API key
- Local access to your Zoom transcript exports if you want Zoom indexing

## Installation

Create a virtual environment and install the project in editable mode:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

If `python3.11` is not available on your machine, use any Python interpreter that satisfies the `>=3.11` requirement in [`pyproject.toml`](/Users/louisknupp/Documents/GitHub/claude-code-playground/pyproject.toml).

After installation, the CLI entrypoint is:

```bash
playground
```

You can also run it as a module from the repo root:

```bash
PYTHONPATH=src python -m playground.cli.main
```

## Configuration

Settings are loaded in this order:

1. Environment variables prefixed with `PLAYGROUND_`
2. `~/.playground/config.toml`
3. Built-in defaults

The OpenAI API key is also read from `OPENAI_API_KEY`.

### Important Settings

- `OPENAI_API_KEY`: required for chat and entity extraction
- `PLAYGROUND_LLM_PROVIDER`: defaults to `openai`
- `PLAYGROUND_LLM_MODEL`: defaults to `gpt-5.4`
- `PLAYGROUND_ENABLED_CONNECTORS`: enabled connectors
- `PLAYGROUND_ZOOM_TRANSCRIPTS_DIR`: defaults to `~/Documents/Zoom`
- `PLAYGROUND_ZOOM_SOURCE_MODE`: `local`, `cloud`, or `both` and defaults to `local`
- `PLAYGROUND_ZOOM_API_CLIENT_ID`: required for cloud mode
- `PLAYGROUND_ZOOM_API_CLIENT_SECRET`: required for cloud mode
- `PLAYGROUND_ZOOM_API_REDIRECT_URI`: defaults to `http://localhost`
- `PLAYGROUND_ZOOM_API_USER_ID`: defaults to `me`
- `PLAYGROUND_ZOOM_API_ACCESS_TOKEN`: populated after Zoom OAuth
- `PLAYGROUND_ZOOM_API_REFRESH_TOKEN`: populated after Zoom OAuth
- `PLAYGROUND_ZOOM_API_TOKEN_EXPIRES_AT`: populated after Zoom OAuth
- `PLAYGROUND_ZOOM_CLOUD_LOOKBACK_DAYS`: defaults to `365`
- `PLAYGROUND_NOTES_MAX_AGE_DAYS`: defaults to `180`
- `PLAYGROUND_DATA_DIR`: defaults to `~/.playground`
- `PLAYGROUND_MAX_CONTEXT_TURNS`: defaults to `20`
- `PLAYGROUND_MAX_AGENT_ITERATIONS`: defaults to `10`
- `PLAYGROUND_FTS_TOP_K`: defaults to `5`

### Example `~/.playground/config.toml`

```toml
llm_provider = "openai"
llm_model = "gpt-5.4"
enabled_connectors = ["zoom", "apple_notes"]
zoom_source_mode = "local"
zoom_transcripts_dir = "/Users/yourname/Documents/Zoom"
zoom_api_client_id = ""
zoom_api_client_secret = ""
zoom_api_redirect_uri = "http://localhost"
zoom_api_user_id = "me"
zoom_api_access_token = ""
zoom_api_refresh_token = ""
zoom_api_token_expires_at = ""
zoom_cloud_lookback_days = 365
notes_max_age_days = 180
max_context_turns = 20
max_agent_iterations = 10
fts_top_k = 5
```

### Example Environment Setup

```bash
export OPENAI_API_KEY="sk-..."
export PLAYGROUND_ZOOM_TRANSCRIPTS_DIR="$HOME/Documents/Zoom"
```

### Example Zoom Cloud Setup

To ingest cloud transcripts with a Zoom General App:

```toml
zoom_source_mode = "cloud"
zoom_api_client_id = "your-client-id"
zoom_api_client_secret = "your-client-secret"
zoom_api_redirect_uri = "http://localhost"
zoom_api_user_id = "me"
zoom_cloud_lookback_days = 365
```

Start the OAuth flow:

```bash
playground zoom-auth
```

After you authorize in the browser, copy the `code` query parameter from the redirect URL and exchange it:

```bash
playground zoom-auth --code "<your-code>"
```

If you want to read both local exports and cloud transcripts, use:

```toml
zoom_source_mode = "both"
```

`both` may surface duplicate meetings if the same transcript exists locally and in Zoom cloud recordings, because the two sources do not share a stable cross-source identifier.

## CLI Usage

### Sync Data

Index all enabled connectors:

```bash
playground sync
```

Show per-file progress and real-time errors:

```bash
playground sync --verbose
```

Keep polling for changes after the initial sync:

```bash
playground sync --watch
```

Use a custom watch polling interval:

```bash
playground sync --watch --poll 10
```

### Authorize Zoom Cloud Access

Print the Zoom OAuth authorization URL:

```bash
playground zoom-auth
```

Exchange the authorization code and save the tokens:

```bash
playground zoom-auth --code "<your-code>"
```

### Start Chat

```bash
playground chat
```

Inside chat, the supported slash commands are:

- `/help`
- `/quit`
- `/exit`

### Show Session History

```bash
playground history
```

Limit the number of sessions shown:

```bash
playground history --limit 20
```

## Data Sources

### Zoom Connector

The Zoom connector reads `.vtt` and `.txt` transcript exports from a local directory tree.

It can now operate in three modes:

- `local`: only local transcript files
- `cloud`: only Zoom cloud transcript files fetched via the API
- `both`: aggregate local and cloud Zoom transcripts

Current behavior:

- Recursively scans the configured transcripts directory
- Accepts `.vtt` and `.txt` files
- Skips chat-export files that contain `chat` in the filename
- Extracts speaker labels when possible
- Derives meeting title and date from the Zoom export folder name
- Generates `file://` deep links back to the original transcript file
- Deduplicates multiple transcript variants in the same recording folder

Deduplication priority within a folder:

1. `meeting_saved_closed_caption.txt`
2. `closed_caption.txt`
3. `*.vtt`
4. Any other `.txt`

Expected folder naming pattern:

```text
YYYY-MM-DD HH.MM.SS Meeting Name
```

Default location:

```text
~/Documents/Zoom
```

#### Zoom Cloud Recordings

Cloud mode uses the Zoom API to:

- authenticate with a Zoom General App OAuth flow
- list recordings for the configured Zoom user
- detect transcript files from each meeting's `recording_files`
- download transcript text and normalize it into the same document format used by local transcripts

Current cloud behavior:

- Uses `zoom_api_user_id = "me"` by default
- Uses saved OAuth access and refresh tokens from `~/.playground/config.toml`
- Pulls transcript files only, not video/audio recording assets
- Chunks the fetch window into 30-day ranges to stay within Zoom recording query constraints
- Uses `play_url` when available as the deep link back to the Zoom recording
- Uses `zoom_cloud_lookback_days` for full syncs and the sync `since` timestamp for incremental updates

### Apple Notes Connector

The Apple Notes connector uses AppleScript to pull notes directly from the Notes app.

Current behavior:

- macOS only
- Requires Automation permission for your terminal app to control Notes
- Converts note HTML to plain text
- Stores a `notes://` deep link so a result can open directly in Notes
- Filters notes by modification date using `notes_max_age_days`

Known practical limitations:

- No native incremental API, so updates require re-fetching all notes and filtering client-side
- AppleScript becomes slow at larger note counts
- The current implementation is best suited to personal or moderate-sized note collections

## How Search and Chat Work

The assistant does not directly read your source files during chat. It answers from the indexed database and uses registered tools to retrieve evidence.

Current tool surface:

- `search_zoom`: keyword search over indexed Zoom transcripts
- `search_notes`: keyword search over indexed Apple Notes
- `lookup_person`: resolves a person or alias and returns appearances across indexed documents

The chat loop:

1. Loads recent conversation context
2. Calls the configured LLM provider
3. Lets the model call tools as needed
4. Returns an answer plus supporting sources
5. Stores messages and audit data in SQLite

## Storage

The project stores runtime data under `~/.playground` by default.

That directory includes:

- `playground.db`: SQLite database with indexed documents, entities, mentions, sessions, and audit data
- `config.toml`: optional user configuration file

## Development

Install development dependencies:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
PYTHONPATH=src pytest
```

Run a focused test file:

```bash
PYTHONPATH=src pytest -q tests/test_zoom_connector.py
```

Run linting:

```bash
ruff check .
```

## Project Layout

```text
src/playground/
  cli/          Typer commands and REPL entrypoints
  connectors/   Source adapters for Zoom and Apple Notes
  core/         Config, models, session state, exceptions, audit logging
  pipeline/     Indexing, entity extraction, and agent orchestration
  providers/    LLM provider interface and OpenAI implementation
  storage/      SQLite access layer
  tools/        Search and lookup tools exposed to the agent
tests/          Test suite
```

## Architecture Overview

- `playground.cli.main`: bootstraps config, database, provider, and tools
- `playground.cli.sync`: runs ingestion over all enabled connectors
- `playground.pipeline.indexer`: orchestrates fetch, dedup, indexing, and entity extraction
- `playground.pipeline.agent_loop`: runs the tool-calling chat loop
- `playground.storage.db`: owns SQLite persistence and FTS search
- `playground.providers.openai`: current LLM backend

## Known Limitations

- Only the OpenAI provider is implemented right now
- Apple Notes ingestion is macOS-specific
- The test workflow currently works most reliably with `PYTHONPATH=src pytest`
- Entity extraction depends on LLM availability and API credentials
- The repo currently contains some generated artifacts that should likely be cleaned up separately

## Troubleshooting

### `OPENAI_API_KEY is not set`

Set it in your shell or add it to `~/.playground/config.toml` through the `OPENAI_API_KEY` alias support in the settings model.

Example:

```bash
export OPENAI_API_KEY="sk-..."
```

### Zoom transcripts directory not found

Set the path explicitly:

```toml
zoom_transcripts_dir = "/absolute/path/to/your/Zoom/transcripts"
```

Or export:

```bash
export PLAYGROUND_ZOOM_TRANSCRIPTS_DIR="/absolute/path/to/your/Zoom/transcripts"
```

### Apple Notes permission denied

Grant your terminal app Automation access in:

`System Settings > Privacy & Security > Automation`

Your terminal must be allowed to control Notes.

### `pytest` cannot import `playground`

Run tests with:

```bash
PYTHONPATH=src pytest
```

Or install the package in editable mode first:

```bash
pip install -e ".[dev]"
```

## Roadmap Ideas

- Additional connectors such as Slack, email, or documents
- Better delta-sync support for sources without native change feeds
- More structured entity extraction and relationship modeling
- Better packaging and test ergonomics
- Cleanup of checked-in generated artifacts

## Status

This project is a practical local prototype, not a polished multi-user platform. It is already useful as a personal knowledge/search layer over Zoom and Notes, but it still has rough edges around packaging, platform assumptions, and connector maturity.
