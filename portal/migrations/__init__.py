"""Versioned schema migrations for supported persistence backends."""
from __future__ import annotations

import importlib

from ..models import TABLE_MODELS


MIGRATIONS = ("0001_initial", "0002_crm_origins")


def _execute_script(connection, script: str) -> None:
    for statement in script.split(";"):
        statement = statement.strip()
        if statement:
            connection.execute(statement)


def migrate(connection, dialect: str, sqlite_schema: str) -> None:
    """Apply unapplied migrations inside the caller's transaction boundary."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations "
        "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {row[0] for row in connection.execute("SELECT version FROM public.schema_migrations").fetchall()}
    for version in MIGRATIONS:
        if version in applied:
            continue
        migration = importlib.import_module(f"{__name__}.{version}")
        migration.up(connection, dialect, sqlite_schema)
        connection.execute("INSERT INTO public.schema_migrations(version) VALUES(?)", (version,))


def rollback_latest(connection, dialect: str, sqlite_schema: str, *, force: bool = False) -> None:
    """Reverse the latest migration, refusing destructive rollback by default."""
    rows = connection.execute("SELECT version FROM public.schema_migrations ORDER BY version DESC LIMIT 1").fetchall()
    if not rows:
        return
    version = rows[0][0]
    migration = importlib.import_module(f"{__name__}.{version}")
    migration.down(connection, dialect, sqlite_schema, force=force)
    connection.execute("DELETE FROM public.schema_migrations WHERE version=?", (version,))


__all__ = ["MIGRATIONS", "TABLE_MODELS", "migrate", "rollback_latest"]
