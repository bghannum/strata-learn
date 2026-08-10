# ADR-001: Modular monolith, not microservices

**Status:** Accepted

## Context

Strata Learn has a multi-stage pipeline (ingestion, structural analysis, semantic analysis, generation, grading) plus a frontend-facing API. At single-user scale, a microservices split would add deployment and operational complexity without a corresponding scaling benefit.

## Decision

Single FastAPI app with clearly separated internal modules: `ingestion/`, `analysis/`, `generation/`, `grading/` (see `quizzing/grading/`). No premature service boundaries.

## Consequences

- One deployable artifact, one Docker image for the API, simpler local dev and hosting (see §13 of `PROJECT_PLAN.md`).
- Module boundaries are enforced by code organization and import discipline, not network calls.
- Revisit only if a specific module needs independent scaling — unlikely at single-user scale.
