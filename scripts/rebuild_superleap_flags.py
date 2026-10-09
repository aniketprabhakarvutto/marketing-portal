"""Recalculate stored direct Superleap stage flags using the current mapping.

Run with the portal stopped. The command backs up the database first and only
updates derived flags on the selected direct CRM connection.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime

from portal import connections
from portal.config import APP
from portal.db import backup_database, connect, query
from portal.providers.superleap import DEFAULT_FIELD_MAP, SuperleapCRMProvider
from portal.rollup import rollup_daily


def rebuild_stage_flags(connection_id: int) -> int:
    saved = connections.get(connection_id)
    if not saved or saved.provider != "superleap_crm":
        raise ValueError("Select an existing direct Superleap CRM connection.")
    provider = SuperleapCRMProvider()
    if provider._field_map(saved) != DEFAULT_FIELD_MAP:
        raise ValueError("Custom stage mapping cannot be rebuilt from stored fields; run a full CRM sync.")

    rows = query(
        "SELECT id, created_date, lead_type, stage, is_qualified "
        "FROM crm_leads WHERE connection_id = ? ORDER BY id", (connection_id,),
    )
    if not rows:
        return 0
    updates = []
    for record in rows:
        normalized = provider._normalise(saved, {
            "lead_category": record["lead_type"],
            "stage1": record["stage"],
            "event_qualification_status": record["is_qualified"],
        }, DEFAULT_FIELD_MAP)
        updates.append((
            normalized["is_contacted"], normalized["is_qualified"],
            normalized["is_visit_booked"], normalized["is_visit_done"],
            normalized["is_sale"], record["id"],
        ))
    database = connect()
    database.executemany(
        "UPDATE crm_leads SET is_contacted=?, is_qualified=?, is_visit_booked=?, "
        "is_visit_done=?, is_sale=? WHERE id=?", updates,
    )
    database.commit()
    dates = [date.fromisoformat(record["created_date"]) for record in rows]
    rollup_daily(min(dates), max(dates))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection_id", type=int)
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = f"{APP.db_path}.before-stage-rebuild-{stamp}"
    backup_database(backup_path)
    count = rebuild_stage_flags(args.connection_id)
    print(f"Backed up the database to {backup_path}")
    print(f"Rebuilt {count:,} stored Superleap opportunity flags and CRM daily rollups.")


if __name__ == "__main__":
    main()
