"""Repair stored Google/Facebook channels from blank-medium Superleap leads.

Stop the portal before applying. The default run only counts candidate rows.
An apply run requires the reviewed count, makes and verifies a private SQLite
backup, then updates local derived rows and both direct CRM rollups in one
transaction. No Superleap source record is changed.
"""
from __future__ import annotations

import argparse
import os
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portal.providers.shape import normalise_channel
from portal.providers.superleap import VUTTO_SOURCE_CHANNELS


MATCH = """connection_id=? AND TRIM(COALESCE(utm_medium,''))=''
AND ((LOWER(TRIM(utm_source))='google' AND channel='google/none')
  OR (LOWER(TRIM(utm_source))='facebook' AND channel='facebook/none'))"""


NAMED_MATCH = """connection_id=? AND vutto_source_channel(utm_source) IS NOT NULL
AND channel!=vutto_source_channel(utm_source)
AND channel=previous_source_channel(utm_source,utm_medium)"""


def _candidates(db: sqlite3.Connection, connection_id: int,
                match: str = MATCH) -> tuple[int, str | None, str | None]:
    return db.execute(
        f"SELECT COUNT(*),MIN(created_date),MAX(created_date) FROM crm_leads WHERE {match}",
        (connection_id,),
    ).fetchone()


def _totals(db: sqlite3.Connection, table: str, columns: tuple[str, ...], connection_id: int) -> tuple:
    expressions = ",".join(f"COALESCE(SUM({name}),0)" for name in columns)
    return tuple(db.execute(
        f"SELECT {expressions} FROM {table} WHERE connection_id=?", (connection_id,)
    ).fetchone())


def _backup(db: sqlite3.Connection, path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = path.with_name(f"{path.name}.before-source-channel-backfill-{stamp}")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        saved = sqlite3.connect(destination)
        try:
            db.backup(saved)
            saved.commit()
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup failed SQLite integrity check")
            for table in ("crm_leads", "crm_daily", "crm_campaign_daily"):
                source_count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                saved_count = saved.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if saved_count != source_count:
                    raise RuntimeError(f"Backup row count differs for {table}")
        finally:
            saved.close()
        os.chmod(destination, 0o600)
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def backfill(path: Path, connection_id: int, *, apply: bool = False,
             expected_count: int | None = None, named_sources: bool = False) -> dict:
    if not path.is_file():
        raise ValueError(f"Database does not exist: {path}")
    if apply and expected_count is None:
        raise ValueError("--expected-count is required with --apply")
    uri = path.resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    db = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        db.execute("PRAGMA busy_timeout=15000")
        provider = db.execute("SELECT provider,config FROM connections WHERE id=?", (connection_id,)).fetchone()
        if not provider or provider[0] != "superleap_crm":
            raise ValueError("Connection must be a direct Superleap CRM source")
        match = MATCH
        update = "CASE LOWER(TRIM(utm_source)) WHEN 'google' THEN 'google_ads' WHEN 'facebook' THEN 'meta_ads' END"
        if named_sources:
            config = json.loads(provider[1] or "{}")
            fields = config.get("field_map") or {}
            if isinstance(fields, str):
                fields = json.loads(fields)
            if (not isinstance(fields, dict) or "channel" in fields
                    or fields.get("utm_source", "vutto_source") != "vutto_source"
                    or fields.get("utm_medium", "lead_type") != "lead_type"):
                raise ValueError("Named-source repair requires the standard Vutto source mapping and no explicit channel override")
            db.create_function("vutto_source_channel", 1,
                               lambda source: VUTTO_SOURCE_CHANNELS.get(str(source or "").strip().lower()),
                               deterministic=True)
            db.create_function("previous_source_channel", 2, normalise_channel, deterministic=True)
            match = NAMED_MATCH
            update = "vutto_source_channel(utm_source)"
        count, start, end = _candidates(db, connection_id, match)
        result = {"candidates": count, "start": start, "end": end, "backup": None}
        if not apply:
            return result
        if count != expected_count:
            raise ValueError(f"Expected {expected_count} candidate rows, found {count}; review before applying")
        if not count:
            return result

        # Refuse a concurrent sync: the reviewed backup must match the rows
        # being repaired. The operator should also stop the portal first.
        data_version = db.execute("PRAGMA data_version").fetchone()[0]
        result["backup"] = _backup(db, path)
        if db.execute("PRAGMA data_version").fetchone()[0] != data_version:
            raise RuntimeError("Database changed during backup; no rows were updated")

        daily_columns = ("leads", "contacted", "qualified", "booked", "completed", "won", "revenue")
        campaign_columns = ("leads", "won")
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("PRAGMA data_version").fetchone()[0] != data_version:
                raise RuntimeError("Database changed before backfill; no rows were updated")
            if _candidates(db, connection_id, match) != (count, start, end):
                raise RuntimeError("Candidate rows changed before backfill")
            daily_before = _totals(db, "crm_daily", daily_columns, connection_id)
            campaign_before = _totals(db, "crm_campaign_daily", campaign_columns, connection_id)
            cursor = db.execute(
                f"UPDATE crm_leads SET channel={update} WHERE {match}", (connection_id,),
            )
            if cursor.rowcount != count:
                raise RuntimeError("Updated row count does not match reviewed count")
            db.execute("DELETE FROM crm_daily WHERE connection_id=? AND date BETWEEN ? AND ?",
                       (connection_id, start, end))
            db.execute("""INSERT INTO crm_daily
                (date,channel,source,lead_type,leads,contacted,qualified,booked,completed,won,revenue,connection_id)
                SELECT created_date,channel,utm_source,lead_type,COUNT(*),
                       COALESCE(SUM(is_contacted),0),COALESCE(SUM(is_qualified),0),
                       COALESCE(SUM(is_visit_booked),0),COALESCE(SUM(is_visit_done),0),
                       COALESCE(SUM(is_sale),0),COALESCE(SUM(revenue),0),connection_id
                FROM crm_leads WHERE connection_id=? AND created_date BETWEEN ? AND ?
                GROUP BY created_date,channel,utm_source,lead_type,connection_id""",
                       (connection_id, start, end))
            db.execute("DELETE FROM crm_campaign_daily WHERE connection_id=? AND date BETWEEN ? AND ?",
                       (connection_id, start, end))
            db.execute("""INSERT INTO crm_campaign_daily
                (date,channel,campaign_id,campaign_name,lead_type,leads,won,connection_id)
                SELECT created_date,channel,'',TRIM(utm_campaign),lead_type,
                       COUNT(*),COALESCE(SUM(is_sale),0),connection_id
                FROM crm_leads WHERE connection_id=? AND created_date BETWEEN ? AND ?
                    AND TRIM(utm_campaign)!=''
                GROUP BY created_date,channel,TRIM(utm_campaign),lead_type,connection_id""",
                       (connection_id, start, end))
            if _candidates(db, connection_id, match)[0]:
                raise RuntimeError("Candidate channels remain after backfill")
            if _totals(db, "crm_daily", daily_columns, connection_id) != daily_before:
                raise RuntimeError("Direct daily totals changed during backfill")
            if _totals(db, "crm_campaign_daily", campaign_columns, connection_id) != campaign_before:
                raise RuntimeError("Direct campaign totals changed during backfill")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Database failed SQLite integrity check")
            db.commit()
        except Exception:
            db.rollback()
            raise
        return result
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Portal SQLite database path")
    parser.add_argument("--connection-id", type=int, required=True)
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--named-sources", action="store_true",
                        help="Repair known Vutto source categories previously assigned by generic UTM rules")
    args = parser.parse_args()
    result = backfill(args.db, args.connection_id, apply=args.apply,
                      expected_count=args.expected_count, named_sources=args.named_sources)
    print(f"Candidate local CRM rows: {result['candidates']} ({result['start']} to {result['end']})")
    if result["backup"]:
        print(f"Updated local channels and direct rollups. Verified backup: {result['backup']}")
    elif not args.apply:
        print("Dry run only. Stop the portal, review the count, then use --expected-count and --apply.")


if __name__ == "__main__":
    main()
