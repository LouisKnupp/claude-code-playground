# Future Improvements

A running log of planned improvements. Each entry includes an author, date, and status.

---

## Schema Normalization + Agent Tool Expansion
**Author:** Claude
**Date:** 2026-03-23
**Status:** Planned

### Problem
All connector-specific metadata (dates, speakers) is buried in opaque `metadata_json`. Date filtering in `search_zoom` is declared but not implemented. The agent has only 3 rigid tools with no way to run ad-hoc queries. There's no table modeling who attended which meetings, and first-name-only entity rejections are silently discarded with no audit trail.

### Phase 1 — Schema Normalization

**New columns on `documents`:**
```sql
ALTER TABLE documents ADD COLUMN doc_date TEXT;     -- ISO-8601: "2026-03-17T19:59:59"
ALTER TABLE documents ADD COLUMN meeting_uuid TEXT; -- Zoom only, NULL for Notes
CREATE INDEX IF NOT EXISTS idx_documents_date ON documents(doc_date);
```
Populated via idempotent `_add_column_if_missing()` helper on startup.

**New `meeting_participants` table** (confirmed speakers, distinct from `entity_mentions` which captures anyone *referenced* in text):
```sql
CREATE TABLE IF NOT EXISTS meeting_participants (
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    entity_id    TEXT NOT NULL REFERENCES entities(id)  ON DELETE CASCADE,
    speaker_name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (document_id, entity_id)
);
```

**New `skipped_entities` table** (audit trail for dropped first-name-only entities):
```sql
CREATE TABLE IF NOT EXISTS skipped_entities (
    id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
    raw_name TEXT NOT NULL, reason TEXT NOT NULL, skipped_at TEXT NOT NULL
);
```

**Backfill:** `playground migrate` CLI command. Zoom `doc_date` is a pure SQL update (`replace(meeting_date, ' ', 'T')`). Apple Notes `modified_at` needs Python parsing via existing `_parse_mod_date()`. `meeting_participants` re-derived from `metadata_json speakers[]` + entity alias lookup.

**Forward pipeline:** `indexer.py` populates `doc_date`, `meeting_uuid`, and `meeting_participants` on every new sync automatically.

### Phase 2 — New Agent Tools

**`query_db(sql)`** — open-ended read-only SQL access. Safeguards: separate read-only connection, SELECT-only guard, auto-append `LIMIT 50`, truncate payload >8KB. Tool description embeds full schema so the LLM knows exact column names. Returns `{rows, row_count, truncated}`.

**`find_co_attendees(person_name, days, limit)`** — "who was in meetings with Alice last month?" Self-join on `meeting_participants`, alias-aware, returns co-attendee name + shared meeting count + most recent date.

**`list_recent_meetings(days, person_name)`** — sorted by `doc_date`, optional person filter via `meeting_participants` join. Returns title, date, speakers, deep link.

**Fix `search_zoom` date filtering** — `date_from`/`date_to` params currently declared but ignored. Implement once `doc_date` is a real column.

**Files to modify:** `db.py`, `indexer.py`, `entity_extractor.py`, `tools/query_db.py` (new), `tools/find_co_attendees.py` (new), `tools/list_recent_meetings.py` (new), `tools/search_zoom.py`, `pipeline/agent_loop.py` (update system prompt), `cli/main.py` (register tools, add `migrate` command).

### Phase 3 — Knowledge Graph (deferred)

SQL + `meeting_participants` handles ~80% of relationship queries at current scale. When the dataset grows to ~200+ meetings / ~50+ people, add a **NetworkX in-memory graph** built at query time from `meeting_participants` — no separate database needed.

Unlocks: shortest path ("how are Alice and Charlie connected?"), degree centrality ("most connected person"), working-group clustering. Rebuild time is <1ms at current scale; cache with TTL as data grows.

Future tool: `find_connections(person_a, person_b)`.

---
