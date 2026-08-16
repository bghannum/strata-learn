"""The one place "is this audio capability on?" is decided.

Same shape as api/attempts.py's get_llm_provider: a plain function returning
`Provider | None`, swappable in tests via app.dependency_overrides. `None`
means the capability is off — regardless of *why*: no backend selected, a
backend selected but its credentials missing, or `local` selected without
the `voice` extra installed. Routes turn `None` into a 503 whose detail
never names a backend, so an error body can't disclose deployment config.

Two things a bare `None` can't carry, both handled here rather than in the
routes:

- *Why* it's off, for the operator. `describe_backends()` returns a reason
  string per capability; app/main.py logs it once at startup, which is where
  "I set SPEECH_BACKEND=local and nothing happened" gets answered.
- *Whether* it's on, for the UI. GET /voice/capabilities (api/voice.py)
  reports booleans so the frontend never renders a mic button that 503s on
  click.

Backend construction lives in the per-backend modules (openai_backend.py,
local_backend.py) and is reached from here lazily, so importing this module
never imports an SDK or a model runtime.
"""

from dataclasses import dataclass

from app.audio.providers import SpeechProvider, TranscriptionProvider
from app.config import settings


@dataclass(frozen=True)
class BackendStatus:
    enabled: bool
    backend: str | None
    model: str | None
    # Human-readable and operator-facing only — never returned by an
    # endpoint. `None` when enabled.
    reason: str | None


def _importable(module: str) -> bool:
    try:
        __import__(module)
    except ImportError:
        return False
    return True


def _openai_status(model: str) -> BackendStatus:
    if not settings.openai_api_key:
        return BackendStatus(enabled=False, backend="openai", model=model, reason="OPENAI_API_KEY is not set")
    # The backend module is checked as well as the credential, so a build
    # that doesn't carry it (the phase lands backend by backend) reports
    # "off" rather than raising at request time.
    if not _importable("app.audio.openai_backend"):
        return BackendStatus(enabled=False, backend="openai", model=model, reason="OpenAI backend is not in this build")
    return BackendStatus(enabled=True, backend="openai", model=model, reason=None)


def _local_status(model: str, *, runtime_module: str, package: str) -> BackendStatus:
    if not _importable(runtime_module):
        return BackendStatus(
            enabled=False,
            backend="local",
            model=model,
            reason=f"{package} is not installed — build the image with the `voice` extra",
        )
    if not _importable("app.audio.local_backend"):
        return BackendStatus(enabled=False, backend="local", model=model, reason="local backend is not in this build")
    return BackendStatus(enabled=True, backend="local", model=model, reason=None)


def transcription_status() -> BackendStatus:
    match settings.transcription_backend:
        case None:
            return BackendStatus(enabled=False, backend=None, model=None, reason="TRANSCRIPTION_BACKEND is not set")
        case "openai":
            return _openai_status(settings.openai_transcription_model)
        case "local":
            return _local_status(settings.local_whisper_model, runtime_module="faster_whisper", package="faster-whisper")
    return BackendStatus(enabled=False, backend=None, model=None, reason="unknown TRANSCRIPTION_BACKEND")


def speech_status() -> BackendStatus:
    match settings.speech_backend:
        case None:
            return BackendStatus(enabled=False, backend=None, model=None, reason="SPEECH_BACKEND is not set")
        case "openai":
            return _openai_status(settings.openai_speech_model)
        case "local":
            return _local_status("kokoro", runtime_module="kokoro_onnx", package="kokoro-onnx")
    return BackendStatus(enabled=False, backend=None, model=None, reason="unknown SPEECH_BACKEND")


def get_transcription_provider() -> TranscriptionProvider | None:
    status = transcription_status()
    if not status.enabled:
        return None
    if status.backend == "openai":
        from app.audio.openai_backend import OpenAITranscriptionProvider

        return OpenAITranscriptionProvider(
            api_key=settings.openai_api_key or "",
            model=settings.openai_transcription_model,
            timeout_seconds=settings.audio_provider_timeout_seconds,
        )
    if status.backend == "local":
        from app.audio.local_backend import LocalTranscriptionProvider

        return LocalTranscriptionProvider(model_name=settings.local_whisper_model)
    return None


def get_speech_provider() -> SpeechProvider | None:
    status = speech_status()
    if not status.enabled:
        return None
    if status.backend == "openai":
        from app.audio.openai_backend import OpenAISpeechProvider

        return OpenAISpeechProvider(
            api_key=settings.openai_api_key or "",
            model=settings.openai_speech_model,
            voice=settings.openai_speech_voice,
            timeout_seconds=settings.audio_provider_timeout_seconds,
        )
    if status.backend == "local":
        from app.audio.local_backend import LocalSpeechProvider

        return LocalSpeechProvider()
    return None


def describe_backends() -> list[str]:
    """One line per capability for the startup log."""
    lines = []
    for name, status in (("transcription", transcription_status()), ("speech", speech_status())):
        if status.enabled:
            lines.append(f"voice.{name} backend={status.backend} model={status.model}")
        else:
            lines.append(f"voice.{name} disabled reason={status.reason!r}")
    return lines
