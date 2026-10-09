"""Import campaign-level Superleap CRM attribution into the local portal."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portal import connections
from portal.db import connect, init_db, upsert


CHANNELS = {"google": "google_ads", "facebook": "meta_ads"}
CONNECTION_NAME = "Superleap CRM — campaigns"
EXTRACT = ROOT / "extracts" / "superleap_campaign_daily.csv"
FIELD_MAP = {
    "date": "date", "channel": "channel", "campaign_id": "campaign_id",
    "campaign_name": "campaign_name", "lead_type": "lead_type",
    "leads": "leads", "won": "won",
}


def records(payload: dict) -> list[dict]:
    out = []
    for result in payload.get("results", []):
        if result.get("error"):
            raise ValueError(f"Superleap query failed: {result['error']}")
        batch = result.get("records", {}).get("records", [])
        if not isinstance(batch, list):
            raise ValueError("Unexpected Superleap result shape")
        out.extend(batch)
    return out


def normalise(raw: dict) -> dict | None:
    source = str(raw.get("source_slp_alias") or "").strip().lower()
    channel = CHANNELS.get(source)
    campaign_name = str(raw.get("campaign_slp_alias") or "").strip()
    if not channel or not campaign_name or campaign_name == "{campaign}":
        return None
    return {
        "date": str(raw.get("date_slp_alias") or "")[:10],
        "channel": channel,
        "campaign_id": str(raw.get("campaign_id_slp_alias") or "").strip(),
        "campaign_name": campaign_name,
        "lead_type": str(raw.get("lead_type_slp_alias") or "").strip().lower(),
        "leads": int(raw.get("leads_slp_alias") or 0),
        "won": int(raw.get("won_slp_alias") or 0),
    }


def main(snapshot: str) -> None:
    payload = json.loads(Path(snapshot).read_text())
    rows = [row for raw in records(payload) if (row := normalise(raw))]
    if not rows:
        raise ValueError("Superleap returned no attributable campaign rows")

    init_db()
    existing = next((c for c in connections.all_connections("csv")
                     if c.name == CONNECTION_NAME), None)
    config = {
        "path": str(EXTRACT),
        "target_table": "crm_campaign_daily",
        "field_map": json.dumps(FIELD_MAP),
    }
    conn = (connections.update(existing.id, config=config) if existing
            else connections.create("csv", CONNECTION_NAME, config))

    EXTRACT.parent.mkdir(parents=True, exist_ok=True)
    with EXTRACT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELD_MAP))
        writer.writeheader()
        writer.writerows(rows)

    start = min(row["date"] for row in rows)
    end = max(row["date"] for row in rows)
    db = connect()
    with db:
        db.execute("DELETE FROM crm_campaign_daily WHERE date BETWEEN ? AND ?", (start, end))
    stored = [dict(row, connection_id=conn.id) for row in rows]
    upsert("crm_campaign_daily", stored)
    print(f"Imported {len(rows):,} campaign attribution rows for {start} through {end}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: import_superleap_campaign_snapshot.py SNAPSHOT.json")
    main(sys.argv[1])
