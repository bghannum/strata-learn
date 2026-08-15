import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def _timestamptz_column(**kwargs: object) -> Column:
    # Field(default_factory=utcnow) alone produces a timezone-*aware* Python
    # datetime, but SQLModel's default DateTime column is TIMESTAMP WITHOUT TIME
    # ZONE — asyncpg then refuses to encode it ("can't subtract offset-naive and
    # offset-aware datetimes"). Store as TIMESTAMPTZ instead, matching the values
    # actually being written.
    return Column(DateTime(timezone=True), nullable=False, **kwargs)


# SQLModel 0.0.39 can't turn a bare `typing.Literal` into a column type (it only
# special-cases `enum.Enum` subclasses) — see get_sqlalchemy_type in sqlmodel/main.py.
# docs/design/original-project-plan.md's schema sketch uses Literal as shorthand; str-Enums are the
# concrete equivalent that this SQLModel version can actually map to a column.
class SourceType(str, Enum):
    git_url = "git_url"
    zip_upload = "zip_upload"


class SnapshotStatus(str, Enum):
    pending = "pending"
    parsing = "parsing"
    analyzing = "analyzing"
    generating = "generating"
    ready = "ready"
    failed = "failed"


class UnitType(str, Enum):
    module = "module"
    class_ = "class"  # "class" is reserved as a Python identifier; value below is the stored string
    function = "function"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class SectionType(str, Enum):
    overview = "overview"
    architecture = "architecture"
    tradeoffs = "tradeoffs"
    glossary = "glossary"
    deep_dive = "deep_dive"


class QuizStatus(str, Enum):
    generating = "generating"
    ready = "ready"
    failed = "failed"


class QuestionType(str, Enum):
    mcq = "mcq"
    fill_blank = "fill_blank"


class FillBlankMode(str, Enum):
    code = "code"
    concept = "concept"


class FeedbackMode(str, Enum):
    # Phase 5.5, #37: ui-spec.md §6.5's per-quiz "Show answers as I go" /
    # "Show results at the end" toggle — Phase 5 always graded and showed
    # feedback immediately (a deliberate scope cut, not an oversight).
    # end_of_quiz is the default, per the spec's own reasoning ("closer to
    # genuine self-assessment").
    immediate = "immediate"
    end_of_quiz = "end_of_quiz"


class AttemptStatus(str, Enum):
    in_progress = "in_progress"
    completed = "completed"


def _by_value(enum_cls: type[Enum]) -> SAEnum:
    # SQLAlchemy's Enum type persists a Python Enum's *name* by default. UnitType.class_'s
    # name ("class_") isn't the string we want stored — values_callable makes it persist
    # by .value ("class") instead, matching docs/design/original-project-plan.md's schema exactly.
    return SAEnum(enum_cls, values_callable=lambda obj: [e.value for e in obj])


# --- Auth (Phase 4, but modeled from Phase 0/1 so it's additive, not retrofitted — ADR-007) ---


class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class Session(SQLModel, table=True):
    """A logged-in session, looked up by hashing the opaque token the cookie
    carries (app/auth/session.py) — the DB never stores the raw token, same
    defense-in-depth reasoning as password hashing (ADR-007's self-implemented,
    DB-backed session design, not a signed cookie)."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())
    expires_at: datetime = Field(sa_column=_timestamptz_column())


# --- Core repo tracking ---


class Repo(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # nullable until Phase 4 auth lands; then required
    user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id", index=True)
    # no "local" — see ADR-008
    source_type: SourceType = Field(sa_column=Column(_by_value(SourceType), nullable=False))
    source_uri: str  # git URL, or original zip filename for reference
    display_name: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())
    # use_alter: Repo <-> AnalysisSnapshot is a circular FK pair (a snapshot belongs to
    # a repo, a repo points at its latest snapshot) — defer this constraint until both
    # tables exist so alembic/SQLAlchemy don't choke on the dependency cycle.
    latest_snapshot_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("analysissnapshot.id", use_alter=True, name="fk_repo_latest_snapshot_id"),
            nullable=True,
        ),
    )


class AnalysisSnapshot(SQLModel, table=True):
    """Full structural (+ later semantic) analysis of a repo at one point in time."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    repo_id: uuid.UUID = Field(foreign_key="repo.id", index=True)
    commit_hash: str | None = None  # null for zip uploads without embedded git history
    indexed_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())
    status: SnapshotStatus = Field(
        default=SnapshotStatus.pending, sa_column=Column(_by_value(SnapshotStatus), nullable=False)
    )
    file_count: int = 0
    language_summary: dict = Field(default_factory=dict, sa_column=Column(JSON))  # {"python": 42, ...}
    dependency_graph: dict = Field(default_factory=dict, sa_column=Column(JSON))  # LAYER A — {"nodes": [...], "edges": [...]}
    entry_points: list = Field(default_factory=list, sa_column=Column(JSON))  # [{"file": ..., "kind": ...}]


class CodeUnit(SQLModel, table=True):
    """One tree-sitter-extracted semantic unit: a module, class, or function. LAYER A — no LLM involved."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    file_path: str
    unit_type: UnitType = Field(sa_column=Column(_by_value(UnitType), nullable=False))
    name: str
    line_start: int
    line_end: int
    signature: str | None = None
    docstring: str | None = None


# --- LAYER B — LLM-inferred, always grounded in Layer A facts (ADR-006) ---


class ModuleSummary(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    file_path: str
    purpose: str
    role_in_system: str
    key_concepts: list = Field(default_factory=list, sa_column=Column(JSON))
    line_start: int
    line_end: int
    # A file with more units than MAX_UNITS_PER_CHUNK (semantics/chunking.py) is
    # summarized in several LLM calls, one row each, all sharing this file_path
    # and the same whole-module line_start/line_end. Without these, those rows
    # are indistinguishable and a consumer can't tell a whole-file summary from
    # one part of one (#14). 1-indexed; a normal single-chunk file is 1 of 1.
    chunk_index: int = 1
    chunk_count: int = 1
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class GeneratedArtifact(SQLModel, table=True):
    """One expensive LLM-generated intermediate, cached against its snapshot so
    a redelivery doesn't pay for it twice (#23).

    Study-guide assembly makes two calls — the architecture narrative (strongest
    model tier, largest prompt in the system) and the diagram labels. Both live
    only in memory until `persist_study_guide` commits, so a crash anywhere in
    between meant the next redelivery regenerated them from scratch and rebilled
    both. Persisting each as soon as it's computed, in its own short
    transaction, bounds that to at most one wasted call per artifact ever.

    Deliberately generic rather than two typed tables: `payload` is whatever the
    producing module needs to rehydrate its own result, and the alternative is a
    new table every time a generated intermediate becomes expensive enough to
    care about.
    """

    __table_args__ = (UniqueConstraint("snapshot_id", "kind", name="uq_generatedartifact_snapshot_kind"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    kind: str
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class Subsystem(SQLModel, table=True):
    """A named group of files that do one job together (#53). Membership and
    ordering are LAYER A — analysis/subsystems.py derives them from directory
    structure and the dependency graph; only `name`/`role` come from an LLM
    (ADR-006's "labels/grouping" allowance), which is why prompt_version/model
    are recorded here the same way they are for any other generated claim."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    key: str  # the directory prefix defining it, stable across re-indexes
    name: str
    role: str
    file_paths: list = Field(default_factory=list, sa_column=Column(JSON))
    depth: int  # edges from the nearest entry point; large sentinel if unreachable
    order: int  # outside-in position, so consumers can't disagree about it
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class PatternClaim(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    primary_pattern: str
    confidence: Confidence = Field(sa_column=Column(_by_value(Confidence), nullable=False))
    evidence: list = Field(default_factory=list, sa_column=Column(JSON))
    caveats: str | None = None
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class TradeoffCard(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    decision: str
    alternatives_considered: list = Field(default_factory=list, sa_column=Column(JSON))
    likely_reasoning: str
    tradeoff_cost: str
    confidence: Confidence = Field(sa_column=Column(_by_value(Confidence), nullable=False))
    evidence_refs: list = Field(default_factory=list, sa_column=Column(JSON))
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


# --- Study guide (Phase 3) — assembled from Layer A/B facts already collected above,
# no new facts invented here, only formatted and cited (see generation/study_guide_builder.py) ---


class StudyGuide(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    repo_id: uuid.UUID = Field(foreign_key="repo.id", index=True)
    snapshot_id: uuid.UUID = Field(foreign_key="analysissnapshot.id", index=True)
    version: int
    generated_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class Section(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    study_guide_id: uuid.UUID = Field(foreign_key="studyguide.id", index=True)
    section_type: SectionType = Field(sa_column=Column(_by_value(SectionType), nullable=False))
    title: str
    order: int
    content_md: str
    diagram_mermaid: str | None = None
    # only the architecture section makes a new LLM call (diagram node labels) — every
    # other section is assembled from already-generated, already-attributed Layer B rows,
    # so these stay null there rather than re-stating provenance that's already recorded
    # on the source ModuleSummary/PatternClaim/TradeoffCard row.
    prompt_version: str | None = None
    model: str | None = None


class Citation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    section_id: uuid.UUID = Field(foreign_key="section.id", index=True)
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str  # the sentence/claim in content_md this citation supports
    snippet_text: str  # actual source lines, captured now — source_dir is deleted right after (§8)


# --- Quizzes (Phase 5) — generated from already-persisted Section/Citation rows,
# not the raw repo checkout, which is long gone by the time a user asks for a quiz
# (ADR-008; see quizzing/generation.py's module docstring) ---


class Quiz(SQLModel, table=True):
    # A DB-level guarantee that at most one generation is ever in flight per
    # study guide — the app-level "check for an existing `generating` quiz
    # first" in generation.create_pending_quiz has a race window between two
    # concurrent POST /quizzes/{repo_id}/generate calls (a double-click, a
    # retry, two tabs) that both pass the check before either commits; this
    # partial unique index turns the loser's insert into a conflict instead
    # of a second paid generation job (found via the Phase 5 Codex review,
    # second pass).
    __table_args__ = (
        Index(
            "uq_quiz_generating_per_study_guide",
            "study_guide_id",
            unique=True,
            postgresql_where=text("status = 'generating'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    repo_id: uuid.UUID = Field(foreign_key="repo.id", index=True)
    study_guide_id: uuid.UUID = Field(foreign_key="studyguide.id", index=True)
    status: QuizStatus = Field(default=QuizStatus.generating, sa_column=Column(_by_value(QuizStatus), nullable=False))
    feedback_mode: FeedbackMode = Field(
        default=FeedbackMode.end_of_quiz, sa_column=Column(_by_value(FeedbackMode), nullable=False)
    )
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class Question(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    quiz_id: uuid.UUID = Field(foreign_key="quiz.id", index=True)
    question_type: QuestionType = Field(sa_column=Column(_by_value(QuestionType), nullable=False))
    order: int
    # mcq: the question text. fill_blank: the blanked_text, "___" marking the blank.
    prompt: str
    choices: list | None = Field(default=None, sa_column=Column(JSON))  # mcq only
    correct_index: int | None = None  # mcq only
    fill_blank_mode: FillBlankMode | None = Field(
        default=None, sa_column=Column(_by_value(FillBlankMode), nullable=True)
    )
    correct_answer: str | None = None  # fill_blank only
    acceptable_alternatives: list = Field(default_factory=list, sa_column=Column(JSON))  # fill_blank only
    explanation: str | None = None  # mcq only — why the correct choice is correct (§9.4); fill_blank has none
    # Singular, not a list like TradeoffCard.evidence_refs — each question is
    # grounded in exactly one source Citation (the seed it was generated
    # from), never synthesized across several the way a trade-off claim is.
    file_path: str
    line_start: int
    line_end: int
    # The subsystem this question's source file belongs to (#61). Denormalized
    # onto the Question rather than resolved through the citation at read time
    # because it is the *join key mastery aggregates on*, and the chain it would
    # otherwise be resolved through — Question -> Citation -> Section ->
    # StudyGuide -> AnalysisSnapshot -> Subsystem — is replaced wholesale by
    # every re-index. A subsystem key is stable across snapshots by
    # construction (analysis/subsystems.py), so copying it here is what lets
    # scores from before and after a re-index describe the same topic.
    #
    # Nullable on purpose: a snapshot indexed before subsystems existed has
    # none, and a file in no subsystem has no key. Aggregation buckets those as
    # "ungrouped" rather than dropping them.
    subsystem_key: str | None = None
    # The seed Citation's own id, not just its file_path/line range copied
    # above — a range alone can't be traced back to one specific Citation
    # row when the same range is cited by more than one Section (e.g. a
    # glossary entry and a deep-dive paragraph over the same lines), so
    # AttemptResults couldn't otherwise show a real, working citation back
    # to the study guide (found via the Phase 5 Codex review). Nullable
    # because a citation can in principle be deleted independently.
    source_citation_id: uuid.UUID | None = Field(default=None, foreign_key="citation.id", index=True)
    prompt_version: str
    model: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())


class Attempt(SQLModel, table=True):
    # Same DB-level guarantee as Quiz's partial index, for the same race:
    # POST /attempts' app-level "resume an existing in_progress attempt"
    # check (api/attempts.py) has a window between two concurrent calls for
    # the same (quiz, user) — React StrictMode's double mount-effect
    # invocation, a reload racing the first request, or a second tab — that
    # both pass the check before either commits (found via the Phase 5
    # Codex review, second pass).
    __table_args__ = (
        Index(
            "uq_attempt_in_progress_per_quiz_user",
            "quiz_id", "user_id",
            unique=True,
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    quiz_id: uuid.UUID = Field(foreign_key="quiz.id", index=True)
    # Required, unlike Repo.user_id — the original plan's Phase-1-era note
    # called for a nullable FK to keep auth "additive" before it existed.
    # Auth is mandatory now (Phase 4b, #28: every router already requires
    # get_current_user), so there's no pre-auth data to stay compatible with.
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    status: AttemptStatus = Field(
        default=AttemptStatus.in_progress, sa_column=Column(_by_value(AttemptStatus), nullable=False)
    )
    started_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    score: float | None = None  # fraction correct across all questions, set on completion


class AnswerSubmission(SQLModel, table=True):
    # A DB-level guarantee, not just app-level care in api/attempts.py's
    # row-locking (belt-and-suspenders against two concurrent PATCHes for
    # the same question racing past the same-session upsert check — found
    # via the Phase 5 Codex review).
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_answersubmission_attempt_question"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    attempt_id: uuid.UUID = Field(foreign_key="attempt.id", index=True)
    question_id: uuid.UUID = Field(foreign_key="question.id", index=True)
    selected_index: int | None = None  # mcq
    answer_text: str | None = None  # fill_blank
    # Graded immediately on submission (§10.1/§10.2), not deferred to
    # POST /attempts/{id}/complete — null only in the instant between insert
    # and the grading call within the same request.
    score: float | None = None
    feedback: str | None = None
    submitted_at: datetime = Field(default_factory=utcnow, sa_column=_timestamptz_column())
