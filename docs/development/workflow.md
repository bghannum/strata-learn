# Development workflow

This is the current branch, pull-request, CI, and review process for Strata Learn. Historical experiments with automatic AI review live in [`../history/ai-review-experiments.md`](../history/ai-review-experiments.md).

## Standard loop

```bash
git switch main
git pull --ff-only
git switch -c feature/descriptive-name

# implement and test
git add <intentional-files>
git commit -m "Describe the change"
git push -u origin HEAD
gh pr create --fill
```

Never commit directly to `main`. Branch protection on `main` (PR required, CI green) backs this up server-side now that the repository is public; the habit predates it and still applies to anyone with admin rights.

Use squash merge and delete the feature branch afterward:

```bash
gh pr merge --squash --delete-branch
```

## Deterministic checks

Before pushing a completed phase or PR, run the same checks CI runs:

```bash
cd backend
pytest -q

cd ../frontend
npm run lint
npm test
npm run build
npm run test:e2e   # Playwright; `npx playwright install chromium` once
```

Backend tests use real PostgreSQL and Redis services, against a dedicated `strata_learn_test` database (never the app's). CI provisions both in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). If another stateful dependency is introduced, update CI at the same time.

Every PR update runs:

- backend migrations and the full pytest suite;
- frontend lint, Vitest, and production build;
- Playwright end-to-end smoke tests.

These deterministic checks are the only automatic review gates.

## Manual Codex review

Claude normally implements a change and Codex provides one independent merge-readiness review. Run it only after the implementation is complete, committed, and passing the full suite:

```bash
./scripts/codex-review
```

The script reviews the feature branch against `main` and writes the gitignored `CODEX_CODE_REVIEW.md`. It requires a clean worktree so the report always corresponds to a reproducible commit.

The report separates findings into:

- `BLOCK`: resolve before merge because the finding risks security, unintended disclosure, data corruption, unbounded or repeated cost, failure on ordinary supported inputs, or a violated acceptance criterion;
- `DEFER`: a valid concern that does not prevent the current phase from safely delivering its intended scope.

The implementing agent must validate findings rather than accept them mechanically. Resolve all blockers in one batch, add regression tests, and create GitHub Issues for valid deferred findings after checking for duplicates.

Do not rerun a full review after every fix. A second review is warranted only when the grouped fixes materially change security boundaries, persistence, concurrency, external-API cost, or architecture.

## Tracking work

Phase outcomes and progress belong in [GitHub Milestones](https://github.com/bghannum/strata-learn/milestones). Actionable and deferred work belongs in [GitHub Issues](https://github.com/bghannum/strata-learn/issues). The repository’s resolved-issues document is historical context, not a backlog.

When a PR resolves an issue, include `Fixes #<number>` in the PR description. Keep phase status in the root [README](../../README.md) concise; GitHub Issues are the detailed tracker.

## Merge checklist

- Full backend tests pass locally.
- Frontend lint, Vitest, and build pass locally.
- CI is green on the final pushed commit.
- The manual Codex review was run once when the change warranted it.
- All validated `BLOCK` findings are resolved with coverage.
- Valid `DEFER` findings are represented by non-duplicate GitHub Issues.
- The PR is squash-merged and its branch is deleted.
