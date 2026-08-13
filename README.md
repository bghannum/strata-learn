# Strata Learn

Strata Learn ingests a repository from a Git URL or zip upload, extracts its structure, and uses citation-grounded semantic analysis to help a developer understand the codebase. The eventual product adds generated study guides and quizzes; the current implementation covers ingestion plus structural and semantic analysis.

## Current status

Phases 0 through 3, plus the core of Phase 4, are complete:

- project scaffolding and local Docker environment;
- repository ingestion and deterministic Layer A analysis;
- Redis/arq background processing and WebSocket progress;
- Anthropic-backed Layer B module summaries, architecture-pattern detection, and trade-off extraction;
- citation-grounded study guide generation (Overview, Architecture, Trade-offs, Glossary, Deep-Dives) plus a Mermaid architecture diagram, served via `GET /repos/{id}/study-guide`;
- a working frontend: add a repo, watch it index live, and read the generated study guide with inline diagrams and clickable citations.

What's left of Phase 4 is session authentication (login/register UI + backend session handling) — the app is currently single-tenant with no login wall, by design for this stage. Phase-level progress lives in [GitHub Milestones](https://github.com/bghannum/strata-learn/milestones); actionable and deferred work lives in [GitHub Issues](https://github.com/bghannum/strata-learn/issues).

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
# Set ANTHROPIC_API_KEY in .env before indexing a repository.
docker compose up --build
```

Compose starts PostgreSQL, Redis, the FastAPI service, the arq worker, and the React/Vite development server.

Verify the API and open the frontend:

```bash
curl http://localhost:8000/health   # {"status":"ok"}
open http://localhost:5173
```

Add a repository from the Dashboard, watch it index, and open the generated study guide once it's ready. Interactive API documentation is available at `http://localhost:8000/docs`.

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
├── frontend/            React/Vite app: add a repo, watch progress, read the study guide
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
