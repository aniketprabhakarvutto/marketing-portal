"""Background refresh.

Every sync re-pulls a rolling *window* rather than only yesterday, and upserts
on each table's natural key. That is deliberate: ad platforms restate
conversions for days after the fact and Search Console lags two to three days,
so overwriting a window is the only way the numbers stay right without a full
reload — and it means re-running a sync can never double-count.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import APP
from .automation import get_settings
from . import platform
from .db import query, scalar, using_postgres
from .sync import run_all

log = logging.getLogger("portal.scheduler")

_stop = threading.Event()
_thread: threading.Thread | None = None


def _portal_now() -> datetime:
    try:
        return datetime.now(ZoneInfo(APP.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now().astimezone()


def _local_run_date(stored_at: str) -> date | None:
    try:
        value = datetime.fromisoformat(stored_at)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo(APP.timezone)).date()
    except (ValueError, ZoneInfoNotFoundError):
        return None


def sync_all(lookback_days: int | None = None) -> list[dict]:
    """Sync every enabled connection across every platform."""
    results = run_all(lookback_days=lookback_days)
    for r in results:
        log.info("%s / %s → %s (%s)", r["provider"], r["name"], r["status"], r["message"])
    return results


def _loop() -> None:
    """Wake often, decide from the wall clock.

    An earlier version counted down with a monotonic clock, which does not
    advance while a laptop is asleep — so a machine that slept through 06:15
    simply never ran its daily sync, and the four-hourly top-up drifted by
    however long the lid was shut. Comparing against the wall clock instead
    means a missed run is noticed and caught up on the next wake.
    """
    now = _portal_now()
    full_window = max(1, APP.sync_lookback_days - 1)
    recent_cutoff = (now - timedelta(days=2)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if using_postgres():
        full_runs = query(
            "SELECT started_at FROM sync_runs WHERE status='ok' AND started_at>=? "
            "AND EXTRACT(EPOCH FROM (window_end::timestamptz - window_start::timestamptz)) / 86400 >= ?",
            (recent_cutoff, full_window),
        )
        latest_success_sql = (
            "SELECT MAX(EXTRACT(EPOCH FROM finished_at::timestamptz)::BIGINT) "
            "FROM sync_runs WHERE status='ok'"
        )
    else:
        full_runs = query(
            "SELECT started_at FROM sync_runs WHERE status='ok' AND started_at>=? "
            "AND julianday(window_end)-julianday(window_start)>=?", (recent_cutoff, full_window))
        latest_success_sql = (
            "SELECT MAX(CAST(strftime('%s',finished_at) AS INTEGER)) "
            "FROM sync_runs WHERE status='ok'"
        )
    full_today = any(_local_run_date(str(row["started_at"])) == now.date() for row in full_runs)
    last_full: date | None = now.date() if full_today else None
    settings = get_settings()
    latest_success = scalar(latest_success_sql, default=None)
    elapsed = max(0, int(_portal_now().timestamp()) - int(latest_success or 0))
    wait_seconds = max(0, settings["topup_hours"] * 3600 - elapsed)
    next_topup = _portal_now() + timedelta(seconds=wait_seconds)

    while not _stop.is_set():
        if _stop.wait(60):
            return
        now = _portal_now()
        try:
            settings = get_settings()
            full_hour, full_minute = map(int, settings["full_sync_time"].split(":"))
            due = now.replace(hour=full_hour, minute=full_minute, second=0, microsecond=0)
            if now >= due and last_full != now.date():
                log.info("daily full sync (%s days)", APP.sync_lookback_days)
                sync_all()
                last_full = now.date()
            elif now >= next_topup:
                log.info("top-up sync (3 days)")
                sync_all(lookback_days=3)
                next_topup = _portal_now() + timedelta(hours=settings["topup_hours"])
            results = platform.run_due_jobs(now.date().isoformat(), now.strftime("%H:%M"))
            for result in results:
                level = logging.WARNING if result["status"] == "error" else logging.INFO
                log.log(level, "scheduled job %s → %s: %s",
                        result["id"], result["status"], result["message"])
        except Exception:  # noqa: BLE001 — the loop must survive a bad sync
            log.exception("scheduled sync failed")
            next_topup = _portal_now() + timedelta(hours=get_settings()["topup_hours"])


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="scheduler")
    _thread.start()
    settings = get_settings()
    log.info("scheduler on — full sync daily at %s local, top-up every %sh",
             settings["full_sync_time"], settings["topup_hours"])


def stop_scheduler() -> None:
    _stop.set()
