"""Per-call metering for audio provider calls: duration, sizes, and an
estimated cost, emitted as one structured log record each.

There is no cost or latency instrumentation anywhere else in this codebase
(LLM calls persist provenance — model, prompt_version — but not cost). This
is the smallest thing that gives the evaluation harness real numbers and
gives an operator something to grep, without a table: a table would mean a
migration, an entry in conftest.py's clean_db list, and a retention story
for rows nobody queries. If a cost dashboard ever becomes a goal, a
VoiceProviderCall table is a ~30-line addition against this same dataclass
— that's the trigger to reverse this, recorded in ADR-010.

Never logged: audio bytes, transcript text, TTS input text, credentials.
Lengths only.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("strata.voice")

Capability = Literal["transcription", "speech"]

# USD. Per-minute for transcription, per-million-characters for speech.
# Verify against the provider's pricing page before trusting any report
# these numbers feed — they will drift, and the as_of date is the reminder.
PRICING_AS_OF = "2026-08"
TRANSCRIPTION_USD_PER_MINUTE: dict[str, float] = {
    "gpt-4o-mini-transcribe": 0.003,
    "gpt-4o-transcribe": 0.006,
    "whisper-1": 0.006,
}
SPEECH_USD_PER_MILLION_CHARS: dict[str, float] = {
    "gpt-4o-mini-tts": 12.0,
    "tts-1": 15.0,
    "tts-1-hd": 30.0,
}


def estimate_transcription_cost(model: str, audio_seconds: float | None) -> float | None:
    rate = TRANSCRIPTION_USD_PER_MINUTE.get(model)
    if rate is None or audio_seconds is None:
        return None
    return round(rate * audio_seconds / 60.0, 6)


def estimate_speech_cost(model: str, input_chars: int | None) -> float | None:
    rate = SPEECH_USD_PER_MILLION_CHARS.get(model)
    if rate is None or input_chars is None:
        return None
    return round(rate * input_chars / 1_000_000.0, 6)


@dataclass
class MetricsBuilder:
    """Filled in by the provider during the call; frozen into CallMetrics on
    exit. Local backends leave estimated_cost_usd as None — they're free —
    which is itself the number the evaluation reports."""

    capability: Capability
    backend: str
    model: str
    input_bytes: int | None = None
    input_chars: int | None = None
    output_bytes: int | None = None
    audio_seconds: float | None = None
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class CallMetrics:
    capability: Capability
    backend: str
    model: str
    duration_ms: int
    ok: bool
    input_bytes: int | None
    input_chars: int | None
    output_bytes: int | None
    audio_seconds: float | None
    estimated_cost_usd: float | None
    error: str | None = None


@asynccontextmanager
async def meter(
    capability: Capability, backend: str, model: str, *, sink: list[CallMetrics] | None = None
) -> AsyncIterator[MetricsBuilder]:
    """Wraps one provider call. Providers fill the builder in; on exit one
    INFO record is emitted whether the call succeeded or raised, and the
    frozen metrics are appended to `sink` if given (the eval harness's way
    of collecting them without parsing logs). Exceptions propagate."""
    builder = MetricsBuilder(capability=capability, backend=backend, model=model)
    started = time.perf_counter()
    error: str | None = None
    try:
        yield builder
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        metrics = CallMetrics(
            capability=capability,
            backend=backend,
            model=model,
            duration_ms=duration_ms,
            ok=error is None,
            input_bytes=builder.input_bytes,
            input_chars=builder.input_chars,
            output_bytes=builder.output_bytes,
            audio_seconds=builder.audio_seconds,
            estimated_cost_usd=builder.estimated_cost_usd,
            error=error,
        )
        if sink is not None:
            sink.append(metrics)
        logger.info(
            "voice.%s backend=%s model=%s ok=%s duration_ms=%d input_bytes=%s input_chars=%s "
            "output_bytes=%s audio_seconds=%s estimated_cost_usd=%s%s",
            capability,
            backend,
            model,
            metrics.ok,
            duration_ms,
            metrics.input_bytes,
            metrics.input_chars,
            metrics.output_bytes,
            metrics.audio_seconds,
            metrics.estimated_cost_usd,
            f" error={error}" if error else "",
        )
