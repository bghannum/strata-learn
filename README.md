# Strata Learn

Strata Learn ingests a repository from a Git URL or zip upload, extracts its structure, uses citation-grounded semantic analysis to help a developer understand the codebase, and generates a study guide plus a quiz to verify that understanding.

## Current status

Phases 0 through 8 are complete. Phases 0 through 5 built the core:

- project scaffolding and local Docker environment;
- repository ingestion and deterministic Layer A analysis;
- Redis/arq background processing and WebSocket progress;
- Anthropic-backed Layer B module summaries, architecture-pattern detection, and trade-off extraction;
- citation-grounded study guide generation (Overview, Architecture, Trade-offs, Glossary, Deep-Dives) plus a Mermaid architecture diagram, served via `GET /repos/{id}/study-guide`;
- a working frontend: register/log in, add a repo, watch it index live, and read the generated study guide with inline diagrams and clickable citations, all scoped to the logged-in user via cookie-based sessions;
- quiz generation (MCQ + fill-in-the-blank, grounded in the study guide's own citations) and taking, with per-answer grading/feedback (either immediate or deferred to the end of the quiz, chosen per quiz), Previous navigation, retakes, and a results view showing each question's submitted and correct answer.

Phase 5.5 (UI design integration) applied the checked-in Organic mockup across every screen — shared primitives, tokens, and light-only styling replacing the earlier default Tailwind look — plus a real reindex/retry action for a failed run.

Phase 6 (generation quality) repaired the Layer A/B facts that generation is built from, added a subsystem layer between "one file" and "the whole repo", and replaced the string-templated Architecture section with a synthesized explanation of how the system works and why — aimed at conceptual understanding rather than a per-file index. Quiz seeding draws from that same conceptual material instead of clustering on whichever code spans sort first.

Phase 7 (versioning and mastery) added staleness detection against a repository's remote, an architectural diff between two snapshots, mastery tracking per subsystem across study-guide versions, and Markdown export. A ready repository can now be re-indexed to pick up new commits, which is what produces the second snapshot a diff compares against, and the repo page's "What changed" panel reads that diff back — subsystems, trade-offs, dependencies, and the primary pattern — directly under the staleness banner that prompted the re-index.

Phase 8 (voice learning) adds read-aloud for study-guide sections and quiz feedback, and spoken answers for fill-in-the-blank questions — transcribed into an editable transcript the learner confirms before it's graded through the ordinary path. Each capability sits behind its own provider protocol with a hosted OpenAI backend *and* an in-process self-hosted one (faster-whisper; Kokoro via ONNX), selected per capability by `TRANSCRIPTION_BACKEND` / `SPEECH_BACKEND` and off by default. The self-hosted runtimes are an opt-in build (`INSTALL_VOICE=true docker compose build api`); weights download on first use into a named volume. `./scripts/voice-eval` compares the transcription backends on this repository's own vocabulary and writes [a committed report](docs/history/voice-backend-evaluation.md). See [ADR-010](docs/adr/ADR-010-voice-providers-and-self-hosted-backends.md) for why both backends, and the deliberate reversal of the original hosted-only scope.

The remaining roadmap phase adds drawing questions (Phase 9, deferred from its original Phase 6 slot).

Phase-level progress lives in [GitHub Milestones](https://github.com/bghannum/strata-learn/milestones); actionable and deferred work lives in [GitHub Issues](https://github.com/bghannum/strata-learn/issues).

## Deliberately out of scope for now

**Productionization — running this as a hosted service.** Deploying to a VPS behind Caddy with automatic TLS, a production Compose topology (built frontend, no published database ports, no source bind mounts), secret handling, migrations as an explicit deploy step, and backups. Also the security work that only matters once the app is reachable: CSRF protection, login rate limiting, and expired-session cleanup.

Deferred rather than blocked. The plan's [§13 hosting sequence](docs/design/original-project-plan.md) gated deployment on the tool being *stable*, not merely on reaching a particular phase, and generation is still being reworked. Hosting also adds no product value for a single user running locally, while adding real ongoing cost, maintenance, and exposure — including an Anthropic API key sitting on a public box. The reason to do it eventually is the operational learning (Linux, Docker in production, TLS, firewalls), which doesn't expire.

The recommended first step when it is picked up is to run the production topology *locally* — Caddy in front, frontend built as static files, same-origin, Caddy's internal CA for local HTTPS — which validates the cookie/CORS/same-origin interactions and the migration step with zero public exposure. Tracked in the [Productionization milestone](https://github.com/bghannum/strata-learn/milestones).

See the [documentation index](docs/README.md) for current architecture, development workflow, decisions, planned UX, and historical design material.

## Prerequisites

For the recommended Docker workflow:

- Docker 24+ with Compose v2;
- Git;
- an Anthropic API key for Layer B indexing.

For host-based development, also install Python 3.12 and Node.js 20 or newer.

OpenAI support remains part of the provider-abstraction plan, but only the Anthropic production provider is currently implemented. `OPENAI_API_KEY` is therefore not required.

## Quickstart

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY before indexing, and replace the default
# REGISTRATION_SECRET before creating the single account.
docker compose up --build
```

Compose starts PostgreSQL, Redis, the FastAPI service, the arq worker, and the React/Vite development server.

Verify the API and open the frontend:

```bash
curl http://localhost:8000/health   # {"status":"ok"}
open http://localhost:5173
```

Add a repository from the Dashboard, watch it index, and open the generated study guide once it's ready. Interactive API documentation is available at `http://localhost:8000/docs`.

On the first visit, register the app's single account using the `REGISTRATION_SECRET` from `.env`. Registration closes permanently after that account is created; later visits use the normal login flow.

Stop the stack with `docker compose down`. Add `-v` only when you intentionally want to delete the local PostgreSQL volume.

## Local development without Docker

Start PostgreSQL and Redis separately, for example:

```bash
docker compose up postgres redis
```

Then run the backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal, run the worker from `backend/` with the same virtual environment active:

```bash
arq app.worker.tasks.WorkerSettings
```

Run the frontend separately:

```bash
cd frontend
npm install
npm run dev
```

The root `.env` is resolved regardless of whether backend commands run from the repository root or `backend/`.

## Tests and review

Backend tests use real PostgreSQL and Redis services rather than mocked database layers:

```bash
cd backend
pytest -q

cd ../frontend
npm run lint
npm run build
```

Deterministic checks run automatically on every pull-request update. AI review is intentional rather than push-triggered: once a change is complete, committed, and passing the full suite, run:

```bash
./scripts/codex-review
```

The report is written to the gitignored `CODEX_CODE_REVIEW.md`. The complete blocker/defer policy is in the [development workflow](docs/development/workflow.md).

## Database migrations

Run Alembic commands from `backend/` with the virtual environment active:

```bash
alembic upgrade head
alembic revision --autogenerate -m "message"
```

## Repository map

```text
strata-learn/
├── backend/             FastAPI, analysis pipeline, arq worker, and tests
├── frontend/            React/Vite app: ingest repos, read guides, and take quizzes
├── docs/
│   ├── adr/             Architecture decision records
│   ├── design/          Planned and historical design documents
│   ├── development/     Current contributor workflow
│   ├── history/         Resolved issues and retired experiments
│   └── prompts/         Versioned runtime LLM prompt templates
├── scripts/             Explicit developer utilities
└── docker-compose.yml   Local service topology
```

For the implemented pipeline and API surface, read [Current architecture](docs/architecture.md).
