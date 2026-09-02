import os
from psycopg_pool import ConnectionPool

# A connection POOL (not a single connection) lets FastAPI handle many
# requests at once -- each request borrows a connection, uses it,
# returns it, instead of every request fighting over one connection.

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost/payments",  # matches macOS Homebrew default
)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=True)
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
