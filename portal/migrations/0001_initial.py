"""Initial PostgreSQL persistence schema."""
from __future__ import annotations

from ..models import TABLE_MODELS, postgres_schema


def up(connection, dialect: str, sqlite_schema: str) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    for statement in postgres_schema(sqlite_schema).split(";"):
        if statement.strip():
            connection.execute(statement)


def down(connection, dialect: str, sqlite_schema: str, *, force: bool = False) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    if not force:
        populated = []
        for model in TABLE_MODELS:
            row = connection.execute(f"SELECT EXISTS (SELECT 1 FROM {model.table} LIMIT 1)").fetchone()
            if row and row[0]:
                populated.append(model.table)
        if populated:
            raise RuntimeError(
                "Refusing destructive rollback while tables contain data: " + ", ".join(populated)
            )
    for model in reversed(TABLE_MODELS):
        connection.execute(f"DROP TABLE IF EXISTS {model.table} CASCADE")
