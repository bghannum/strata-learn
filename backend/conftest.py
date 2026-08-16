"""Root-level pytest configuration: repoint the suite at its own database
*before* anything imports `app.db.session` (#99).

pytest loads this conftest ahead of `tests/conftest.py`, and `app.config` is
safe to import here (it only builds the Settings object). Mutating
`settings.database_url` before `app.db.session` is imported means the async
engine — created at that module's import time — binds to the test database,
as does every `async_session_factory()` the app and the fixtures use.

Nothing under `app/` knows the test database exists; the app always uses
`DATABASE_URL`. See `tests/testdb.py` for the derivation and safety check.
"""

import os

import pytest

from app.config import settings
from tests.testdb import ensure_test_database, resolve_test_database_url

APP_DATABASE_URL = settings.database_url
TEST_DATABASE_URL = resolve_test_database_url(APP_DATABASE_URL)

settings.database_url = TEST_DATABASE_URL
# Anything that re-reads the environment (e.g. an alembic subprocess spawned
# by a test) must land on the same database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def pytest_sessionstart(session: pytest.Session) -> None:
    ensure_test_database(APP_DATABASE_URL, TEST_DATABASE_URL)
