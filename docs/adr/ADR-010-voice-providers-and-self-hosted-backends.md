# ADR-010: Voice providers, self-hosted backends, and audio retention

**Status:** Accepted (Phase 8). Partially supersedes ADR-009 for audio only; ADR-009's stance on text LLMs is unchanged.

## Context

Phase 8 adds two audio capabilities on top of the text-first learning flows: read-aloud for persisted study-guide sections and quiz feedback, and spoken answers for fill-in-the-blank questions. The original plan (`docs/design/original-project-plan.md`, Phase 8) scoped these as "hosted OpenAI services initially" and listed "self-hosted Whisper/model serving" as an explicit non-goal. ADR-009 deferred self-hosted and open-source inference generally as "infra/ops, not product".

The optimization target for this phase is different from every phase before it. Phases 0–7 optimized for a working product; Phase 8 optimizes for *learning and demonstrable AI/ML integration work*. That changes what is worth building:

- The cheapest working read-aloud is the browser's `speechSynthesis` API. It is deliberately not used — there is no model, no integration, and nothing to learn from.
- A single hosted backend per capability would ship fastest. Two backends per capability are in scope because the provider-abstraction lesson only exists when a second implementation forces the first one's assumptions into the open. An abstraction with one implementation is a wrapper.
- A word-error-rate evaluation comparing the backends on this codebase's own vocabulary is not in the original plan. It is the highest-value addition, because it turns "integrated Whisper" into "measured the accuracy/cost trade-off and chose deliberately".

## Decisions

### 1. Two protocols, separate from `LLMProvider`

`app/audio/providers.py` defines `TranscriptionProvider` (bytes → text) and `SpeechProvider` (text → streamed bytes) as `typing.Protocol`s, mirroring `LLMProvider`'s shape (frozen dataclasses, structural typing, fakes living in `app/`). They are not methods on `LLMProvider`: the two capabilities share nothing at the call site, may legitimately be pointed at different backends, and widening the text interface would force every text-only implementation to stub audio.

`SpeechProvider.synthesize` is a plain `def` returning `AsyncIterator[bytes]` (an async-generator function is a callable that returns an iterator, not a coroutine). `media_type` is a protocol attribute the route reads, so a backend swap cannot produce a `Content-Type` that lies about the body.

### 2. Two backends per capability: hosted OpenAI and self-hosted, in-process

Each protocol gets an OpenAI backend and a local one running **in the API process** (faster-whisper for transcription; Kokoro via ONNX for speech). In-process rather than an OpenAI-compatible sidecar was a deliberate choice: when both backends speak the same wire protocol, "hosted vs local" collapses to a base-URL change and the abstraction claim degrades to "I changed a URL". In-process means genuinely different client code plus the model-lifecycle work — lazy loading, thread offload, concurrency bounding — that is the actual lesson.

Both local runtimes are chosen to avoid torch (CTranslate2 and onnxruntime respectively) and to avoid a system ffmpeg (faster-whisper decodes through PyAV's bundled FFmpeg). They live in a `voice` extra behind a Dockerfile build argument: the compose api image installs it by default (so `docker compose up` gives working voice with no key), the worker image never does, and CI installs `.[dev]` only — so the test image and the suite are unchanged.

Speech uses the espeak-free stack — `NeuML/kokoro-base-onnx` (Apache-2.0) with `ttstokenizer` for grapheme-to-phoneme — rather than the `kokoro-onnx` package. The plan had leaned toward `kokoro-onnx` for its smoother packaging and treated its espeak-ng (GPL) dependency as academic for a private tool; in practice its bundled macOS build has a build-machine data path compiled in and exits the process on first use, which settled the question. The espeak-free path also happens to be the cleaner license story. `ttstokenizer` needs one NLTK data pack (a POS tagger for English G2P), fetched into the same models directory on first use.

This reverses ADR-009's deferral for audio. ADR-009's reasoning — don't add a second unknown variable while prompts are unstable — was about text generation, whose prompts are now stable; audio has no prompts to destabilize.

### 3. One degradation seam, per capability

`app/audio/dependencies.py` is the only place "is this capability on?" is decided. Each capability has one setting (`TRANSCRIPTION_BACKEND`, `SPEECH_BACKEND`), independent of the other, defaulting to `local` — the free backend — so a fresh checkout works out of the box; the compose build installs the runtimes into the api image by default and the models are warmed in a background task at startup. In CI and in a bare `pip install -e ".[dev]"` the runtimes are absent and both resolve to off. A selected backend whose prerequisites are missing — no `OPENAI_API_KEY`, or the `voice` extra not installed — resolves to off. Endpoints turn "off" into a 503 whose detail never names a backend; `GET /voice/capabilities` reports booleans so the UI never renders a control that would 503; the *reason* a capability is off is logged once at startup and nowhere else.

### 4. Audio is a convenience layer; text stays canonical

Read-aloud speaks only persisted text identified by id (a `Section`, an `AnswerSubmission`'s feedback) — never caller-supplied text, so the endpoint is not a paid TTS proxy. Feedback speech is gated on the same visibility rule as the results view, so `end_of_quiz` mode cannot be defeated through the audio side channel.

Spoken answers return an editable transcript; **only the learner-confirmed text is ever graded**, through the existing `PATCH /attempts/{id}/answers/{qid}` path and the existing `AnswerSubmission.answer_text` field. No new grading path and no new persisted field.

### 5. Vocabulary hints never include the answer key

Transcription is conditioned on technical terms drawn from the code the question was generated from (`Citation.snippet_text`, `Question.file_path`, the subsystem name). `Question.correct_answer` and `acceptable_alternatives` are excluded by construction, and any term the answer is a component of is excluded too. An ASR prompt biases decoding toward its terms; priming the correct token for an exact-match question would turn an accessibility feature into an answer oracle that is invisible in the transcript. `tests/audio/test_vocabulary.py` guards this.

### 6. No raw audio is persisted; metering is logged, not stored

Uploaded recordings exist only for the duration of the request. Generated speech is streamed with `Cache-Control: no-store` and never written. Per-call metering (duration, sizes, estimated cost) is emitted as one structured log record via the `strata.voice` logger — never audio bytes, transcript text, TTS input, or credentials. A table was rejected: it would need a migration, an entry in the test suite's table-cleaning list, and a retention story for rows nobody queries. If a cost dashboard ever becomes a goal, a table against the same `CallMetrics` dataclass is the ~30-line change; that is the trigger to revisit.

### 7. Duration is bounded by bytes, not measured

`MediaRecorder` output is a live stream with no reliable duration header (no EBML `Duration` in Chrome's webm, no reliable `mvhd` in Safari's fragmented mp4); measuring means decoding. A 2 MiB byte cap bounds Opus to roughly eight minutes — ~25× the intended answer — and is enforced with a bounded read before validation, the same pattern as the zip upload. The client's countdown is a UX bound, not a security one.

### 8. Server-side streaming buys memory bounding, not perceived latency

The read-aloud endpoint is a `StreamingResponse`, and the first chunk is pulled inside the handler so a provider refusal becomes a real 503 rather than a truncated 200. The frontend, however, fetches to a `Blob` before playing (an `<audio src>` cross-origin subresource cannot surface the app's `ApiError` details or fire the 401 handler). So streaming currently bounds server memory and preserves an upgrade path to `MediaSource`; it does not shorten time-to-first-audio, and this ADR does not claim it does. The local speech backend also buffers rather than streams for now (concatenated WAV chunks do not play; MP3 frame-streaming is the stretch), which the protocol accommodates without change.

### 9. Evaluation is a deliberate, billed script — never a test

`scripts/voice-eval` runs hand-recorded fixture clips through both backends and writes `docs/history/voice-backend-evaluation.md`. It is kept out of `pytest -q` by three independent mechanisms (`testpaths`, a non-`test_` filename, and a required `--confirm-billing` flag plus a present key). Fixtures are ~2–4 MB of committed WAV, a conscious decision to be resisted from growing. It reports WER with a transform chain that preserves `_ / . -` (the default punctuation stripper would shred the identifiers under test) alongside identifier recall, because a transcript can score well on WER while missing the one token the learner needed.

## Consequences

- Two new provider protocols and their fakes; the `LLMProvider` interface is untouched.
- New optional `Settings` fields; nothing is required for any non-audio feature, and CI runs with every capability off.
- The `openai` package, a dependency since Phase 2, gains its first real consumer. `OPENAI_API_KEY` is now meaningful.
- A `voice` extra and `INSTALL_VOICE` build argument, on by default for the api image, off for the worker and CI.
- ADR-009 remains accepted for text LLMs. Its "revisit as a Phase 7+ stretch" clause is exercised here for audio specifically.
- The plan's Phase 8 non-goals list is amended: self-hosted model serving is in scope; realtime speech-to-speech, barge-in, a conversational tutor, voice control for MCQ, diarization, and durable audio storage remain out.
