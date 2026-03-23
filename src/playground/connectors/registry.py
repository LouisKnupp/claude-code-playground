"""Connector registry — mirrors providers/registry.py pattern.

Connector modules call register() at import time.
main.py imports this module then each enabled connector module.

Usage:
    from playground.connectors import registry
    import playground.connectors.zoom          # triggers registration
    import playground.connectors.apple_notes   # triggers registration

    connector = registry.get("zoom", transcripts_dir=Path("~/Downloads/zoom"))
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from playground.connectors.base import DataConnector
from playground.core.exceptions import ConfigError

_registry: dict[str, Callable[..., DataConnector]] = {}


def register(name: str, factory: Callable[..., DataConnector]) -> None:
    _registry[name] = factory


def get(name: str, **kwargs: Any) -> DataConnector:
    if name not in _registry:
        available = ", ".join(_registry) or "(none registered)"
        raise ConfigError(f"Unknown connector '{name}'. Available: {available}")
    return _registry[name](**kwargs)


def available() -> list[str]:
    return list(_registry.keys())
