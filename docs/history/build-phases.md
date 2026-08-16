# Build phases (MVP, Phases 0–8)

The MVP was built in numbered phases, each closed by a GitHub Milestone. This is the narrative that used to live in the root README's "Current status" section, preserved here as history now that the MVP is complete. Live status is in [GitHub Milestones](https://github.com/bghannum/strata-learn/milestones); the roadmap is in the [README](../../README.md#roadmap).

## Phases 0–5: the core

- project scaffolding and local Docker environment;
- repository ingestion and deterministic Layer A analysis;
- Redis/arq background processing and WebSocket progress;
- Anthropic-backed Layer B module summaries, architecture-pattern detection, and trade-off extraction;
- citation-grounded study guide generation (Overview, Architecture, Trade-offs, Glossary, Deep-Dives) plus a Mermaid architecture diagram, served via `GET /repos/{id}/study-guide`;
- a working frontend: first-run account setup and login, add a repo, watch it index live, and read the generated study guide with inline diagrams and clickable citations, all scoped to the logged-in user via cookie-based sessions;
- quiz generation (MCQ + short-answer questions, grounded in the study guide's own citations — short answer replaced fill-in-the-blank after Phase 8, so a quiz asks *why* and *how* in a sentence or three, graded by an LLM judge against a rubric of key points, rather than "guess the blanked word") and taking, with per-answer grading/feedback (either immediate or deferred to the end of the quiz, chosen per quiz), Previous navigation, retakes, and a results view showing each question's submitted answer, a model answer, and which key points landed.

## Phase 5.5: UI design integration

Applied the checked-in Organic mockup ([`docs/design/strata-learn-ui-mockups.zip`](../design/strata-learn-ui-mockups.zip)) across every screen — shared primitives, tokens, and light-only styling replacing the earlier default Tailwind look — plus a real reindex/retry action for a failed run.

## Phase 6: generation quality

Repaired the Layer A/B facts that generation is built from, added a subsystem layer between "one file" and "the whole repo", and replaced the string-templated Architecture section with a synthesized explanation of how the system works and why — aimed at conceptual understanding rather than a per-file index. Quiz seeding draws from that same conceptual material instead of clustering on whichever code spans sort first.

## Phase 7: versioning and mastery

Added staleness detection against a repository's remote, an architectural diff between two snapshots, mastery tracking per subsystem across study-guide versions, and Markdown export. A ready repository can be re-indexed to pick up new commits, which is what produces the second snapshot a diff compares against, and the repo page's "What changed" panel reads that diff back — subsystems, trade-offs, dependencies, and the primary pattern — directly under the staleness banner that prompted the re-index.

## Phase 8: voice learning

Read-aloud for study-guide sections and quiz feedback, and spoken answers for written questions — transcribed into an editable transcript the learner confirms before it's graded through the ordinary path. Each capability sits behind its own provider protocol with an in-process self-hosted backend (faster-whisper; Kokoro via ONNX) *and* a hosted OpenAI one, selected per capability by `TRANSCRIPTION_BACKEND` / `SPEECH_BACKEND`. Local is the default, so voice works out of the box with no key and no bill. `./scripts/voice-eval` compares the transcription backends on this repository's own vocabulary and writes [a committed report](voice-backend-evaluation.md). See [ADR-010](../adr/ADR-010-voice-providers-and-self-hosted-backends.md) for why both backends, and the deliberate reversal of the original hosted-only scope.

## First-run auth (MVP close)

A fresh install lands on "Set up your account" with no secret to copy out of `.env`, sessions last 30 days, and `./scripts/reset-password` recovers a forgotten password without wiping the database. With that in place the MVP was declared complete (2026-08-16). Drawing questions (Phase 9, deferred from the original Phase 6 slot) are deferred scope, not a remaining milestone; [ADR-005](../adr/ADR-005-drawing-question-graph-data.md) keeps the design.
