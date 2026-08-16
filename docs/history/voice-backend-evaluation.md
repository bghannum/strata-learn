# Voice backend evaluation

Generated 2026-08-16 13:53 UTC by `scripts/voice-eval` (ADR-010). Manifest `62af33cea938`, 10 clips, source: synthetic-kokoro.

> **Read this first.** The committed fixture clips are *synthetic* — spoken by the local Kokoro backend from the manifest's reference sentences (`evaluation/voice/make_fixtures.py`). They measure how each ASR backend handles this repository's vocabulary in a clean, consistent voice; they do not measure a real human speaker. In particular they inherit the TTS's *pronunciation* of identifiers — a text-to-speech model given `run_layer_b` does not say "run underscore layer underscore b" the way a person would (which the scoring's spoken-form aliases would credit) — so the identifier numbers below are pessimistic for a human speaker. Re-record the clips (same ids, same references, `source: "human"`) and re-run for the numbers that matter.

## What is measured

- **Identifier recall** — the fraction of each clip's `expected_identifiers` present as exact normalized tokens in the transcript. This is the metric that matters for the feature: a spoken answer that gets the prose right and `run_layer_b` wrong is a wrong answer.
- **WER** — word error rate over a normalization that *preserves* `_ / . -` inside tokens (jiwer's default punctuation stripping would shred the identifiers under test) and applies spoken-form aliases (`"underscore"` → `_`, `"dot"` → `.`) symmetrically to reference and hypothesis. jiwer is cross-checked against the project's own implementation on every clip.
- **Latency** p50/p95 per call, and **estimated cost** from `app/audio/metering.py`'s pricing table (local is free).

Every clip is run twice per backend: without vocabulary hints and with the hints the app would derive for it. The hinted rows are the ones the deployed feature actually gets.

## Not run

- **openai** — not requested — pass `--confirm-billing` to run the paid hosted backend

## Results

| Backend | Hints | Category | Clips | Identifier recall | WER | p50 ms | p95 ms | Cost |
|---|---|---|---|---|---|---|---|---|
| local (faster-whisper/base.en) | no | all | 10 | 6% | 0.25 | 335 | 360 | free |
| local (faster-whisper/base.en) | no | filename | 2 | 0% | 0.31 | 311 | 331 | free |
| local (faster-whisper/base.en) | no | identifier | 3 | 0% | 0.16 | 332 | 345 | free |
| local (faster-whisper/base.en) | no | library | 3 | 17% | 0.46 | 338 | 360 | free |
| local (faster-whisper/base.en) | no | prose | 2 | — | 0.00 | 337 | 357 | free |
| local (faster-whisper/base.en) | yes | all | 10 | 19% | 0.19 | 339 | 380 | free |
| local (faster-whisper/base.en) | yes | filename | 2 | 0% | 0.31 | 324 | 345 | free |
| local (faster-whisper/base.en) | yes | identifier | 3 | 0% | 0.21 | 320 | 364 | free |
| local (faster-whisper/base.en) | yes | library | 3 | 50% | 0.23 | 350 | 380 | free |
| local (faster-whisper/base.en) | yes | prose | 2 | — | 0.00 | 327 | 362 | free |

## Does vocabulary conditioning help?

- **local (faster-whisper/base.en)**: identifier recall 6% → 19%; WER 0.25 → 0.19.

## Reading these numbers

- **Prose is not the problem.** Every backend run so far transcribes ordinary English sentences essentially perfectly; the entire error budget is in identifiers, filenames, and library names — which is why identifier recall, not WER, is the headline metric.
- **Hints help most where the term is a real word the model already knows** (library names like `Postgres`, `arq`, `Tree-sitter`) and least where it is a novel token (`snake_case` identifiers, dotted paths). Conditioning biases decoding toward *known* vocabulary; it does not teach a small model a token it has never seen.
- **The editable transcript is doing real work.** The confirmation step exists precisely because these numbers say the transcript will be wrong on the one token that matters some fraction of the time — and a learner fixing `RunLiber` to `run_layer_b` before submitting costs nothing (ADR-010).
- **Local vs hosted** is a cost/accuracy trade the hosted column quantifies when run; local is free and ~a third of a second per clip on a laptop CPU. A larger local model (`--whisper-model small.en`) roughly triples latency and is the next thing to try if identifier recall is the priority.

## Per-clip transcripts

| Backend | Hints | Clip | WER | Recall | Transcript |
|---|---|---|---|---|---|
| local (faster-whisper/base.en) | no | identifier-01 | 0.11 | 0% | The worker calls RunLiber after the structural analysis finishes. |
| local (faster-whisper/base.en) | no | identifier-02 | 0.07 | 0% | The fill blank grader falls back to gradefiglance when there is no exact match. |
| local (faster-whisper/base.en) | no | identifier-03 | 0.30 | 0% | Each question copies its subsistec, so mastery survives a ray index. |
| local (faster-whisper/base.en) | no | filename-01 | 0.40 | 0% | The job lives in Parker, Valencia and publishes progress over Rita's |
| local (faster-whisper/base.en) | no | filename-02 | 0.22 | 0% | The provider protocol is defined in Lempervordia under Epismantics. |
| local (faster-whisper/base.en) | no | library-01 | 0.33 | 0% | The API talks to post-rays through async and inquiese jobs with ARC. |
| local (faster-whisper/base.en) | no | library-02 | 0.55 | 0% | Tree sitter parses each file in sclimital maps, the rows to post grays. |
| local (faster-whisper/base.en) | no | library-03 | 0.50 | 50% | The front end is react with Vite in the test's run under Viteist. |
| local (faster-whisper/base.en) | no | prose-01 | 0.00 | — | The study guide explains how the system works and why rather than listing every file. |
| local (faster-whisper/base.en) | no | prose-02 | 0.00 | — | Only the answer the learner confirms is ever graded so a mistake in the transcript costs nothing. |
| local (faster-whisper/base.en) | yes | identifier-01 | 0.11 | 0% | The worker calls RunLiber after the structural analysis finishes. |
| local (faster-whisper/base.en) | yes | identifier-02 | 0.21 | 0% | The fill-blank grader falls back to gradefiglance, when there is no exact match. |
| local (faster-whisper/base.en) | yes | identifier-03 | 0.30 | 0% | Each question copies its subsistec, so mastery survives a ray index. |
| local (faster-whisper/base.en) | yes | filename-01 | 0.40 | 0% | The job lives in Parker, Valencia, and publishes progress over Rita's. |
| local (faster-whisper/base.en) | yes | filename-02 | 0.22 | 0% | The provider protocol is defined in Lempervordiope under AppSpantics. |
| local (faster-whisper/base.en) | yes | library-01 | 0.17 | 50% | The API talks to Postgres through async and inquiese jobs with arq. |
| local (faster-whisper/base.en) | yes | library-02 | 0.18 | 50% | Tree-sitter parses each file in slimital maps, the rows to Postgres. |
| local (faster-whisper/base.en) | yes | library-03 | 0.33 | 50% | The front end is React with Vite in the tests run under Viteist. |
| local (faster-whisper/base.en) | yes | prose-01 | 0.00 | — | The study guide explains how the system works and why rather than listing every file. |
| local (faster-whisper/base.en) | yes | prose-02 | 0.00 | — | Only the answer the learner confirms is ever graded so a mistake in the transcript costs nothing. |

## How to re-run

```bash
./scripts/voice-eval                     # local backend only (needs the `voice` extra)
./scripts/voice-eval --confirm-billing   # also the hosted backend (needs OPENAI_API_KEY)
```

Pricing in `app/audio/metering.py` carries an `as_of` date; verify against the provider before quoting the cost column.
