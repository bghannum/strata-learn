"""Caches study-guide assembly's expensive LLM intermediates against their
snapshot, so a crash between generating one and committing the finished guide
doesn't rebill it on redelivery (#23).

## Session discipline

Each function here opens its own short session and closes it before returning.
That's consistent with the rule the rest of the pipeline follows — never hold a
session open *across* an LLM call, because NullPool (db/session.py) makes every
checkout a real Postgres connection — rather than a broader "no DB access
during assembly". `build_sections` loads before each call and saves after, with
no session alive while the model is being waited on.

## Why this exists at all

The original resume fix (Phase 3) covered the expensive case: a redelivery
landing between Layer B's commit and the guide's commit skips Layer B entirely.
It didn't cover the calls study-guide assembly makes itself, which was
acceptable when that was one diagram-label call at the cheapest tier. Phase 6
added the architecture narrative — strongest tier, largest prompt — to the same
unprotected window, which is what made this worth fixing.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import GeneratedArtifact
from app.db.session import async_session_factory

ARCHITECTURE_NARRATIVE = "architecture_narrative"
COMPONENT_DIAGRAM = "component_diagram"


@dataclass(frozen=True)
class CachedArtifact:
    payload: dict
    prompt_version: str
    model: str


async def load_artifact(snapshot_id: UUID, kind: str) -> CachedArtifact | None:
    async with async_session_factory() as session:
        row = (
            await session.exec(
                select(GeneratedArtifact).where(
                    GeneratedArtifact.snapshot_id == snapshot_id, GeneratedArtifact.kind == kind
                )
            )
        ).first()
    if row is None:
        return None
    return CachedArtifact(payload=row.payload, prompt_version=row.prompt_version, model=row.model)


async def save_artifact(snapshot_id: UUID, kind: str, payload: dict, prompt_version: str, model: str) -> None:
    """Insert-if-absent. A losing race (two redeliveries generating the same
    artifact concurrently) keeps whichever committed first rather than raising:
    both paid for a result, and the two are interchangeable, so there's nothing
    to reconcile — the point is only that a *third* attempt won't pay again.
    """
    async with async_session_factory() as session:
        session.add(
            GeneratedArtifact(
                snapshot_id=snapshot_id,
                kind=kind,
                payload=payload,
                prompt_version=prompt_version,
                model=model,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()  # unique (snapshot_id, kind) — someone else got there first
