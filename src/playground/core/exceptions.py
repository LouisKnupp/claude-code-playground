"""Domain exception hierarchy. All playground errors inherit from PlaygroundError."""


class PlaygroundError(Exception):
    """Base for all playground errors."""


class ConfigError(PlaygroundError):
    """Invalid or missing configuration."""


class ProviderError(PlaygroundError):
    """LLM provider call failed."""


class EmbeddingError(PlaygroundError):
    """Embedding provider call failed."""


class ConnectorError(PlaygroundError):
    """Data source connector failed to fetch documents."""

    def __init__(self, connector: str, message: str) -> None:
        super().__init__(f"[{connector}] {message}")
        self.connector = connector


class ToolError(PlaygroundError):
    """A tool call failed during agent execution."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"[{tool_name}] {message}")
        self.tool_name = tool_name


class StorageError(PlaygroundError):
    """SQLite read/write failed."""


class PermissionError(ConnectorError):
    """OS-level permission denied (e.g. Apple Notes Automation access)."""
