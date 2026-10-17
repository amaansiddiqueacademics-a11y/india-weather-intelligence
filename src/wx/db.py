"""Database helpers: connection, database creation, DDL application."""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from . import config

DDL_FILES = ["00_schemas.sql", "20_star.sql", "30_snow.sql"]


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(config.DSN, autocommit=autocommit, row_factory=dict_row)


def ensure_database() -> None:
    """Create the `wx` database if it does not exist (admin connection)."""
    with psycopg.connect(config.ADMIN_DSN, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = 'wx'").fetchone()
        if not exists:
            c.execute("CREATE DATABASE wx")


def apply_ddl(conn: psycopg.Connection) -> None:
    """Apply all DDL files in order. Statements are idempotent (IF NOT EXISTS)."""
    for name in DDL_FILES:
        conn.execute((config.SQL_DIR / name).read_text(encoding="utf-8"))
    conn.commit()


def query(conn: psycopg.Connection, sql: str, params: tuple | dict | None = None) -> list[dict]:
    return conn.execute(sql, params).fetchall()
