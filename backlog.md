# Backlog

Items worth doing but not blocking the current phase — deferred scope, cleanup, ideas surfaced mid-build. Not the same as `PROJECT_PLAN.md` §4 Open Questions (those are pre-identified design decisions the plan deliberately deferred); this file is for things that come up *while building* that didn't exist as a question when the plan was written.

Pull an item off this list into the relevant phase task when it becomes relevant, rather than letting it sit here forever unaddressed.

## Format

```
- [ ] Short description (found while working on: <phase/task>, <date>)
      Why it matters / context, if not obvious.
```

## Open

- [ ] `main` has no branch protection (found while working on: CI pipeline setup, 2026-08-11)
      GitHub's branch protection and rulesets APIs both refuse private repos on the free tier (`403: Upgrade to GitHub Pro or make this repository public`) — confirmed by trying both. The PR-before-merge habit is documented in `CLAUDE.md` but not technically enforced; nothing stops a direct push to `main`. Revisit if the repo goes public or gets a Pro upgrade.
- [ ] Frontend has no test runner configured (found while working on: CI pipeline setup, 2026-08-11)
      `frontend/package.json` only has `lint` (oxlint) and `build` (tsc + vite) scripts — no `test` script, so `.github/workflows/ci.yml`'s `frontend-build` job only lints and builds. Add `vitest` (fits the existing Vite setup) once frontend logic exists that's worth unit-testing.
- [ ] `entry_points.py` heuristics regex-match raw file bytes, not parsed AST (found while working on: Phase 1 `analysis/entry_points.py`, 2026-08-10)
      Means a file that merely *mentions* `FastAPI(`, `WorkerSettings`, etc. in a comment/string/docstring gets flagged as an entry point, not just files that actually call/define them — confirmed by dogfooding: `entry_points.py` flags itself, since its own reason strings contain the literal pattern text. Low priority — real target repos (Flask/FastAPI apps) are very unlikely to hit this; would need AST-aware call-expression detection (reusing parser.py's tree-sitter trees) to fix properly.

## Resolved

- [x] tree-sitter core (0.26.0) + latest tree-sitter-python/-javascript (0.25.0, ABI 15) alongside tree-sitter-typescript's latest release (0.23.2, ABI 14) caused reproducible native segfaults during parsing (found while working on: Phase 1 `analysis/snapshot.py` orchestration checkpoint, 2026-08-10).
      Resolved by pinning all four packages to the matching ABI-14 generation (tree-sitter==0.23.2, tree-sitter-python==0.23.6, tree-sitter-javascript==0.23.1, tree-sitter-typescript==0.23.2) in `pyproject.toml`. Revisit only once tree-sitter-typescript ships an ABI-15 build. Now guarded by `tests/test_tree_sitter_smoke.py`.
- [x] `db/session.py`'s module-level async engine used the default connection pool, which broke ("Future attached to a different loop") whenever DB access happened from a different event loop than whichever one first touched the engine — reproducible with pytest-asyncio fixtures + `TestClient` in the same test (found while working on: Phase 1 API test suite, 2026-08-10).
      Resolved by switching the engine to `NullPool` (no cross-loop connection reuse). Also would have eventually bitten Phase 1.5's worker process. Now guarded by `tests/api/test_repos.py`.
