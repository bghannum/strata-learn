"""No real OpenAI calls anywhere in this file — both providers are exercised
through an injected stub client, the same way test_llm_provider.py stubs
the Anthropic SDK. The assertions are the *contract* both backends have to
meet: the vocabulary prompt reaches the request, the result shape is
populated, and provider errors surface as AudioProviderError rather than a
raw SDK exception."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import openai
import pytest

from app.audio.errors import AudioProviderError
from app.audio.metering import CallMetrics
from app.audio.openai_backend import OpenAISpeechProvider, OpenAITranscriptionProvider
from app.audio.providers import AudioClip

CLIP = AudioClip(data=b"\x1a\x45\xdf\xa3" + b"\x00" * 100, content_type="audio/webm", filename="answer.webm")


@dataclass
class _DurationUsage:
    seconds: float
    type: str = "duration"


@dataclass
class _StubTranscription:
    text: str
    usage: object = None
    languages: list = field(default_factory=list)


class _StubTranscriptions:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response: _StubTranscription | None = None
        self.raise_error: Exception | None = None

    async def create(self, **kwargs) -> _StubTranscription:
        self.calls.append(kwargs)
        if self.raise_error is not None:
            raise self.raise_error
        assert self.response is not None
        return self.response


class _StubStreamedResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _StubSpeechStreaming:
    def __init__(self, owner: "_StubSpeech") -> None:
        self._owner = owner

    @asynccontextmanager
    async def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if self._owner.raise_error is not None:
            raise self._owner.raise_error
        yield _StubStreamedResponse(self._owner.chunks)


class _StubSpeech:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.chunks: list[bytes] = []
        self.raise_error: Exception | None = None
        self.with_streaming_response = _StubSpeechStreaming(self)


@dataclass
class _StubAudio:
    transcriptions: _StubTranscriptions
    speech: _StubSpeech


@dataclass
class _StubClient:
    audio: _StubAudio


def _client() -> _StubClient:
    return _StubClient(audio=_StubAudio(transcriptions=_StubTranscriptions(), speech=_StubSpeech()))


# --- transcription ---


async def test_transcription_sends_the_file_tuple_and_the_vocabulary_prompt() -> None:
    client = _client()
    client.audio.transcriptions.response = _StubTranscription(text="the worker uses arq", usage=_DurationUsage(3.5))
    sink: list[CallMetrics] = []
    provider = OpenAITranscriptionProvider(api_key="k", model="gpt-4o-mini-transcribe", client=client, metrics_sink=sink)

    result = await provider.transcribe(CLIP, vocabulary=["arq", "run_layer_b"])

    call = client.audio.transcriptions.calls[0]
    # The SDK sniffs the container from the filename, so the tuple matters.
    assert call["file"] == ("answer.webm", CLIP.data, "audio/webm")
    assert call["model"] == "gpt-4o-mini-transcribe"
    assert call["prompt"] == "Technical terms: arq, run_layer_b."
    assert result.text == "the worker uses arq"
    assert result.model == "gpt-4o-mini-transcribe"
    assert result.duration_seconds == 3.5
    assert result.usage["audio_seconds"] == 3.5
    # Metered, with a cost estimate from the duration.
    assert sink[0].ok is True
    assert sink[0].input_bytes == len(CLIP.data)
    assert sink[0].estimated_cost_usd == pytest.approx(0.003 * 3.5 / 60)


async def test_transcription_omits_the_prompt_when_there_is_no_vocabulary() -> None:
    client = _client()
    client.audio.transcriptions.response = _StubTranscription(text="hi")
    provider = OpenAITranscriptionProvider(api_key="k", model="m", client=client)

    await provider.transcribe(CLIP)

    # NOT_GIVEN, not an empty string — an empty prompt is still a prompt to
    # the API, and it must not look like the app conditioned on nothing.
    assert client.audio.transcriptions.calls[0]["prompt"] is openai.NOT_GIVEN


async def test_transcription_wraps_sdk_errors_and_still_meters_the_failure() -> None:
    client = _client()
    client.audio.transcriptions.raise_error = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    sink: list[CallMetrics] = []
    provider = OpenAITranscriptionProvider(api_key="k", model="m", client=client, metrics_sink=sink)

    with pytest.raises(AudioProviderError):
        await provider.transcribe(CLIP)
    assert sink[0].ok is False
    assert sink[0].error == "AudioProviderError"


def test_transcription_requires_a_key_when_no_client_is_injected() -> None:
    with pytest.raises(ValueError):
        OpenAITranscriptionProvider(api_key="", model="m")


# --- speech ---


async def test_speech_streams_chunks_as_they_arrive_and_meters_output() -> None:
    client = _client()
    client.audio.speech.chunks = [b"one", b"two", b"three"]
    sink: list[CallMetrics] = []
    provider = OpenAISpeechProvider(api_key="k", model="gpt-4o-mini-tts", voice="alloy", client=client, metrics_sink=sink)

    chunks = [c async for c in provider.synthesize("Hello there.")]

    assert chunks == [b"one", b"two", b"three"]
    call = client.audio.speech.calls[0]
    assert call["input"] == "Hello there."
    assert call["voice"] == "alloy"
    assert call["response_format"] == "mp3"
    assert provider.media_type == "audio/mpeg"
    assert sink[0].input_chars == len("Hello there.")
    assert sink[0].output_bytes == 11
    assert sink[0].estimated_cost_usd == pytest.approx(12.0 * 12 / 1_000_000)


async def test_speech_voice_override_wins_over_the_default() -> None:
    client = _client()
    client.audio.speech.chunks = [b"x"]
    provider = OpenAISpeechProvider(api_key="k", model="m", voice="alloy", client=client)
    async for _ in provider.synthesize("t", voice="nova"):
        pass
    assert client.audio.speech.calls[0]["voice"] == "nova"


async def test_speech_wraps_sdk_errors_on_the_first_pull() -> None:
    # The route pulls the first chunk before committing to a StreamingResponse
    # precisely so this surfaces as a real error — see the speech routes.
    client = _client()
    client.audio.speech.raise_error = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    provider = OpenAISpeechProvider(api_key="k", model="m", client=client)
    with pytest.raises(AudioProviderError):
        async for _ in provider.synthesize("t"):
            pass
