"""T5.AC2: each role can use only its own database and cannot create databases,
roles or extensions. Checked as `brain_test`; `brain_app` is checked through the
catalogue only, so the test never connects to the real database.
"""

import psycopg
import pytest

from company_brain.db import HOST, PORT, connect
from company_brain.keychain import get_password
from company_brain.settings import Settings

pytestmark = pytest.mark.integration


def test_roles_have_no_admin_rights() -> None:
    with connect() as conn:
        rows = conn.execute(
            """SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                      rolbypassrls, rolcanlogin
               FROM pg_roles WHERE rolname IN ('brain_app', 'brain_test') ORDER BY 1"""
        ).fetchall()
    assert rows == [
        ("brain_app", False, False, False, False, False, True),
        ("brain_test", False, False, False, False, False, True),
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE DATABASE cb_should_fail",
        "CREATE ROLE cb_should_fail",
        "CREATE EXTENSION IF NOT EXISTS hstore",
    ],
)
def test_cannot_create(statement: str) -> None:
    with connect() as conn:
        conn.autocommit = True
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


@pytest.mark.parametrize("database", ["postgres", "template1"])
def test_cannot_connect_to_other_databases(database: str) -> None:
    settings = Settings.from_env()
    password = get_password(f"{settings.keychain_prefix}-db", "brain_test")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(
            host=HOST, port=PORT, dbname=database, user="brain_test", password=password
        ).close()


def test_connect_rights_only_for_own_role() -> None:
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.rolname, d.datname, has_database_privilege(r.oid, d.oid, 'CONNECT')
               FROM pg_roles r CROSS JOIN pg_database d
               WHERE r.rolname IN ('brain_app', 'brain_test')
                 AND d.datname IN ('company_brain', 'company_brain_test', 'postgres', 'template1')
               ORDER BY 1, 2"""
        ).fetchall()
    allowed = {(role, db) for role, db, ok in rows if ok}
    assert allowed == {("brain_app", "company_brain"), ("brain_test", "company_brain_test")}
