#!/usr/bin/env python3
"""Run the Marketing Analytics export from Vortex Veri's saved connections.

Writes are disabled unless MARKETING_ANALYTICS_WRITE=1. Test is the default
target. A live run requires MARKETING_ANALYTICS_TARGET=live and can only use
the explicit month-and-workbook allowlist in ``marketing_db_exports``.
"""
from __future__ import annotations

from datetime import timedelta
import json
import os
import sys

from portal import connections, marketing_analytics, marketing_db_exports, sync
from portal.api import portal_today

# These are the two private copies explicitly approved for testing. A staging
# tab name alone is not proof that a workbook is safe to modify.
TEST_WORKBOOKS = marketing_analytics.TEST_WORKBOOKS


def _test_destinations():
    destinations = []
    for role, workbook_id in TEST_WORKBOOKS.items():
        targets = [conn for conn in connections.all_connections(provider="google_sheets", enabled_only=True)
                   if marketing_analytics._role(conn) == role
                   and marketing_analytics._spreadsheet_id(conn) == workbook_id]
        if len(targets) != 1:
            raise RuntimeError(f"Exactly one enabled {role} test workbook must be configured")
        target = targets[0]
        tab = str(target.get("campaign_cost_sheet") or "").strip()
        if tab != "__vortex_cost_test":
            raise RuntimeError("Only the __vortex_cost_test staging tab is supported until the real sheet layout is verified")
        destinations.append((role, target, tab))
    return destinations


def _live_destinations(month: str):
    expected = marketing_db_exports.LIVE_WORKBOOKS.get(month)
    if not expected:
        raise RuntimeError(f"No approved live Marketing Analytics workbooks are configured for {month}")
    destinations = []
    for role, workbook_id in expected.items():
        targets = [conn for conn in connections.all_connections(provider="google_sheets")
                   if marketing_analytics._role(conn) == role
                   and marketing_analytics._spreadsheet_id(conn) == workbook_id]
        if len(targets) != 1:
            raise RuntimeError(f"Exactly one configured live {role} workbook is required for {month}")
        destinations.append((role, targets[0], "Backend"))
    return destinations


def _accounts() -> dict[str, set[str]]:
    out = {"google_ads": set(), "meta_ads": set()}
    for conn in connections.all_connections(enabled_only=True):
        if conn.provider == "google_ads":
            out["google_ads"].update(value.replace("-", "") for value in conn.list_of("customer_ids"))
        elif conn.provider == "meta_ads":
            out["meta_ads"].update(value.removeprefix("act_") for value in conn.list_of("account_ids"))
    return {provider: values & marketing_analytics.MARKETING_ACCOUNT_IDS[provider]
            for provider, values in out.items()}


def _source_connections(active):
    """Return only sources that belong to this workbook automation."""
    selected = []
    for conn in active:
        if conn.provider == "google_ads":
            ids = {value.replace("-", "") for value in conn.list_of("customer_ids")}
            if ids & marketing_analytics.MARKETING_ACCOUNT_IDS["google_ads"]:
                selected.append(conn)
        elif conn.provider == "meta_ads":
            ids = {value.removeprefix("act_") for value in conn.list_of("account_ids")}
            if ids & marketing_analytics.MARKETING_ACCOUNT_IDS["meta_ads"]:
                selected.append(conn)
        elif conn.provider == "superleap_crm":
            selected.append(conn)
    return selected


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


def _backend_exports() -> tuple[str, str, str] | None:
    demand = os.getenv("MARKETING_DEMAND_EXPORT", "").strip()
    supply = os.getenv("MARKETING_SUPPLY_EXPORT", "").strip()
    month = os.getenv("MARKETING_ANALYTICS_MONTH", "").strip()
    if not demand and not supply and not month:
        return None
    if not demand or not supply or not month:
        raise RuntimeError("Set MARKETING_DEMAND_EXPORT, MARKETING_SUPPLY_EXPORT and MARKETING_ANALYTICS_MONTH together")
    return demand, supply, month


def run() -> dict:
    day = (portal_today() - timedelta(days=1)).isoformat()
    sync_results = []
    active = connections.all_connections(enabled_only=True)
    sources = _source_connections(active)
    required = {"google_ads", "meta_ads", "superleap_crm"}
    missing = sorted(required - {conn.provider for conn in sources})
    if missing:
        raise RuntimeError("Required sources not enabled: " + ", ".join(missing))
    for conn in sources:
        sync_results.append(sync.run_connection(conn, portal_today() - timedelta(days=2), portal_today()))
    failed = [item for item in sync_results if item["status"] != "ok"]
    if failed:
        raise RuntimeError("Source refresh failed or returned no data: " + "; ".join(item["name"] for item in failed))

    mapping_conn = _mapping_connection()
    mappings = marketing_analytics.mapping_pairs(
        _mapping_values(mapping_conn),
        mapping_conn.get("mapping_source_column") or "A",
        mapping_conn.get("mapping_target_column") or "B",
        mapping_conn.get("mapping_side_column") or "C",
    )
    accounts = _accounts()
    rows = marketing_analytics.paid_rows(day)
    previews, written, prepared_by_role = {}, [], {}
    backend_rows = {}
    export_config = _backend_exports()
    if export_config:
        demand_export, supply_export, month = export_config
        if month != day[:7]:
            raise RuntimeError(f"Backend export month {month} does not match campaign-cost month {day[:7]}")
        backend_rows = {
            "demand": marketing_db_exports.read_export(demand_export, "demand", month, allow_other_months=True),
            "supply": marketing_db_exports.read_export(supply_export, "supply", month, allow_other_months=True),
        }
        previews["backends"] = {role: marketing_db_exports.summarise(rows)
                                for role, rows in backend_rows.items()}
    for role in ("demand", "supply"):
        other_role = "supply" if role == "demand" else "demand"
        prepared = marketing_analytics.prepare_cost_rows(
            rows, mappings[role], day, accounts,
            other_side_campaigns=[source for source, _target in mappings[other_role]],
            ignored_campaigns=marketing_analytics.IGNORED_CAMPAIGNS,
        )
        previews[role] = {"rows": len(prepared["rows"]), "unmapped": prepared["unmapped"]}
        if prepared["unmapped"]:
            raise RuntimeError(f"{role.title()} has unmapped campaigns: " + ", ".join(prepared["unmapped"][:20]))
        prepared_by_role[role] = prepared
    if os.getenv("MARKETING_ANALYTICS_WRITE", "").strip() == "1":
        live = os.getenv("MARKETING_ANALYTICS_TARGET", "").strip().lower() == "live"
        destinations = _live_destinations(day[:7]) if live else _test_destinations()
        if not backend_rows:
            raise RuntimeError("Backend exports are required before workbook writes")
        for role, target, _tab in destinations:
            written.append(marketing_db_exports.write_backend(target.id, role, backend_rows[role], live=live))
        # Campaign Cost is maintained in Demand only. Supply reads it from
        # Demand, so writing a second copy would create two sources of truth.
        for role, target, tab in destinations if not live else []:
            if role != "demand":
                continue
            values = [["Date", "Platform", "Source campaign", "Mapped campaign", "Cost"]]
            values.extend([[row[0], row[1], row[2], row[3], row[4]]
                           for row in prepared_by_role[role]["rows"]])
            written.append(marketing_analytics.write_cost_tab(target.id, tab, values))
        if live:
            demand_target = next(target for role, target, _tab in destinations if role == "demand")
            combined = prepared_by_role["demand"]["rows"] + prepared_by_role["supply"]["rows"]
            written.append(marketing_db_exports.append_live_campaign_cost(demand_target.id, day, combined))
    return {"date": day, "mode": "write" if written else "preview", "sources": sync_results,
            "previews": previews, "ignored_inactive_mappings": mappings["unclassified"],
            "written": written}


if __name__ == "__main__":
    try:
        print(json.dumps(run(), indent=2, default=str))
    except Exception as exc:
        print(f"Marketing Analytics automation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
