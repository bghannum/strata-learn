## Git workflow

The canonical process is [`docs/development/workflow.md`](docs/development/workflow.md).

- Never commit directly to `main`; use a feature branch and pull request.
- Before a completed PR is pushed, run `pytest -q` from `backend/` with PostgreSQL and Redis reachable, then run `npm run lint` and `npm run build` from `frontend/`.
- Routine pushes run deterministic CI only. AI review is intentional, not automatic.
- After implementation is complete, committed, and passing the full suite, run `./scripts/codex-review` once. It writes the gitignored `CODEX_CODE_REVIEW.md`.
- Validate findings rather than accepting them mechanically. Resolve all `BLOCK` findings together with regression coverage. Create non-duplicate GitHub Issues for valid `DEFER` findings.
- Re-review only when the grouped fixes materially change security boundaries, persistence, concurrency, external-API cost, or architecture.
- Use squash merge and delete the feature branch afterward.
- Open and deferred work belongs in [GitHub Issues](https://github.com/bghannum/strata-learn/issues). [`docs/history/resolved-engineering-issues.md`](docs/history/resolved-engineering-issues.md) is historical context, not a backlog.

No branch protection is currently configured because this private repository’s GitHub plan does not provide it. The pull-request habit is enforced by discipline. Squash-only merge and automatic branch deletion are enabled.

## Testing

- Backend database tests use real PostgreSQL; clean tables for isolation rather than mocking the DB layer. See the `clean_db` fixture in `backend/tests/conftest.py`.
- Redis-backed behavior also uses a real Redis service in CI.
- Paid, nondeterministic LLM calls are the exception: automated tests use `FakeLLMProvider`; real Anthropic calls occur only in manual checkpoints and application use.
- Add pytest coverage with each phase and run the full backend suite at phase checkpoints.
- Keep `.github/workflows/ci.yml` synchronized when a new stateful dependency is introduced.

## Dependency constraints

- `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`, and `tree-sitter-typescript` are pinned to one ABI generation. Bump all four together only after `tree-sitter-typescript` ships a compatible ABI-15 release. `backend/tests/test_tree_sitter_smoke.py` guards this constraint.
- The async engine in `backend/app/db/session.py` intentionally uses `NullPool` because pytest-asyncio and `TestClient` exercise different event loops. Do not replace it with pooled connections without retesting that exact scenario.
