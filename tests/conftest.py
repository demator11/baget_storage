import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from baget_storage.infrastructure.postgres.database import create_engine, create_session_factory


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = os.environ.get("BAGET_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set BAGET_TEST_DATABASE_URL to run PostgreSQL integration tests")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+asyncpg" or not (parsed.database or "").endswith("_test"):
        pytest.fail("Integration tests require a postgresql+asyncpg database ending in _test")
    return value


async def clear_test_database(engine: AsyncEngine, expected_name: str) -> None:
    async with engine.begin() as connection:
        actual_name = await connection.scalar(text("SELECT current_database()"))
        if actual_name != expected_name or not actual_name.endswith("_test"):
            pytest.fail("Refusing cleanup outside the explicitly configured test database")
        migrated = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if migrated is None:
            pytest.fail("Apply Alembic migrations to the test database before integration tests")
        names = await connection.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                "ORDER BY tablename"
            )
        )
        quote = connection.dialect.identifier_preparer.quote_identifier
        tables = ", ".join(f"public.{quote(name)}" for name in names)
        if tables:
            await connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def session_factory(
    test_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(test_database_url)
    database_name = make_url(test_database_url).database
    if database_name is None:
        pytest.fail("A named test database is required")
    try:
        await clear_test_database(engine, database_name)
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
