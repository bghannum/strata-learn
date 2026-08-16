"""Self-hosted, in-process backends for both audio protocols (ADR-010):
faster-whisper for transcription, Kokoro-82M via ONNX for speech.

In-process rather than a sidecar was a deliberate choice — the point of the
second backend is that its client code is genuinely different from the
hosted one's, not a base-URL swap. That makes three things mandatory here,
and they are the actual lesson:

1. **A lazy, module-level singleton per model.** Construction loads weights
   (hundreds of MB, seconds of work); it must happen once per process, on
   first use, never per request.
2. **`asyncio.to_thread` around inference.** Both runtimes are blocking
   CPU work; run inline they'd stall the event loop that also serves the
   progress WebSocket. Same reason repos.py wraps check_git_url_reachable.
3. **A `Semaphore(1)` per model.** Concurrent requests otherwise
   oversubscribe the cores and starve the default thread pool. One at a time
   is the honest capacity of a laptop CPU anyway.

Both runtimes were chosen to avoid torch (CTranslate2 and onnxruntime) and
avoid a system ffmpeg (faster-whisper decodes through PyAV's bundled
FFmpeg, so webm/Opus and mp4/AAC both work). Speech deliberately uses the
espeak-free stack — NeuML/kokoro-base-onnx (Apache-2.0) with `ttstokenizer`
for grapheme-to-phoneme — rather than the `kokoro-onnx` package: that one
phonemizes through espeak-ng (GPL), and its bundled macOS build has a
build-machine data path compiled in that makes the process exit() on first
use. Everything is imported lazily inside the constructors: this module is
only reached when a capability is pointed at "local" and dependencies.py
has confirmed the runtime imports.

Local TTS *buffers* rather than streams: Kokoro's ~510-token context forces
sentence-wise synthesis, and N concatenated WAV files don't play (each has
its own header). Emitting one WAV keeps the SpeechProvider contract
identical — the route neither knows nor cares. MP3 frame-streaming is the
stretch upgrade.
"""

import array
import asyncio
import io
import logging
import re
import struct
import threading
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

from app.audio.errors import AudioProviderError
from app.audio.metering import CallMetrics, meter
from app.audio.providers import AudioClip, TranscriptionResult
from app.audio.vocabulary import build_vocabulary_prompt

logger = logging.getLogger("strata.voice")

# Weights come from the Hugging Face Hub, same as faster-whisper's — one
# cache, one download mechanism, one volume.
KOKORO_REPO = "NeuML/kokoro-base-onnx"
KOKORO_SAMPLE_RATE = 24000
KOKORO_DEFAULT_VOICE = "af_sarah"
# ttstokenizer's English G2P uses NLTK's POS tagger; this is the one data
# pack it needs, fetched into the same models dir on first use.
NLTK_TAGGER = "averaged_perceptron_tagger_eng"


# --- transcription ---


class LocalTranscriptionProvider:
    _model_lock: ClassVar[threading.Lock] = threading.Lock()
    _models: ClassVar[dict[str, Any]] = {}

    def __init__(self, model_name: str, *, models_dir: Path | None = None, metrics_sink: list[CallMetrics] | None = None) -> None:
        self.model = f"faster-whisper/{model_name}"
        self._model_name = model_name
        self._models_dir = models_dir
        self._metrics_sink = metrics_sink
        # One inference at a time per process — see the module docstring.
        self._inference = asyncio.Semaphore(1)

    def _load(self) -> Any:
        """Lazy singleton, keyed by model name so a config change doesn't
        reuse the wrong weights. The lock guards first-time construction
        against two concurrent requests both deciding to load."""
        with self._model_lock:
            model = self._models.get(self._model_name)
            if model is None:
                from faster_whisper import WhisperModel

                logger.info("voice.transcription loading faster-whisper model=%s (first use)", self._model_name)
                model = WhisperModel(
                    self._model_name,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self._models_dir) if self._models_dir else None,
                )
                self._models[self._model_name] = model
                logger.info("voice.transcription loaded model=%s", self._model_name)
            return model

    def _transcribe_blocking(self, clip: AudioClip, prompt: str) -> tuple[str, str | None, float | None]:
        model = self._load()
        segments, info = model.transcribe(
            io.BytesIO(clip.data),
            language="en",
            beam_size=5,
            # The same string the hosted backend sends as `prompt` — see
            # vocabulary.build_vocabulary_prompt for why they must match.
            initial_prompt=prompt or None,
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text, getattr(info, "language", None), getattr(info, "duration", None)

    async def transcribe(self, clip: AudioClip, *, vocabulary: Sequence[str] = ()) -> TranscriptionResult:
        prompt = build_vocabulary_prompt(vocabulary)
        async with meter("transcription", "local", self.model, sink=self._metrics_sink) as m:
            m.input_bytes = len(clip.data)
            async with self._inference:
                try:
                    text, language, duration = await asyncio.to_thread(self._transcribe_blocking, clip, prompt)
                except Exception as exc:  # the runtime raises a variety of types
                    raise AudioProviderError(f"local transcription failed: {type(exc).__name__}") from exc
            m.audio_seconds = duration
            # Free — the number the evaluation reports.
            m.estimated_cost_usd = None
            return TranscriptionResult(
                text=text,
                model=self.model,
                language=language,
                duration_seconds=duration,
                usage={"audio_seconds": duration} if duration is not None else {},
            )


# --- speech ---


def _wav_bytes(samples: Any, sample_rate: int) -> bytes:
    """16-bit PCM WAV from float samples in [-1, 1]. Written by hand rather
    than via a library: it's forty-four bytes of header, and pulling in
    soundfile for that would mean another native dependency. Takes a numpy
    array when the runtime is present (it always is alongside kokoro-onnx)
    and any float sequence otherwise, so the framing is testable without
    the extras installed."""
    if hasattr(samples, "astype"):
        clipped = samples.clip(-1.0, 1.0)
        data = (clipped * 32767.0).astype("<i2").tobytes()
    else:
        pcm = array.array("h", (int(max(-1.0, min(1.0, float(x))) * 32767) for x in samples))
        data = pcm.tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,  # PCM fmt chunk size
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,  # block align
        16,  # bits per sample
        b"data",
        len(data),
    )
    return header + data


class LocalSpeechProvider:
    media_type = "audio/wav"
    model = "kokoro-base-onnx"

    _model_lock: ClassVar[threading.Lock] = threading.Lock()
    _runtime: ClassVar[Any] = None  # (session, tokenizer, voices)

    def __init__(
        self,
        *,
        models_dir: Path,
        voice: str = KOKORO_DEFAULT_VOICE,
        metrics_sink: list[CallMetrics] | None = None,
    ) -> None:
        self._models_dir = models_dir
        self._voice = voice
        self._metrics_sink = metrics_sink
        self._inference = asyncio.Semaphore(1)

    def _load(self) -> Any:
        with self._model_lock:
            if LocalSpeechProvider._runtime is None:
                import json

                import nltk
                import numpy as np
                import onnxruntime
                from huggingface_hub import hf_hub_download
                from ttstokenizer import IPATokenizer

                self._models_dir.mkdir(parents=True, exist_ok=True)
                logger.info("voice.speech loading %s (first use; downloads on a cold cache)", KOKORO_REPO)
                model_path = hf_hub_download(KOKORO_REPO, "model.onnx", cache_dir=str(self._models_dir))
                voices_path = hf_hub_download(KOKORO_REPO, "voices.json", cache_dir=str(self._models_dir))
                nltk_dir = self._models_dir / "nltk_data"
                nltk.download(NLTK_TAGGER, download_dir=str(nltk_dir), quiet=True)
                if str(nltk_dir) not in nltk.data.path:
                    nltk.data.path.insert(0, str(nltk_dir))

                session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
                with open(voices_path, encoding="utf-8") as fh:
                    voices = {name: np.asarray(style, dtype=np.float32) for name, style in json.load(fh).items()}
                LocalSpeechProvider._runtime = (session, IPATokenizer(), voices)
                logger.info("voice.speech loaded %s voices=%d", KOKORO_REPO, len(voices))
            return LocalSpeechProvider._runtime

    def _synthesize_blocking(self, text: str, voice: str) -> bytes:
        import numpy as np

        session, tokenizer, voices = self._load()
        # `in`, not `.get(...) or`: a numpy array's truth value is ambiguous.
        style_table = voices[voice] if voice in voices else voices[KOKORO_DEFAULT_VOICE]
        # Kokoro's context is short (~510 phoneme tokens), so long text is
        # synthesized sentence by sentence and the waveforms concatenated
        # into one buffer — which is why this backend emits one WAV rather
        # than streaming: N concatenated WAV files don't play.
        parts: list[Any] = []
        for sentence in _sentences(text):
            # ttstokenizer returns an ndarray; a plain list is what the
            # model feed wants and what `len`/truthiness are safe on.
            tokens = [int(t) for t in tokenizer(sentence)]
            if not tokens:
                continue
            # The style table is indexed by token count — the model's own
            # convention for matching prosody to utterance length.
            style = style_table[min(len(tokens), len(style_table) - 1)]
            outputs = session.run(
                None,
                {
                    "tokens": [[0, *tokens, 0]],
                    "style": style,
                    "speed": np.ones(1, dtype=np.float32),
                },
            )
            parts.append(np.asarray(outputs[0], dtype=np.float32).reshape(-1))
        if not parts:
            return b""
        return _wav_bytes(np.concatenate(parts), KOKORO_SAMPLE_RATE)

    async def synthesize(self, text: str, *, voice: str | None = None) -> AsyncIterator[bytes]:
        async with meter("speech", "local", self.model, sink=self._metrics_sink) as m:
            m.input_chars = len(text)
            m.estimated_cost_usd = None
            async with self._inference:
                try:
                    wav = await asyncio.to_thread(self._synthesize_blocking, text, voice or self._voice)
                except Exception as exc:
                    raise AudioProviderError(f"local speech synthesis failed: {type(exc).__name__}") from exc
            m.output_bytes = len(wav)
            if not wav:
                return
            # One chunk: local buffers rather than streams (module docstring).
            yield wav


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Roughly where a single sentence would overrun the model's phoneme context;
# a run-on gets cut on commas or words rather than failing outright.
_MAX_SENTENCE_CHARS = 350


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > _MAX_SENTENCE_CHARS:
            cut = sentence.rfind(",", 0, _MAX_SENTENCE_CHARS)
            if cut < 40:
                cut = sentence.rfind(" ", 0, _MAX_SENTENCE_CHARS)
            if cut < 40:
                cut = _MAX_SENTENCE_CHARS
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip(", ")
        out.append(sentence)
    return out
