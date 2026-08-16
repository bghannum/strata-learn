"""The self-hosted backends, exercised against *fake runtime modules*
injected into sys.modules — faster_whisper and kokoro_onnx are opt-in
extras that CI never installs, and these tests must run without them. What
they verify is the contract the real runtimes are wrapped in: weights load
once (lazy singleton), the vocabulary prompt reaches initial_prompt as the
same string the hosted backend sends, output is well-formed, and runtime
failures surface as AudioProviderError."""

import struct
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from app.audio.errors import AudioProviderError
from app.audio.metering import CallMetrics
from app.audio.providers import AudioClip
from app.audio.vocabulary import build_vocabulary_prompt

CLIP = AudioClip(data=b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 64, content_type="audio/wav", filename="a.wav")


# --- fake faster_whisper ---


@dataclass
class _Segment:
    text: str


@dataclass
class _Info:
    language: str
    duration: float


class _FakeWhisperModel:
    constructed: ClassVar[list[dict]] = []
    transcribe_calls: ClassVar[list[dict]] = []
    raise_error: ClassVar[Exception | None] = None

    def __init__(self, model_size_or_path, **kwargs) -> None:
        _FakeWhisperModel.constructed.append({"model": model_size_or_path, **kwargs})

    def transcribe(self, audio, **kwargs):
        _FakeWhisperModel.transcribe_calls.append({"audio": audio, **kwargs})
        if _FakeWhisperModel.raise_error is not None:
            raise _FakeWhisperModel.raise_error
        return iter([_Segment(" the worker "), _Segment("uses arq ")]), _Info(language="en", duration=2.5)


@pytest.fixture
def fake_faster_whisper(monkeypatch) -> type[_FakeWhisperModel]:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    _FakeWhisperModel.constructed = []
    _FakeWhisperModel.transcribe_calls = []
    _FakeWhisperModel.raise_error = None
    # A fresh provider class state per test — the singleton is the thing
    # under test, so it must not leak between tests.
    from app.audio import local_backend

    monkeypatch.setattr(local_backend.LocalTranscriptionProvider, "_models", {})
    return _FakeWhisperModel


async def test_local_transcription_loads_weights_once_and_passes_the_shared_prompt(fake_faster_whisper) -> None:
    from app.audio.local_backend import LocalTranscriptionProvider

    sink: list[CallMetrics] = []
    provider = LocalTranscriptionProvider(model_name="base.en", models_dir=Path("/tmp/models"), metrics_sink=sink)

    first = await provider.transcribe(CLIP, vocabulary=["arq", "run_layer_b"])
    second = await provider.transcribe(CLIP)

    # Constructed exactly once across two calls, on CPU, int8, into models_dir.
    assert len(fake_faster_whisper.constructed) == 1
    built = fake_faster_whisper.constructed[0]
    assert built["model"] == "base.en"
    assert built["device"] == "cpu"
    assert built["compute_type"] == "int8"
    assert built["download_root"] == "/tmp/models"

    # The same string the hosted backend sends as `prompt`.
    calls = fake_faster_whisper.transcribe_calls
    assert calls[0]["initial_prompt"] == build_vocabulary_prompt(["arq", "run_layer_b"])
    assert calls[1]["initial_prompt"] is None
    # An in-memory file, not a temp path.
    assert calls[0]["audio"].read() == CLIP.data

    assert first.text == "the worker uses arq"
    assert first.model == "faster-whisper/base.en"
    assert first.language == "en"
    assert first.duration_seconds == 2.5
    assert second.text == first.text
    # Metered as free — the number the evaluation reports.
    assert sink[0].estimated_cost_usd is None
    assert sink[0].audio_seconds == 2.5


async def test_local_transcription_wraps_runtime_failures(fake_faster_whisper) -> None:
    from app.audio.local_backend import LocalTranscriptionProvider

    fake_faster_whisper.raise_error = RuntimeError("ctranslate2 exploded")
    provider = LocalTranscriptionProvider(model_name="base.en")
    with pytest.raises(AudioProviderError):
        await provider.transcribe(CLIP)


# --- fake Kokoro runtime (onnxruntime + ttstokenizer + huggingface_hub + nltk) ---


class _FakeSession:
    constructed: ClassVar[list[str]] = []
    runs: ClassVar[list[dict]] = []
    raise_error: ClassVar[Exception | None] = None

    def __init__(self, model_path: str, providers=None) -> None:
        _FakeSession.constructed.append(model_path)

    def run(self, _outputs, feeds):
        _FakeSession.runs.append(feeds)
        if _FakeSession.raise_error is not None:
            raise _FakeSession.raise_error
        # A quarter-second of a flat tone per sentence, shaped like the real
        # model's (1, N) float32 output.
        return [[[0.5] * 6000]]


class _FakeTokenizer:
    def __call__(self, text: str) -> list[int]:
        return [ord(c) % 50 + 1 for c in text][:100]


@pytest.fixture
def fake_kokoro(monkeypatch, tmp_path: Path) -> type[_FakeSession]:
    numpy = pytest.importorskip("numpy") if "numpy" in sys.modules else None
    if numpy is None:
        # numpy isn't in the base venv (it arrives with the voice extra); a
        # tiny stand-in covers the four calls the provider makes.
        np = types.ModuleType("numpy")

        def _flat(x):
            for item in x:
                if isinstance(item, list):
                    yield from _flat(item)
                else:
                    yield item

        class _Arr(list):
            def reshape(self, _shape):
                return _Arr(_flat(self))

            def clip(self, lo, hi):
                return _Arr(max(lo, min(hi, x)) for x in self)

            def astype(self, _dtype):
                return self

            def __mul__(self, k):
                return _Arr(x * k for x in self)

            def tobytes(self):
                import array as _array

                return _array.array("h", (int(x) for x in self)).tobytes()

        np.float32 = float  # type: ignore[attr-defined]
        np.asarray = lambda x, dtype=None: _Arr(x)  # type: ignore[attr-defined]
        np.ones = lambda n, dtype=None: _Arr([1.0] * n)  # type: ignore[attr-defined]
        np.concatenate = lambda parts: _Arr(x for p in parts for x in p)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "numpy", np)

    ort = types.ModuleType("onnxruntime")
    ort.InferenceSession = _FakeSession  # type: ignore[attr-defined]
    tts = types.ModuleType("ttstokenizer")
    tts.IPATokenizer = _FakeTokenizer  # type: ignore[attr-defined]
    hub = types.ModuleType("huggingface_hub")
    downloaded: list[tuple[str, str]] = []

    def hf_hub_download(repo_id: str, filename: str, cache_dir: str | None = None) -> str:
        downloaded.append((repo_id, filename))
        path = tmp_path / filename
        if filename == "voices.json":
            path.write_text('{"af_sarah": [[0.1, 0.2], [0.3, 0.4]], "am_adam": [[0.5, 0.6], [0.7, 0.8]]}')
        else:
            path.write_bytes(b"onnx")
        return str(path)

    hub.hf_hub_download = hf_hub_download  # type: ignore[attr-defined]
    nltk = types.ModuleType("nltk")
    nltk.download = lambda *a, **k: True  # type: ignore[attr-defined]
    nltk.data = types.SimpleNamespace(path=[])  # type: ignore[attr-defined]
    for name, module in (("onnxruntime", ort), ("ttstokenizer", tts), ("huggingface_hub", hub), ("nltk", nltk)):
        monkeypatch.setitem(sys.modules, name, module)
    _FakeSession.constructed = []
    _FakeSession.runs = []
    _FakeSession.raise_error = None
    from app.audio import local_backend

    monkeypatch.setattr(local_backend.LocalSpeechProvider, "_runtime", None)
    _FakeSession.downloaded = downloaded  # type: ignore[attr-defined]
    return _FakeSession


async def test_local_speech_buffers_sentences_into_one_valid_wav(fake_kokoro, tmp_path: Path) -> None:
    from app.audio.local_backend import LocalSpeechProvider

    sink: list[CallMetrics] = []
    provider = LocalSpeechProvider(models_dir=tmp_path, voice="af_sarah", metrics_sink=sink)

    chunks = [c async for c in provider.synthesize("First sentence. Second sentence.")]
    again = [c async for c in provider.synthesize("Again.", voice="am_adam")]

    # One chunk — local buffers rather than streams, by design.
    assert len(chunks) == 1
    wav = chunks[0]
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    channels = struct.unpack("<H", wav[22:24])[0]
    rate = struct.unpack("<I", wav[24:28])[0]
    bits = struct.unpack("<H", wav[34:36])[0]
    assert (channels, rate, bits) == (1, 24000, 16)
    data_len = struct.unpack("<I", wav[40:44])[0]
    # Two sentences -> two model runs -> two 6000-sample buffers, 16-bit mono.
    assert data_len == 12000 * 2
    assert len(wav) == 44 + data_len

    assert provider.media_type == "audio/wav"
    # Weights fetched and the session constructed once across two calls.
    assert fake_kokoro.downloaded == [("NeuML/kokoro-base-onnx", "model.onnx"), ("NeuML/kokoro-base-onnx", "voices.json")]  # type: ignore[attr-defined]
    assert len(fake_kokoro.constructed) == 1
    # Sentence-wise: three runs total, and the tokens are framed with the
    # model's start/end padding.
    assert len(fake_kokoro.runs) == 3
    assert fake_kokoro.runs[0]["tokens"][0][0] == 0 and fake_kokoro.runs[0]["tokens"][0][-1] == 0
    # The voice override selected the other style table.
    assert list(fake_kokoro.runs[2]["style"]) == [0.7, 0.8]
    assert len(again) == 1
    assert sink[0].estimated_cost_usd is None
    assert sink[0].output_bytes == len(wav)


async def test_local_speech_wraps_runtime_failures(fake_kokoro, tmp_path: Path) -> None:
    from app.audio.local_backend import LocalSpeechProvider

    fake_kokoro.raise_error = RuntimeError("onnx exploded")
    provider = LocalSpeechProvider(models_dir=tmp_path)
    with pytest.raises(AudioProviderError):
        async for _ in provider.synthesize("x"):
            pass


def test_sentence_splitting_bounds_run_ons() -> None:
    from app.audio.local_backend import _sentences

    assert _sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]
    long = ", ".join(["clause"] * 120) + "."
    pieces = _sentences(long)
    assert len(pieces) > 1
    assert all(len(p) <= 350 for p in pieces)


def test_local_status_reports_on_only_when_the_runtime_imports(monkeypatch, fake_faster_whisper) -> None:
    from app.audio.dependencies import speech_status, transcription_status
    from app.config import settings

    monkeypatch.setattr(settings, "transcription_backend", "local")
    monkeypatch.setattr(settings, "speech_backend", "local")
    # faster_whisper is faked in; ttstokenizer is not.
    assert transcription_status().enabled is True
    assert transcription_status().backend == "local"
    assert speech_status().enabled is False
    assert "voice" in (speech_status().reason or "")
