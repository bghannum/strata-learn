"""The test suite's own database (#99).

`tests/conftest.py`'s autouse `clean_db` fixture wipes every app table before
and after each test. Pointed at the same database the running Compose app
uses — which is exactly what `.env.example`'s `DATABASE_URL` does — a local
`pytest -q` silently deleted the developer's account, repos, study guides and
quizzes. The suite therefore runs against a *separate* database, derived from
the app URL by default (`strata_learn` → `strata_learn_test`) or given
explicitly via `TEST_DATABASE_URL`, and refuses to start if the two resolve to
the same place.

Everything here is pure or plain-psycopg2 so `backend/conftest.py` can use it
before any `app.*` module that builds the async engine is imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TEST_SUFFIX = "_test"

BACKEND_DIR = Path(__file__).resolve().parents[1]


def derive_test_database_url(app_url: str) -> str:
    """`.../strata_learn` → `.../strata_learn_test`, keeping scheme, host,
    credentials, and any query string exactly as they were."""
    parts = urlsplit(app_url)
    db_name = parts.path.lstrip("/")
    if not db_name:
        raise ValueError(
            f"DATABASE_URL has no database name to derive a test database from: {app_url!r}"
        )
    if db_name.endswith(TEST_SUFFIX):
        return app_url
    return urlunsplit(parts._replace(path=f"/{db_name}{TEST_SUFFIX}"))


def same_database(url_a: str, url_b: str) -> bool:
    """True when the two URLs name the same database on the same server —
    the condition the suite must refuse. Ignores driver (`+asyncpg` vs
    `+psycopg2`) and query string, since those don't change *which* database
    gets wiped."""

    def key(url: str) -> tuple[str, int | None, str, str]:
        parts = urlsplit(url)
        return (
            (parts.hostname or "localhost").lower(),
            parts.port or 5432,
            parts.username or "",
            parts.path.lstrip("/"),
        )

    return key(url_a) == key(url_b)


def resolve_test_database_url(
    app_url: str, environ: dict[str, str] | None = None
) -> str:
    """The URL the suite will use: `TEST_DATABASE_URL` if set, else derived
    from the app URL. Raises if it would collide with the app database."""
    env = os.environ if environ is None else environ
    test_url = env.get("TEST_DATABASE_URL") or derive_test_database_url(app_url)
    if same_database(app_url, test_url):
        raise RuntimeError(
            "Refusing to run tests against the application database.\n"
            f"  DATABASE_URL      = {app_url}\n"
            f"  TEST_DATABASE_URL = {test_url}\n"
            "The suite wipes every table before and after each test. Unset "
            "TEST_DATABASE_URL to use the derived '<db>_test' database, or point "
            "it at a database that is not the app's."
        )
    return test_url


def _psycopg2_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("postgresql+psycopg2", "postgresql")


def ensure_test_database(app_url: str, test_url: str) -> None:
    """Create the test database if it doesn't exist (connecting through the
    app database, which Compose/CI already provision), then bring it to the
    current Alembic head. Runs once per pytest session."""
    import psycopg2
    from psycopg2 import sql

    test_db_name = urlsplit(test_url).path.lstrip("/")
    conn = psycopg2.connect(_psycopg2_url(app_url))
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_db_name,))
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db_name))
                )
    finally:
        conn.close()

    # alembic/env.py reads settings.database_url, and Settings prefers the
    # process environment over .env, so a subprocess with DATABASE_URL
    # overridden migrates the test database and nothing else.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": test_url},
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed against {test_url}:\n{result.stdout}\n{result.stderr}"
        )
