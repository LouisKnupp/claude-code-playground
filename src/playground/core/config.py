"""Application configuration via pydantic-settings.

Values are loaded from (in order of precedence):
  1. Environment variables prefixed with PLAYGROUND_
  2. ~/.playground/config.toml
  3. Defaults defined here
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_DATA_DIR = Path.home() / ".playground"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PLAYGROUND_",
        toml_file=str(_DEFAULT_DATA_DIR / "config.toml"),
        extra="ignore",
    )

    # Storage
    data_dir: Path = Field(default=_DEFAULT_DATA_DIR)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "playground.db"

    # LLM provider
    llm_provider: str = "openai"
    llm_model: str = "gpt-5.4"

    # OpenAI credentials (also read from OPENAI_API_KEY for convenience)
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # Connectors
    enabled_connectors: list[str] = ["zoom", "apple_notes"]
    zoom_transcripts_dir: Path = Field(default=Path.home() / "Documents" / "Zoom")

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
