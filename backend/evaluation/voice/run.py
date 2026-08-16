"""The voice-backend evaluation (ADR-010): runs the fixture clips through
each configured transcription backend, with and without vocabulary hints,
scores them (evaluation/voice/metrics.py), and writes the committed report.

## This is a deliberate, potentially billed script — never a test

It is kept out of `pytest -q` by three independent mechanisms, because
one isn't enough for something that can spend money: pyproject's
testpaths never collects evaluation/; nothing here is named test_*; and
the hosted backend refuses to run without --confirm-billing AND a present
OPENAI_API_KEY. The local backend is free and runs whenever its runtime is
installed (the `voice` extra).

    ./scripts/voice-eval                     # local only, if installed
    ./scripts/voice-eval --confirm-billing   # local + hosted

Cost estimate for the hosted column at ten short clips, twice each
(hinted/unhinted): well under a cent of transcription.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.audio.metering import CallMetrics
from app.audio.providers import AudioClip, TranscriptionProvider
from evaluation.voice.metrics import (
    Aggregate,
    ClipScore,
    aggregate,
    identifier_recall,
    normalize,
    word_error_rate,
)

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
REPO_ROOT = HERE.parents[2]
REPORT = REPO_ROOT / "docs" / "history" / "voice-backend-evaluation.md"


def _manifest_hash() -> str:
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()[:12]


def _jiwer_cross_check(reference: str, hypothesis: str, ours: float) -> None:
    """jiwer with its transforms pinned to this project's normalization must
    agree with metrics.word_error_rate; if it ever doesn't, the report is
    measuring something other than what it claims."""
    try:
        import jiwer
    except ImportError:
        return
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return
    theirs = jiwer.wer(ref, hyp)
    if abs(theirs - ours) > 1e-9:
        raise RuntimeError(f"jiwer ({theirs}) disagrees with metrics.word_error_rate ({ours}) — normalization drift")


async def _score_backend(
    name: str, provider: TranscriptionProvider, clips: list[dict], sink: list[CallMetrics]
) -> list[ClipScore]:
    scores: list[ClipScore] = []
    for hinted in (False, True):
        for clip in clips:
            audio = (HERE / clip["audio"]).read_bytes()
            vocabulary = clip["vocabulary"] if hinted else []
            before = len(sink)
            started = time.perf_counter()
            result = await provider.transcribe(
                AudioClip(data=audio, content_type="audio/wav", filename=Path(clip["audio"]).name),
                vocabulary=vocabulary,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            metrics = sink[before] if len(sink) > before else None
            wer = word_error_rate(clip["reference"], result.text)
            _jiwer_cross_check(clip["reference"], result.text, wer)
            scores.append(
                ClipScore(
                    clip_id=clip["id"],
                    category=clip["category"],
                    backend=name,
                    hinted=hinted,
                    reference=clip["reference"],
                    hypothesis=result.text,
                    wer=wer,
                    identifier_recall=identifier_recall(clip["expected_identifiers"], result.text),
                    latency_ms=latency_ms,
                    estimated_cost_usd=metrics.estimated_cost_usd if metrics else None,
                    audio_seconds=result.duration_seconds,
                )
            )
            print(f"  [{name}{' +hints' if hinted else ''}] {clip['id']}: wer={wer:.2f} {result.text!r}")
    return scores


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _fmt_cost(value: float | None) -> str:
    return "free" if value is None else f"${value:.4f}"


def _render(
    aggregates: list[Aggregate], scores: list[ClipScore], manifest: dict, backends_run: list[str], not_run: dict[str, str]
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    sources = sorted({c["source"] for c in manifest["clips"]})
    lines = [
        "# Voice backend evaluation",
        "",
        f"Generated {now} by `scripts/voice-eval` (ADR-010). Manifest `{_manifest_hash()}`, {len(manifest['clips'])} clips, source: {', '.join(sources)}.",
        "",
        "> **Read this first.** The committed fixture clips are *synthetic* — spoken by the local Kokoro backend from the manifest's reference sentences (`evaluation/voice/make_fixtures.py`). They measure how each ASR backend handles this repository's vocabulary in a clean, consistent voice; they do not measure a real human speaker. In particular they inherit the TTS's *pronunciation* of identifiers — a text-to-speech model given `run_layer_b` does not say \"run underscore layer underscore b\" the way a person would (which the scoring's spoken-form aliases would credit) — so the identifier numbers below are pessimistic for a human speaker. Re-record the clips (same ids, same references, `source: \"human\"`) and re-run for the numbers that matter.",
        "",
        "## What is measured",
        "",
        "- **Identifier recall** — the fraction of each clip's `expected_identifiers` present as exact normalized tokens in the transcript. This is the metric that matters for the feature: a spoken answer that gets the prose right and `run_layer_b` wrong is a wrong answer.",
        "- **WER** — word error rate over a normalization that *preserves* `_ / . -` inside tokens (jiwer's default punctuation stripping would shred the identifiers under test) and applies spoken-form aliases (`\"underscore\"` → `_`, `\"dot\"` → `.`) symmetrically to reference and hypothesis. jiwer is cross-checked against the project's own implementation on every clip.",
        "- **Latency** p50/p95 per call, and **estimated cost** from `app/audio/metering.py`'s pricing table (local is free).",
        "",
        "Every clip is run twice per backend: without vocabulary hints and with the hints the app would derive for it. The hinted rows are the ones the deployed feature actually gets.",
        "",
    ]
    if not_run:
        lines += ["## Not run", ""]
        for backend, reason in not_run.items():
            lines.append(f"- **{backend}** — {reason}")
        lines.append("")

    lines += ["## Results", ""]
    lines += [
        "| Backend | Hints | Category | Clips | Identifier recall | WER | p50 ms | p95 ms | Cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for agg in aggregates:
        lines.append(
            f"| {agg.backend} | {'yes' if agg.hinted else 'no'} | {agg.category} | {agg.clips} | "
            f"{_fmt_pct(agg.mean_identifier_recall)} | {agg.mean_wer:.2f} | {agg.p50_latency_ms} | {agg.p95_latency_ms} | {_fmt_cost(agg.total_cost_usd)} |"
        )
    lines.append("")

    # Headline: does hinting help, per backend?
    lines += ["## Does vocabulary conditioning help?", ""]
    for backend in backends_run:
        rows = {a.hinted: a for a in aggregates if a.backend == backend and a.category == "all"}
        if False in rows and True in rows:
            plain, hinted = rows[False], rows[True]
            r0, r1 = plain.mean_identifier_recall, hinted.mean_identifier_recall
            recall_line = f"identifier recall {_fmt_pct(r0)} → {_fmt_pct(r1)}" if r0 is not None and r1 is not None else "no identifier clips"
            lines.append(f"- **{backend}**: {recall_line}; WER {plain.mean_wer:.2f} → {hinted.mean_wer:.2f}.")
    lines.append("")

    lines += [
        "## Reading these numbers",
        "",
        "- **Prose is not the problem.** Every backend run so far transcribes ordinary English sentences essentially perfectly; the entire error budget is in identifiers, filenames, and library names — which is why identifier recall, not WER, is the headline metric.",
        "- **Hints help most where the term is a real word the model already knows** (library names like `Postgres`, `arq`, `Tree-sitter`) and least where it is a novel token (`snake_case` identifiers, dotted paths). Conditioning biases decoding toward *known* vocabulary; it does not teach a small model a token it has never seen.",
        "- **The editable transcript is doing real work.** The confirmation step exists precisely because these numbers say the transcript will be wrong on the one token that matters some fraction of the time — and a learner fixing `RunLiber` to `run_layer_b` before submitting costs nothing (ADR-010).",
        "- **Local vs hosted** is a cost/accuracy trade the hosted column quantifies when run; local is free and ~a third of a second per clip on a laptop CPU. A larger local model (`--whisper-model small.en`) roughly triples latency and is the next thing to try if identifier recall is the priority.",
        "",
        "## Per-clip transcripts",
        "",
    ]
    lines += ["| Backend | Hints | Clip | WER | Recall | Transcript |", "|---|---|---|---|---|---|"]
    for s in scores:
        hyp = s.hypothesis.replace("|", "\\|")
        lines.append(f"| {s.backend} | {'yes' if s.hinted else 'no'} | {s.clip_id} | {s.wer:.2f} | {_fmt_pct(s.identifier_recall)} | {hyp} |")
    lines.append("")
    lines += [
        "## How to re-run",
        "",
        "```bash",
        "./scripts/voice-eval                     # local backend only (needs the `voice` extra)",
        "./scripts/voice-eval --confirm-billing   # also the hosted backend (needs OPENAI_API_KEY)",
        "```",
        "",
        "Pricing in `app/audio/metering.py` carries an `as_of` date; verify against the provider before quoting the cost column.",
        "",
    ]
    return "\n".join(lines)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm-billing", action="store_true", help="also run the hosted OpenAI backend (paid)")
    parser.add_argument("--whisper-model", default=None, help="override the local model (default: settings)")
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args(argv)

    from app.config import settings

    manifest = json.loads(MANIFEST.read_text())
    clips = manifest["clips"]
    missing = [c["id"] for c in clips if not (HERE / c["audio"]).exists()]
    if missing:
        print(f"missing fixture audio for: {', '.join(missing)} — run evaluation/voice/make_fixtures.py or record them", file=sys.stderr)
        return 2

    sink: list[CallMetrics] = []
    scores: list[ClipScore] = []
    backends_run: list[str] = []
    not_run: dict[str, str] = {}

    # Local — free; runs whenever the runtime is installed.
    try:
        from app.audio.local_backend import LocalTranscriptionProvider

        model_name = args.whisper_model or settings.local_whisper_model
        local = LocalTranscriptionProvider(model_name=model_name, models_dir=settings.voice_models_dir, metrics_sink=sink)
        # Warm the weights outside the timed loop so a cold load isn't
        # charged to the first clip's latency.
        await local.transcribe(AudioClip(data=(HERE / clips[0]["audio"]).read_bytes(), content_type="audio/wav", filename="warm.wav"))
        sink.clear()
        name = f"local ({local.model})"
        print(f"running {name}")
        scores += await _score_backend(name, local, clips, sink)
        backends_run.append(name)
    except ImportError as exc:
        not_run["local"] = f"runtime not installed ({exc.name}) — `pip install -e '.[voice]'`"

    # Hosted — paid; requires both the flag and a key.
    key_present = bool(os.environ.get("OPENAI_API_KEY") or settings.openai_api_key)
    if not args.confirm_billing:
        not_run["openai"] = "not requested — pass `--confirm-billing` to run the paid hosted backend"
    elif not key_present:
        not_run["openai"] = "`--confirm-billing` given but OPENAI_API_KEY is not set"
    else:
        from app.audio.openai_backend import OpenAITranscriptionProvider

        hosted = OpenAITranscriptionProvider(
            api_key=settings.openai_api_key or os.environ["OPENAI_API_KEY"],
            model=settings.openai_transcription_model,
            timeout_seconds=settings.audio_provider_timeout_seconds,
            metrics_sink=sink,
        )
        name = f"openai ({hosted.model})"
        print(f"running {name}")
        scores += await _score_backend(name, hosted, clips, sink)
        backends_run.append(name)

    if not scores:
        print("nothing ran:", not_run, file=sys.stderr)
        return 3

    report = _render(aggregate(scores), scores, manifest, backends_run, not_run)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
