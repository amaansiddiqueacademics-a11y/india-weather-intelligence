"""Database helpers: connection, database creation, DDL application."""
from __future__ import annotations

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import config

DDL_FILES = ["00_schemas.sql", "20_star.sql", "30_snow.sql"]


def _hint(dsn: str, exc: psycopg.OperationalError) -> psycopg.OperationalError:
    """Same error, plus what to do: most first-run failures are 'server not started' or 'wrong user/password'."""
    shown = " ".join(f"{k}={v}" for k, v in conninfo_to_dict(dsn).items() if k != "password")
    return psycopg.OperationalError(f"{exc}\nCould not connect to PostgreSQL ({shown}). Start the server, or point the project at yours "
                                    "with WX_DSN and WX_ADMIN_DSN, e.g. WX_DSN=\"host=localhost dbname=wx user=postgres password=...\" (see README).")


def connect(autocommit: bool = False) -> psycopg.Connection:
    try:
        return psycopg.connect(config.DSN, autocommit=autocommit, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise _hint(config.DSN, exc) from exc


def ensure_database() -> None:
    """Create the warehouse database named in WX_DSN (default `wx`) if it does not exist (admin connection)."""
    name = conninfo_to_dict(config.DSN).get("dbname", "wx")
    try:
        admin = psycopg.connect(config.ADMIN_DSN, autocommit=True)
    except psycopg.OperationalError as exc:
        raise _hint(config.ADMIN_DSN, exc) from exc
    with admin as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if not exists:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def apply_ddl(conn: psycopg.Connection) -> None:
    """Apply all DDL files in order. Statements are idempotent (IF NOT EXISTS)."""
    for name in DDL_FILES:
        conn.execute((config.SQL_DIR / name).read_text(encoding="utf-8"))
    conn.commit()
