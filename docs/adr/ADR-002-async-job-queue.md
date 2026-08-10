# ADR-002: Async job queue from day one

**Status:** Accepted

## Context

Repo indexing is slow (seconds to minutes depending on size) and multi-stage (clone → parse → analyze → generate). A synchronous request-response model would block on this and time out on anything but the smallest repos.

## Decision

Model indexing as a job pipeline with persisted state from the start (`AnalysisSnapshot.status`), using `arq` (Redis-backed) as the queue, even though it adds infra complexity earlier than strictly necessary for a first walking skeleton.

`POST /repos` is built synchronously first in Phase 1 (D13) and refactored to enqueue in Phase 1.5 — the data model and status states are designed for the async shape from Phase 1 so this refactor is mechanical, not a redesign.

## Consequences

- Redis becomes a required piece of local/hosted infra (see `docker-compose.yml`).
- Every pipeline stage publishes progress, enabling `WS /repos/{id}/progress` and the `IndexingProgress.tsx` UI.
- Retrofitting async job handling after the fact — once callers assume synchronous responses — would be significantly more expensive than building it in now.
