from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env lives at the repo root (README.md: `cp .env.example .env` runs
    # there), not in backend/ — but the README's own local-dev workflow says
    # `cd backend` before running anything. A bare "env_file=".env"" is
    # CWD-relative, so it silently finds nothing there, and every setting
    # normally sourced from it (e.g. anthropic_api_key) falls back to its
    # Python default. That was harmless before Phase 2 (nothing read
    # anthropic_api_key), but now makes every host-run indexing job fail
    # before parsing (found via Codex's Phase 2 pre-push review). Resolve an
    # absolute path instead, matching prompts_dir's approach below — this
    # finds the real file regardless of CWD. Doesn't affect the Docker path:
    # docker-compose.yml injects env vars directly via its own env_file:
    # directive, and no .env is ever copied into the image, so this path
    # simply doesn't exist there and pydantic-settings falls through to the
    # already-correctly-populated process environment, same as before.
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn"
    redis_url: str = "redis://localhost:6379/0"

    # ADR-007's single-tenant lockout only closes registration *after* the
    # first account exists — on a freshly reachable deployment, whoever hits
    # POST /auth/register first (not necessarily the operator) permanently
    # owns the app. Requiring this out-of-band secret in the request body
    # closes that race (found via Codex's Phase 4b pre-push review, round
    # 3). Reusing the slot originally reserved for session signing, which
    # went unused once sessions ended up DB-backed rather than signed
    # (see app/auth/session.py) — same "operator must change this before a
    # real deployment" placeholder value either way.
    registration_secret: str = "change-me"
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
