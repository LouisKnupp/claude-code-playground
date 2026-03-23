"""Employee roster — authoritative list of known full names for entity disambiguation.

The roster is loaded from a plain text file (one full name per line).
It builds an index from first-name token → [full names] so that a first-name-only
entity can be quickly checked for uniqueness.

Typical location: ~/.playground/employees.txt
"""

from __future__ import annotations

from pathlib import Path


class EmployeeRoster:
    """Immutable first-name index built from a flat list of full names.

    Optionally augmented with manual overrides (first_name → full_name) that
    take highest priority over all other resolution methods.
    """

    def __init__(
        self,
        names: list[str],
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._names: list[str] = [n.strip() for n in names if n.strip()]
        # Index: lowercase first token → list of full names
        self._by_first: dict[str, list[str]] = {}
        for name in self._names:
            key = name.split()[0].lower()
            self._by_first.setdefault(key, []).append(name)
        # Manual overrides: lowercase first-name → full name
        self._overrides: dict[str, str] = {
            k.strip().lower(): v.strip() for k, v in (overrides or {}).items()
        }

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def resolve_override(self, first_name: str) -> str | None:
        """Return the manually overridden full name for *first_name*, or None."""
        return self._overrides.get(first_name.strip().lower())

    def resolve(self, first_name: str) -> list[str]:
        """Return all full names whose first token matches *first_name* (case-insensitive)."""
        return self._by_first.get(first_name.strip().lower(), [])

    def resolve_unique(self, first_name: str) -> str | None:
        """Return the single matching full name, or None if zero or multiple match."""
        matches = self.resolve(first_name)
        return matches[0] if len(matches) == 1 else None

    def is_known_full_name(self, name: str) -> bool:
        """Return True if *name* appears verbatim in the roster (case-insensitive)."""
        lower = name.strip().lower()
        return any(n.lower() == lower for n in self._names)

    @property
    def all_names(self) -> list[str]:
        return list(self._names)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path, overrides_path: Path | None = None) -> "EmployeeRoster":
        """Load roster from a text file; returns an empty roster if file is absent.

        Optionally loads a name_overrides file (``FirstName = Full Name`` lines).
        """
        names: list[str] = []
        if path.exists():
            names = path.read_text(encoding="utf-8").splitlines()

        overrides: dict[str, str] = {}
        if overrides_path and overrides_path.exists():
            for line in overrides_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    first, _, full = line.partition("=")
                    overrides[first.strip()] = full.strip()

        return cls(names, overrides)

    @classmethod
    def empty(cls) -> "EmployeeRoster":
        return cls([])
