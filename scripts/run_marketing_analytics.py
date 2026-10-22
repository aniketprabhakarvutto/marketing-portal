#!/usr/bin/env python3
"""Run the Marketing Analytics export from Vortex Veri's saved connections.

Writes are disabled unless MARKETING_ANALYTICS_WRITE=1. This prevents a newly
deployed scheduler from touching production workbooks before its preview has
been checked.
"""
from __future__ import annotations

from datetime import timedelta
import json
import os
import sys

from portal import connections, marketing_analytics, sync
from portal.api import portal_today


def _accounts() -> dict[str, set[str]]:
    out = {"google_ads": set(), "meta_ads": set()}
    for conn in connections.all_connections(enabled_only=True):
        if conn.provider == "google_ads":
            out["google_ads"].update(value.replace("-", "") for value in conn.list_of("customer_ids"))
        elif conn.provider == "meta_ads":
            out["meta_ads"].update(value.removeprefix("act_") for value in conn.list_of("account_ids"))
    return out


def _mapping_connection():
    for conn in connections.all_connections(provider="google_sheets", enabled_only=True):
        if str(conn.get("analytics_role") or "").strip().lower() == "mapping":
            return conn
    raise RuntimeError("No enabled Google Sheets connection is tagged as the mapping workbook")


def _mapping_values(conn) -> list[list]:
    provider = marketing_analytics._sheet_provider()
    spreadsheet_id = provider._spreadsheet_id(str(conn.get("spreadsheet_url") or ""))
    tab = str(conn.get("sheet_name") or "Sheet2").strip()
    escaped = marketing_analytics.quote(f"'{tab.replace(chr(39), chr(39) * 2)}'!A1:ZZ2000", safe="")
    body = marketing_analytics.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{escaped}",
        headers={"Authorization": f"Bearer {marketing_analytics._token(conn)}"},
        params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
    )
    return body.get("values") or []


def run() -> dict:
    day = (portal_today() - timedelta(days=1)).isoformat()
    sync_results = []
    for conn in connections.all_connections(enabled_only=True):
        if conn.provider in {"google_ads", "meta_ads", "superleap_crm"}:
            sync_results.append(sync.run_connection(conn, portal_today() - timedelta(days=2), portal_today()))
    failed = [item for item in sync_results if item["status"] not in {"ok", "empty"}]
    if failed:
        raise RuntimeError("Source refresh failed: " + "; ".join(item["name"] for item in failed))

    mapping_conn = _mapping_connection()
    mappings = marketing_analytics.mapping_pairs(
        _mapping_values(mapping_conn),
        mapping_conn.get("mapping_source_column") or "A",
        mapping_conn.get("mapping_target_column") or "B",
        mapping_conn.get("mapping_side_column") or "C",
    )
    if mappings["unclassified"]:
        raise RuntimeError("Unclassified campaign mappings: " + ", ".join(mappings["unclassified"][:20]))
    accounts = _accounts()
    rows = marketing_analytics.paid_rows(day)
    previews, written = {}, []
    for role in ("demand", "supply"):
        prepared = marketing_analytics.prepare_cost_rows(rows, mappings[role], day, accounts)
        previews[role] = {"rows": len(prepared["rows"]), "unmapped": prepared["unmapped"]}
        if prepared["unmapped"]:
            raise RuntimeError(f"{role.title()} has unmapped campaigns: " + ", ".join(prepared["unmapped"][:20]))
        if os.getenv("MARKETING_ANALYTICS_WRITE", "").strip() != "1":
            continue
        values = [["Date", "Platform", "Source campaign", "Mapped campaign", "Cost"]]
        values.extend([[row[0], row[1], row[2], row[3], str(row[4])] for row in prepared["rows"]])
        targets = [conn for conn in connections.all_connections(provider="google_sheets", enabled_only=True)
                   if marketing_analytics._role(conn) == role]
        if not targets:
            raise RuntimeError(f"No enabled {role} workbook is configured")
        for target in targets:
            tab = str(target.get("campaign_cost_sheet") or "").strip()
            if not tab:
                raise RuntimeError(
                    f"{target.name} has no explicit campaign cost tab. "
                    "Configure a staging or approved destination before enabling writes."
                )
            written.append(marketing_analytics.write_cost_tab(target.id, tab, values))
    return {"date": day, "mode": "write" if written else "preview", "sources": sync_results,
            "previews": previews, "written": written}


if __name__ == "__main__":
    try:
        print(json.dumps(run(), indent=2, default=str))
    except Exception as exc:
        print(f"Marketing Analytics automation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
