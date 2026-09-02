from contextlib import contextmanager
from typing import Generator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import settings


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool

    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            open=True,
            kwargs={"row_factory": dict_row},
        )

    return _pool


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    pool = get_pool()

    with pool.connection() as connection:
        yield connection


def close_pool() -> None:
    global _pool

    if _pool is not None:
        _pool.close()
        _pool = None


def database_is_ready() -> bool:
    try:
        with get_connection() as connection:
            result = connection.execute("SELECT 1 AS ready").fetchone()
            return result is not None and result["ready"] == 1
    except Exception:
        return False