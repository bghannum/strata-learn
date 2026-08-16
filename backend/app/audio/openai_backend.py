"""Hosted OpenAI backends for both audio protocols.

Constructed only by app/audio/dependencies.py, and only when a capability is
pointed at "openai" with OPENAI_API_KEY set. Same test seam as
AnthropicProvider: an injectable `client` so the provider can be exercised
against a stub with no network and no key.

Every call runs inside metering.meter(), which is where duration, sizes and
estimated cost are recorded — providers own that, not routes, because a
route shouldn't know which backend it's talking to.
"""

from collections.abc import AsyncIterator, Sequence

import openai

from app.audio.errors import AudioProviderError
from app.audio.metering import (
    CallMetrics,
    estimate_speech_cost,
    estimate_transcription_cost,
    meter,
)
from app.audio.providers import AudioClip, TranscriptionResult
from app.audio.vocabulary import build_vocabulary_prompt

# The SDK reads the container format from the filename tuple, so the clip's
# validated extension has to travel with the bytes — see AudioClip.filename.
_TTS_CHUNK_BYTES = 4096


class OpenAITranscriptionProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 30.0,
        client: openai.AsyncOpenAI | None = None,
        metrics_sink: list[CallMetrics] | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("OpenAITranscriptionProvider requires a non-empty api_key")
        # A timeout, not retries: the SDK's default is two automatic retries,
        # and an auto-retried paid audio call doubles the bill for a request
        # the user can simply resend. max_retries=0 keeps that decision at
        # the API layer, where the established disposition is "persist
        # nothing, return 503".
        self._client = client if client is not None else openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )
        self.model = model
        self._metrics_sink = metrics_sink

    async def transcribe(self, clip: AudioClip, *, vocabulary: Sequence[str] = ()) -> TranscriptionResult:
        prompt = build_vocabulary_prompt(vocabulary)
        async with meter("transcription", "openai", self.model, sink=self._metrics_sink) as m:
            m.input_bytes = len(clip.data)
            try:
                response = await self._client.audio.transcriptions.create(
                    file=(clip.filename, clip.data, clip.content_type),
                    model=self.model,
                    # `prompt` is the portable conditioning path — accepted by
                    # every transcription model the SDK exposes, and it's the
                    # same string the local backend gets as initial_prompt.
                    prompt=prompt or openai.NOT_GIVEN,
                    response_format="json",
                )
            except openai.OpenAIError as exc:
                raise AudioProviderError(f"transcription failed: {type(exc).__name__}") from exc

            duration = _duration_seconds(response)
            m.audio_seconds = duration
            m.estimated_cost_usd = estimate_transcription_cost(self.model, duration)
            usage: dict[str, float] = {}
            if duration is not None:
                usage["audio_seconds"] = duration
            tokens = getattr(response, "usage", None)
            if tokens is not None and getattr(tokens, "type", None) == "tokens":
                usage["input_tokens"] = float(tokens.input_tokens)
                usage["output_tokens"] = float(tokens.output_tokens)

            return TranscriptionResult(
                text=response.text,
                model=self.model,
                language=_language(response),
                duration_seconds=duration,
                usage=usage,
            )


def _duration_seconds(response: object) -> float | None:
    """The SDK returns one of two usage shapes depending on how the model is
    billed — by audio duration or by tokens. Only the former carries a
    duration; a token-billed response leaves it None and the cost estimate
    falls back to None too, which is honest rather than invented."""
    usage = getattr(response, "usage", None)
    if usage is not None and getattr(usage, "type", None) == "duration":
        return float(usage.seconds)
    duration = getattr(response, "duration", None)
    return float(duration) if isinstance(duration, (int, float)) else None


def _language(response: object) -> str | None:
    languages = getattr(response, "languages", None)
    if languages:
        first = languages[0]
        return getattr(first, "language", None) or (first if isinstance(first, str) else None)
    language = getattr(response, "language", None)
    return language if isinstance(language, str) else None


class OpenAISpeechProvider:
    media_type = "audio/mpeg"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        voice: str = "alloy",
        timeout_seconds: float = 30.0,
        client: openai.AsyncOpenAI | None = None,
        metrics_sink: list[CallMetrics] | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("OpenAISpeechProvider requires a non-empty api_key")
        self._client = client if client is not None else openai.AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )
        self.model = model
        self._voice = voice
        self._metrics_sink = metrics_sink

    async def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[bytes]:
        """An async generator: yields MP3 as it arrives from the SDK's
        streaming response. The first `await` inside the route (pulling the
        first chunk) is what surfaces a refused request as a real 503 rather
        than a truncated 200 — see the speech routes."""
        async with meter("speech", "openai", self.model, sink=self._metrics_sink) as m:
            m.input_chars = len(text)
            m.estimated_cost_usd = estimate_speech_cost(self.model, len(text))
            output = 0
            try:
                async with self._client.audio.speech.with_streaming_response.create(
                    model=self.model,
                    voice=voice or self._voice,
                    input=text,
                    response_format="mp3",
                ) as response:
                    async for chunk in response.iter_bytes(_TTS_CHUNK_BYTES):
                        output += len(chunk)
                        yield chunk
            except openai.OpenAIError as exc:
                raise AudioProviderError(f"speech synthesis failed: {type(exc).__name__}") from exc
            finally:
                m.output_bytes = output
