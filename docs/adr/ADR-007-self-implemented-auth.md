# ADR-007: Self-implemented session auth, not a library

**Status:** Accepted

## Context

Strata Learn is single-tenant (one user account). A library like `fastapi-users` would satisfy the functional requirement faster, but hand-building auth is a stated learning goal independent of the study-guide product itself.

## Decision

Auth is single-tenant, session-based, and deliberately hand-built:

- Password hashing via bcrypt/argon2
- `User` table (modeled from Phase 0, wired up in Phase 4 — see `PROJECT_PLAN.md` §12)
- HTTP-only cookie sessions
- `/auth/register`, `/auth/login`, `/auth/logout` endpoints

## Consequences

- Slower to build than reaching for a library — an explicit, accepted trade of build speed for hands-on auth experience.
- `Repo.user_id` and `Attempt.user_id` are modeled as nullable FKs from the phases that create those tables, so wiring up real auth in Phase 4 is additive, not a retrofit.
- Revisit if auth scope ever grows beyond single-tenant (OAuth, multi-user sharing) — that's a different, larger project and explicitly out of scope for now (see Open Question Q6 in `PROJECT_PLAN.md` §4).
