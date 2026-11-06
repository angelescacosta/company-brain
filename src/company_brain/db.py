"""Database connections and migrations (T5).

Each database has its own role: `brain_app` for `company_brain` and
`brain_test` for `company_brain_test`. Tables live in the `brain` schema,
which the role owns; pgvector lives in `public`, created by the owner's setup.
"""

from __future__ import annotations

from importlib import resources

import psycopg

from company_brain import isolation
from company_brain.keychain import get_password
from company_brain.settings import Settings

HOST = "127.0.0.1"
PORT = 5432
ROLES = {isolation.REAL_DATABASE: "brain_app", isolation.TEST_DATABASE: "brain_test"}


def connect(settings: Settings | None = None) -> psycopg.Connection:
    settings = settings or Settings.from_env()
    role = ROLES[settings.database]
    password = get_password(f"{settings.keychain_prefix}-db", role)
    return psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=settings.database,
        user=role,
        password=password,
        options="-c search_path=brain,public",
    )


def migration_files() -> list[tuple[str, str]]:
    folder = resources.files("company_brain") / "migrations"
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    return [(f.name.removesuffix(".sql"), f.read_text(encoding="utf-8")) for f in files]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply missing migrations, each in its own transaction; return those applied."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS brain.schema_migrations (
               version text PRIMARY KEY,
               applied_at timestamptz NOT NULL DEFAULT now())"""
    )
    conn.commit()
    done = {row[0] for row in conn.execute("SELECT version FROM brain.schema_migrations")}
    conn.commit()
    applied = []
    for version, sql in migration_files():
        if version in done:
            continue
        with conn.transaction():
            conn.execute(sql.encode())
            conn.execute("INSERT INTO brain.schema_migrations (version) VALUES (%s)", (version,))
        applied.append(version)
    return applied
