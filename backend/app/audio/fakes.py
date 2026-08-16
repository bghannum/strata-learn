"""Test doubles for the audio providers. In app/, not tests/, for the same
reason FakeLLMProvider lives in app/semantics/llm_provider.py: the seam
they plug into is application code, and automated tests must never make a
real (billed, nondeterministic) audio call.

Same shape as FakeLLMProvider — a FIFO of canned results supplied at
construction, every call recorded so tests can assert on what was sent, and
a loud AssertionError on exhaustion rather than a silent default. An empty
Fake*Provider([]) is the idiom for "would fail if called", used to prove a
code path never reaches the provider.
"""

from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass

from app.audio.providers import AudioClip, TranscriptionResult


@dataclass
class RecordedTranscription:
    clip: AudioClip
    vocabulary: tuple[str, ...]


class FakeTranscriptionProvider:
    def __init__(self, results: Iterable[TranscriptionResult]) -> None:
        self._results: deque[TranscriptionResult] = deque(results)
        self.calls: list[RecordedTranscription] = []

    async def transcribe(self, clip: AudioClip, *, vocabulary: Sequence[str] = ()) -> TranscriptionResult:
        self.calls.append(RecordedTranscription(clip=clip, vocabulary=tuple(vocabulary)))
        if not self._results:
            raise AssertionError(
                f"FakeTranscriptionProvider exhausted: {len(self.calls)} calls made but only "
                f"{len(self.calls) - 1} results were seeded"
            )
        return self._results.popleft()


@dataclass
class RecordedSynthesis:
    text: str
    voice: str | None


class FakeSpeechProvider:
    """Each seeded response is a *list of chunks*, not one bytes value, so a
    test can assert the route re-yields multiple chunks rather than
    collapsing the stream — the streaming behaviour is part of the contract."""

    media_type = "audio/mpeg"
    model = "fake-speech"

    def __init__(self, responses: Iterable[list[bytes]]) -> None:
        self._responses: deque[list[bytes]] = deque(responses)
        self.calls: list[RecordedSynthesis] = []

    def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[bytes]:
        self.calls.append(RecordedSynthesis(text=text, voice=voice))
        if not self._responses:
            raise AssertionError(
                f"FakeSpeechProvider exhausted: {len(self.calls)} calls made but only "
                f"{len(self.calls) - 1} responses were seeded"
            )
        chunks = self._responses.popleft()

        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return stream()
