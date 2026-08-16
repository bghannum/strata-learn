"""POST /attempts/{id}/answers/{qid}/transcription — the spoken-answer
route. Every test runs the route against FakeTranscriptionProvider via
app.dependency_overrides (the same seam get_llm_provider uses), so nothing
here touches a network or a key.

See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.audio.dependencies import get_transcription_provider
from app.audio.fakes import FakeTranscriptionProvider
from app.audio.providers import TranscriptionResult
from app.config import settings
from app.db.models import (
    Attempt,
    AttemptStatus,
    Citation,
    FillBlankMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
    SourceType,
    StudyGuide,
    Subsystem,
)
from app.db.session import async_session_factory
from app.main import app
from tests.conftest import login_as_new_user, register_test_user

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _result(text: str = "the worker uses arq") -> TranscriptionResult:
    return TranscriptionResult(text=text, model="fake", language="en", duration_seconds=3.0)


@pytest.fixture
def fake_transcriber() -> Iterator[FakeTranscriptionProvider]:
    fake = FakeTranscriptionProvider([_result()])
    app.dependency_overrides[get_transcription_provider] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_transcription_provider, None)


async def _quiz(pending_repo_factory, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A quiz with one mcq and one fill_blank whose seed citation and
    subsystem exist — the shape the vocabulary lookup walks."""
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git", user_id=user_id)
    async with async_session_factory() as session:
        session.add(
            Subsystem(
                snapshot_id=snapshot_id, key="app/worker", name="Background worker", role="r",
                file_paths=["app/worker/pipeline.py"], depth=0, order=0, prompt_version="v1", model="fake",
            )
        )
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        section = Section(study_guide_id=guide.id, section_type=SectionType.overview, title="O", order=0, content_md="x")
        session.add(section)
        await session.flush()
        citation = Citation(
            section_id=section.id, file_path="app/worker/pipeline.py", line_start=1, line_end=3,
            claim_excerpt="c", snippet_text="async def index_repo(ctx, snapshot_id):\n    await run_layer_b(llm)",
        )
        session.add(citation)
        await session.flush()
        quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
        session.add(quiz)
        await session.flush()
        mcq = Question(
            quiz_id=quiz.id, question_type=QuestionType.mcq, order=0, prompt="q", choices=["a", "b"],
            correct_index=0, file_path="a.py", line_start=1, line_end=2, prompt_version="v1", model="fake",
        )
        fill_blank = Question(
            quiz_id=quiz.id, question_type=QuestionType.fill_blank, order=1, prompt="the job is ___",
            fill_blank_mode=FillBlankMode.code, correct_answer="index_repo", acceptable_alternatives=["indexRepo"],
            file_path="app/worker/pipeline.py", line_start=1, line_end=3, subsystem_key="app/worker",
            source_citation_id=citation.id, prompt_version="v1", model="fake",
        )
        session.add(mcq)
        session.add(fill_blank)
        await session.commit()
        return quiz.id, mcq.id, fill_blank.id


def _post(client: TestClient, attempt_id: str, question_id: uuid.UUID, data: bytes = WEBM, content_type="audio/webm"):
    return client.post(
        f"/attempts/{attempt_id}/answers/{question_id}/transcription",
        files={"file": ("answer.webm", data, content_type)},
    )


async def test_returns_an_editable_transcript_and_writes_nothing(pending_repo_factory, fake_transcriber) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        response = _post(client, attempt["id"], fb)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "the worker uses arq"
    assert "duration_ms" in body
    # Booleans-only capability discipline: no model or backend name in the body.
    assert "model" not in body
    # Nothing graded, nothing persisted — the learner confirms first.
    async with async_session_factory() as session:
        results = client.get(f"/attempts/{attempt['id']}").json()
        assert all(q["answered"] is False for q in results["questions"])
        assert (await session.get(Attempt, uuid.UUID(attempt["id"]))).status == AttemptStatus.in_progress


async def test_vocabulary_reaches_the_provider_but_the_answer_key_never_does(
    pending_repo_factory, fake_transcriber
) -> None:
    # The load-bearing property, checked at the route level: identifiers from
    # the seed snippet, the file path, and the subsystem name are handed to
    # the provider; correct_answer and its alternatives are not — even though
    # correct_answer literally appears in the snippet.
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        assert _post(client, attempt["id"], fb).status_code == 200

    sent = fake_transcriber.calls[0].vocabulary
    assert "run_layer_b" in sent
    assert "pipeline.py" in sent
    assert "Background worker" in sent
    assert "index_repo" not in sent
    assert "indexRepo" not in sent
    assert not any("index_repo" in term.lower() for term in sent)


async def test_clip_carries_the_sniffed_format_not_the_declared_one(pending_repo_factory, fake_transcriber) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        # Declared as mp4, actually webm bytes.
        assert _post(client, attempt["id"], fb, content_type="audio/mp4").status_code == 200

    clip = fake_transcriber.calls[0].clip
    assert clip.content_type == "audio/webm"
    assert clip.filename == "answer.webm"


async def test_404_for_another_users_attempt(pending_repo_factory, fake_transcriber) -> None:
    with TestClient(app) as owner:
        user = register_test_user(owner)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = owner.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert _post(other, attempt["id"], fb).status_code == 404
    assert fake_transcriber.calls == []


async def test_409_once_the_attempt_is_completed(pending_repo_factory, fake_transcriber) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq}", json={"selected_index": 0})
        assert client.post(f"/attempts/{attempt['id']}/complete").status_code == 200

        assert _post(client, attempt["id"], fb).status_code == 409
    assert fake_transcriber.calls == []


async def test_422_for_an_mcq_question(pending_repo_factory, fake_transcriber) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq, _fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        response = _post(client, attempt["id"], mcq)
    assert response.status_code == 422
    assert fake_transcriber.calls == []


async def test_422_for_an_unsupported_container_and_for_oversize(
    pending_repo_factory, fake_transcriber, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "audio_upload_max_bytes", 256)
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        bad = _post(client, attempt["id"], fb, data=b"%PDF-1.4 definitely not audio")
        big = _post(client, attempt["id"], fb, data=WEBM * 100)

    assert bad.status_code == 422
    assert "unsupported audio format" in bad.json()["detail"]
    assert big.status_code == 422
    assert "exceeds" in big.json()["detail"]
    assert fake_transcriber.calls == []


async def test_503_when_no_transcription_provider_is_configured(pending_repo_factory) -> None:
    # No override: the real dependency, with the default (off) config.
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        response = _post(client, attempt["id"], fb)
    assert response.status_code == 503
    # Generic — never says which backend, or that none is selected.
    assert "openai" not in response.json()["detail"].lower()
    assert "backend" not in response.json()["detail"].lower()


async def test_429_past_the_hourly_limit_and_the_refused_call_is_not_billed(
    pending_repo_factory, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "voice_calls_per_hour", 2)
    fake = FakeTranscriptionProvider([_result(), _result(), _result()])
    app.dependency_overrides[get_transcription_provider] = lambda: fake
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            statuses = [_post(client, attempt["id"], fb).status_code for _ in range(3)]
    finally:
        app.dependency_overrides.pop(get_transcription_provider, None)

    assert statuses == [200, 200, 429]
    # The third call was refused before reaching the provider.
    assert len(fake.calls) == 2


async def test_503_when_the_provider_refuses_and_nothing_is_persisted(pending_repo_factory) -> None:
    from app.audio.errors import AudioProviderError

    class Refusing:
        async def transcribe(self, clip, *, vocabulary=()):
            raise AudioProviderError("simulated provider outage")

    app.dependency_overrides[get_transcription_provider] = lambda: Refusing()
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, _mcq, fb = await _quiz(pending_repo_factory, uuid.UUID(user["id"]))
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            response = _post(client, attempt["id"], fb)
            results = client.get(f"/attempts/{attempt['id']}").json()
    finally:
        app.dependency_overrides.pop(get_transcription_provider, None)

    assert response.status_code == 503
    assert all(q["answered"] is False for q in results["questions"])
