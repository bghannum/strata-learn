import logging

import pytest

from app.audio.fakes import FakeSpeechProvider, FakeTranscriptionProvider
from app.audio.metering import (
    CallMetrics,
    estimate_speech_cost,
    estimate_transcription_cost,
    meter,
)
from app.audio.providers import AudioClip, TranscriptionResult

CLIP = AudioClip(data=b"\x1a\x45\xdf\xa3", content_type="audio/webm", filename="answer.webm")


async def test_fake_transcription_returns_seeded_results_in_order_and_records_calls() -> None:
    fake = FakeTranscriptionProvider(
        [
            TranscriptionResult(text="first", model="fake", language="en", duration_seconds=1.0),
            TranscriptionResult(text="second", model="fake", language="en", duration_seconds=1.0),
        ]
    )
    first = await fake.transcribe(CLIP, vocabulary=["asyncpg"])
    second = await fake.transcribe(CLIP)
    assert (first.text, second.text) == ("first", "second")
    assert fake.calls[0].vocabulary == ("asyncpg",)
    assert fake.calls[1].vocabulary == ()


async def test_fake_transcription_fails_loud_when_exhausted() -> None:
    # An empty fake is the idiom for "would fail if called" — proving a code
    # path never reaches the provider — so exhaustion must be an error, not
    # a silent default.
    fake = FakeTranscriptionProvider([])
    with pytest.raises(AssertionError, match="exhausted"):
        await fake.transcribe(CLIP)


async def test_fake_speech_yields_the_seeded_chunks_separately() -> None:
    # Streaming is part of the contract; a fake that collapsed chunks would
    # let a route that buffered everything pass.
    fake = FakeSpeechProvider([[b"one", b"two", b"three"]])
    chunks = [chunk async for chunk in fake.synthesize("hello", voice="alloy")]
    assert chunks == [b"one", b"two", b"three"]
    assert fake.calls[0].text == "hello"
    assert fake.calls[0].voice == "alloy"


def test_fake_speech_fails_loud_when_exhausted() -> None:
    with pytest.raises(AssertionError, match="exhausted"):
        FakeSpeechProvider([]).synthesize("x")


async def test_meter_records_a_successful_call_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    sink: list[CallMetrics] = []
    with caplog.at_level(logging.INFO, logger="strata.voice"):
        async with meter("transcription", "openai", "gpt-4o-mini-transcribe", sink=sink) as m:
            m.input_bytes = 1234
            m.audio_seconds = 15.0
            m.estimated_cost_usd = estimate_transcription_cost("gpt-4o-mini-transcribe", 15.0)

    assert len(sink) == 1
    metrics = sink[0]
    assert metrics.ok is True
    assert metrics.input_bytes == 1234
    assert metrics.estimated_cost_usd == pytest.approx(0.00075)
    assert metrics.duration_ms >= 0
    assert "voice.transcription backend=openai" in caplog.text
    # Lengths, never content.
    assert "1234" in caplog.text


async def test_meter_records_a_failed_call_and_re_raises(caplog: pytest.LogCaptureFixture) -> None:
    sink: list[CallMetrics] = []
    with caplog.at_level(logging.INFO, logger="strata.voice"), pytest.raises(RuntimeError):
        async with meter("speech", "local", "kokoro", sink=sink):
            raise RuntimeError("boom")

    assert sink[0].ok is False
    assert sink[0].error == "RuntimeError"
    assert "error=RuntimeError" in caplog.text


def test_cost_estimates_are_none_for_unknown_models_and_free_backends() -> None:
    assert estimate_transcription_cost("some-local-model", 30.0) is None
    assert estimate_transcription_cost("gpt-4o-mini-transcribe", None) is None
    assert estimate_speech_cost("kokoro", 1000) is None
    assert estimate_speech_cost("gpt-4o-mini-tts", 1_000_000) == pytest.approx(12.0)
