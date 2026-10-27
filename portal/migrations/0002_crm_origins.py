"""Add CRM aggregate provenance carried by existing SQLite databases."""
from __future__ import annotations


def up(connection, dialect: str, sqlite_schema: str) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    connection.execute(
        "ALTER TABLE crm_daily ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'rollup'"
    )
    connection.execute(
        "ALTER TABLE crm_city_daily ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'rollup'"
    )


def down(connection, dialect: str, sqlite_schema: str, *, force: bool = False) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    if not force:
        raise RuntimeError("Refusing to drop CRM provenance columns without force=True")
    connection.execute("ALTER TABLE crm_city_daily DROP COLUMN IF EXISTS origin")
    connection.execute("ALTER TABLE crm_daily DROP COLUMN IF EXISTS origin")
