"""Create or check the PostgreSQL cluster, roles and databases (T5). Idempotent.

Run by Claude as the owner: `uv run python scripts/setup_database.py`.
The cluster is Postgres.app's default folder for PostgreSQL 18. The owner's
account is the superuser and connects only through the local socket (peer);
each role connects only to its own database over 127.0.0.1 with a password
kept in the Keychain.
"""

from __future__ import annotations

import getpass
import subprocess
import time
from pathlib import Path

import psycopg
from psycopg import sql

from company_brain import isolation
from company_brain.db import HOST, PORT, ROLES, connect, migrate
from company_brain.keychain import ensure_password
from company_brain.settings import Settings

BIN = Path("/Applications/Postgres.app/Contents/Versions/18/bin")
DATA = Path.home() / "Library" / "Application Support" / "Postgres" / "var-18"
SOCKET_DIR = "/tmp"  # noqa: S108 - Postgres.app's default socket folder
SUPERUSER = getpass.getuser()
PREFIX = {
    isolation.REAL_DATABASE: isolation.REAL_KEYCHAIN_PREFIX,
    isolation.TEST_DATABASE: isolation.TEST_KEYCHAIN_PREFIX,
}

CONF = f"""# Written by scripts/setup_database.py (T5, T4). Do not edit by hand.
listen_addresses = '{HOST}'
port = {PORT}
unix_socket_directories = '{SOCKET_DIR}'
password_encryption = 'scram-sha-256'
"""

HBA = f"""# Written by scripts/setup_database.py (T5). Anything not listed is refused.
local   all                 {SUPERUSER}   peer
host    {isolation.REAL_DATABASE}       brain_app    {HOST}/32   scram-sha-256
host    {isolation.TEST_DATABASE}  brain_test   {HOST}/32   scram-sha-256
"""


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603


def ensure_cluster() -> None:
    if not (DATA / "PG_VERSION").exists():
        DATA.parent.mkdir(parents=True, exist_ok=True)
        result = run(
            str(BIN / "initdb"),
            "-D",
            str(DATA),
            "-U",
            SUPERUSER,
            "-E",
            "UTF8",
            "--locale=en_US.UTF-8",
            "--auth-local=peer",
            "--auth-host=scram-sha-256",
        )
        if result.returncode != 0:
            raise SystemExit(f"initdb failed: {result.stderr}")
        print("cluster created")
    conf = DATA / "postgresql.conf"
    include = "include_if_exists = 'company_brain.conf'"
    if include not in conf.read_text():
        conf.write_text(conf.read_text() + f"\n{include}\n")
    (DATA / "company_brain.conf").write_text(CONF)
    (DATA / "pg_hba.conf").write_text(HBA)


def ensure_running() -> None:
    if run(str(BIN / "pg_ctl"), "-D", str(DATA), "status").returncode == 0:
        run(str(BIN / "pg_ctl"), "-D", str(DATA), "reload")
        return
    subprocess.run(  # noqa: S603
        [str(BIN / "pg_ctl"), "-D", str(DATA), "-l", str(DATA / "server.log"), "-w", "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    time.sleep(0.5)


def admin(dbname: str = "postgres") -> psycopg.Connection:
    return psycopg.connect(
        host=SOCKET_DIR, port=PORT, dbname=dbname, user=SUPERUSER, autocommit=True
    )


def ensure_roles_and_databases() -> None:
    with admin() as conn:
        for database, role in ROLES.items():
            password = ensure_password(f"{PREFIX[database]}-db", role)
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
            conn.execute(
                sql.SQL(
                    "{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS NOINHERIT PASSWORD {}"
                ).format(verb, sql.Identifier(role), sql.Literal(password))
            )
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                print(f"database {database} created")
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            # No CREATE on the database: it would let the role add trusted extensions.
            conn.execute(
                sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path = brain, public").format(
                    sql.Identifier(role), sql.Identifier(database)
                )
            )
        for database in ("postgres", "template1"):
            conn.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database)
                )
            )
    for database, role in ROLES.items():
        with admin(database) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")
            conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS brain AUTHORIZATION {}").format(
                    sql.Identifier(role)
                )
            )


def migrate_all() -> None:
    for database in ROLES:
        settings = Settings(
            root=None,
            data_dir=Path.home() / "CompanyBrainData",
            exports_dir=Path.home() / "CompanyBrainExports",
            database=database,
            keychain_prefix=PREFIX[database],
            openwebui_port=isolation.REAL_PORTS["openwebui"],
            backend_port=isolation.REAL_PORTS["backend"],
        )
        with connect(settings) as conn:
            print(database, "migrations applied:", migrate(conn) or "none")


def main() -> None:
    ensure_cluster()
    ensure_running()
    ensure_roles_and_databases()
    migrate_all()
    print("database setup complete")


if __name__ == "__main__":
    main()
