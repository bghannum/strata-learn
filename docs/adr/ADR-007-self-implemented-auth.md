# ADR-007: Self-implemented session auth, not a library

**Status:** Accepted

## Context

Strata Learn is single-tenant (one user account). A library like `fastapi-users` would satisfy the functional requirement faster, but hand-building auth is a stated learning goal independent of the study-guide product itself.

## Decision

Auth is single-tenant, session-based, and deliberately hand-built:

- Password hashing via bcrypt/argon2
- `User` table (modeled from Phase 0, wired up in Phase 4 — see `docs/design/original-project-plan.md` §12)
- HTTP-only cookie sessions
- `/auth/register`, `/auth/login`, `/auth/logout` endpoints

## Consequences

- Slower to build than reaching for a library — an explicit, accepted trade of build speed for hands-on auth experience.
- `Repo.user_id` and `Attempt.user_id` are modeled as nullable FKs from the phases that create those tables, so wiring up real auth in Phase 4 is additive, not a retrofit.
- Revisit if auth scope ever grows beyond single-tenant (OAuth, multi-user sharing) — that's a different, larger project and explicitly out of scope for now (see Open Question Q6 in `docs/design/original-project-plan.md` §4).
- Provisioning is a first-run *setup* step, not a signup: `GET /auth/status` tells the frontend whether the account exists yet, and `/register` only ever creates the first one. `REGISTRATION_SECRET` — added after Phase 4b review to close the race where a stranger reaches a freshly *hosted* install before its operator — is opt-in and blank by default (post-Phase 8): on localhost the first visitor is the operator, and demanding a value copied out of `.env` was the one thing between `docker compose up` and a usable app. Setting it is part of the productionization checklist. There is no in-app password recovery, because nothing over HTTP could prove it's the operator asking; `python -m app.cli reset-password` (wrapped by `scripts/reset-password`) is the recovery path, with shell access as the proof.
