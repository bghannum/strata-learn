"""See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern this file follows."""

from fastapi.testclient import TestClient

from app.audio.dependencies import (
    describe_backends,
    speech_status,
    transcription_status,
)
from app.config import settings
from app.main import app
from tests.conftest import register_test_user


def test_capabilities_requires_a_session() -> None:
    with TestClient(app) as client:
        assert client.get("/voice/capabilities").status_code == 401


def test_capabilities_are_both_off_by_default() -> None:
    # The state CI always runs in: no backend selected, so nothing renders a
    # mic button or a read-aloud control.
    with TestClient(app) as client:
        register_test_user(client)
        assert client.get("/voice/capabilities").json() == {"transcription": False, "speech": False}


def test_a_selected_backend_without_credentials_reports_off_and_why(monkeypatch) -> None:
    monkeypatch.setattr(settings, "transcription_backend", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)

    status = transcription_status()
    assert status.enabled is False
    assert status.backend == "openai"
    assert "OPENAI_API_KEY" in (status.reason or "")

    with TestClient(app) as client:
        register_test_user(client)
        # The endpoint says only *whether*, never *why* — the reason is for
        # the startup log, not for a caller.
        assert client.get("/voice/capabilities").json()["transcription"] is False


def test_openai_backend_with_a_key_reports_on(monkeypatch) -> None:
    # Constructing the provider never makes a network call, so this is safe
    # with a dummy key.
    monkeypatch.setattr(settings, "transcription_backend", "openai")
    monkeypatch.setattr(settings, "speech_backend", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")

    assert transcription_status().enabled is True
    assert speech_status().enabled is True
    with TestClient(app) as client:
        register_test_user(client)
        assert client.get("/voice/capabilities").json() == {"transcription": True, "speech": True}


def test_local_backend_without_the_voice_extra_reports_off_and_why(monkeypatch) -> None:
    # CI never installs the extra, so this is the branch it exercises.
    monkeypatch.setattr(settings, "speech_backend", "local")
    status = speech_status()
    assert status.enabled is False
    assert "voice" in (status.reason or "")


def test_startup_description_names_the_reason_per_capability(monkeypatch) -> None:
    monkeypatch.setattr(settings, "transcription_backend", None)
    monkeypatch.setattr(settings, "speech_backend", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)
    lines = describe_backends()
    assert lines[0].startswith("voice.transcription disabled")
    assert "TRANSCRIPTION_BACKEND" in lines[0]
    assert lines[1].startswith("voice.speech disabled")
    assert "OPENAI_API_KEY" in lines[1]
