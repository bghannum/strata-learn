"""The two audio provider protocols and their I/O shapes.

Mirrors app/semantics/llm_provider.py: `typing.Protocol` rather than an ABC
(structural typing lets the fakes in fakes.py and any test double satisfy
the interface without inheriting), frozen dataclasses for inputs and
outputs, and a `usage` dict shaped like LLMResponse.usage.

Two separate protocols rather than one "AudioProvider" because the two
capabilities have nothing in common at the call site — one takes bytes and
returns text, the other takes text and yields bytes — and a deployment may
legitimately point them at different backends (hosted transcription with
local speech, say). Keeping them separate from LLMProvider is the original
plan's own wording; widening the text interface with audio methods would
force every text-only implementation to stub them.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class AudioClip:
    """One uploaded recording, already validated (validation.py) — providers
    never see bytes the API hasn't allowlisted first."""

    data: bytes
    content_type: str
    # OpenAI sniffs the container format from the *filename*, not the
    # Content-Type, so the extension has to be right. Derived from the
    # validated content type, never trusted from the upload.
    filename: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    model: str
    language: str | None
    duration_seconds: float | None
    usage: dict[str, float] = field(default_factory=dict)


class TranscriptionProvider(Protocol):
    async def transcribe(self, clip: AudioClip, *, vocabulary: Sequence[str] = ()) -> TranscriptionResult:
        """`vocabulary` is a sequence of terms, not a pre-formatted prompt
        string: shared code (vocabulary.py) owns *which* terms, and each
        backend owns how to encode them for its API (`prompt=` for OpenAI,
        `initial_prompt=` for faster-whisper). Both call the same
        build_vocabulary_prompt() so an evaluation comparing them measures
        model differences, not prompt-format differences."""
        ...


class SpeechProvider(Protocol):
    # The route reads these rather than hardcoding a Content-Type: the hosted
    # backend returns MP3, and a local one may return WAV, so a backend swap
    # must not be able to produce a header that lies about the body.
    media_type: str
    model: str

    # `def`, not `async def`, on purpose. An `async def ... yield` function
    # is a plain callable that *returns* an async iterator; declaring the
    # protocol member `async def` would require callers to await it before
    # iterating, which is exactly wrong for an async generator. Getting this
    # backwards produces a confusing TypeError at the first `async for`.
    def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[bytes]: ...
