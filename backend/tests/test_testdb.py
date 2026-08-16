"""Guards for #99: the suite must never run against the app database."""

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.session import async_session_factory, engine
from tests.testdb import (
    derive_test_database_url,
    resolve_test_database_url,
    same_database,
)


def test_derive_swaps_only_the_database_name() -> None:
    assert (
        derive_test_database_url(
            "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn"
        )
        == "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn_test"
    )
    assert (
        derive_test_database_url(
            "postgresql+asyncpg://u:p@postgres:5432/strata_learn?ssl=require"
        )
        == "postgresql+asyncpg://u:p@postgres:5432/strata_learn_test?ssl=require"
    )


def test_derive_is_idempotent_and_rejects_missing_name() -> None:
    url = "postgresql+asyncpg://strata:strata@localhost/strata_learn_test"
    assert derive_test_database_url(url) == url
    with pytest.raises(ValueError):
        derive_test_database_url("postgresql+asyncpg://strata:strata@localhost/")


def test_same_database_ignores_driver_and_query() -> None:
    assert same_database(
        "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn",
        "postgresql+psycopg2://strata:strata@localhost/strata_learn?sslmode=disable",
    )
    assert not same_database(
        "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn",
        "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn_test",
    )


def test_resolve_prefers_explicit_env_and_refuses_collision() -> None:
    app_url = "postgresql+asyncpg://strata:strata@localhost:5432/strata_learn"
    other = "postgresql+asyncpg://strata:strata@localhost:5432/elsewhere"
    assert resolve_test_database_url(app_url, {"TEST_DATABASE_URL": other}) == other
    assert resolve_test_database_url(app_url, {}) == derive_test_database_url(app_url)
    with pytest.raises(
        RuntimeError, match="Refusing to run tests against the application database"
    ):
        resolve_test_database_url(app_url, {"TEST_DATABASE_URL": app_url})


async def test_engine_is_bound_to_a_test_database() -> None:
    """The live check: the settings object and the engine both point at a
    `_test` database, and the connection agrees."""
    assert settings.database_url.rstrip("/").endswith("_test")
    assert str(engine.url).endswith("_test")
    async with async_session_factory() as session:
        current = (await session.exec(text("SELECT current_database()"))).scalar_one()
    assert current.endswith("_test")
