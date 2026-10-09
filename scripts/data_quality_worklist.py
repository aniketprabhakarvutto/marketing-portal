"""Export source-level CRM discrepancies without changing reporting data."""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from portal import api, db
from portal.config import APP


def _key(row: Any) -> tuple[str, str, str, str]:
    return (str(row["date"]), str(row["channel"]),
            api._campaign_key(row["campaign_name"]), api._lead_side(row["lead_type"]))


def build_worklists(start: str, end: str) -> dict[str, list[dict[str, Any]]]:
    """Return the exact source records behind unresolved CRM quality checks."""
    start, end = api.date_range(start, end)
    raw = db.query(
        "SELECT d.date,d.channel,d.campaign_name,d.lead_type,d.connection_id,"
        "d.leads,d.won,c.name source,c.provider,c.config "
        "FROM crm_campaign_daily d LEFT JOIN connections c ON c.id=d.connection_id "
        "WHERE d.date BETWEEN ? AND ? ORDER BY d.date,d.id", (start, end))
    grouped: dict[tuple[str, str, str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in raw:
        key = _key(row)
        if not key[2]:
            continue
        connection_id = int(row["connection_id"])
        won_measured = api._crm_campaign_won_measured(row["provider"], row["config"])
        item = grouped[key].setdefault(connection_id, {
            "date": key[0], "channel": key[1], "campaign": str(row["campaign_name"] or ""),
            "lead_side": key[3], "connection_id": connection_id,
            "source": str(row["source"] or f"Connection {connection_id}"),
            "provider": str(row["provider"] or ""), "leads": 0,
            "won_measured": won_measured, "won": 0 if won_measured else None,
        })
        item["leads"] += int(row["leads"] or 0)
        if item["won_measured"]:
            item["won"] += int(row["won"] or 0)

    selected = {
        (row["date"], row["channel"], row["campaign_key"], row["lead_side"]):
        row["connection_id"]
        for row in api._crm_campaign_reporting_rows(start, end)
    }
    conflicts: list[dict[str, Any]] = []
    for key, sources in grouped.items():
        if len(sources) < 2 or not api._crm_campaign_totals_conflict(sources.values()):
            continue
        for row in sources.values():
            conflicts.append({**row, "selected_for_reporting": row["connection_id"] == selected.get(key)})

    other_channels = [
        {"date": row["date"], "channel": row["channel"],
         "campaign": ", ".join(sorted(row["campaign_names"])),
         "lead_side": row["lead_side"], "connection_id": row["connection_id"],
         "source": row["source"], "leads": row["leads"], "won": row["won"]}
        for row in api._crm_campaign_reporting_rows(start, end)
        if str(row["channel"] or "").strip().lower() in {"other", "others", ""}
    ]
    unqualified_supply = [dict(row) for row in db.query(
        "SELECT external_id,created_date,stage,channel,utm_campaign,connection_id "
        "FROM " + api.CRM_LEADS_FROM + " WHERE created_date BETWEEN ? AND ? "
        "AND lead_type='sell' AND is_qualified=0 "
        "AND (is_visit_booked=1 OR is_visit_done=1 OR is_sale=1) "
        "ORDER BY created_date,external_id", (start, end))]
    return {"feed_conflicts": conflicts, "other_channels": other_channels,
            "unqualified_supply": unqualified_supply}


def _safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_worklists(start: str, end: str, output: Path) -> dict[str, int]:
    start, end = api.date_range(start, end)
    rows = build_worklists(start, end)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output, 0o700)
    for name, items in rows.items():
        path = output / f"{name}.csv"
        columns = list(items[0]) if items else {
            "feed_conflicts": ["date", "channel", "campaign", "lead_side", "connection_id",
                               "source", "provider", "leads", "won_measured", "won",
                               "selected_for_reporting"],
            "other_channels": ["date", "channel", "campaign", "lead_side", "connection_id",
                               "source", "leads", "won"],
            "unqualified_supply": ["external_id", "created_date", "stage", "channel",
                                   "utm_campaign", "connection_id"],
        }[name]
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows({key: _safe_cell(item.get(key)) for key in columns} for item in items)
    counts = {name: len(items) for name, items in rows.items()}
    note = (
        f"# CRM data-quality worklists, {start} to {end}\n\n"
        "No source records were changed. These files identify discrepancies for source owners to resolve.\n\n"
        f"feed_conflicts: {counts['feed_conflicts']} source rows in groups whose lead or measured sale counts disagree. "
        "Compare all rows for the same date, channel, campaign and lead side; the selected_for_reporting "
        "column identifies the feed currently used by portal reports. Blank won with "
        "won_measured=False means that source does not supply sales counts.\n\n"
        f"other_channels: {counts['other_channels']} reported campaign groups with unknown channel. "
        "Confirm source classification before assigning paid credit.\n\n"
        f"unqualified_supply: {counts['unqualified_supply']} supply opportunities in advanced stages "
        "without a Superleap qualification flag. Confirm or correct the CRM status; do not infer it.\n"
    )
    fd = os.open(output / "README.md", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(note)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or Path(APP.db_path).parent / "quality-worklists" / stamp
    counts = write_worklists(args.start, args.end, output)
    print(f"Private worklists written to {output}")
    print(", ".join(f"{name}: {count}" for name, count in counts.items()))


if __name__ == "__main__":
    main()
