"""Provider registry.

Provider modules call register() at import time. main.py imports this module
plus each enabled provider module, which triggers self-registration.

Usage:
    from playground.providers import registry
    import playground.providers.openai  # triggers registration

    provider = registry.get("openai", model="gpt-5.4", api_key="sk-...")
"""

from __future__ import annotations

from typing import Any, Callable

from playground.core.exceptions import ConfigError
from playground.providers.base import LLMProvider

_registry: dict[str, Callable[..., LLMProvider]] = {}


def register(name: str, factory: Callable[..., LLMProvider]) -> None:
    """Register a provider factory under a string key."""
    _registry[name] = factory


def get(name: str, **kwargs: Any) -> LLMProvider:
    """Instantiate and return a registered provider."""
    if name not in _registry:
        available = ", ".join(_registry) or "(none registered)"
        raise ConfigError(f"Unknown LLM provider '{name}'. Available: {available}")
    return _registry[name](**kwargs)


def available() -> list[str]:
    return list(_registry.keys())
