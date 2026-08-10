# ADR-008: No local-filesystem ingestion — git URL and zip upload only

**Status:** Accepted

## Context

The obvious "fast path" for a solo-use tool would be to let the app read an arbitrary local directory the user points at. But `.gitignore`-based filtering is a signal-reduction step (skip noise like `node_modules`), not a permission boundary — a file-walker bug or edge case could otherwise read outside the intended scope of an arbitrary local path.

## Decision

Repos are ingested only by cloning a remote git URL (`GitPython`) or accepting a zip upload, extracted into a scoped temp working directory fully controlled by the app (`/tmp/strata-learn-jobs/{snapshot_id}/`). There is no local-filesystem-path ingestion option, in `ingestion/source.py` or in `AddRepo.tsx`.

## Consequences

- Bounds the trust boundary to a scoped, user-intentional snapshot rather than an open-ended local path.
- Removes any architectural dependency on where the app is hosted (§13) — there's no "local machine" the backend needs filesystem access to, which is what makes the local-Docker-Compose → VPS hosting sequencing (§13) a pure deployment-logistics move rather than a redesign.
- The temp working directory is deleted after each pipeline run (§8 step 13); citations capture `snippet_text` at generation time specifically so nothing downstream needs to re-read the original source.
