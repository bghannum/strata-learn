# Branch → PR → CI workflow, for your next project

Battle-tested on `strata-learn` (Aug 2026) — several things below turned out wrong or incomplete on first contact with a real repo. Corrections are inline; a full recap is at the bottom under [Known gotchas](#known-gotchas-found-building-this-the-first-time).

## The loop

```
git checkout -b feature/thing-name   # branch off main
# ...make changes, commit as you go...
git add -A && git commit -m "add thing"
git push -u origin feature/thing-name
```

Push the branch and GitHub shows a "Compare & pull request" banner on the repo page — click it (or run `gh pr create` if you have the GitHub CLI installed) to open the PR.

**Exception:** the very first push to a brand-new repo has to go straight to `main` — there's no remote branch to PR against yet. Everything after that first push goes through the loop.

If you've set up CI (below), it runs automatically against the PR and posts a green check or a red X. Look over your own diff in the PR's "Files changed" tab — you'll catch things there you don't catch in your editor, just from seeing it as a diff. Then merge.

For a solo repo, use **squash merge**: it collapses all your branch commits into one clean commit on `main`, so your main-branch history reads as "one entry per feature" instead of "wip", "fix typo", "actually fix it". Turn this on in Settings → General → Pull Requests → uncheck "Allow merge commits" and "Allow rebase merging", leave only "Allow squash merging".

After merge, delete the branch (button's right there in the merge UI). Or turn on Settings → General → "Automatically delete head branches" so it happens for you every time.

**These two settings are not defaults and nothing else turns them on for you.** The bootstrap script at the bottom sets both — but only if you actually run it. On `strata-learn` we built the pipeline incrementally instead of running the script up front, and both settings silently stayed off for three PRs before anyone noticed. Run the bootstrap script (or click these two boxes) right after `gh repo create`, not "eventually."

## CI: run tests automatically on every PR

Create `.github/workflows/ci.yml` in the repo root. The shape below is a starting point, not a template to copy verbatim — the real work is adapting each piece to your actual stack:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"   # or -r requirements.txt — see below
      - run: pytest -q
```

**Things this glosses over that bit us on a real repo:**

- **Dependency install isn't one-size-fits-all.** `pip install -r requirements.txt` only works if a `requirements.txt` exists. A `pyproject.toml`-based project (Poetry, Hatchling, PDM) needs `pip install -e ".[dev]"` or `uv sync` instead — check which one your project actually uses before pasting a step. If there's a lock file (`uv.lock`, `poetry.lock`), use it — otherwise CI resolves "latest compatible" on every run, which can silently drift into a version conflict your local env never hits. This matters a lot more if any dependency has a pinned-version comment explaining a bug it works around (see [Known gotchas](#known-gotchas-found-building-this-the-first-time)) — an unpinned CI install can reintroduce exactly that bug.
- **One `test` job assumes one stack.** A repo with a backend and a frontend needs two jobs (e.g. `backend-test`, `frontend-build`), each with its own `working-directory`, its own dependency install, and — critically — its own set of things later steps depend on.
- **If tests hit a real database (or Redis, or any other service), CI needs a service container for it**, plus whatever setup step brings it to a ready state (migrations, etc.):
  ```yaml
  services:
    postgres:
      image: postgres:16
      env:
        POSTGRES_USER: youruser
        POSTGRES_PASSWORD: yourpass
        POSTGRES_DB: yourdb
      ports:
        - 5432:5432
      options: >-
        --health-cmd "pg_isready -U youruser -d yourdb"
        --health-interval 5s
        --health-timeout 5s
        --health-retries 10
  ```
  followed by a migration step (`alembic upgrade head` or equivalent) *before* `pytest`. Skip this and every DB-backed test fails in CI while passing locally, because your local Postgres is already there and already migrated. This is the single most common gap between "works on my machine" and a fresh CI runner.
- **Action versions drift constantly and the ones below will be stale by the time you read this.** Check what's actually current before pasting: `gh api repos/actions/checkout/releases/latest --jq .tag_name` (swap in any action's `owner/repo`). Moving major tags like `@v1` are usually fine to leave as-is; pinned versions like `@v4` age out.

Commit and push that file — no other setup needed, GitHub detects `.github/workflows/*.yml` automatically.

Once it's in, every PR gets a checks section showing pass/fail before you merge. **A green check means the job exited 0 — it does not mean the job did what you think it did.** Watch the actual output (or the actual side effect — a comment posted, a file changed) at least once per new automation, not just the checkmark. This bit us directly: a Claude review job went green on every run while silently posting nothing, for two PRs, because an invalid config input was dropped with a warning instead of an error (details below).

## Making it non-optional (recommended for a portfolio repo)

**This needs GitHub Pro if the repo is private.** Both the classic branch-protection API and the newer rulesets API return a flat `403: Upgrade to GitHub Pro or make this repository public` on a private repo under a free personal account — confirmed by trying both directly, not documentation-inferred. There's no free-tier path to a technically-enforced rule on a private repo. Your options, in order of how we'd actually rank them:

1. **Skip it, rely on discipline.** Document the branch/PR habit in `CLAUDE.md`/`AGENTS.md` (see below) and just follow it. You still get the CI checks and both AI reviews on every PR — you just aren't technically blocked from pushing straight to `main` if you decide to. This is what we landed on for a solo learning repo.
2. **Make the repo public.** Free, unlocks protection immediately. Only sensible if you're fine with the source being visible — worth considering for a portfolio piece, but think about it before flipping the switch, not after something sensitive lands in the history.
3. **Upgrade to GitHub Pro** (~$4/mo). Keeps the repo private, unlocks protection. A billing decision — do it yourself at `github.com/settings/billing`, then everything below works as written.

If you do have protection available (public repo, Pro, or an org), the two settings underneath it are:

Settings → Branches → Add branch protection rule → branch name pattern `main`:
- "Require a pull request before merging" — blocks direct pushes to main, forces the branch/PR habit
- "Require status checks to pass before merging" → select your CI job(s) — blocks merging a PR with failing tests

This is what actually makes the green-check signal mean something — it's not just decorative, main literally can't get broken code without someone (you) overriding it deliberately.

## Required checks: pytest + Claude review + Codex review

### pytest (status check)

Already covered above — each job in `ci.yml` is a status check. Add it under "Require status checks to pass" once it's run at least once (GitHub only lists checks it's seen before). If you have multiple jobs (backend + frontend), require all of them.

### Claude review (advisory — not a required approval)

**Correction from the original version of this doc:** `post_as_review: true` is not a real input on `claude-code-action`, and even if it were spelled right, formal PR reviews are off the table entirely. Anthropic's own docs for the action are explicit and permanent about this:

> **What Claude Cannot Do:** Submit PR Reviews — Claude cannot submit formal GitHub PR reviews. Approve PRs — for security reasons, Claude cannot approve pull requests.

That's not a missing config flag, it's a deliberate restriction with no workaround. So Claude review can never satisfy a "required approvals" branch-protection rule — plan for it as advisory only, the same tier as Codex below. (On a solo repo this is moot anyway: GitHub separately blocks a PR author from approving their own PR, so "required approvals" isn't achievable there regardless of what Claude can do.)

**Don't have Claude call GitHub-posting tools itself — have it return structured findings and post them in a separate, deterministic step instead.** The first working version of this doc had Claude call `gh pr comment` directly, matching Anthropic's own documented "Automatic PR Code Review" example. That's the wrong shape for anything beyond a trivial diff: granting *just* the posting tools (`gh pr comment`, `gh pr diff`, `gh pr view`, inline comments) works on a small docs-only PR, but on a real multi-file code diff Claude also wants to read full file contents for context, not just the diff hunk — and without `Read`/`Grep`/`Glob` every one of those attempts gets denied. Confirmed on a real PR: the job ran 6+ minutes, made 27 turns, hit **43 permission denials**, and posted nothing. Widening `--allowedTools` to include `Read`/`Grep`/`Glob` helped (43 denials → 15) but didn't fully close it, and GitHub Actions redacts the action's detailed tool-call output by default (`show_full_output: false`, deliberately — the log could otherwise leak tokens), so there's no cheap way to see exactly which calls are still failing.

The fix that actually works: skip tool-based posting entirely. Pass `--json-schema` so Claude returns structured findings as its final message — confirmed locally with `permission_denials: []`, zero GitHub tool access needed for that at all — and let a plain `actions/github-script` step (not an LLM tool call, so it can't flake) do the posting. This is the exact same architecture as the Codex Action alternative below; apply it to Claude too instead of asking an LLM to reliably chain tool calls under a webhook's time/turn budget:

```yaml
name: Claude Review
on:
  pull_request:
    types: [opened, synchronize]
permissions:
  contents: read
  pull-requests: write
  id-token: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - uses: anthropics/claude-code-action@v1
        id: claude
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          plugin_marketplaces: "https://github.com/anthropics/claude-code.git"
          plugins: "code-review@claude-code-plugins"
          prompt: |
            REPO: ${{ github.repository }}
            PR NUMBER: ${{ github.event.pull_request.number }}

            Run /code-review:code-review on this PR. Return your findings as
            your final message, matching the provided JSON schema exactly.
            Do not call any GitHub-posting tool yourself — a separate,
            deterministic step later in this workflow handles posting the
            result, so nothing you post directly would ever be seen anyway.
          claude_args: |
            --allowedTools "Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(gh pr diff:*),Bash(gh pr view:*)"
            --json-schema '{"type":"object","properties":{"verdict":{"type":"string","enum":["approve","comment","request_changes"]},"summary":{"type":"string"},"findings":{"type":"string"}},"required":["verdict","summary","findings"]}'

      # structured_output is empty whenever the action didn't run to
      # completion — most commonly the workflow-file self-verification skip
      # a few paragraphs down. Expected, not a failure: every step below is
      # guarded so the job finishes quietly instead of crashing trying to
      # parse nothing.
      - name: Write review to file
        if: steps.claude.outputs.structured_output != ''
        env:
          REVIEW_JSON: ${{ steps.claude.outputs.structured_output }}
        run: echo "$REVIEW_JSON" > claude_review.json

      - name: Upload review artifact
        if: steps.claude.outputs.structured_output != ''
        uses: actions/upload-artifact@v7
        with:
          name: claude-review-pr-${{ github.event.pull_request.number }}
          path: claude_review.json

      - name: Post PR comment
        if: steps.claude.outputs.structured_output != ''
        uses: actions/github-script@v9
        with:
          script: |
            const fs = require('fs');
            const r = JSON.parse(fs.readFileSync('claude_review.json', 'utf8'));
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.payload.pull_request.number,
              body: `**Claude review — verdict: ${r.verdict}**\n\n${r.summary}\n\n${r.findings}`,
            });
```

`fetch-depth: 0` (full history, not shallow) matters here — Claude's `git diff`/`log`/`show` access needs the base branch's commit objects actually present locally to work, not just the merge commit's tree contents. Runs off your Claude subscription instead of API billing: run `claude setup-token` locally (Pro/Max/Team/Enterprise), save the result as a `CLAUDE_CODE_OAUTH_TOKEN` repo secret, and pass `claude_code_oauth_token` as above.

The env-var indirection in the "Write review to file" step (`REVIEW_JSON: ${{ ... }}` then `echo "$REVIEW_JSON"`) isn't stylistic — splicing a `${{ }}` expression straight into a `run:` script body is a real script-injection risk when the value can contain LLM-generated text derived from untrusted input (a malicious PR's content, in principle). Passing it through `env:` and referencing it as a shell variable avoids that; this is the same reason the Codex option below writes its own JSON to a file rather than interpolating text directly.

**A subtler gotcha:** `claude-code-action` refuses to run using a workflow-file version that differs from what's on the default branch — a deliberate anti-tampering guard, so a malicious PR can't rewrite this file to exfiltrate secrets or disable review on itself. Practical effect: **you cannot verify a change to `claude-review.yml` from inside the PR that makes the change.** The job goes green in seconds, having done nothing, and logs `Skipping action due to workflow validation` — read as informational, not a failure. Merge first, then check the *next* PR to confirm the new behavior actually works.

(Anthropic also ships a zero-workflow "Code Review" product — a Settings toggle instead of a YAML file — if you don't want to maintain this file yourself. Worth checking `code.claude.com/docs/en/code-review` before writing your own. It presumably has the same can't-formally-approve restriction, unconfirmed.)

### Codex review — two options, pick based on whether you want it PR-visible or free

**Option A: local, via a pre-push git hook (recommended for solo/subscription use).** The Codex CLI supports two auth modes: ChatGPT subscription login (Plus/Pro/Business/Edu/Enterprise — usage included, no metered billing) or an API key (usage-based). `codex login` gets you the former. Wire it into a **tracked** hook directory (git hooks in `.git/hooks/` aren't versioned by git, so use `core.hooksPath` instead):

```bash
# .githooks/pre-push
#!/usr/bin/env bash
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel)
output_file="$repo_root/CODEX_CODE_REVIEW.md"
rm -f "$output_file"

command -v codex >/dev/null 2>&1 || { echo "pre-push: codex CLI not found, skipping" >&2; exit 0; }

zero_oid="0000000000000000000000000000000000000000"
head_oid=$(git rev-parse HEAD)

while read -r local_ref local_oid _remote_ref _remote_oid; do
  [ "$local_oid" = "$zero_oid" ] && continue        # deleting a remote ref
  branch="${local_ref#refs/heads/}"
  [ "$branch" = "main" ] && continue
  [ "$local_oid" != "$head_oid" ] && { echo "pre-push: skipping $branch — not checked-out HEAD" >&2; continue; }

  echo "pre-push: running local Codex review of $branch against main..." >&2
  if codex exec review --base main --title "$branch" --output-last-message "$output_file"; then
    echo "pre-push: written to CODEX_CODE_REVIEW.md" >&2
  else
    echo "pre-push: Codex review failed, skipping (push continues)" >&2
    rm -f "$output_file"
  fi
done

exit 0
```

Read pushed refs from stdin (git's actual pre-push contract) rather than assuming the checked-out branch is what's being pushed — a first draft that skipped this got a wrong-branch review under `git push origin other-branch`, caught by Codex reviewing its own hook. One-time setup per clone: `chmod +x .githooks/pre-push && git config core.hooksPath .githooks`. Gitignore the output file (`CODEX_CODE_REVIEW.md`) — it's regenerated per push, never committed. Never blocks the push (missing CLI or failed review just skips); never posts anywhere visible (it's a local file) — you (or an agent working in the repo) have to be asked to go read it.

**Option B: GitHub Action (if you want it visible on the PR and don't mind paying).** `openai/codex-action` has no subscription-auth option — costs real OpenAI API credits per PR, billed per token regardless of any ChatGPT subscription:

```yaml
name: Codex Review
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  codex-review:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v7
        with:
          ref: refs/pull/${{ github.event.pull_request.number }}/merge

      - name: Run Codex review
        id: codex
        uses: openai/codex-action@v1
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
          prompt: "Review the diff for this PR. Return findings with a severity for each: low, medium, high, or critical."
          output-schema: |
            {
              "type": "object",
              "properties": {
                "severity": {"type": "string", "enum": ["none", "low", "medium", "high", "critical"]},
                "summary": {"type": "string"},
                "findings": {"type": "string"}
              },
              "required": ["severity", "summary", "findings"]
            }
          output-file: code_review.json

      - name: Upload code_review artifact
        uses: actions/upload-artifact@v7
        with:
          name: code_review-pr-${{ github.event.pull_request.number }}
          path: code_review.json

      - name: Post PR comment
        uses: actions/github-script@v9
        with:
          script: |
            const fs = require('fs');
            const r = JSON.parse(fs.readFileSync('code_review.json', 'utf8'));
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.payload.pull_request.number,
              body: `**Codex review — severity: ${r.severity}**\n\n${r.summary}\n\n${r.findings}`,
            });

      - name: Flag severity (visible, non-blocking)
        run: |
          sev=$(jq -r '.severity' code_review.json)
          echo "Codex severity: $sev"
          if [ "$sev" = "critical" ] || [ "$sev" = "high" ]; then
            echo "::error::Codex flagged a $sev issue — see PR comment / code_review artifact"
            exit 1
          fi
```

Deliberately **do not** add `codex-review` to "Require status checks to pass" even if you go this route — it should show a red X on the PR when something's flagged, without forcing you to resolve it before merging.

### Putting it together

Branch protection rule for `main` (if available — see the GitHub Pro / public-repo caveat above):
- Require a pull request before merging
- Require status checks to pass: your CI job(s) (`codex-review`, if you're using the Action version, stays off this list on purpose)
- **Do not set a required-approval count.** Nothing can satisfy one on a repo where Claude can't approve and the author can't self-approve — setting it to 1+ anyway just produces a permanent, unclearable block that only an admin override gets past.
- Require branches to be up to date before merging (optional — forces a rebase/merge from main before you can merge, avoids "worked on my branch, broke on main")

## Making this reusable across every new repo

Two different kinds of setup here — files that travel with the repo (copy them in, done), and GitHub-side configuration that lives on GitHub's servers, not in git, so it has to be (re)applied per repo no matter what you commit.

### Files to drop in (git-tracked, fully portable)

- `.github/workflows/ci.yml` — tests, shown above (adapt to your actual stack — dependency install, service containers, one job per stack)
- `.github/workflows/claude-review.yml` — Claude advisory review, shown above
- `.githooks/pre-push` — local advisory Codex review, shown above (or `.github/workflows/codex-review.yml` if you'd rather pay for a PR-visible version)
- `CLAUDE.md` and `AGENTS.md` (same content in both — Claude Code reads `CLAUDE.md`, Codex CLI/Codex reviews read `AGENTS.md` by convention) — a short block telling either agent, when it's working *in* the repo (not just reviewing PRs), to follow this same pipeline:

```markdown
## Git workflow
- Never commit directly to `main`. Create a feature branch (`git checkout -b feature/x`) and open a PR.
- Run the test suite locally before pushing.
- Pushing a non-`main` branch runs `.githooks/pre-push`: a local, advisory Codex review written to `CODEX_CODE_REVIEW.md` (gitignored). One-time setup per clone: `git config core.hooksPath .githooks`.
- Every PR also gets an automated Claude review, posted as a PR comment. Both AI reviews are advisory only — neither can gate a merge. CI must pass before merge.
- Before merging, ask the agent working the repo to check both reviews and act on anything real — neither can loop itself in, this only happens when asked.
- Use squash merge. Delete the branch after merge.
```

Committing these files into a repo is just `git add`/`commit`/`push` like anything else — no GitHub-side action needed for this part.

### What actually has to change on GitHub itself (not a git command)

This is the part that isn't a file, so copying files alone won't set it up:

1. **Install the Claude GitHub App**, once, at `github.com/apps/claude` → choose "All repositories" instead of selecting them one at a time. Do this once and every future repo — including ones that don't exist yet — is already covered. (Skippable if you already did this when you first set up Claude review.)
2. **Repo secrets** — `CLAUDE_CODE_OAUTH_TOKEN`, and `OPENAI_API_KEY` only if you're using the Codex Action instead of the local hook. Personal GitHub accounts (not organizations) can't share secrets across repos, so these need setting per repo. Scriptable with `gh secret set`, so it's one command, not a UI trip. Set secrets by running the command yourself (or piping from a local file) rather than pasting the raw value into a chat session — keeps it out of any transcript or shell history that isn't yours.
3. **Merge settings + branch protection** — per-repo, not stored in a file. Scriptable with `gh api`/`gh repo edit`. **These two run at different times, not both "immediately after `gh repo create`"** — secrets and merge settings don't depend on repo content and can run right away; branch protection targets `branches/main/protection`, and a freshly-created repo has no `main` branch yet (no commits = no branches at all) until your first push creates one. Running the whole script too early makes the branch-protection call 403 for a reason that has nothing to do with the GitHub-Pro/private-repo limitation below — split the run:

```bash
#!/usr/bin/env bash
# usage: ./bootstrap-repo.sh <owner>/<repo>
# Run right after `gh repo create` — before any push exists.
set -euo pipefail
REPO="$1"

# secrets — only CLAUDE_CODE_OAUTH_TOKEN if you're using the local Codex hook
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "$REPO" --body "$CLAUDE_CODE_OAUTH_TOKEN"
# gh secret set OPENAI_API_KEY --repo "$REPO" --body "$OPENAI_API_KEY"   # only if using the Codex Action

# merge settings: squash-only, auto-delete branches — always works, no tier restriction
gh repo edit "$REPO" \
  --enable-squash-merge \
  --enable-merge-commit=false \
  --enable-rebase-merge=false \
  --delete-branch-on-merge
```

```bash
#!/usr/bin/env bash
# usage: ./bootstrap-branch-protection.sh <owner>/<repo>
# Run after your first push to main — the branch has to exist first, or
# this 403s for an entirely different reason than the GitHub-Pro one below.
set -euo pipefail
REPO="$1"

# branch protection: require PR, CI must pass, no approval count.
# 403s on a private repo without GitHub Pro — that's expected on the free
# tier, not a bug in this script. Decide public-vs-Pro-vs-skip (see doc
# above) before assuming this step should have worked.
gh api "repos/$REPO/branches/main/protection" --method PUT --input - <<'JSON' || \
  echo "Branch protection failed — expected on a free-tier private repo (see doc), or main doesn't exist yet if you ran this too early" >&2
{
  "required_status_checks": { "strict": true, "contexts": ["test"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null
}
JSON
```

Export `CLAUDE_CODE_OAUTH_TOKEN` (and `OPENAI_API_KEY` if applicable) as local env vars before running it (don't hardcode them in the script). The `"contexts": ["test"]` line must match your CI job's actual `id`(s) in `ci.yml` — plural and stack-specific in most real repos (e.g. `["backend-test", "frontend-build"]`), not necessarily the single `test` from the minimal example.

## Day-to-day command reference

```
git checkout main && git pull        # start from a fresh main
git checkout -b feature/x            # new branch
git commit -m "..."                  # commit locally as you go
git push -u origin feature/x         # first push of the branch
gh pr create --fill                  # open PR (if using GitHub CLI)
gh pr checks                         # watch CI status from terminal
gh pr merge --squash --delete-branch # merge + cleanup, once green
```

No `gh` CLI needed — everything above the last three lines also works entirely through the GitHub web UI.

## Known gotchas (found building this the first time)

- **`post_as_review: true` isn't a real input** — silently dropped with a warning, not an error. The job goes green having done nothing.
- **Claude cannot submit formal PR reviews or approve PRs, period** — a permanent Anthropic restriction, not a config option. Plan Claude review as advisory from the start.
- **A PR author can't approve their own PR either** (a GitHub rule, not repo-specific), so "required approvals" is unachievable on a solo repo regardless of what Claude can or can't do.
- **Classic branch protection and rulesets both 403 on private repos without GitHub Pro.** No free-tier workaround except going public.
- **A change to a review workflow's own YAML can't be verified from inside the PR that makes the change** — the action refuses to run a workflow-file version that differs from the default branch, on purpose, as a tamper guard. Merge first, verify on the next PR.
- **DB-backed (or otherwise stateful) tests need a matching service container in CI**, or they pass locally and fail on every PR forever. If your dependencies have a pinned-version comment explaining a past bug (native ABI mismatch, connection-pool/event-loop conflict, etc.), an un-pinned or under-pinned CI install can silently reintroduce exactly that bug — lock files matter more than they look like they do.
- **A green check is not proof of a working step.** Check the actual side effect (comment posted, file written, review left) at least once per new piece of automation, not just the pass/fail badge — this caught both the `post_as_review` no-op above and an under-scoped `--allowedTools` list that burned 6 minutes and 43 permission denials on a real diff without posting anything, job still green throughout.
- **Repo-level settings (squash-only, auto-delete-branch) don't apply themselves** — even with a bootstrap script written, it only works if you actually run it, and run it early.
- **Don't ask an LLM review step to reliably chain GitHub-posting tool calls itself.** It's an extra source of flakiness with no upside — use `--json-schema` (or the equivalent structured-output flag for your tool) to get findings back as data, then post them with a plain, deterministic script step. Same architecture either reviewer should use, not just Codex.
