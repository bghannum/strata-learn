## Project status

Live phase tracker — update this table the moment a phase starts or its checkpoint passes, so status is visible here without opening `PROJECT_PLAN.md`. Full task lists/checkpoints per phase: `PROJECT_PLAN.md` §12.

| Phase | Status |
|---|---|
| 0 — Scoping & Setup | Done (2026-08-09) |
| 1 — Ingestion + Layer A (structural analysis) | Done (2026-08-10) |
| 1.5 — Job Queue Wiring | Done (2026-08-11) |
| 2 — Layer B (Semantic Analysis) | Code complete, tests passing, one real-repo checkpoint run reviewed and judged good (2026-08-12, `feature/phase-2-layer-b-semantic-analysis`) — hands-on review of the output deferred by the user to Phase 4 (no frontend yet to look at it through) |
| 3 — Study Guide Generation | Not started |
| 4 — Frontend Shell | Not started |
| 5 — Quiz Generation & Taking (MCQ/fill-blank) | Not started |
| 6 — Drawing Questions | Not started |
| 7 — Versioning, Diffing, Hosting, Polish | Not started |

Claude Code: don't wait to be asked "what's next" — when the previous phase's status here reads "Done" and no other work is in flight, name the next phase as the natural next step instead of leaving it for the user to notice.

## Git workflow

See `branch-pr-ci-workflow.md` at the repo root for the full reusable playbook (branch → PR → CI mechanics, why each piece of the review setup below is shaped the way it is, and every gotcha hit building it) — this section is just the strata-learn-specific summary.

- Never commit directly to `main`. Create a feature branch (`git checkout -b feature/x`) and open a PR.
- Run `pytest -q` (from `backend/`, with `postgres` reachable) and `npm run build` (from `frontend/`) locally before pushing.
- Pushing a non-`main` branch runs `.githooks/pre-push` automatically: a local, advisory-only Codex review (`codex exec review --base main`) written to `CODEX_CODE_REVIEW.md` at the repo root (gitignored — regenerated per push, never committed). Runs off Codex's ChatGPT subscription auth (`codex login`), not a metered API key, and never blocks the push — a missing `codex` CLI or a failed review just skips. One-time setup per clone: `git config core.hooksPath .githooks`.
- Every PR also gets an automated Claude review, posted as a PR comment by `.github/workflows/claude-review.yml`. This is advisory only, same tier as the local Codex review — Anthropic's `claude-code-action` cannot submit formal GitHub PR reviews or approve PRs (a deliberate, permanent restriction, not a config option — see its `docs/capabilities-and-limitations.md`). Don't reach for `post_as_review` or similar; it isn't a real input and fails silently (posts nothing, no error). Also don't have Claude call `gh pr comment`/inline-comment tools itself — on a real multi-file diff it needs `Read`/`Grep`/`Glob` for context, and every attempt at those got denied under a posting-tools-only `--allowedTools` list (6+ min, 43 permission denials, nothing posted, job still green). The workflow instead has Claude return structured findings via `--json-schema` (no GitHub tool access needed for that at all) and posts them in a separate, deterministic `actions/github-script` step — same architecture as the Codex Action alternative in the playbook doc, and far more reliable than hoping an LLM's tool calls all land. A change to `claude-review.yml` itself can't be verified from inside the PR that makes it — the action refuses to run a workflow-file version that differs from `main`, on purpose. Merge first, verify on the next PR.
- No branch protection is configured — GitHub requires Pro (or a public repo) for it on the free tier, and this repo is private. Nothing technically stops a direct push to `main`; the PR habit above is enforced by discipline only, not by GitHub. Revisit if the repo ever goes public or gets a Pro upgrade ([#10](https://github.com/bghannum/strata-learn/issues/10)). Repo settings that don't have a tier restriction *are* set: squash-only merge, auto-delete branch on merge.
- Before merging, ask Claude Code directly to check both reviews and act on anything real: read `CODEX_CODE_REVIEW.md` (already local from the pre-push hook) and fetch the Claude review comment (`gh pr view <N> --comments`), fix what's worth fixing, push again. Neither review can loop itself in — this only happens when you ask.
- Use squash merge. Delete the branch after merge.
- Deferred/non-blocking items (found mid-build, not worth stopping for) go in [GitHub Issues](https://github.com/bghannum/strata-learn/issues), not `backlog.md` — that file is a resolved-bugs log now (why a fix was needed, kept next to the code instead of buried in commit history), not a todo list. Issues don't live in git, so they don't create merge conflicts the way a shared markdown todo list did.

## Testing

- Backend tests hit a real Postgres, no mocking — a deliberate project philosophy (see the `clean_db` docstring in `backend/tests/conftest.py`). Don't introduce mocks for DB-backed tests; if a test needs isolation, clean the tables, don't fake the DB layer. This does not extend to the LLM provider (Phase 2+): `AnthropicProvider` calls a paid, non-deterministic external API, so pytest uses `FakeLLMProvider` instead — real calls happen only in each phase's manual checkpoint.
- Write real pytest coverage for each phase's new code as it's built, and run the full suite (`pytest -q` from `backend/`) at every phase checkpoint — not just for the files you just touched.
- CI's `backend-test` job (`.github/workflows/ci.yml`) provisions both Postgres and Redis service containers — keep this in sync if a future phase adds another stateful dependency (a new external service, a new DB, etc.); the same "passes locally, fails on every PR" gap bites every time this is forgotten.

## Dependency pinning — don't bump these casually

- `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript` are pinned together to a matching ABI generation (see the comment above them in `backend/pyproject.toml`). Bumping one without the others reproduces a native segfault already diagnosed once (`backlog.md` → Resolved). Bump all four together, only once `tree-sitter-typescript` ships an ABI-15 release. Guarded by `backend/tests/test_tree_sitter_smoke.py`.
- The async DB engine (`backend/app/db/session.py`) uses `NullPool` instead of default pooling — required because pytest-asyncio and `TestClient` exercise different event loops than a pooled engine tolerates (`backlog.md` → Resolved, "Future attached to a different loop"). Don't revert this for a perf "optimization" without re-testing that exact scenario.
