"""Editable refresh schedules and locally generated CSV reports."""
from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

from . import api
from .config import APP
from .db import execute, query


DEFAULTS = {"full_sync_time": f"{APP.sync_hour:02d}:15", "topup_hours": str(APP.topup_hours)}


def get_settings() -> dict:
    saved = {row["key"]: row["value"] for row in query("SELECT key, value FROM portal_settings")}
    values = {**DEFAULTS, **saved}
    return {"full_sync_time": values["full_sync_time"],
            "topup_hours": int(values["topup_hours"] or APP.topup_hours)}


def _clock(value: object) -> str:
    text = str(value or "")
    try:
        hour, minute = (int(part) for part in text.split(":"))
    except (ValueError, TypeError):
        raise ValueError("Full refresh time must be a valid time") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Full refresh time must be a valid time")
    return f"{hour:02d}:{minute:02d}"


def save_settings(body: dict) -> dict:
    current = get_settings()
    full_time = _clock(body.get("full_sync_time", current["full_sync_time"]))
    try:
        topup_hours = int(body.get("topup_hours", current["topup_hours"]))
    except (TypeError, ValueError):
        raise ValueError("Refresh interval must be a number") from None
    if topup_hours not in (1, 2, 4, 6, 12, 24):
        raise ValueError("Choose a refresh interval between 1 and 24 hours")
    for key, value in {"full_sync_time": full_time, "topup_hours": str(topup_hours)}.items():
        execute("INSERT INTO portal_settings(key,value,updated_at) VALUES(?,?,datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=datetime('now')",
                (key, value))
    return get_settings()


def get_rules() -> list[dict]:
    raw = next((r["value"] for r in query("SELECT value FROM portal_settings WHERE key='automation_rules'")), "[]")
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def save_rules(body: dict) -> dict:
    rules = body.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Automation rules must be a list")
    rules = [rule for rule in rules[:100] if isinstance(rule, dict)]
    execute("INSERT INTO portal_settings(key,value,updated_at) VALUES('automation_rules',?,datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=datetime('now')",
            (json.dumps(rules),))
    return {"rules": rules}


def safe_csv_value(value):
    """Export source strings as text when spreadsheets could treat them as formulas.

    Apostrophe protects text import; the following tab remains a second guard
    for Excel resaving behavior. This changes export representation, not data.
    """
    if isinstance(value, str) and (value.lstrip().startswith(tuple("=+-@＝＋－＠"))
                                   or value.startswith(("\t", "\r", "\n"))):
        return "'\t" + value
    return value


def build_csv(start: str | None = None, end: str | None = None) -> tuple[str, bytes]:
    end = end or (api.portal_today() - timedelta(days=1)).isoformat()
    start = start or (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    start, end = api.date_range(start, end)
    overview = api.overview(start, end)
    channels = api.channels(start, end)
    campaigns = api.campaigns(start, end, limit=None)
    stream = io.StringIO(newline="")
    csv_writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    class ReportWriter:
        def writerow(self, values):
            csv_writer.writerow([safe_csv_value(value) for value in values])
    writer = ReportWriter()
    writer.writerow(["Vortex Veri report", f"{start} to {end}"])
    writer.writerow([])
    writer.writerow(["Overview metric", "Value"])
    for key, value in overview["current"].items():
        writer.writerow([key, value])
    writer.writerow([])

    def section(title: str, rows: list[dict]) -> None:
        writer.writerow([title])
        if not rows:
            writer.writerow(["No data"])
            writer.writerow([])
            return
        columns = list(rows[0].keys())
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column, "") for column in columns])
        writer.writerow([])

    section("Channel performance", channels)
    section("Campaign performance", campaigns)
    filename = f"marketing-report-{start}-to-{end}.csv"
    return filename, stream.getvalue().encode("utf-8-sig")
