# Contributing

Strata Learn is a solo learning project that happens to be public. Issues, questions, and pull requests are welcome, with the understanding that the maintainer's time is limited and the [roadmap](README.md#roadmap) reflects what *they* want to learn next — a well-made PR outside that roadmap may sit for a while.

## Before you start

- **Bugs**: open an issue with the reproduction (repo URL or zip you indexed, what you expected, what happened, relevant `docker compose logs api worker` output). Check the [open issues](https://github.com/bghannum/strata-learn/issues) first.
- **Features**: open an issue before writing code. Most planned work already has a milestone; comment on the existing issue if one matches.
- **Security**: see [`SECURITY.md`](SECURITY.md).

## Working on the code

The branch, PR, CI, and review process is documented in [`docs/development/workflow.md`](docs/development/workflow.md); the short version:

1. Branch from `main`; never commit to `main` directly.
2. Keep the change focused. Small PRs get reviewed; sweeping ones get deferred.
3. Run the full checks locally before pushing — they're what CI runs:
   ```bash
   cd backend && pytest -q          # needs PostgreSQL + Redis, e.g. `docker compose up postgres redis`
   cd frontend && npm run lint && npm test && npm run build
   ```
4. Add tests with the change. Backend tests use real PostgreSQL/Redis (no mocked DB layer) and `FakeLLMProvider` for anything that would otherwise call a paid API — a PR must not add real LLM calls to the test suite.
5. If a change alters an implemented endpoint, service, persistence, or pipeline behavior, update [`docs/architecture.md`](docs/architecture.md) in the same PR. If it changes an architectural decision, add or supersede an ADR under [`docs/adr/`](docs/adr/) rather than rewriting an accepted one.
6. Files under [`docs/prompts/`](docs/prompts/) are **runtime assets** loaded by the backend; changing one is a behavior change and needs a new version file plus tests, not an in-place edit.
7. PRs are squash-merged.

## Conventions worth knowing

- `tree-sitter` and its language grammars are pinned to one ABI generation and must move together (`backend/tests/test_tree_sitter_smoke.py` guards it).
- The async SQLAlchemy engine deliberately uses `NullPool` (see `CLAUDE.md`); don't switch it without retesting pytest + `TestClient` together.
- The arq worker does not hot-reload; `docker compose restart worker` after touching `app/quizzing/`, `app/generation/`, `app/semantics/`, or `app/worker/`.
- The project is built with an AI coding agent in the loop and reviewed by a second one (`scripts/codex-review`); `CLAUDE.md`/`AGENTS.md` are the operating instructions those agents follow. Human contributors are held to the same checklist, not a stricter one.

## License

By contributing you agree your contribution is licensed under the [MIT License](LICENSE).
