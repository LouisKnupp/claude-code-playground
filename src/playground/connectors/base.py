"""DataConnector protocol.

Each data source (Zoom, Apple Notes, Slack, …) implements this protocol.
Connectors are responsible for fetching raw content and returning Document
objects with clean plain text and a deep_link for source verification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from playground.core.models import Document


@runtime_checkable
class DataConnector(Protocol):
    """Contract all data source connectors must satisfy."""

    @property
    def source_type(self) -> str:
        """Stable identifier: 'zoom', 'apple_notes', 'slack', …"""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable name shown in CLI output."""
        ...

    def fetch_all(self) -> list[Document]:
        """Return all documents from this source."""
        ...

    def fetch_updated(self, since: datetime) -> list[Document]:
        """Return only documents modified after `since` (for --watch mode)."""
        ...
