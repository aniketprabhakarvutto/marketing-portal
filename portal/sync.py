"""Running a connection: fetch, store, record what happened.

Every sync re-pulls a rolling window rather than only yesterday, and upserts on
each table's natural key. Ad platforms restate conversions for days afterwards
and Search Console lags two to three days, so overwriting a window is the only
way the numbers stay right without a full reload — and it means re-running a
sync can never double-count.
"""
from __future__ import annotations

import logging
import traceback
from datetime import date, timedelta

from . import connections as conns
from .config import APP
from .db import connect, execute, upsert
from .providers import REGISTRY, table_for
from .providers.base import Connection
from .rollup import rollup_campaign_daily, rollup_daily

log = logging.getLogger("portal.sync")


def recover_interrupted_runs() -> int:
    """Close runs left active when the portal was stopped mid-sync."""
    return execute(
        "UPDATE sync_runs SET status='error', finished_at=datetime('now'), "
        "message='Sync interrupted before completion. Run Sync latest again.' "
        "WHERE status='running'"
    )


def run_connection(conn: Connection, start: date, end: date) -> dict:
    """Sync one account. Never raises — failures are recorded and returned."""
    provider = REGISTRY.get(conn.provider)
    if provider is not None and not provider.syncable:
        return {
            "connection_id": conn.id, "provider": conn.provider, "name": conn.name,
            "status": "skipped", "rows": 0,
            "message": f"{provider.label} is an action-only connection and does not sync rows.",
        }
    db = connect()
    cur = db.execute(
        "INSERT INTO sync_runs (connector, connection_id, status, window_start, window_end) "
        "VALUES (?, ?, 'running', ?, ?)",
        (conn.provider, conn.id, start.isoformat(), end.isoformat()),
    )
    db.commit()
    run_id = cur.lastrowid

    status, written, message = "ok", 0, ""
    try:
        if provider is None:
            status, message = "error", f"No provider named '{conn.provider}' is installed."
        elif not conn.enabled:
            status, message = "skipped", "Connection is turned off."
        elif not provider.ready(conn):
            status = "skipped"
            message = "Not ready — still needs: " + ", ".join(provider.missing(conn))
        else:
            table = table_for(provider, conn)
            rows = provider.fetch(conn, start, end)
            for row in rows:
                row["connection_id"] = conn.id
            written = upsert(table, rows)
            provider.after_load(conn, start, end)
            if table in ("crm_leads",):
                dates = [date.fromisoformat(str(row["created_date"])) for row in rows
                         if row.get("created_date")]
                rollup_start = min([start, *dates]) if dates else start
                rollup_end = max([end, *dates]) if dates else end
                rollup_daily(rollup_start, rollup_end)
                if conn.provider == "superleap_crm":
                    rollup_campaign_daily(rollup_start, rollup_end, conn.id)
            if not written:
                status = "empty"
            message = (f"{written:,} rows into {table} for {start} → {end}"
                       if written else f"No rows returned for {start} → {end}")
    except Exception as exc:  # noqa: BLE001 — surfaced in the UI, never swallowed
        status = "error"
        message = f"{type(exc).__name__}: {exc}"
        log.error("sync failed for %s/%s\n%s", conn.provider, conn.name, traceback.format_exc())

    execute(
        "UPDATE sync_runs SET status=?, rows_written=?, message=?, "
        "finished_at=datetime('now') WHERE id=?",
        (status, written, message[:4000], run_id),
    )
    return {
        "connection_id": conn.id, "provider": conn.provider, "name": conn.name,
        "status": status, "rows": written, "message": message,
    }


def run_all(lookback_days: int | None = None, connection_id: int | None = None) -> list[dict]:
    from .api import portal_today

    end = portal_today()
    days = max(1, APP.sync_lookback_days if lookback_days is None else lookback_days)
    start = end - timedelta(days=days - 1)
    if connection_id is not None:
        conn = conns.get(connection_id)
        return [run_connection(conn, start, end)] if conn else []
    return [run_connection(c, start, end) for c in conns.all_connections(enabled_only=True)
            if (REGISTRY.get(c.provider) and REGISTRY[c.provider].syncable)]
