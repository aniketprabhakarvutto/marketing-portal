#!/usr/bin/env python3
"""Copy the local SQLite portal database into an empty PostgreSQL schema.

The operation is deliberately one-way and non-destructive: SQLite is opened
read-only, PostgreSQL must have no application rows, and the source database
is never modified.  Re-run with ``--dry-run`` to inspect exact table counts.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

# Support both ``python3 scripts/backfill_postgres.py`` and module execution
# from a repository checkout.  The migration must not depend on a globally
# installed ``portal`` package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal.config import APP
from portal.models import TABLE_MODELS


DEFAULT_SOURCE = Path.home() / "Library" / "Application Support" / "VortexVeri" / "data" / "portal.db"
TABLES = tuple(model.table for model in TABLE_MODELS)


def quoted(identifier: str) -> str:
    if identifier not in TABLES:
        raise ValueError(f"Unknown portal table: {identifier}")
    return f'"{identifier}"'


def source_columns(source: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in source.execute(f"PRAGMA table_info({quoted(table)})")]


def source_counts(source: sqlite3.Connection) -> dict[str, int]:
    present = {
        row[0]
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        table: int(source.execute(f"SELECT count(*) FROM {quoted(table)}").fetchone()[0])
        if table in present else 0
        for table in TABLES
    }


def target_counts(target) -> dict[str, int]:
    with target.cursor() as cursor:
        return {
            table: int(cursor.execute(f"SELECT count(*) FROM {quoted(table)}").fetchone()[0])
            for table in TABLES
        }


def target_keys(target, table: str) -> set[object]:
    key = "id" if table != "portal_settings" else "key"
    with target.cursor() as cursor:
        return {row[0] for row in cursor.execute(f"SELECT \"{key}\" FROM {quoted(table)}")}


def backfill_database_url(database_url: str) -> str:
    """Use Supabase's transaction pooler for the short-lived bulk transfer.

    The portal itself keeps its session-pooler URL because it has long-lived
    connections.  A one-time backfill has the opposite shape, so port 6543
    avoids leaving a session pinned if the local process is interrupted.
    """
    parts = urlsplit(database_url)
    if parts.hostname and parts.hostname.endswith(".pooler.supabase.com") and parts.port == 5432:
        host = parts.netloc.rsplit(":", 1)[0]
        return urlunsplit((parts.scheme, host + ":6543", parts.path, parts.query, ""))
    return database_url


def copy_table(
    source: sqlite3.Connection,
    database_url: str,
    table: str,
    batch_size: int,
    *,
    existing_keys: set[object] | None = None,
) -> int:
    columns = source_columns(source, table)
    if not columns:
        return 0
    qtable = quoted(table)
    qcolumns = ", ".join(f'"{column}"' for column in columns)
    key = "id" if "id" in columns else "key"
    cursor = source.execute(f"SELECT {qcolumns} FROM {qtable} ORDER BY \"{key}\"")
    copied = len(existing_keys or ())
    key_index = columns.index(key)
    while rows := cursor.fetchmany(batch_size):
        if existing_keys:
            rows = [row for row in rows if row[key_index] not in existing_keys]
        if not rows:
            continue
        # Each batch gets its own committed transaction.  This prevents a
        # laptop sleep or network loss from losing a multi-minute transfer.
        with psycopg.connect(database_url, autocommit=True) as target:
            with target.cursor() as destination:
                with destination.copy(f"COPY {qtable} ({qcolumns}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(tuple(row))
        copied += len(rows)
        print(f"  {table}: {copied}", flush=True)
    if "id" in columns:
        with psycopg.connect(database_url, autocommit=True) as target:
            with target.cursor() as destination:
                destination.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM " + qtable + "), 1), true)",
                    (table,),
                )
    return copied


def run(source_path: Path, *, dry_run: bool, batch_size: int, resume: bool = False) -> dict[str, int]:
    if not APP.database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("PORTAL_DATABASE_URL or DATABASE_URL must point to PostgreSQL.")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source not found: {source_path}")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Refusing to copy a SQLite database that fails integrity_check.")
        expected = source_counts(source)
        if dry_run:
            return expected
        database_url = backfill_database_url(APP.database_url)
        with psycopg.connect(database_url) as target:
            existing = target_counts(target)
            overfull = {table: existing[table] for table in TABLES if existing[table] > expected[table]}
            if overfull:
                details = ", ".join(f"{table}={count}" for table, count in overfull.items())
                raise RuntimeError(f"PostgreSQL contains more rows than SQLite: {details}")
            occupied = {table: count for table, count in existing.items() if count}
            if occupied and not resume:
                details = ", ".join(f"{table}={count}" for table, count in occupied.items())
                raise RuntimeError(
                    "Refusing to overwrite a non-empty PostgreSQL target. "
                    "Use --resume only for a previously interrupted backfill: " + details
                )
            existing_keys = {
                table: target_keys(target, table)
                for table in TABLES
                if existing[table] and existing[table] < expected[table]
            }
            for table in TABLES:
                if existing[table] < expected[table]:
                    copy_table(source, database_url, table, batch_size, existing_keys=existing_keys.get(table))
        with psycopg.connect(database_url) as target:
            copied = target_counts(target)
        if copied != expected:
            mismatches = [f"{table}: source={expected[table]}, target={copied[table]}" for table in TABLES if expected[table] != copied[table]]
            raise RuntimeError("Backfill verification failed: " + "; ".join(mismatches))
        return copied
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="continue a previously interrupted backfill")
    parser.add_argument("--batch-size", type=int, default=1_000)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    counts = run(args.source, dry_run=args.dry_run, batch_size=args.batch_size, resume=args.resume)
    label = "Source counts" if args.dry_run else "Verified copied rows"
    print(label + ":")
    for table in TABLES:
        print(f"  {table}: {counts[table]}")


if __name__ == "__main__":
    main()
