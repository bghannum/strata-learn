import uuid
from datetime import UTC, datetime, timedelta

from app.quizzing.mastery import (
    UNGROUPED_KEY,
    UNGROUPED_NAME,
    CountedAttempt,
    GradedAnswer,
    compute_mastery,
    select_counted_attempts,
)

_BASE = datetime(2026, 8, 1, tzinfo=UTC)


def _attempt(quiz_id, minutes: int, answers: list[tuple[str | None, float]]) -> CountedAttempt:
    return CountedAttempt(
        quiz_id=quiz_id,
        completed_at=_BASE + timedelta(minutes=minutes),
        answers=[GradedAnswer(subsystem_key=k, score=s) for k, s in answers],
    )


def _by_key(buckets):
    return {b.subsystem_key: b for b in buckets}


# --- retake handling ---


def test_only_the_most_recent_attempt_per_quiz_counts() -> None:
    # Three attempts at one quiz aren't three independent measurements — they
    # cover the same questions, and counting them all would weight whichever
    # quiz was retaken most, usually the one that was failed.
    quiz = uuid.uuid4()
    attempts = [
        _attempt(quiz, 0, [("app/api", 0.0)]),
        _attempt(quiz, 10, [("app/api", 0.5)]),
        _attempt(quiz, 20, [("app/api", 1.0)]),
    ]

    counted = select_counted_attempts(attempts)

    assert len(counted) == 1
    assert counted[0].answers[0].score == 1.0


def test_different_quizzes_all_count() -> None:
    attempts = [_attempt(uuid.uuid4(), 0, [("app/api", 1.0)]) for _ in range(3)]

    assert len(select_counted_attempts(attempts)) == 3


def test_counted_attempts_are_ordered_oldest_first() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    counted = select_counted_attempts([_attempt(b, 30, []), _attempt(a, 10, [])])

    assert [c.quiz_id for c in counted] == [a, b]


# --- aggregation ---


def test_scores_are_averaged_per_subsystem() -> None:
    # 0.5 is a real grade (fill-in-the-blank concept grading awards partial
    # credit), so this averages rather than counting right/wrong.
    attempts = [_attempt(uuid.uuid4(), 0, [("app/api", 1.0), ("app/api", 0.5), ("app/db", 0.0)])]

    buckets = _by_key(compute_mastery(attempts, {}))

    assert buckets["app/api"].average_score == 0.75
    assert buckets["app/api"].answered == 2
    assert buckets["app/db"].average_score == 0.0


def test_aggregation_spans_multiple_quizzes() -> None:
    attempts = [
        _attempt(uuid.uuid4(), 0, [("app/api", 1.0)]),
        _attempt(uuid.uuid4(), 10, [("app/api", 0.0)]),
    ]

    bucket = _by_key(compute_mastery(attempts, {}))["app/api"]

    assert bucket.attempts == 2
    assert bucket.answered == 2
    assert bucket.average_score == 0.5


def test_history_has_one_point_per_attempt_oldest_first() -> None:
    attempts = [
        _attempt(uuid.uuid4(), 0, [("app/api", 0.0), ("app/api", 0.0)]),
        _attempt(uuid.uuid4(), 60, [("app/api", 1.0)]),
    ]

    history = _by_key(compute_mastery(attempts, {}))["app/api"].history

    assert [p.average_score for p in history] == [0.0, 1.0]
    assert [p.answered for p in history] == [2, 1]
    assert history[0].completed_at < history[1].completed_at


def test_weakest_subsystem_sorts_first() -> None:
    # The point of this view is deciding what to study next; the answer should
    # be at the top, not somewhere in an alphabetical list.
    attempts = [_attempt(uuid.uuid4(), 0, [("app/aaa", 1.0), ("app/zzz", 0.0)])]

    assert [b.subsystem_key for b in compute_mastery(attempts, {})] == ["app/zzz", "app/aaa"]


# --- ungrouped ---


def test_questions_without_a_subsystem_are_bucketed_not_dropped() -> None:
    # A snapshot indexed before subsystems existed produces nothing but these;
    # excluding them would under-report how much was answered.
    attempts = [_attempt(uuid.uuid4(), 0, [(None, 0.0), ("app/api", 1.0)])]

    buckets = _by_key(compute_mastery(attempts, {}))

    assert buckets[UNGROUPED_KEY].answered == 1
    assert buckets[UNGROUPED_KEY].name == UNGROUPED_NAME


def test_ungrouped_sorts_last_even_when_weakest() -> None:
    # "You're weak on Ungrouped" is not an actionable finding.
    attempts = [_attempt(uuid.uuid4(), 0, [(None, 0.0), ("app/api", 0.5)])]

    assert [b.subsystem_key for b in compute_mastery(attempts, {})][-1] == UNGROUPED_KEY


# --- names ---


def test_current_names_are_applied() -> None:
    attempts = [_attempt(uuid.uuid4(), 0, [("app/semantics", 1.0)])]

    buckets = compute_mastery(attempts, {"app/semantics": "Semantic analysis"})

    assert buckets[0].name == "Semantic analysis"


def test_a_key_with_no_current_name_falls_back_to_the_key() -> None:
    # The subsystem's directory no longer exists, so nothing claims the key —
    # but the history is still real, and dropping it would rewrite the past.
    attempts = [_attempt(uuid.uuid4(), 0, [("app/removed", 0.0)])]

    buckets = compute_mastery(attempts, {"app/api": "HTTP API"})

    assert buckets[0].name == "app/removed"


def test_no_attempts_produces_no_buckets() -> None:
    assert compute_mastery([], {"app/api": "HTTP API"}) == []


def test_an_attempt_with_no_answers_contributes_nothing() -> None:
    # A completed attempt where every submission is still ungraded.
    assert compute_mastery([_attempt(uuid.uuid4(), 0, [])], {}) == []
