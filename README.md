# Strata Learn

A tool that ingests a repository (git URL or zip upload) and generates a study guide — architecture diagrams, plain-English explanations, trade-off analysis — plus quizzes (multiple choice, fill-in-blank, diagram-drawing) to verify you actually understand a codebase, not just that it runs.

Full design context lives in [`PROJECT_PLAN.md`](./PROJECT_PLAN.md) (architecture, data model, build phases) and [`UI_SPEC.md`](./UI_SPEC.md) (frontend flows). Architecture decisions are recorded as individual ADRs in [`docs/adr/`](./docs/adr/); LLM prompt templates are versioned in [`docs/prompts/`](./docs/prompts/). The branch/PR/CI setup itself — reusable across other projects, not specific to this one — is documented in [`branch-pr-ci-workflow.md`](./branch-pr-ci-workflow.md).

## Prerequisites

Everything below was already present on this machine as of 2026-08-09 — the versions shown under "found" are what was detected. If you're setting this up somewhere new, install anything missing.

| Tool | Needed for | Minimum version | Found here | Install |
|---|---|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Compose) | Running the full stack — postgres, redis, api, worker, web (§1, §13) | Docker 24+, Compose v2+ | Docker 29.6.2, Compose v5.3.1 | `brew install --cask docker` |
| [Python](https://www.python.org/) | Backend (FastAPI, tree-sitter, etc.) if you run it outside Docker | 3.12 | 3.12.13 (as `python3.12` — the plain `python3` on this Mac is the older system 3.9.6, so scripts below call `python3.12` explicitly) | `brew install python@3.12` |
| [Node.js](https://nodejs.org/) | Frontend (Vite + React) if you run it outside Docker | 20+ | v26.3.1 (npm 11.16.0) | `brew install node` |
| [git](https://git-scm.com/) | Cloning repos to study, and this repo's own version control | any recent | 2.55.0 | `brew install git` |
| [Homebrew](https://brew.sh/) | Installing the above on macOS | — | 6.0.15 | — |

Everything above is optional except Docker if you only ever run via `docker compose up` — Python and Node are only needed for running backend/frontend directly on the host (faster iteration loop, e.g. `pytest` without a container rebuild).

### API keys (needed from Phase 2 onward, not Phase 0/1)

The semantic-analysis layer (module summaries, pattern detection, trade-off extraction — see ADR-003, ADR-009) calls Anthropic and OpenAI. Get keys from:

- Anthropic: https://console.anthropic.com/
- OpenAI: https://platform.openai.com/

Put them in `.env` (see below) — nothing in Phase 0/1 (ingestion + structural analysis) needs them.

## Quickstart (Docker Compose — recommended)

```bash
cp .env.example .env   # fill in ANTHROPIC_API_KEY / OPENAI_API_KEY when you reach Phase 2
docker compose up --build
```

This brings up 5 services: `postgres` (5432), `redis` (6379), `api` (8000), `worker` (arq, no exposed port), `web` (5173).

Verify:

```bash
curl http://localhost:8000/health   # {"status": "ok"}
open http://localhost:5173
```

Bring it down with `docker compose down` (add `-v` to also drop the postgres volume).

## Local dev (without Docker)

**Backend:**

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

You'll still need `postgres` and `redis` reachable — either run just those two via `docker compose up postgres redis`, or point `DATABASE_URL`/`REDIS_URL` in `.env` at your own instances.

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Git hooks

One-time per clone, to enable the local pre-push Codex review (see `CLAUDE.md` "Git workflow"):

```bash
git config core.hooksPath .githooks
```

Requires the [Codex CLI](https://developers.openai.com/codex) installed and logged in (`codex login`, ChatGPT subscription auth — not an API key). Advisory only: a missing CLI or a failed review just skips, never blocks the push.

## Database migrations

Migrations are managed with Alembic, run from `backend/` with the venv active:

```bash
alembic upgrade head                          # apply migrations
alembic revision --autogenerate -m "message"  # generate one from model changes
```

## Repository structure

See `PROJECT_PLAN.md` §6 for the full annotated layout. At a glance:

```
strata-learn/
├── docs/adr/        # architecture decision records
├── docs/prompts/     # versioned LLM prompt templates
├── backend/          # FastAPI app, tree-sitter analysis, arq worker
├── frontend/          # React + TypeScript + Vite + Tailwind
└── docker-compose.yml
```

## Status

Phase 0 (scoping & setup) complete — see `PROJECT_PLAN.md` §12 for the full phase-by-phase build plan.
