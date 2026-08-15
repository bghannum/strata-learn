"""Aggregates quiz performance per subsystem over time (#64).

Every attempt was previously an isolated score. Nothing accumulated, so the
tool could tell you how you did on one quiz but not what you understand — which
is the actual question it exists to answer.

Pure functions over already-fetched rows; the API layer owns the queries, the
same split analysis/snapshot.py and generation/diffing.py already use.

## Why subsystem_key rather than section or question id

Re-indexing replaces every Section, Question, and Citation row. Aggregating on
any of them would silently reset a learner's history at exactly the moment it
became interesting. Question.subsystem_key (#61) is copied off the seed
citation at generation time precisely so scores from before and after a
re-index describe the same topic.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

# What a question with no subsystem aggregates under. Bucketed rather than
# dropped: a snapshot indexed before subsystems existed produces nothing but
# these, and silently excluding them would under-report how much was answered.
UNGROUPED_KEY = "__ungrouped__"
UNGROUPED_NAME = "Ungrouped"


@dataclass(frozen=True)
class GradedAnswer:
    subsystem_key: str | None
    # 0.0 / 0.5 / 1.0 — fill-in-the-blank concept grading awards partial credit
    # (quizzing/grading/fill_blank_grader.py), so this is averaged rather than
    # counted as right/wrong.
    score: float


@dataclass(frozen=True)
class CountedAttempt:
    quiz_id: UUID
    completed_at: datetime
    answers: list[GradedAnswer]


@dataclass(frozen=True)
class MasteryPoint:
    completed_at: datetime
    answered: int
    average_score: float


@dataclass(frozen=True)
class MasteryBucket:
    subsystem_key: str
    name: str
    attempts: int
    answered: int
    average_score: float
    history: list[MasteryPoint]


def select_counted_attempts(attempts: list[CountedAttempt]) -> list[CountedAttempt]:
    """One attempt per quiz: the most recently completed.

    A retake covers the same questions as the attempt before it, so three
    attempts at one quiz are not three independent measurements — counting them
    all would weight whichever quiz was retaken most, which is usually the one
    that was failed. Keeping the latest reads the aggregate as "what you
    understand now", which is the question a learner is asking; keeping the
    first would answer "what you understood before studying", which is a
    different and less useful question.

    Deliberately a separate, named step rather than a clause inside the
    aggregation, because it is the assumption most likely to need revisiting.
    """
    latest: dict[UUID, CountedAttempt] = {}
    for attempt in attempts:
        current = latest.get(attempt.quiz_id)
        if current is None or attempt.completed_at > current.completed_at:
            latest[attempt.quiz_id] = attempt
    return sorted(latest.values(), key=lambda a: a.completed_at)


def compute_mastery(attempts: list[CountedAttempt], subsystem_names: dict[str, str]) -> list[MasteryBucket]:
    """Per-subsystem totals plus a point per attempt, oldest first.

    `subsystem_names` maps key -> display name, normally from the repo's latest
    snapshot. A key with no current name (its directory no longer exists, so no
    subsystem claims it) falls back to the key itself rather than disappearing:
    the history is still real, and dropping it would quietly rewrite the past.
    """
    counted = select_counted_attempts(attempts)

    totals: dict[str, list[float]] = {}
    per_attempt: dict[str, list[MasteryPoint]] = {}

    for attempt in counted:
        by_key: dict[str, list[float]] = {}
        for answer in attempt.answers:
            key = answer.subsystem_key or UNGROUPED_KEY
            by_key.setdefault(key, []).append(answer.score)
            totals.setdefault(key, []).append(answer.score)
        for key, scores in by_key.items():
            per_attempt.setdefault(key, []).append(
                MasteryPoint(
                    completed_at=attempt.completed_at,
                    answered=len(scores),
                    average_score=sum(scores) / len(scores),
                )
            )

    buckets = [
        MasteryBucket(
            subsystem_key=key,
            name=UNGROUPED_NAME if key == UNGROUPED_KEY else subsystem_names.get(key, key),
            attempts=len(per_attempt.get(key, [])),
            answered=len(scores),
            average_score=sum(scores) / len(scores),
            history=per_attempt.get(key, []),
        )
        for key, scores in totals.items()
    ]

    # Weakest first: the point of this view is deciding what to study next, and
    # the answer is at the top rather than somewhere in an alphabetical list.
    # Ungrouped sorts last regardless — it isn't a topic, so "you're weak on
    # Ungrouped" is not an actionable finding.
    return sorted(
        buckets, key=lambda b: (b.subsystem_key == UNGROUPED_KEY, b.average_score, b.subsystem_key)
    )
