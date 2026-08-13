# AI review experiments and lessons learned

**Status:** Historical. This document explains why Strata Learn uses one intentional manual Codex review instead of automatic AI review on every push. Follow the [current development workflow](../development/workflow.md), not the retired configurations described here.

## Approaches tried

The repository initially ran two advisory reviewers:

1. a local Codex review from a tracked `pre-push` hook, reviewing the full feature branch against `main`;
2. a Claude GitHub Action on every pull-request open and synchronization event.

Neither reviewer could approve or formally gate a merge. Both were intended as extra signals alongside deterministic CI.

## What failed

### Full review on every push

The pre-push hook ran `codex exec review --base main` after every correction. Because that command reviews the cumulative branch diff, each push reprocessed the same large change and tended to find progressively smaller edge cases. Phase 2 reached nine Codex rounds before the process was stopped.

The early rounds found important issues, including unintended symlink reads, premature terminal states, and unbounded paid LLM work. The problem was not the independent review; it was coupling a full review to every push and treating every new finding as another loop iteration.

### Two AI reviewers for the same diff

Claude authored most changes, Claude reviewed every PR update in CI, Codex reviewed every push, and Claude then triaged both outputs. This duplicated context consumption while weakening reviewer independence.

### LLM-controlled comment posting

The first Claude workflow asked the model to call GitHub posting tools. It worked on a small documentation PR but failed on a real multi-file diff: restricted tool permissions prevented the model from reading enough context, widening permissions still left denials, and the green job produced no comment.

The reliable version returned structured JSON and used a deterministic `actions/github-script` step to post it. That fixed delivery but did not fix the repeated-review economics.

### Misleading green checks

Several review jobs exited successfully while producing no review. A green automation badge proves only that a job exited successfully; new automation must be verified by checking its intended side effect.

### Workflow self-verification

The Claude action would not execute a workflow-file revision that differed from the default branch, as an anti-tampering safeguard. A PR changing the review workflow therefore could not validate that workflow version from within the same PR.

## Decision

Strata Learn now uses:

- deterministic tests, lint, and builds on every PR update;
- Claude as the usual implementing agent;
- one manually triggered Codex review when a change is merge-ready;
- consequence-based `BLOCK` versus `DEFER` triage;
- at most one targeted verification review for materially risky corrections.

The retired `.githooks/pre-push` and `.github/workflows/claude-review.yml` files were removed. Their useful operational lessons are preserved here without keeping obsolete runnable examples in the main workflow.
