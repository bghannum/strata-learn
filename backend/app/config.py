from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn"
    redis_url: str = "redis://localhost:6379/0"

    session_secret: str = "change-me"
    cors_origins: list[str] = ["http://localhost:5173"]

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # docs/prompts/ lives at the repo root, a sibling of backend/ — this default
    # assumes the local-dev checkout layout (backend/app/config.py -> ../../docs/prompts).
    # The worker/api Docker images only contain backend/ at their WORKDIR, so that
    # relative path is wrong inside a container; PROMPTS_DIR (set via
    # docker-compose.yml's bind mount) overrides it there.
    prompts_dir: Path = Path(__file__).resolve().parents[2] / "docs" / "prompts"

    zip_upload_max_bytes: int = 50 * 1024 * 1024
    zip_upload_max_files: int = 5000

    # walker per-file size cap — skip individual files above this (generated
    # bundles, data dumps, etc.) rather than feeding them to tree-sitter
    max_file_size_bytes: int = 1 * 1024 * 1024


settings = Settings()
