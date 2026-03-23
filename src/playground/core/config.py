"""Application configuration via pydantic-settings.

Values are loaded from (in order of precedence):
  1. Environment variables prefixed with PLAYGROUND_
  2. ~/.playground/config.toml
  3. Defaults defined here
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import tomllib

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


_DEFAULT_DATA_DIR = Path.home() / ".playground"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLAYGROUND_",
        toml_file=str(_DEFAULT_DATA_DIR / "config.toml"),
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            TomlConfigSettingsSource(settings_cls),
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    # Storage
    data_dir: Path = Field(default=_DEFAULT_DATA_DIR)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "playground.db"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    # LLM provider
    llm_provider: str = "openai"
    llm_model: str = "gpt-5.4"

    # OpenAI credentials (also read from OPENAI_API_KEY for convenience)
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )

    # Connectors
    enabled_connectors: list[str] = ["zoom", "apple_notes"]
    zoom_source_mode: str = "local"  # local | cloud | both
    zoom_transcripts_dir: Path = Field(default=Path.home() / "Documents" / "Zoom")
    zoom_api_client_id: str = ""
    zoom_api_client_secret: str = ""
    zoom_api_redirect_uri: str = "http://localhost"
    zoom_api_user_id: str = "me"
    zoom_api_access_token: str = ""
    zoom_api_refresh_token: str = ""
    zoom_api_token_expires_at: str = ""
    zoom_cloud_lookback_days: int = 365
    notes_max_age_days: int = 180

    # Employee roster (used for first-name disambiguation)
    employees_file: Path = Field(default=_DEFAULT_DATA_DIR / "employees.txt")
    name_overrides_file: Path = Field(default=_DEFAULT_DATA_DIR / "name_overrides.txt")

    # Agent loop
    max_context_turns: int = 20       # conversation turns kept in LLM context
    max_agent_iterations: int = 10    # max tool-call rounds per user query
    fts_top_k: int = 5                # results returned per tool search

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Load settings, creating the data directory if needed."""
    settings = Settings()
    settings.ensure_data_dir()
    return settings


def _serialize_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Path):
        return json.dumps(str(value))
    if isinstance(value, datetime):
        return json.dumps(value.isoformat())
    if isinstance(value, list):
        return "[" + ", ".join(_serialize_toml_value(item) for item in value) + "]"
    return json.dumps(value)


def save_config_values(values: dict[str, Any], config_path: Path | None = None) -> Path:
    path = config_path or (_DEFAULT_DATA_DIR / "config.toml")
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            existing = tomllib.load(fh)

    existing.update(values)
    lines = [f"{key} = {_serialize_toml_value(existing[key])}" for key in sorted(existing)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
