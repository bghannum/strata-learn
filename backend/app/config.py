from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# The two audio backends each capability can be pointed at (ADR-010). `None`
# on the *_backend settings below means the capability is off entirely, which
# is the default and the state CI always runs in.
AudioBackend = Literal["openai", "local"]


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

    # --- Phase 8: voice (ADR-010) ---
    #
    # One switch per capability, independent of each other, `None` = off.
    # Both default to "local" so the app works out of the box with no key
    # and no bill; the compose build installs the runtimes by default
    # (INSTALL_VOICE) and weights download on first use. A selected backend
    # whose prerequisites are missing (no OPENAI_API_KEY, or the `voice`
    # extra not installed for `local` — which is the case in CI and in a
    # bare `pip install -e ".[dev]"`) resolves to off with the reason in
    # the startup log — see app/audio/dependencies.py, the single place that
    # decision is made. Nothing here is required for any non-audio feature.
    transcription_backend: AudioBackend | None = "local"
    speech_backend: AudioBackend | None = "local"
    # Load (and on a cold cache, download) the local models in the
    # background at startup, so the first read-aloud click isn't the one that
    # waits on a 300 MB fetch. Off in tests.
    voice_warm_on_startup: bool = True

    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_speech_model: str = "gpt-4o-mini-tts"
    openai_speech_voice: str = "alloy"
    # faster-whisper model name. base.en is comfortably faster than realtime
    # on CPU for short clips; small.en roughly triples the latency, which is
    # where a request-path transcription stops being acceptable.
    local_whisper_model: str = "base.en"
    # Where the local backends keep their weights. faster-whisper downloads
    # into it on first use (Hugging Face cache layout); the Kokoro files are
    # fetched into it by local_backend.py. A named volume under compose so a
    # rebuild doesn't re-download ~500 MB.
    voice_models_dir: Path = Path(__file__).resolve().parents[2] / ".voice-models"
    local_speech_voice: str = "af_sarah"

    # Bytes are the enforcement boundary for a microphone upload; duration is
    # deliberately *not* measured server-side (MediaRecorder output is a live
    # stream with no reliable duration header, and measuring means decoding).
    # 2 MiB of Opus at MediaRecorder's default bitrate is roughly eight
    # minutes — ~25x the intended answer length — so it bounds duration
    # transitively. audio_upload_max_seconds is the *client's* countdown, a
    # UX bound rather than a security one.
    audio_upload_max_bytes: int = 2 * 1024 * 1024
    audio_upload_max_seconds: int = 60
    # OpenAI's TTS `input` ceiling is 4096 characters; enforced here, before
    # the paid call, rather than by eating a 400 from the provider.
    speech_max_chars: int = 4000
    # Vocabulary hints handed to the transcription backend (app/audio/
    # vocabulary.py) are bounded twice — by term count and by joined length —
    # because they're interpolated into a paid request.
    transcription_vocab_max_terms: int = 24
    transcription_vocab_max_chars: int = 300
    voice_calls_per_hour: int = 60
    # A timeout, not retries: an auto-retried paid audio call doubles the
    # bill, and the established disposition (api/attempts.py) is "persist
    # nothing, return 503, let the user resubmit".
    audio_provider_timeout_seconds: float = 30.0


settings = Settings()
