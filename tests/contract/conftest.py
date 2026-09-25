from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Iterator

import pytest

from tableforge.composition import Repositories, repositories_for
from tableforge.infrastructure.sql import Database, SQLiteDatabase
from tests.s3_support import s3_store  # noqa: F401

POSTGRES_URL_ENV = "TABLEFORGE_TEST_POSTGRES_URL"
DEFAULT_POSTGRES_URL = "postgresql://tableforge:tableforge@127.0.0.1:55432/tableforge"


def _postgres_url() -> str | None:
    if os.environ.get("TABLEFORGE_DISABLE_POSTGRES_TESTS"):
        return None
    configured = os.environ.get(POSTGRES_URL_ENV)
    url = configured or DEFAULT_POSTGRES_URL
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(url, connect_timeout=3):
            return url
    except psycopg.OperationalError:
        if configured:
            raise
        return None


POSTGRES_URL = _postgres_url()
BACKENDS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "postgres",
        id="postgres",
        marks=[
            pytest.mark.postgres,
            pytest.mark.skipif(
                POSTGRES_URL is None,
                reason=(f"PostgreSQL not reachable; start it or set {POSTGRES_URL_ENV}"),
            ),
        ],
    ),
]

DatabaseFactory = Callable[[], Database]


@pytest.fixture(params=BACKENDS)
def database_factory(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[DatabaseFactory]:
    opened: list[Database] = []
    if request.param == "sqlite":
        path = tmp_path_factory.mktemp("sqlite") / "tableforge.db"

        def open_sqlite() -> Database:
            database = SQLiteDatabase(path)
            opened.append(database)
            return database

        yield open_sqlite
        return

    import psycopg

    from tableforge.infrastructure.sql.postgres import PostgresDatabase

    assert POSTGRES_URL is not None
    schema = f"test_{secrets.token_hex(6)}"
    with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')

    def open_postgres() -> Database:
        database = PostgresDatabase(POSTGRES_URL, schema=schema, max_size=8)
        opened.append(database)
        return database

    try:
        yield open_postgres
    finally:
        for database in opened:
            database.close()
        with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def database(database_factory: DatabaseFactory) -> Database:
    return database_factory()


@pytest.fixture
def repositories(database: Database) -> Repositories:
    return repositories_for(database)
