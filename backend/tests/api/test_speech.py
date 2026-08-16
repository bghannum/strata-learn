"""The two read-aloud routes, run against FakeSpeechProvider via
app.dependency_overrides. Nothing here touches a network or a key.

See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.audio.dependencies import get_speech_provider
from app.audio.errors import AudioProviderError
from app.audio.fakes import FakeSpeechProvider
from app.audio.speech_response import TRUNCATED_HEADER
from app.config import settings
from app.db.models import (
    FeedbackMode,
    FillBlankMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
    SourceType,
    StudyGuide,
)
from app.db.session import async_session_factory
from app.main import app
from tests.conftest import login_as_new_user, register_test_user


@pytest.fixture
def fake_speaker() -> Iterator[FakeSpeechProvider]:
    fake = FakeSpeechProvider([[b"ID3", b"frame-1", b"frame-2"]])
    app.dependency_overrides[get_speech_provider] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_speech_provider, None)


async def _guide_with_section(pending_repo_factory, user_id: uuid.UUID, content_md: str) -> tuple[uuid.UUID, uuid.UUID]:
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git", user_id=user_id)
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        section = Section(
            study_guide_id=guide.id, section_type=SectionType.architecture, title="Architecture", order=0,
            content_md=content_md,
        )
        session.add(section)
        await session.commit()
        return guide.id, section.id


# --- study-guide sections ---


async def test_streams_the_provider_chunks_with_its_media_type(pending_repo_factory, fake_speaker) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id, section_id = await _guide_with_section(
            pending_repo_factory, uuid.UUID(user["id"]), "## Architecture\n\nThe **worker** runs `run_layer_b`."
        )
        response = client.get(f"/study-guides/{guide_id}/sections/{section_id}/speech")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers[TRUNCATED_HEADER] == "0"
    # All chunks, in order — the route re-yields rather than collapsing.
    assert response.content == b"ID3frame-1frame-2"
    # The provider was handed *speakable* text, not raw Markdown.
    assert fake_speaker.calls[0].text == "Architecture The worker runs run_layer_b."


async def test_marks_truncation_in_a_header(pending_repo_factory, fake_speaker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "speech_max_chars", 30)
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id, section_id = await _guide_with_section(
            pending_repo_factory, uuid.UUID(user["id"]), "First sentence here. Second sentence goes past the cap."
        )
        response = client.get(f"/study-guides/{guide_id}/sections/{section_id}/speech")

    assert response.status_code == 200
    assert response.headers[TRUNCATED_HEADER] == "1"
    assert fake_speaker.calls[0].text == "First sentence here."


async def test_404_for_another_users_guide_and_for_a_foreign_section(pending_repo_factory, fake_speaker) -> None:
    with TestClient(app) as owner:
        user = register_test_user(owner)
        guide_id, section_id = await _guide_with_section(pending_repo_factory, uuid.UUID(user["id"]), "Text.")
        other_guide_id, _other_section = await _guide_with_section(pending_repo_factory, uuid.UUID(user["id"]), "T.")
        # A real section, but not this guide's — must not be reachable
        # through a guide it doesn't belong to.
        assert owner.get(f"/study-guides/{other_guide_id}/sections/{section_id}/speech").status_code == 404

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/study-guides/{guide_id}/sections/{section_id}/speech").status_code == 404
    assert fake_speaker.calls == []


async def test_503_when_no_speech_provider_is_configured(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id, section_id = await _guide_with_section(pending_repo_factory, uuid.UUID(user["id"]), "Text.")
        response = client.get(f"/study-guides/{guide_id}/sections/{section_id}/speech")
    assert response.status_code == 503
    assert "backend" not in response.json()["detail"].lower()


async def test_provider_refusal_is_a_real_503_not_a_truncated_200(pending_repo_factory) -> None:
    # The whole point of awaiting the first chunk inside the handler.
    class Refusing:
        media_type = "audio/mpeg"
        model = "refusing"

        def synthesize(self, text, *, voice=None):
            async def gen():
                raise AudioProviderError("simulated outage")
                yield b""  # pragma: no cover — makes this an async generator

            return gen()

    app.dependency_overrides[get_speech_provider] = lambda: Refusing()
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            guide_id, section_id = await _guide_with_section(pending_repo_factory, uuid.UUID(user["id"]), "Text.")
            response = client.get(f"/study-guides/{guide_id}/sections/{section_id}/speech")
    finally:
        app.dependency_overrides.pop(get_speech_provider, None)

    assert response.status_code == 503
    assert response.json()["detail"]


async def test_422_when_the_section_has_nothing_speakable(pending_repo_factory, fake_speaker) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id, section_id = await _guide_with_section(
            pending_repo_factory, uuid.UUID(user["id"]), "```mermaid\ngraph TD; A-->B;\n```"
        )
        response = client.get(f"/study-guides/{guide_id}/sections/{section_id}/speech")
    # A diagram-only section reads as "Code example omitted." — that *is*
    # speakable, and honestly says what happened.
    assert response.status_code == 200
    assert fake_speaker.calls[0].text == "Code example omitted."


# --- quiz feedback ---


async def _graded_fill_blank(
    pending_repo_factory, user_id: uuid.UUID, feedback_mode: FeedbackMode
) -> tuple[uuid.UUID, uuid.UUID]:
    """A quiz with one fill_blank question. Returns (quiz_id, question_id)."""
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git", user_id=user_id)
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready, feedback_mode=feedback_mode)
        session.add(quiz)
        await session.flush()
        question = Question(
            quiz_id=quiz.id, question_type=QuestionType.fill_blank, order=0, prompt="uses ___",
            fill_blank_mode=FillBlankMode.code, correct_answer="arq", acceptable_alternatives=[],
            file_path="w.py", line_start=1, line_end=2, prompt_version="v1", model="fake",
        )
        session.add(question)
        await session.commit()
        return quiz.id, question.id


async def test_speaks_feedback_in_immediate_mode(pending_repo_factory, fake_speaker) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, question_id = await _graded_fill_blank(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        graded = client.patch(f"/attempts/{attempt['id']}/answers/{question_id}", json={"answer_text": "arq"}).json()
        assert graded["feedback"]

        response = client.get(f"/attempts/{attempt['id']}/answers/{question_id}/feedback-speech")

    assert response.status_code == 200
    assert response.content == b"ID3frame-1frame-2"
    assert fake_speaker.calls[0].text == graded["feedback"]


async def test_end_of_quiz_feedback_is_not_a_side_channel(pending_repo_factory, fake_speaker) -> None:
    # The JSON withholds feedback until completion in end_of_quiz mode; the
    # audio must too, or the mode is defeated. Same leak class as #37.
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, question_id = await _graded_fill_blank(
            pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.end_of_quiz
        )
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{question_id}", json={"answer_text": "arq"})

        before = client.get(f"/attempts/{attempt['id']}/answers/{question_id}/feedback-speech")
        assert client.post(f"/attempts/{attempt['id']}/complete").status_code == 200
        after = client.get(f"/attempts/{attempt['id']}/answers/{question_id}/feedback-speech")

    assert before.status_code == 404
    assert after.status_code == 200
    # Exactly one paid call — the withheld request never reached the provider.
    assert len(fake_speaker.calls) == 1


async def test_404_for_an_unanswered_question_and_another_users_attempt(pending_repo_factory, fake_speaker) -> None:
    with TestClient(app) as owner:
        user = register_test_user(owner)
        quiz_id, question_id = await _graded_fill_blank(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
        attempt = owner.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        assert owner.get(f"/attempts/{attempt['id']}/answers/{question_id}/feedback-speech").status_code == 404

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/attempts/{attempt['id']}/answers/{question_id}/feedback-speech").status_code == 404
    assert fake_speaker.calls == []
