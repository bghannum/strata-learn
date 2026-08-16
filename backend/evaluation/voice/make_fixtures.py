"""Generates the SYNTHETIC fixture clips for the voice evaluation from the
manifest's reference sentences, using the local Kokoro backend.

Synthetic clips let the harness run end to end and measure how each ASR
backend handles this repository's vocabulary in a clean, consistent voice.
They do *not* measure a real human speaker — replace them with hand
recordings (same ids, same references, source="human") for the numbers
that matter. The manifest says so too.

Needs the `voice` extra. Never runs under pytest.

    python -m evaluation.voice.make_fixtures
"""

import argparse
import asyncio
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
FIXTURE_RATE = 16_000


def _resample_wav_to_16k(wav: bytes) -> bytes:
    """Kokoro emits 24 kHz; the fixtures are 16 kHz mono so they're smaller
    in git and match what ASR models want natively. Linear interpolation is
    plenty for speech fixtures."""
    import numpy as np

    src_rate = struct.unpack("<I", wav[24:28])[0]
    pcm = np.frombuffer(wav[44:], dtype="<i2").astype(np.float32) / 32767.0
    if src_rate != FIXTURE_RATE:
        n_out = round(len(pcm) * FIXTURE_RATE / src_rate)
        x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        pcm = np.interp(x_new, x_old, pcm).astype(np.float32)
    data = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, 1,
        FIXTURE_RATE, FIXTURE_RATE * 2, 2, 16, b"data", len(data),
    )
    return header + data


async def main(voice: str, only: set[str] | None) -> int:
    from app.audio.local_backend import LocalSpeechProvider
    from app.config import settings

    manifest = json.loads(MANIFEST.read_text())
    speaker = LocalSpeechProvider(models_dir=settings.voice_models_dir, voice=voice)
    for clip in manifest["clips"]:
        if only and clip["id"] not in only:
            continue
        # The reference is what the ASR should produce; speak it verbatim.
        wav = b"".join([chunk async for chunk in speaker.synthesize(clip["reference"])])
        out = HERE / clip["audio"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_resample_wav_to_16k(wav))
        seconds = (out.stat().st_size - 44) / 2 / FIXTURE_RATE
        print(f"{clip['id']}: {seconds:.1f}s -> {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", default="af_sarah")
    parser.add_argument("--only", nargs="*", help="clip ids to regenerate")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.voice, set(args.only) if args.only else None)))
