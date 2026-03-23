"""One-time and on-demand cleanup of ambiguous person entities.

Strategy
--------
1. Find all person entities whose canonical_name is a single token (first-name-only).
2. For each, check whether any stored alias is a full name → if so, promote it.
3. For the remainder, look at every document the entity is mentioned in.
   Pull the ``speakers`` list from that document's metadata_json and try to
   match the first name to exactly one speaker.
4. If a unique match is found, rename the entity (or merge it into the
   existing full-name entity if one already exists).
5. If the match is ambiguous or absent, leave the entity alone and report it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from playground.core.roster import EmployeeRoster
from playground.storage.db import Database


@dataclass
class CleanupResult:
    promoted: list[tuple[str, str]] = field(default_factory=list)   # (old_name, new_name)
    merged: list[tuple[str, str]] = field(default_factory=list)     # (first_name, kept_full_name)
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)  # (name, candidates)
    unresolvable: list[str] = field(default_factory=list)            # names with no speaker match


def _is_first_name_only(name: str) -> bool:
    return " " not in name.strip()


def _first_token(name: str) -> str:
    return name.strip().split()[0].lower()


def _apply_fix(
    db: Database,
    dry_run: bool,
    result: CleanupResult,
    entity_id: str,
    canonical: str,
    best: str,
) -> None:
    """Shared logic to either merge into an existing entity or rename this one."""
    existing = db.get_entity_by_alias(best)
    if existing and existing["id"] != entity_id:
        result.merged.append((canonical, best))
        if not dry_run:
            if canonical not in db.get_aliases(existing["id"]):
                db.add_alias(canonical, existing["id"])
            db.merge_entity_into(keep_id=existing["id"], discard_id=entity_id)
            db.commit()
    else:
        result.promoted.append((canonical, best))
        if not dry_run:
            db.update_canonical_name(entity_id, best)
            db.add_alias(canonical, entity_id)
            db.commit()


def run_cleanup(
    db: Database,
    dry_run: bool = False,
    roster: EmployeeRoster | None = None,
    delete_unresolvable: bool = False,
) -> CleanupResult:
    """Scan all person entities and fix first-name-only entries.

    Resolution order for each ambiguous entity:
    1. Roster unique match — authoritative, overrides LLM aliases
    2. Full-name alias that is also confirmed by the roster
    3. Speaker metadata from source documents (unique only)
    4. Any remaining full-name alias (longest wins) — only if no roster loaded
    5. Unresolvable / ambiguous

    When *dry_run* is True, no writes are made — results show what *would* happen.
    """
    result = CleanupResult()

    persons = db.list_person_entities()
    for row in persons:
        entity_id: str = row["id"]
        canonical: str = row["canonical_name"]

        if not _is_first_name_only(canonical):
            continue  # Already a full name — nothing to do

        aliases = db.get_aliases(entity_id)

        # --- Step 0: Manual override (highest priority) ---
        if roster:
            override = roster.resolve_override(canonical)
            if override:
                _apply_fix(db, dry_run, result, entity_id, canonical, override)
                continue

        # --- Step 1: Roster unique match ---
        if roster:
            roster_match = roster.resolve_unique(canonical)
            if roster_match:
                _apply_fix(db, dry_run, result, entity_id, canonical, roster_match)
                continue

        # --- Step 2: Full-name alias confirmed by roster ---
        full_name_aliases = [a for a in aliases if not _is_first_name_only(a)]
        if roster and full_name_aliases:
            roster_confirmed = [a for a in full_name_aliases if roster.is_known_full_name(a)]
            if len(roster_confirmed) == 1:
                _apply_fix(db, dry_run, result, entity_id, canonical, roster_confirmed[0])
                continue

        # --- Step 3: Speaker metadata from source documents ---
        doc_rows = db.get_entity_documents(entity_id)
        candidate_speakers: set[str] = set()
        for doc_row in doc_rows:
            try:
                meta = json.loads(doc_row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                continue
            for speaker in meta.get("speakers", []):
                if _first_token(speaker) == _first_token(canonical):
                    candidate_speakers.add(speaker)

        candidate_speakers.discard(canonical)
        full_candidates = [s for s in candidate_speakers if not _is_first_name_only(s)]

        if len(full_candidates) == 1:
            _apply_fix(db, dry_run, result, entity_id, canonical, full_candidates[0])
            continue

        # --- Step 4: Any full-name alias, if no roster loaded ---
        if not roster and full_name_aliases:
            _apply_fix(db, dry_run, result, entity_id, canonical, max(full_name_aliases, key=len))
            continue

        # Ambiguous or unresolvable
        if roster:
            roster_candidates = roster.resolve(canonical)
            if roster_candidates:
                result.ambiguous.append((canonical, sorted(roster_candidates)))
                continue
        if full_candidates:
            result.ambiguous.append((canonical, sorted(full_candidates)))
        else:
            result.unresolvable.append(canonical)
            if delete_unresolvable and not dry_run:
                db.delete_entity(entity_id)
                db.commit()

    return result
