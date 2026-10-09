"""Import aggregate rows returned by the connected Superleap CRM tool.

The CRM connector is intentionally kept outside the portal process. This small
bridge validates the connector result and refreshes only the requested dates,
so rerunning it cannot duplicate or partially replace dashboard data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portal.db import connect, init_db


CHANNELS = {
    "google": "google_ads",
    "facebook": "meta_ads",
    "organic": "organic_search",
    "gmb": "gmb",
    "whatsapp": "whatsapp",
    "influencer": "influencer",
    "cross_funnel": "cross_funnel",
    "flipkart": "flipkart",
    "olx": "olx",
    "store": "store",
    "others": "others",
    "superleap": "others",
    "(unknown)": "unknown",
}


def _records(payload: dict, result_id: str) -> list[dict]:
    result = next((r for r in payload.get("results", []) if r.get("id") == result_id), None)
    if not result or result.get("error"):
        raise ValueError(f"Missing or failed {result_id} result: {result}")
    records = result.get("records", {}).get("records", [])
    if not isinstance(records, list):
        raise ValueError(f"Unexpected {result_id} result shape")
    return records


def _day(value: str) -> str:
    day = str(value)[:10]
    datetime.strptime(day, "%Y-%m-%d")
    return day


def main(path: str) -> None:
    payload = json.loads(Path(path).read_text())
    daily_raw = _records(payload, "daily")
    city_raw = _records(payload, "city")
    if not daily_raw or not city_raw:
        raise ValueError("Superleap returned no CRM rows")

    daily = []
    for r in daily_raw:
        source = str(r.get("source_slp_alias") or "(unknown)")
        daily.append({
            "date": _day(r["date_slp_alias"]),
            "channel": CHANNELS.get(source.lower(), source.lower()),
            "source": source,
            "lead_type": str(r["lead_type_slp_alias"]),
            "leads": int(r.get("leads_slp_alias") or 0),
            "contacted": int(r.get("contacted_slp_alias") or 0),
            "qualified": int(r.get("qualified_slp_alias") or 0),
            "booked": int(r.get("booked_slp_alias") or 0),
            "completed": int(r.get("completed_slp_alias") or 0),
            "won": int(r.get("won_slp_alias") or 0),
            "revenue": 0.0,
        })
    cities = [{
        "date": _day(r["date_slp_alias"]),
        "city": str(r.get("city_slp_alias") or "(unknown)"),
        "lead_type": str(r["lead_type_slp_alias"]),
        "leads": int(r.get("leads_slp_alias") or 0),
        "won": int(r.get("won_slp_alias") or 0),
    } for r in city_raw]

    start = min(r["date"] for r in daily)
    end = max(r["date"] for r in daily)
    if start != min(r["date"] for r in cities) or end != max(r["date"] for r in cities):
        raise ValueError("Daily and city result windows do not match")

    init_db()
    db = connect()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db:
        db.execute("DELETE FROM crm_daily WHERE date BETWEEN ? AND ?", (start, end))
        db.execute("DELETE FROM crm_city_daily WHERE date BETWEEN ? AND ?", (start, end))
        db.executemany(
            "INSERT INTO crm_daily (date,channel,source,lead_type,leads,contacted,qualified,booked,completed,won,revenue,origin,ingested_at,connection_id) "
            "VALUES (:date,:channel,:source,:lead_type,:leads,:contacted,:qualified,:booked,:completed,:won,:revenue,'superleap_live',:stamp,0)",
            [dict(r, stamp=stamp) for r in daily],
        )
        db.executemany(
            "INSERT INTO crm_city_daily (date,city,lead_type,leads,won,origin,ingested_at,connection_id) "
            "VALUES (:date,:city,:lead_type,:leads,:won,'superleap_live',:stamp,0)",
            [dict(r, stamp=stamp) for r in cities],
        )
    print(f"Imported {len(daily)} daily rows and {len(cities)} city rows for {start} through {end}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: import_superleap_snapshot.py SNAPSHOT.json")
    main(sys.argv[1])
