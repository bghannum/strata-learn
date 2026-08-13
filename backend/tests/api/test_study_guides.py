import uuid

from fastapi.testclient import TestClient

from app.db.models import Citation, Section, SectionType, SourceType, StudyGuide
from app.db.session import async_session_factory
from app.main import app
from tests.conftest import register_test_user


def test_get_study_guide_404_for_unknown_id() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get(f"/study-guides/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_get_study_guide_returns_ordered_sections_and_citations(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
        )

        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.flush()

            section_2 = Section(
                study_guide_id=guide.id,
                section_type=SectionType.tradeoffs,
                title="Trade-offs",
                order=1,
                content_md="## Trade-offs",
            )
            section_1 = Section(
                study_guide_id=guide.id,
                section_type=SectionType.overview,
                title="Overview",
                order=0,
                content_md="## Overview",
            )
            session.add(section_1)
            session.add(section_2)
            await session.flush()

            session.add(
                Citation(
                    section_id=section_1.id,
                    file_path="app/main.py",
                    line_start=1,
                    line_end=2,
                    claim_excerpt="claim",
                    snippet_text="import app",
                )
            )
            await session.commit()
            guide_id = guide.id

        response = client.get(f"/study-guides/{guide_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(guide_id)
    assert body["version"] == 1
    # Returned in `order`, not insertion order (section_2 was added first above).
    assert [s["section_type"] for s in body["sections"]] == ["overview", "tradeoffs"]
    assert len(body["sections"][0]["citations"]) == 1
    assert body["sections"][0]["citations"][0]["file_path"] == "app/main.py"
    assert body["sections"][1]["citations"] == []


async def test_get_study_guide_404_for_another_users_guide(pending_repo_factory) -> None:
    with TestClient(app) as client_a:
        user_a = register_test_user(client_a)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user_a["id"])
        )
        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.commit()
            guide_id = guide.id

    with TestClient(app) as client_b:
        register_test_user(client_b)
        response = client_b.get(f"/study-guides/{guide_id}")

    assert response.status_code == 404
