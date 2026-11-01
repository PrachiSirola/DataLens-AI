"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    database_name: str = "datalensai"

    # Persistent data directory (DuckDB files + raw uploads)
    # Defaults to ~/.datalensai — keeps data outside the project, like MongoDB
    data_dir: str = "~/.datalensai"

    # Session
    session_secret_key: str = Field(min_length=32)
    session_expire_hours: int = 24

    # Admin bootstrap
    admin_email: str = "admin@datalensai.com"
    admin_password: str = "Admin@Secure#2026"
    admin_name: str = "Admin"

    # Initial invite code (created on first startup if none exist)
    initial_invite_code: str = "CHANGE-ME"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # Local LLM via Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "datalens-sql"
    ollama_embed_model: str = "nomic-embed-text"  # embedding model for RAG

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        """Resolved, absolute data directory path."""
        return Path(self.data_dir).expanduser().resolve()

    @property
    def duckdb_dir(self) -> Path:
        """Directory for per-user DuckDB database files."""
        p = self.data_path / "duckdb"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_dir(self) -> Path:
        """Directory for raw uploaded files."""
        p = self.data_path / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
