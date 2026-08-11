## Git workflow

- Never commit directly to `main`. Create a feature branch (`git checkout -b feature/x`) and open a PR.
- Run `pytest -q` (from `backend/`, with `postgres` reachable) and `npm run build` (from `frontend/`) locally before pushing.
- Every PR gets an automated Claude review (`post_as_review: true` — a formal GitHub review, required for merge) and a Codex review (advisory PR comment, non-blocking). CI (`backend-test` + `frontend-build`) must pass before merge.
- GitHub won't let a PR author approve their own PR, so Claude's review is what actually satisfies the "require approval" branch protection rule on this solo repo. Still read the PR's "Files changed" tab and Claude's inline comments yourself before merging, and leave your own review comments — that's the human-review habit worth keeping even though it's not enforced by branch protection.
- Use squash merge. Delete the branch after merge.
