import os
from pathlib import Path

import psycopg


PROJECT_ROOT = Path(__file__).resolve().parent.parent

MIGRATION_FILES = [
    PROJECT_ROOT / "app" / "schema.sql",
    *sorted((PROJECT_ROOT / "migrations").glob("*.sql")),
]


def run_migrations() -> None:
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        for migration_path in MIGRATION_FILES:
            migration_name = str(
                migration_path.relative_to(PROJECT_ROOT)
            )

            already_applied = connection.execute(
                """
                SELECT 1
                FROM schema_migrations
                WHERE filename = %s
                """,
                (migration_name,),
            ).fetchone()

            if already_applied:
                print(
                    f"Skipping already applied migration: "
                    f"{migration_name}"
                )
                continue

            print(f"Applying migration: {migration_name}")

            migration_sql = migration_path.read_text(
                encoding="utf-8"
            )

            connection.execute(migration_sql)

            connection.execute(
                """
                INSERT INTO schema_migrations (filename)
                VALUES (%s)
                """,
                (migration_name,),
            )

            print(f"Applied migration: {migration_name}")

    print("Database migrations completed successfully")


if __name__ == "__main__":
    run_migrations()