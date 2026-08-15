import uuid

from fastapi.testclient import TestClient

from app.db.models import AnalysisSnapshot, Citation, Repo, Section, SectionType, SourceType, StudyGuide
from app.db.session import async_session_factory
from app.main import app
from tests.conftest import login_as_new_user, register_test_user


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

    # A second account can't come from POST /auth/register (only the first
    # account ever created is allowed — ADR-007's single-tenant design), so
    # this uses login_as_new_user's DB-level bypass instead.
    with TestClient(app) as client_b:
        await login_as_new_user(client_b)
        response = client_b.get(f"/study-guides/{guide_id}")

    assert response.status_code == 404


# --- #66: Markdown export ---


async def _guide_for_export(pending_repo_factory, user_id: uuid.UUID) -> uuid.UUID:
    repo_id, snapshot_id = await pending_repo_factory(
        SourceType.git_url, "https://example.com/repo.git", user_id=user_id
    )
    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        snapshot.commit_hash = "a" * 40
        session.add(snapshot)

        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=3)
        session.add(guide)
        await session.flush()

        section = Section(
            study_guide_id=guide.id,
            section_type=SectionType.architecture,
            title="Architecture",
            order=0,
            content_md="The app hands slow work to a worker.",
            diagram_mermaid="flowchart TD\n    n0[\"API\"]",
        )
        session.add(section)
        await session.flush()
        session.add(
            Citation(
                section_id=section.id,
                file_path="app/worker/tasks.py",
                line_start=1,
                line_end=40,
                claim_excerpt="indexing runs in a worker",
                snippet_text="class WorkerSettings:",
            )
        )
        await session.commit()
        return guide.id


async def test_export_markdown_includes_content_diagram_and_citations(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id = await _guide_for_export(pending_repo_factory, uuid.UUID(user["id"]))
        response = client.get(f"/study-guides/{guide_id}/export.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text
    assert "## Architecture" in body
    assert "The app hands slow work to a worker." in body
    # a Mermaid fence, not an image — the diagram is text by design (ADR-004)
    assert "```mermaid" in body
    assert 'n0["API"]' in body
    assert "`app/worker/tasks.py:1-40` — indexing runs in a worker" in body
    # provenance a reader can act on: which version, and which commit
    assert "Version 3" in body
    assert "a" * 40 in body


async def test_export_filename_is_derived_and_sanitized(pending_repo_factory) -> None:
    # display_name is user-supplied (a git URL, or an uploaded zip's own
    # filename) and goes straight into a Content-Disposition header.
    with TestClient(app) as client:
        user = register_test_user(client)
        guide_id = await _guide_for_export(pending_repo_factory, uuid.UUID(user["id"]))
        async with async_session_factory() as session:
            guide = await session.get(StudyGuide, guide_id)
            repo = await session.get(Repo, guide.repo_id)
            repo.display_name = 'evil"; rm -rf /; name.git'
            session.add(repo)
            await session.commit()

        response = client.get(f"/study-guides/{guide_id}/export.md")

    disposition = response.headers["content-disposition"]
    assert disposition == 'attachment; filename="evil-rm-rf-name-git-v3.md"'
    assert '"' not in disposition[len('attachment; filename="') : -1]
    assert "/" not in disposition[len('attachment; filename="') : -1]


async def test_export_404_for_another_users_guide(pending_repo_factory) -> None:
    with TestClient(app) as owner:
        user = register_test_user(owner)
        guide_id = await _guide_for_export(pending_repo_factory, uuid.UUID(user["id"]))

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/study-guides/{guide_id}/export.md").status_code == 404


def test_export_404_for_unknown_id() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/study-guides/00000000-0000-0000-0000-000000000000/export.md")
    assert response.status_code == 404
