"""SQLite warehouse — schema, connections and idempotent upserts.

Five tables, one grain each. The point is that a Google Ads row and a Meta Ads
row are the *same shape*, so blended reporting is a GROUP BY rather than a join
across vendors. Dates are stored as ISO text, which sorts and compares
correctly in SQLite and stays readable if you open the file in any SQL client.
"""
from __future__ import annotations

import logging
import json
import os
import re
import sqlite3
import threading
from typing import Any, Iterable, Sequence

from .config import APP
from .models import TABLE_MODELS
from .migrations import migrate as apply_migrations

SCHEMA = """
CREATE TABLE IF NOT EXISTS paid_metrics (
    id               INTEGER PRIMARY KEY,
    date             TEXT    NOT NULL,
    source           TEXT    NOT NULL,          -- google_ads | meta_ads
    account_id       TEXT    NOT NULL DEFAULT '',
    account_name     TEXT    NOT NULL DEFAULT '',
    campaign_id      TEXT    NOT NULL DEFAULT '',
    campaign_name    TEXT    NOT NULL DEFAULT '',
    adset_id         TEXT    NOT NULL DEFAULT '',
    adset_name       TEXT    NOT NULL DEFAULT '',
    campaign_status  TEXT    NOT NULL DEFAULT '',
    daily_budget     REAL,
    bidding_strategy TEXT    NOT NULL DEFAULT '',
    channel          TEXT    NOT NULL DEFAULT '',
    impressions      INTEGER NOT NULL DEFAULT 0,
    reach            INTEGER,
    frequency        REAL,
    clicks           INTEGER NOT NULL DEFAULT 0,
    spend            REAL    NOT NULL DEFAULT 0,
    conversions      REAL    NOT NULL DEFAULT 0,
    all_conversions  REAL,
    conversion_value REAL    NOT NULL DEFAULT 0,
    impression_share REAL,
    budget_lost_impression_share REAL,
    rank_lost_impression_share REAL,
    video_views      INTEGER,
    video_completion_rate REAL,
    currency         TEXT    NOT NULL DEFAULT 'INR',
    connection_id    INTEGER NOT NULL DEFAULT 0,
    ingested_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, source, date, account_id, campaign_id, adset_id)
);
CREATE INDEX IF NOT EXISTS ix_paid_date   ON paid_metrics (date);
CREATE INDEX IF NOT EXISTS ix_paid_source ON paid_metrics (date, source);

CREATE TABLE IF NOT EXISTS web_metrics (
    id               INTEGER PRIMARY KEY,
    date             TEXT    NOT NULL,
    property_id      TEXT    NOT NULL DEFAULT '',
    channel_group    TEXT    NOT NULL DEFAULT '',
    session_source   TEXT    NOT NULL DEFAULT '',
    session_medium   TEXT    NOT NULL DEFAULT '',
    session_campaign TEXT    NOT NULL DEFAULT '',
    sessions         INTEGER NOT NULL DEFAULT 0,
    total_users      INTEGER NOT NULL DEFAULT 0,
    new_users        INTEGER NOT NULL DEFAULT 0,
    engaged_sessions INTEGER NOT NULL DEFAULT 0,
    conversions      REAL    NOT NULL DEFAULT 0,
    connection_id    INTEGER NOT NULL DEFAULT 0,
    ingested_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, property_id, channel_group, session_source,
            session_medium, session_campaign)
);
CREATE INDEX IF NOT EXISTS ix_web_date ON web_metrics (date);

CREATE TABLE IF NOT EXISTS search_metrics (
    id          INTEGER PRIMARY KEY,
    date        TEXT    NOT NULL,
    site_url    TEXT    NOT NULL DEFAULT '',
    query       TEXT    NOT NULL DEFAULT '',
    page        TEXT    NOT NULL DEFAULT '',
    country     TEXT    NOT NULL DEFAULT '',
    device      TEXT    NOT NULL DEFAULT '',
    clicks      INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    position    REAL    NOT NULL DEFAULT 0,
    connection_id INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, site_url, query, page, device)
);
CREATE INDEX IF NOT EXISTS ix_search_date ON search_metrics (date);

CREATE TABLE IF NOT EXISTS ahrefs_metrics (
    id                   INTEGER PRIMARY KEY,
    date                 TEXT    NOT NULL,
    target               TEXT    NOT NULL DEFAULT '',
    country              TEXT    NOT NULL DEFAULT '',
    domain_rating        REAL,
    ahrefs_rank          INTEGER,
    organic_keywords     INTEGER NOT NULL DEFAULT 0,
    top3_keywords        INTEGER NOT NULL DEFAULT 0,
    organic_traffic      INTEGER NOT NULL DEFAULT 0,
    organic_traffic_value REAL   NOT NULL DEFAULT 0,
    live_backlinks       INTEGER NOT NULL DEFAULT 0,
    live_refdomains      INTEGER NOT NULL DEFAULT 0,
    all_time_backlinks   INTEGER NOT NULL DEFAULT 0,
    all_time_refdomains  INTEGER NOT NULL DEFAULT 0,
    connection_id        INTEGER NOT NULL DEFAULT 0,
    ingested_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, target, country)
);
CREATE INDEX IF NOT EXISTS ix_ahrefs_date ON ahrefs_metrics (date);

CREATE TABLE IF NOT EXISTS crm_leads (
    id              INTEGER PRIMARY KEY,
    external_id     TEXT    NOT NULL,
    created_date    TEXT    NOT NULL,
    utm_source      TEXT    NOT NULL DEFAULT '',
    utm_medium      TEXT    NOT NULL DEFAULT '',
    utm_campaign    TEXT    NOT NULL DEFAULT '',
    channel         TEXT    NOT NULL DEFAULT '',
    lead_type       TEXT    NOT NULL DEFAULT '',
    city            TEXT    NOT NULL DEFAULT '',
    store           TEXT    NOT NULL DEFAULT '',
    stage           TEXT    NOT NULL DEFAULT '',
    is_contacted    INTEGER NOT NULL DEFAULT 0,
    is_qualified    INTEGER NOT NULL DEFAULT 0,
    is_visit_booked INTEGER NOT NULL DEFAULT 0,
    is_visit_done   INTEGER NOT NULL DEFAULT 0,
    is_sale         INTEGER NOT NULL DEFAULT 0,
    revenue         REAL    NOT NULL DEFAULT 0,
    connection_id   INTEGER NOT NULL DEFAULT 0,
    ingested_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_crm_date    ON crm_leads (created_date);
CREATE INDEX IF NOT EXISTS ix_crm_channel ON crm_leads (created_date, channel);

-- Reporting layer for CRM. A busy pipeline can hold hundreds of thousands of
-- leads a quarter, so the dashboard reads pre-aggregated daily rows rather than
-- re-scanning every lead on each page load. Fed either by rolling up crm_leads
-- after a sync, or by importing an aggregate extract directly.
CREATE TABLE IF NOT EXISTS crm_daily (
    id          INTEGER PRIMARY KEY,
    date        TEXT    NOT NULL,
    channel     TEXT    NOT NULL DEFAULT '',   -- normalised, joins to paid_metrics.source
    source      TEXT    NOT NULL DEFAULT '',   -- raw source as the CRM records it
    lead_type   TEXT    NOT NULL DEFAULT '',   -- buy (demand) | sell (supply)
    leads       INTEGER NOT NULL DEFAULT 0,
    contacted   INTEGER NOT NULL DEFAULT 0,
    qualified   INTEGER NOT NULL DEFAULT 0,
    booked      INTEGER NOT NULL DEFAULT 0,    -- visit booked / inspection planned
    completed   INTEGER NOT NULL DEFAULT 0,    -- visit done / inspection done
    won         INTEGER NOT NULL DEFAULT 0,    -- sales done / procured
    revenue     REAL    NOT NULL DEFAULT 0,
    origin      TEXT    NOT NULL DEFAULT 'rollup',
    connection_id INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, channel, source, lead_type)
);
CREATE INDEX IF NOT EXISTS ix_crmd_date ON crm_daily (date);

CREATE TABLE IF NOT EXISTS crm_city_daily (
    id          INTEGER PRIMARY KEY,
    date        TEXT    NOT NULL,
    city        TEXT    NOT NULL DEFAULT '',
    lead_type   TEXT    NOT NULL DEFAULT '',
    leads       INTEGER NOT NULL DEFAULT 0,
    won         INTEGER NOT NULL DEFAULT 0,
    origin      TEXT    NOT NULL DEFAULT 'rollup',
    connection_id INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, city, lead_type)
);
CREATE INDEX IF NOT EXISTS ix_crmc_date ON crm_city_daily (date);

CREATE TABLE IF NOT EXISTS crm_campaign_daily (
    id            INTEGER PRIMARY KEY,
    date          TEXT    NOT NULL,
    channel       TEXT    NOT NULL DEFAULT '',
    campaign_id   TEXT    NOT NULL DEFAULT '',
    campaign_name TEXT    NOT NULL DEFAULT '',
    lead_type     TEXT    NOT NULL DEFAULT '',
    leads         INTEGER NOT NULL DEFAULT 0,
    won           INTEGER NOT NULL DEFAULT 0,
    connection_id INTEGER NOT NULL DEFAULT 0,
    ingested_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (connection_id, date, channel, campaign_id, campaign_name, lead_type)
);
CREATE INDEX IF NOT EXISTS ix_crm_campaign_date ON crm_campaign_daily (date);
CREATE INDEX IF NOT EXISTS ix_crm_campaign_match
    ON crm_campaign_daily (date, channel, campaign_id, campaign_name);

-- A saved account on some platform. Credentials live here rather than in .env
-- so accounts can be added from the browser, and so one portal can hold many
-- accounts across many platforms without editing a file.
CREATE TABLE IF NOT EXISTS connections (
    id         INTEGER PRIMARY KEY,
    provider   TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    config     TEXT    NOT NULL DEFAULT '{}',   -- JSON, non-secret settings
    secrets    TEXT    NOT NULL DEFAULT '{}',   -- JSON, tokens and keys
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (provider, name)
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY,
    connector    TEXT NOT NULL,
    connection_id INTEGER NOT NULL DEFAULT 0,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',  -- running|ok|empty|error|skipped
    rows_written INTEGER NOT NULL DEFAULT 0,
    window_start TEXT,
    window_end   TEXT,
    message      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sync_connector ON sync_runs (connector, id DESC);

CREATE TABLE IF NOT EXISTS portal_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portal_users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('admin','analyst','viewer')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT NOT NULL DEFAULT '',
    preferences_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS access_invitations (
    id              INTEGER PRIMARY KEY,
    email           TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'viewer' CHECK(role='viewer'),
    token_hash      TEXT NOT NULL UNIQUE,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    accepted_at     TEXT,
    accepted_user_id INTEGER REFERENCES portal_users(id) ON DELETE SET NULL,
    revoked_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_access_invites_email ON access_invitations(email, expires_at);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    csrf_token  TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES portal_users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_user ON auth_sessions(user_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL DEFAULT 'local-admin',
    action      TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT '',
    object_id   TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_audit_time ON audit_log(occurred_at DESC);

CREATE TABLE IF NOT EXISTS metric_definitions (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    formula     TEXT NOT NULL DEFAULT '',
    format      TEXT NOT NULL DEFAULT 'number',
    owner       TEXT NOT NULL DEFAULT 'Marketing',
    enabled     INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transformation_rules (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    field       TEXT NOT NULL,
    match_type  TEXT NOT NULL DEFAULT 'contains' CHECK(match_type IN ('equals','contains','regex')),
    pattern     TEXT NOT NULL,
    output      TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS saved_explorer_views (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    definition  TEXT NOT NULL DEFAULT '{}',
    owner       TEXT NOT NULL DEFAULT 'local-admin',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dashboards (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    layout      TEXT NOT NULL DEFAULT '[]',
    owner       TEXT NOT NULL DEFAULT 'local-admin',
    shared      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_destinations (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK(kind IN ('download','folder','webhook','email','google_sheets','warehouse')),
    config      TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'ready',
    last_error  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    job_type    TEXT NOT NULL CHECK(job_type IN ('sync','report','alert','quality','snapshot','webhook','backup')),
    schedule    TEXT NOT NULL DEFAULT 'daily',
    run_at      TEXT NOT NULL DEFAULT '09:00',
    definition  TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_runs (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    message     TEXT NOT NULL DEFAULT '',
    artifact    TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(job_id) REFERENCES scheduled_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS report_annotations (
    id          INTEGER PRIMARY KEY,
    report_date TEXT NOT NULL,
    title       TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    owner       TEXT NOT NULL DEFAULT 'local-admin',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Columns that make up each table's natural key, used for conflict resolution.
CONFLICT_KEYS = {
    "paid_metrics": ("connection_id", "source", "date", "account_id", "campaign_id", "adset_id"),
    "web_metrics": ("connection_id", "date", "property_id", "channel_group", "session_source",
                    "session_medium", "session_campaign"),
    "search_metrics": ("connection_id", "date", "site_url", "query", "page", "device"),
    "ahrefs_metrics": ("connection_id", "date", "target", "country"),
    "crm_leads": ("connection_id", "external_id"),
    "crm_daily": ("connection_id", "date", "channel", "source", "lead_type"),
    "crm_city_daily": ("connection_id", "date", "city", "lead_type"),
    "crm_campaign_daily": ("connection_id", "date", "channel", "campaign_id", "campaign_name", "lead_type"),
}

ALL_TABLES = tuple(model.table for model in TABLE_MODELS)

log = logging.getLogger("portal.db")

_local = threading.local()
_write_lock = threading.Lock()
_audit_context = threading.local()


def using_postgres() -> bool:
    """Whether the process was configured with a PostgreSQL connection URL."""
    return APP.database_url.startswith(("postgresql://", "postgres://"))


def _postgres_sql(sql: str) -> str:
    """Translate the small SQLite compatibility surface used by the app."""
    sql = sql.replace("BEGIN IMMEDIATE", "BEGIN")
    # Timestamp columns are deliberately TEXT in the portable schema.  SQLite
    # emits UTC ``YYYY-MM-DD HH:MM:SS`` strings for datetime('now'); preserve
    # that type and ordering on PostgreSQL instead of comparing TEXT to a
    # timestamptz value, which aborts session lookups.
    utc_format = "'YYYY-MM-DD HH24:MI:SS'"
    sql = re.sub(
        r"datetime\('now'\s*,\s*'([^']+)'\)",
        lambda match: (
            "TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + "
            f"INTERVAL '{match.group(1)}', {utc_format})"
        ),
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"datetime\('now'\s*,\s*\?\)",
        "TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + (?)::interval, " + utc_format + ")",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"datetime\('now'\)",
        "TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')",
        sql,
        flags=re.IGNORECASE,
    )
    # The application uses qmark parameters to keep SQLite development simple.
    # Queries contain no literal question marks, so this is safe and preserves
    # one shared query layer for PostgreSQL's pyformat driver.
    return sql.replace("?", "%s")


class _PostgresCursor:
    """Minimal sqlite cursor compatibility for the existing query layer."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None

    def fetchone(self):
        row = self._cursor.fetchone()
        return _row_compat(row)

    def fetchall(self):
        return [_row_compat(row) for row in self._cursor.fetchall()]


def _row_compat(row):
    """Give PostgreSQL dict rows SQLite's numeric-index access too."""
    if row is None or not isinstance(row, dict):
        return row

    class Row(dict):
        def __getitem__(self, key):
            return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)

    return Row(row)


class _PostgresConnection:
    """Adapter that keeps the current SQLite-shaped repository API stable."""

    def __init__(self, connection):
        self._connection = connection

    @property
    def closed(self) -> bool:
        return bool(self._connection.closed)

    def execute(self, sql: str, params: Sequence[Any] = ()):
        try:
            cursor = self._connection.execute(_postgres_sql(sql), params)
        except Exception:
            # PostgreSQL aborts a transaction after any statement error.
            # SQLite does not, so reset the transaction before the shared
            # query layer handles another request or scheduler iteration.
            try:
                self._connection.rollback()
            except Exception:
                self._connection.close()
            raise
        wrapped = _PostgresCursor(cursor)
        # psycopg does not provide sqlite's lastrowid.  Only identity-backed
        # portal tables may call LASTVAL(). Calling it after inserts such as
        # schema_migrations aborts the PostgreSQL transaction when no sequence
        # was advanced, which would roll back a whole migration at commit.
        insert = re.match(r"\s*INSERT(?:\s+OR\s+REPLACE)?\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.I)
        if insert and insert.group(1) in set(ALL_TABLES) - {"portal_settings"}:
            try:
                identity = self._connection.execute("SELECT LASTVAL() AS id").fetchone()
                wrapped.lastrowid = identity["id"] if isinstance(identity, dict) else identity[0]
            except Exception:  # No sequence was advanced; callers do not need an id here.
                pass
        return wrapped

    def executemany(self, sql: str, params_seq):
        cursor = self._connection.cursor()
        cursor.executemany(_postgres_sql(sql), params_seq)
        return _PostgresCursor(cursor)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        self.rollback() if exc_type else self.commit()
        return False


def set_audit_actor(actor: str | None) -> None:
    _audit_context.actor = actor


def audit_actor() -> str | None:
    return getattr(_audit_context, "actor", None)


def audit(action: str, object_type: str = "", object_id: object = "", detail: dict | None = None,
          actor: str | None = None) -> None:
    actor = actor or getattr(_audit_context, "actor", None) or "system"
    execute("INSERT INTO audit_log(actor,action,object_type,object_id,detail) VALUES(?,?,?,?,?)",
            (actor, action, object_type, str(object_id or ""), json.dumps(detail or {})))


PROVENANCE_TABLES = ("paid_metrics", "web_metrics", "search_metrics", "ahrefs_metrics",
                     "crm_leads", "crm_daily", "crm_city_daily", "crm_campaign_daily")


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    for table in PROVENANCE_TABLES + ("sync_runs",):
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if cols and "connection_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN connection_id INTEGER NOT NULL DEFAULT 0")
    paid_columns = {
        "campaign_status": "TEXT NOT NULL DEFAULT ''",
        "daily_budget": "REAL",
        "bidding_strategy": "TEXT NOT NULL DEFAULT ''",
        "reach": "INTEGER",
        "frequency": "REAL",
        "all_conversions": "REAL",
        "impression_share": "REAL",
        "budget_lost_impression_share": "REAL",
        "rank_lost_impression_share": "REAL",
        "video_views": "INTEGER",
        "video_completion_rate": "REAL",
    }
    existing = {r[1] for r in conn.execute("PRAGMA table_info(paid_metrics)")}
    for name, definition in paid_columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paid_metrics ADD COLUMN {name} {definition}")
    user_columns = {r[1] for r in conn.execute("PRAGMA table_info(portal_users)")}
    if "password_hash" not in user_columns:
        conn.execute("ALTER TABLE portal_users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
    if "preferences_json" not in user_columns:
        conn.execute("ALTER TABLE portal_users ADD COLUMN preferences_json TEXT NOT NULL DEFAULT '{}'")
    _migrate_connection_scoped_keys(conn)
    _migrate_backup_job_type(conn)


def _migrate_backup_job_type(conn: sqlite3.Connection) -> None:
    """Older databases rejected the backup type offered by the job form."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'").fetchone()
    if not row or "'backup'" in (row[0] or ""):
        return
    # A copy is required before rebuilding a table that contains job history.
    conn.commit()
    backup_path = f"{APP.db_path}.before-backup-job-migration"
    backup = sqlite3.connect(backup_path)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        log.warning("could not restrict permissions on migration backup %s", backup_path)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE scheduled_jobs_new (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL CHECK(job_type IN ('sync','report','alert','quality','snapshot','webhook','backup')),
            schedule TEXT NOT NULL DEFAULT 'daily',
            run_at TEXT NOT NULL DEFAULT '09:00',
            definition TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            next_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT INTO scheduled_jobs_new SELECT * FROM scheduled_jobs")
        conn.execute("DROP TABLE scheduled_jobs")
        conn.execute("ALTER TABLE scheduled_jobs_new RENAME TO scheduled_jobs")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Job migration failed its foreign key check")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _migrate_connection_scoped_keys(conn: sqlite3.Connection) -> None:
    """Rebuild older fact tables whose natural key was shared by all accounts."""
    for table, desired in CONFLICT_KEYS.items():
        unique_sets = []
        for index in conn.execute(f"PRAGMA index_list({table})"):
            if index[2]:
                unique_sets.append(tuple(r[2] for r in conn.execute(f"PRAGMA index_info({index[1]})")))
        if tuple(desired) in unique_sets:
            continue
        source = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not source or not source[0]:
            continue
        old = f"__old_{table}"
        indexes = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        conn.execute(f"ALTER TABLE {table} RENAME TO {old}")
        create_sql = re.sub(rf"^(CREATE TABLE\s+)(?:IF NOT EXISTS\s+)?{re.escape(table)}",
                            rf"\1{table}", source[0], count=1, flags=re.I)
        create_sql = re.sub(r",?\s*UNIQUE\s*\([^)]*\)", "", create_sql, flags=re.I)
        create_sql = re.sub(r"\bUNIQUE\b", "", create_sql, flags=re.I)
        pos = create_sql.rfind(")")
        create_sql = create_sql[:pos].rstrip().rstrip(",") + ",\n    UNIQUE (" + ", ".join(desired) + ")\n" + create_sql[pos:]
        conn.execute(create_sql)
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({old})")]
        names = ",".join(columns)
        conn.execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM {old}")
        conn.execute(f"DROP TABLE {old}")
        for sql in indexes:
            conn.execute(sql)


def connect():
    """One connection per thread for SQLite development or PostgreSQL production."""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(conn, "closed", False):
        _local.conn = None
        conn = None
    if conn is None:
        if using_postgres():
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL is configured but the psycopg package is missing. "
                    "Install the project's requirements before starting the portal."
                ) from exc
            conn = _PostgresConnection(psycopg.connect(APP.database_url, row_factory=dict_row))
            _local.conn = conn
            return conn
        conn = sqlite3.connect(APP.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        # WAL is faster, but it needs shared memory and real file locking, which
        # network shares and cloud-synced folders (an iCloud-backed Documents
        # among them) do not provide. Fall back rather than fail to start.
        try:
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
        except sqlite3.Error:
            log.debug("WAL unavailable on this filesystem; using the default journal")
        _secure_database_files()
        _local.conn = conn
    return conn


def close_connection() -> None:
    """Release this thread's connection — called when a request thread ends."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db() -> None:
    try:
        with _write_lock:
            conn = connect()
            if using_postgres():
                apply_migrations(conn, "postgresql", SCHEMA)
                conn.commit()
                return
            conn.executescript(SCHEMA)
            _migrate(conn)
            conn.commit()
            _secure_database_files()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            f"Could not open the database at {APP.db_path} ({exc}).\n"
            "Some folders — network shares, and cloud-synced folders such as an "
            "iCloud-backed Documents — cannot host a SQLite file. Point the "
            "database somewhere local by setting PORTAL_DB_PATH in .env, "
            "for example:\n"
            "    PORTAL_DB_PATH=~/Library/Application Support/marketing-portal/portal.db"
        ) from None


def query(sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def scalar(sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
    row = connect().execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with _write_lock:
        conn = connect()
        cur = conn.execute(sql, params)
        conn.commit()
    return cur.rowcount


def backup_database(destination: str) -> None:
    """Create and integrity-check a consistent SQLite backup while online."""
    if using_postgres():
        raise RuntimeError(
            "SQLite file backups are unavailable for PostgreSQL. Use pg_dump or the managed database backup service."
        )
    target = sqlite3.connect(destination)
    try:
        with _write_lock:
            connect().backup(target)
        target.commit()
    finally:
        target.close()
    # A successful SQLite backup call is not enough to prove the artifact can
    # be restored. Check the closed file independently before reporting success.
    check = sqlite3.connect(destination)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(
                f"Database backup failed integrity check: {result[0] if result else 'no result'}"
            )
    except Exception:
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise
    finally:
        check.close()
    try:
        os.chmod(destination, 0o600)
    except OSError:
        log.warning("could not restrict permissions on database backup %s", destination)


def _secure_database_files() -> None:
    """Restrict the database and SQLite sidecars that may contain credentials."""
    for path in (APP.db_path, APP.db_path + "-wal", APP.db_path + "-shm", APP.db_path + "-journal"):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def upsert(table: str, rows: Iterable[dict]) -> int:
    """Insert rows, overwriting anything already at the same natural key.

    Re-running a sync for a date range must never double-count, so every fact
    table has a UNIQUE key and we upsert on it. That is what makes the rolling
    refresh window safe.
    """
    rows = list(rows)
    if not rows:
        return 0
    keys = CONFLICT_KEYS[table]
    cols = list(rows[0].keys())
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {updates}"
        if updates
        else f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    )
    payload = [tuple(r[c] for c in cols) for r in rows]
    with _write_lock:
        conn = connect()
        conn.executemany(sql, payload)
        conn.commit()
    return len(payload)


def reset_all() -> None:
    with _write_lock:
        conn = connect()
        for t in PROVENANCE_TABLES + ("sync_runs", "connections", "portal_settings"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.commit()
    init_db()
