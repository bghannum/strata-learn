## Git workflow

- Never commit directly to `main`. Create a feature branch (`git checkout -b feature/x`) and open a PR.
- Run `pytest -q` (from `backend/`, with `postgres` reachable) and `npm run build` (from `frontend/`) locally before pushing.
- Pushing a non-`main` branch runs `.githooks/pre-push` automatically: a local, advisory-only Codex review (`codex exec review --base main`) written to `CODEX_CODE_REVIEW.md` at the repo root (gitignored — regenerated per push, never committed). Runs off Codex's ChatGPT subscription auth (`codex login`), not a metered API key, and never blocks the push — a missing `codex` CLI or a failed review just skips. One-time setup per clone: `git config core.hooksPath .githooks`.
- Every PR also gets an automated Claude review (`post_as_review: true` — a formal GitHub review, required for merge). CI (`backend-test` + `frontend-build`) must pass before merge.
- GitHub won't let a PR author approve their own PR, so Claude's review is what actually satisfies the "require approval" branch protection rule on this solo repo. Still read the PR's "Files changed" tab and Claude's inline comments yourself before merging, and leave your own review comments — that's the human-review habit worth keeping even though it's not enforced by branch protection.
- Use squash merge. Delete the branch after merge.
