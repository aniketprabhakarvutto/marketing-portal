"""Reporting layer.

Plain functions over SQL, returning JSON-ready dicts. The logic lives here
rather than in the browser so the same numbers are available to a script, a
spreadsheet, or anything else you point at http://localhost:8787/api/.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import connections as conns
from .config import APP
from .db import query, scalar
from .providers import REGISTRY, table_for
from .providers.shape import channel_from_label, normalise_channel
from . import platform

PAID_SOURCES = ("google_ads", "meta_ads", "linkedin_ads", "tiktok_ads")

# When direct Superleap rows exist for a day they replace the older CSV/Sheet
# aggregates for that day. Dates without API rows keep the legacy backfill.
CRM_DAILY_FROM = """(SELECT d.* FROM crm_daily d
    LEFT JOIN connections c ON c.id=d.connection_id
    WHERE (c.id IS NULL OR c.enabled=1) AND (c.provider='superleap_crm'
       OR NOT EXISTS (SELECT 1 FROM crm_daily direct JOIN connections dc
                      ON dc.id=direct.connection_id AND dc.provider='superleap_crm' AND dc.enabled=1
                      WHERE direct.date=d.date))) AS crm_daily"""
CRM_CITY_DAILY_FROM = """(SELECT d.* FROM crm_city_daily d
    LEFT JOIN connections c ON c.id=d.connection_id
    WHERE (c.id IS NULL OR c.enabled=1) AND (c.provider='superleap_crm'
       OR NOT EXISTS (SELECT 1 FROM crm_city_daily direct JOIN connections dc
                      ON dc.id=direct.connection_id AND dc.provider='superleap_crm' AND dc.enabled=1
                      WHERE direct.date=d.date))) AS crm_city_daily"""
CRM_LEADS_FROM = """(SELECT d.* FROM crm_leads d
    LEFT JOIN connections c ON c.id=d.connection_id
    WHERE (c.id IS NULL OR c.enabled=1) AND (c.provider='superleap_crm'
       OR NOT EXISTS (SELECT 1 FROM crm_leads direct JOIN connections dc
                      ON dc.id=direct.connection_id AND dc.provider='superleap_crm' AND dc.enabled=1
                      WHERE direct.created_date=d.created_date))) AS crm_leads"""


# --------------------------------------------------------------------- helpers
def portal_today() -> date:
    """Return today's date in the reporting timezone, not the host's timezone."""
    try:
        return datetime.now(ZoneInfo(APP.timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return date.today()


def date_range(start: str | None, end: str | None) -> tuple[str, str]:
    today = portal_today()
    end_date = date.fromisoformat(end) if end else today - timedelta(days=1)
    end_date = min(end_date, today)
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=29)
    if start_date > end_date:
        raise ValueError("start date is after end date")
    return start_date.isoformat(), end_date.isoformat()


def _previous(s: str, e: str) -> tuple[str, str]:
    ds, de = date.fromisoformat(s), date.fromisoformat(e)
    span = (de - ds).days + 1
    return (ds - timedelta(days=span)).isoformat(), (ds - timedelta(days=1)).isoformat()


def _delta(cur: float | None, prev: float | None) -> float | None:
    return None if cur is None or not prev else round((cur - prev) / prev * 100, 1)


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 2) if b else None


def _campaign_key(value: str | None) -> str:
    """Match campaign names despite formatting and simple singular/plural variants.

    Ad platforms and CRM exports often disagree on a harmless final ``s`` —
    for example, ``Commuters`` in Meta and ``Commuter`` in Superleap.  Keeping
    the words first lets us make that limited normalisation without turning
    unrelated names into the same key.
    """
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    return "".join(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
                   for word in words)


def _lead_side(value: str | None) -> str:
    side = str(value or "").strip().lower()
    if side in {"buy", "demand"}:
        return "buy"
    if side in {"sell", "supply"}:
        return "sell"
    return side


def _crm_campaign_won_measured(provider: str | None, config: str | dict | None) -> bool:
    """Whether a campaign feed supplied sales, rather than a warehouse default zero."""
    if provider == "superleap_crm":
        return True  # Direct campaign sales are rolled up from CRM opportunity flags.
    try:
        settings = json.loads(config or "{}") if isinstance(config, str) else (config or {})
        mapping = settings.get("field_map") or {}
        mapping = json.loads(mapping) if isinstance(mapping, str) else mapping
        constants = settings.get("constants") or {}
        constants = json.loads(constants) if isinstance(constants, str) else constants
        return bool(mapping.get("won")) or "won" in constants
    except (AttributeError, TypeError, ValueError):
        return False


def _crm_campaign_totals_conflict(sources: Any) -> bool:
    """Compare leads everywhere, sales only between feeds that measure sales."""
    rows = list(sources)
    return (len({row["leads"] for row in rows}) > 1 or
            len({row["won"] for row in rows if row["won_measured"]}) > 1)


def _crm_campaign_reporting_rows(start: str, end: str) -> list[dict[str, Any]]:
    """One CRM campaign feed per day, channel, campaign and lead side.

    The campaign CSV and the Demand/Supply Sheets are alternate extracts of
    Superleap, not independent leads. Prefer direct API rows on overlaps; use
    legacy feeds where no matching campaign/day exists. Keep the chosen
    connection visible so a report can explain where each count came from.
    """
    source_rows = query(
        "SELECT d.date, d.channel, d.campaign_id, d.campaign_name, d.lead_type, "
        "d.leads, d.won, d.connection_id, c.provider, c.name source_name "
        "FROM crm_campaign_daily d LEFT JOIN connections c ON c.id = d.connection_id "
        "WHERE d.date BETWEEN ? AND ? AND (c.id IS NULL OR c.enabled=1) ORDER BY d.date, d.id", (start, end))
    grouped: dict[tuple[str, str, str, str], dict[int, dict[str, Any]]] = {}
    for row in source_rows:
        name_key = _campaign_key(row["campaign_name"])
        campaign_key = name_key or f"id:{str(row['campaign_id'] or '').strip()}"
        # No identifier means two unrelated blank campaigns cannot be
        # reconciled. Preserve them by row identity rather than merge them.
        if campaign_key == "id:":
            campaign_key = f"row:{row['date']}:{row['connection_id']}:{len(grouped)}"
        key = (row["date"], row["channel"], campaign_key, _lead_side(row["lead_type"]))
        sources = grouped.setdefault(key, {})
        connection_id = int(row["connection_id"])
        candidate = sources.setdefault(connection_id, {
            "date": row["date"], "channel": row["channel"],
            "campaign_key": campaign_key, "lead_side": key[3],
            "lead_type": row["lead_type"], "campaign_ids": set(),
            "campaign_names": set(), "leads": 0, "won": 0,
            "connection_id": connection_id, "provider": row["provider"] or "",
            "source": row["source_name"] or f"Connection {connection_id}",
        })
        if row["campaign_id"]:
            candidate["campaign_ids"].add(str(row["campaign_id"]).strip())
        if row["campaign_name"]:
            candidate["campaign_names"].add(str(row["campaign_name"]).strip())
        candidate["leads"] += int(row["leads"] or 0)
        candidate["won"] += int(row["won"] or 0)

    priority = {"superleap_crm": 0, "csv": 1, "google_sheets": 2}
    direct_keys = {
        (item["date"], item["campaign_key"], item["lead_side"])
        for sources in grouped.values() for item in sources.values()
        if item["provider"] == "superleap_crm"
    }
    selected = [min(sources.values(), key=lambda item: (
        priority.get(item["provider"], 2), item["connection_id"]))
        for sources in grouped.values()]
    # A legacy sheet may label a campaign "Other" while the direct CRM assigns
    # its real channel. The channel-specific grouping above would otherwise
    # count both copies. Only suppress an unclassified legacy row when the
    # exact date, campaign and lead side are present in the direct CRM.
    return [item for item in selected if not (
        item["provider"] != "superleap_crm"
        and str(item["channel"] or "").strip().lower() in {"other", "others", ""}
        and (item["date"], item["campaign_key"], item["lead_side"]) in direct_keys
    )]


# -------------------------------------------------------------------- snapshot
def _snapshot(s: str, e: str) -> tuple[dict[str, float | None], dict[str, bool], dict[str, dict[str, Any]]]:
    paid = query(
        "SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) lo, MAX(date) hi, "
        "COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency, "
        "COALESCE(SUM(spend),0) spend, COALESCE(SUM(impressions),0) impressions, "
        "COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(conversions),0) conv, "
        "COALESCE(SUM(conversion_value),0) val "
        "FROM paid_metrics WHERE date BETWEEN ? AND ?", (s, e)
    )[0]
    crm = query(
        "SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) lo, MAX(date) hi, "
        "COALESCE(SUM(leads),0) leads, COALESCE(SUM(qualified),0) qualified, "
        "COALESCE(SUM(won),0) sales, COALESCE(SUM(revenue),0) revenue, "
        "SUM(CASE WHEN revenue IS NOT NULL AND revenue != 0 THEN 1 ELSE 0 END) revenue_rows "
        "FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?", (s, e)
    )[0]
    web = query(
        "SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) lo, MAX(date) hi, "
        "COALESCE(SUM(sessions),0) sessions, COALESCE(SUM(conversions),0) conv "
        "FROM web_metrics WHERE date BETWEEN ? AND ?", (s, e)
    )[0]
    seo = query(
        "SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) lo, MAX(date) hi, "
        "COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(impressions),0) impressions "
        "FROM search_metrics WHERE date BETWEEN ? AND ?", (s, e)
    )[0]

    available = {
        "paid": bool(paid["n"]), "crm": bool(crm["n"]),
        "web": bool(web["n"]), "search": bool(seo["n"]),
    }
    total_days = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
    coverage = {
        source: {
            "days": int(row["days"] or 0), "total_days": total_days,
            "data_from": row["lo"], "data_to": row["hi"],
            "complete": int(row["days"] or 0) >= total_days,
        }
        for source, row in (("paid", paid), ("crm", crm), ("web", web), ("search", seo))
    }
    paid_currency_ok = (
        int(paid["currencies"] or 0) == 1
        and not int(paid["missing_currency"] or 0)
        and str(paid["currency"] or "").upper() == APP.currency.upper()
    )
    value = lambda row, key, source: float(row[key]) if available[source] else None
    t: dict[str, float | None] = {
        "spend": value(paid, "spend", "paid") if paid_currency_ok else None,
        "impressions": value(paid, "impressions", "paid"),
        "clicks": value(paid, "clicks", "paid"),
        "platform_conversions": value(paid, "conv", "paid"),
        "conversion_value": value(paid, "val", "paid") if paid_currency_ok else None,
        "leads": value(crm, "leads", "crm"),
        "qualified": value(crm, "qualified", "crm"),
        "sales": value(crm, "sales", "crm"),
        # A zero-filled CRM revenue column means the field was not mapped. Do
        # not present that as real zero revenue or a real 0x ROAS.
        "revenue": float(crm["revenue"]) if available["crm"] and crm["revenue_rows"] else None,
        "sessions": value(web, "sessions", "web"),
        "ga4_conversions": value(web, "conv", "web"),
        "organic_clicks": value(seo, "clicks", "search"),
        "organic_impressions": value(seo, "impressions", "search"),
    }
    t["cpl"] = _div(t["spend"], t["leads"])
    t["cac"] = _div(t["spend"], t["sales"])
    t["cpc"] = _div(t["spend"], t["clicks"])
    ctr = _div(t["clicks"], t["impressions"])
    t["ctr"] = ctr * 100 if ctr is not None else None
    t["roas"] = _div(t["revenue"], t["spend"])
    lead_to_sale = _div(t["sales"], t["leads"])
    t["lead_to_sale"] = lead_to_sale * 100 if lead_to_sale is not None else None
    return t, available, coverage


# ---------------------------------------------------------------------- routes
def meta() -> dict[str, Any]:
    bounds = query(
        "SELECT MIN(d) lo, MAX(d) hi FROM ("
        "  SELECT date d FROM paid_metrics WHERE date <= ? UNION ALL"
        "  SELECT date FROM " + CRM_DAILY_FROM + " WHERE date <= ? UNION ALL"
        "  SELECT date FROM web_metrics WHERE date <= ?"
        ")",
        (portal_today().isoformat(),) * 3,
    )[0]
    counts = {
        t: scalar(f"SELECT COUNT(*) FROM {t}")
        for t in ("paid_metrics", "web_metrics", "search_metrics", "ahrefs_metrics",
                  "crm_leads", "crm_daily", "crm_city_daily", "crm_campaign_daily")
    }
    today = portal_today()
    today_iso = today.isoformat()
    source_specs = (
        ("ahrefs", "Ahrefs", "SELECT MAX(date) FROM ahrefs_metrics WHERE date <= ?", 7),
        ("google_ads", "Google Ads", "SELECT MAX(date) FROM paid_metrics WHERE source = 'google_ads' AND date <= ?", 1),
        ("meta_ads", "Meta Ads", "SELECT MAX(date) FROM paid_metrics WHERE source = 'meta_ads' AND date <= ?", 1),
        ("ga4", "GA4", "SELECT MAX(date) FROM web_metrics WHERE date <= ?", 1),
        ("gsc", "Search Console", "SELECT MAX(date) FROM search_metrics WHERE date <= ?", 3),
        # Campaign-attributed leads and funnel stages have separate freshness
        # because legacy exports may update independently. A direct Superleap
        # API sync can refresh both feeds in one run.
        ("crm_campaign", "CRM campaign leads", "SELECT MAX(date) FROM crm_campaign_daily WHERE date <= ?", 1),
        ("crm", "Superleap CRM funnel", "SELECT MAX(date) FROM " + CRM_DAILY_FROM + " WHERE date <= ?", 1),
    )
    saved = connections()
    sources = []
    for source_id, label, sql, expected_lag in source_specs:
        through = scalar(sql, (today_iso,), default=None)
        age = (today - date.fromisoformat(through)).days if through else None
        if source_id in {"crm_campaign", "crm"}:
            target = "crm_campaign_daily" if source_id == "crm_campaign" else "crm_daily"
            related = [c for c in saved if (
                c["provider"] == "superleap_crm"
                or (c["provider"] in {"csv", "google_sheets"}
                    and c.get("config", {}).get("target_table") == target)
            )]
        else:
            related = [c for c in saved if c["provider"] == source_id]
        enabled = [c for c in related if c.get("enabled")]
        direct = [c for c in enabled if c["provider"] == "superleap_crm"]
        health_accounts = direct or enabled
        failed = [c for c in health_accounts if c.get("last_status") == "error"]
        empty_runs = [c for c in enabled if c.get("last_status") == "empty"]
        status = "empty" if age is None else ("current" if age <= expected_lag else "delayed")
        detail = None
        if related and not enabled:
            status, detail = "disabled", "Connection is turned off"
        elif failed:
            status = "error"
            detail = failed[0].get("last_message") or "Latest refresh failed"
        elif direct:
            latest_run = max(direct, key=lambda c: c.get("last_run_at") or "")
            run_at = latest_run.get("last_run_at") or ""
            try:
                last_sync = datetime.fromisoformat(str(run_at).replace("Z", "+00:00"))
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                sync_age = (today - last_sync.astimezone(ZoneInfo(APP.timezone)).date()).days
            except (ValueError, ZoneInfoNotFoundError):
                sync_age = None
            if latest_run.get("last_status") in {"ok", "empty"} and sync_age is not None and sync_age <= 1:
                if source_id == "crm_campaign":
                    tagged_through = scalar(
                        "SELECT MAX(date) FROM crm_campaign_daily WHERE connection_id = ? AND date <= ?",
                        (latest_run["id"], today_iso), default=None)
                    if tagged_through:
                        status = "current"
                        detail = f"Superleap API checked recently; campaign leads through {tagged_through}"
                    else:
                        status = "empty"
                        detail = "Superleap API is connected; no campaign-tagged opportunities are available yet"
                else:
                    status = "current" if through else "empty"
                    detail = (f"Superleap API checked recently; lead-created dates through {through}"
                              if through else "Superleap API checked recently; no opportunity rows are available")
        elif status == "delayed" and source_id == "ahrefs":
            detail = (f"No active Ahrefs connection; last saved snapshot {through}"
                      if not enabled else f"Latest Ahrefs result is dated {through}")
        elif status == "delayed" and source_id == "crm_campaign":
            detail = f"Connected Sheets and exports contain no campaign leads after {through}"
        elif status == "delayed" and source_id == "crm":
            detail = f"Detailed funnel export ends {through}; a newer CRM export is needed"
        if empty_runs and status in {"delayed", "empty"}:
            detail = ((detail + ". ") if detail else "") + (
                "Latest refresh returned no new rows for "
                + ", ".join(str(c["name"]) for c in empty_runs)
            )
        sources.append({
            "id": source_id,
            "label": label,
            "data_to": through,
            "age_days": age,
            "status": status,
            "detail": detail,
        })
    return {
        "currency": APP.currency,
        "timezone": APP.timezone,
        "demo_mode": APP.demo_mode,
        "data_from": bounds["lo"],
        "data_to": bounds["hi"],
        "row_counts": counts,
        "sources": sources,
        "scheduler_enabled": APP.scheduler_enabled,
        "sync_hour": APP.sync_hour,
        "topup_hours": APP.topup_hours,
        "sync_lookback_days": APP.sync_lookback_days,
    }


def providers() -> list[dict[str, Any]]:
    """Everything the portal can connect to, as the setup form consumes it."""
    from .providers import catalogue

    return catalogue()


def connections() -> list[dict[str, Any]]:
    """Saved accounts, with how each one's last sync went."""
    out = []
    for conn in conns.all_connections():
        provider = REGISTRY.get(conn.provider)
        card = conns.public(conn, provider)
        last = query(
            "SELECT * FROM sync_runs WHERE connection_id = ? ORDER BY id DESC LIMIT 1",
            (conn.id,),
        )
        ok = query(
            "SELECT started_at FROM sync_runs WHERE connection_id = ? AND status = 'ok' "
            "ORDER BY id DESC LIMIT 1", (conn.id,),
        )
        row = last[0] if last else None
        card.update({
            "last_status": row["status"] if row else "never",
            "last_message": row["message"] if row else "",
            "last_run_at": row["started_at"] if row else None,
            "last_success_at": ok[0]["started_at"] if ok else None,
            "rows_held": _rows_held(conn.id),
        })
        out.append(card)
    return out


def data_quality(start: str | None, end: str | None) -> dict[str, Any]:
    """Evidence-based quality checks for the selected reporting range."""
    s, e = date_range(start, end)
    checks: list[dict[str, Any]] = []
    saved = connections()
    enabled = [c for c in saved if c.get("enabled") and c.get("syncable", True)]
    failed = [c for c in enabled if c.get("last_status") == "error"]
    _, _, selected_coverage = _snapshot(s, e)
    source_tables = {
        "paid": {"paid_metrics"}, "crm": {"crm_daily", "crm_leads"},
        "web": {"web_metrics"}, "search": {"search_metrics"},
    }
    active_tables = set()
    for connection in enabled:
        provider = REGISTRY.get(connection["provider"])
        if provider:
            target = connection.get("config", {}).get("target_table") or provider.table
            active_tables.add(target)
            if connection["provider"] == "superleap_crm":
                active_tables.add("crm_daily")
    incomplete = [name for name, coverage in selected_coverage.items()
                  if source_tables[name] & active_tables and not coverage["complete"]]
    reporting_sources = [name for name, tables in source_tables.items() if tables & active_tables]
    checks.append({
        "id": "sync-health", "status": "fail" if failed else "warn" if incomplete or not reporting_sources else "pass",
        "title": "Selected-range coverage",
        "detail": (f"{len(failed)} enabled source(s) have a failed latest refresh: " +
                  ", ".join(c["name"] for c in failed)) if failed
                  else ("Incomplete data in this range: " + ", ".join(incomplete)) if incomplete
                  else "No reporting source is enabled; stored history may still be visible." if not reporting_sources
                  else "Every enabled reporting category has data on each day in this range.",
    })
    crm_to = scalar("SELECT MAX(date) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?", (s, e), default=None)
    crm_days = selected_coverage["crm"]["days"]
    total_days = selected_coverage["crm"]["total_days"]
    checks.append({
        "id": "crm-coverage", "status": "pass" if crm_days == total_days else "warn",
        "title": "CRM coverage",
        "detail": (f"CRM data covers {crm_days}/{total_days} days, through {crm_to}."
                   if crm_to else "No CRM data exists in this range."),
    })
    revenue_rows = int(scalar(
        "SELECT COUNT(*) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? AND revenue IS NOT NULL AND revenue != 0",
        (s, e), default=0) or 0)
    checks.append({
        "id": "revenue", "status": "pass" if revenue_rows else "warn",
        "title": "Revenue mapping",
        "detail": f"{revenue_rows:,} CRM rows contain revenue." if revenue_rows
                  else "Revenue is not mapped; CRM ROAS stays unavailable.",
    })
    campaign_revenue_rows = int(scalar(
        "SELECT COUNT(*) FROM " + CRM_LEADS_FROM + " WHERE created_date BETWEEN ? AND ? "
        "AND revenue != 0 AND TRIM(utm_campaign) != ''", (s, e), default=0) or 0)
    checks.append({
        "id": "campaign-revenue", "status": "pass" if campaign_revenue_rows else "warn",
        "title": "Campaign-attributed CRM revenue",
        "detail": (f"{campaign_revenue_rows:,} CRM leads have both revenue and a campaign tag."
                   if campaign_revenue_rows else
                   "No CRM lead rows have both revenue and a campaign tag. CRM ROAS and revenue per lead stay unavailable."),
    })
    currency = query(
        "SELECT COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency, COUNT(*) rows "
        "FROM paid_metrics WHERE date BETWEEN ? AND ?", (s, e),
    )[0]
    currency_ok = (
        not int(currency["rows"] or 0)
        or (
            int(currency["currencies"] or 0) == 1
            and not int(currency["missing_currency"] or 0)
            and str(currency["currency"] or "").upper() == APP.currency.upper()
        )
    )
    checks.append({
        "id": "currency", "status": "pass" if currency_ok else "warn",
        "title": "Paid-data currency",
        "detail": (f"Paid amounts use {APP.currency}, the configured reporting currency." if currency_ok
                   else "Paid amounts use mixed, missing, or non-reporting currencies. Financial totals and rates are withheld until accounts use one currency or an FX policy is configured."),
    })
    duplicate_leads = int(scalar(
        "SELECT COALESCE(SUM(n-1),0) FROM ("
        "SELECT COUNT(*) n FROM crm_leads d LEFT JOIN connections c "
        "ON c.id=d.connection_id WHERE d.created_date BETWEEN ? AND ? AND (c.id IS NULL OR c.enabled=1) "
        "GROUP BY d.external_id HAVING COUNT(DISTINCT d.connection_id) > 1)",
        (s, e), default=0) or 0)
    lead_rows = int(scalar(
        "SELECT COUNT(*) FROM crm_leads d LEFT JOIN connections c "
        "ON c.id=d.connection_id WHERE d.created_date BETWEEN ? AND ? AND (c.id IS NULL OR c.enabled=1)",
        (s, e), default=0) or 0)
    checks.append({
        "id": "duplicates", "status": "warn" if duplicate_leads or not lead_rows else "pass",
        "title": "Potential cross-source CRM duplicates",
        "detail": (f"{duplicate_leads:,} lead record(s) share an external ID across multiple CRM connections. "
                   "Review the source rows; records are not automatically merged or removed."
                   if duplicate_leads else
                   "Not checked: no lead-level CRM records or external IDs exist in this range."
                   if not lead_rows else
                   "No external lead IDs repeat across CRM connections in this range."),
    })
    invalid_rows = 0
    for side_row in query(
        "SELECT DISTINCT lead_type FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?",
        (s, e),
    ):
        invalid_rows += _invalid_funnel_rows(s, e, side_row["lead_type"])
    funnel_rows = int(scalar(
        "SELECT COUNT(*) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?",
        (s, e), default=0) or 0)
    unqualified_supply = int(scalar(
        "SELECT COUNT(*) FROM " + CRM_LEADS_FROM + " WHERE created_date BETWEEN ? AND ? "
        "AND lead_type = 'sell' AND is_qualified = 0 "
        "AND (is_visit_booked = 1 OR is_visit_done = 1 OR is_sale = 1)",
        (s, e), default=0) or 0)
    supply_unit = "opportunity" if unqualified_supply == 1 else "opportunities"
    qualification_detail = (
        f" {unqualified_supply:,} supply {supply_unit} reached inspection or procurement "
        "but are not marked qualified in Superleap."
        if unqualified_supply else ""
    )
    checks.append({
        "id": "funnel-order", "status": "fail" if invalid_rows else "warn" if not funnel_rows else "pass",
        "title": "CRM funnel stage order",
        "detail": (f"{invalid_rows:,} grouped CRM row(s) have a later stage larger than an earlier one."
                   + qualification_detail +
                   " Check the source qualification field; the portal does not invent missing stages."
                   if invalid_rows else "Not checked: no detailed CRM funnel rows in this range."
                   if not funnel_rows else "No CRM rows violate the configured funnel stage order."),
    })
    campaign_rows = query(
        "SELECT d.date, d.channel, d.campaign_name, d.lead_type, d.connection_id, "
        "SUM(d.leads) leads, SUM(d.won) won, c.provider, c.config "
        "FROM crm_campaign_daily d LEFT JOIN connections c ON c.id=d.connection_id "
        "WHERE d.date BETWEEN ? AND ? AND (c.id IS NULL OR c.enabled=1) "
        "GROUP BY d.date, d.channel, d.campaign_name, d.lead_type, d.connection_id", (s, e))
    overlap: dict[tuple[str, str, str, str], dict[int, dict[str, Any]]] = {}
    for row in campaign_rows:
        key = (row["date"], row["channel"], _campaign_key(row["campaign_name"]),
               _lead_side(row["lead_type"]))
        if key[2]:
            totals = overlap.setdefault(key, {}).setdefault(int(row["connection_id"]), {
                "leads": 0, "won": 0,
                "won_measured": _crm_campaign_won_measured(row["provider"], row["config"]),
            })
            totals["leads"] += int(row["leads"] or 0)
            if totals["won_measured"]:
                totals["won"] += int(row["won"] or 0)
    overlapping_groups = sum(len(sources) > 1 for sources in overlap.values())
    conflicting_groups = sum(
        len(sources) > 1 and _crm_campaign_totals_conflict(sources.values())
        for sources in overlap.values()
    )
    identical_groups = overlapping_groups - conflicting_groups
    checks.append({
        "id": "crm-feed-overlap", "status": "warn" if conflicting_groups or not campaign_rows else "pass",
        "title": "Overlapping CRM campaign feeds",
        "detail": (f"{conflicting_groups:,} campaign/day/lead-side groups disagree on leads or measured sales "
                   f"across stored feeds; {identical_groups:,} are identical on comparable fields. "
                   "Reports select one preferred feed per group. Reconcile the conflicting source definitions."
                   if conflicting_groups else
                   f"{identical_groups:,} overlapping campaign groups are identical on comparable fields "
                   "and reports select one feed."
                   if identical_groups else "Not checked: no CRM campaign rows exist in this range."
                   if not campaign_rows else "No campaign/day/lead-side groups overlap across connections."),
    })
    ambiguous_channels = sum(
        str(row["channel"] or "").strip().lower() in {"other", "others", ""}
        for row in _crm_campaign_reporting_rows(s, e)
    )
    checks.append({
        "id": "channels", "status": "warn" if ambiguous_channels else "pass",
        "title": "Other CRM channels",
        "detail": f"{ambiguous_channels:,} reported CRM campaign group(s) have an unmapped channel. Check source channel fields before attributing them to paid platforms." if ambiguous_channels
                  else "All campaign rows have a specific channel.",
    })
    # A warning without a next action makes the quality card a dead end.  Keep
    # the remediation separate from the evidence above: it tells the operator
    # what to do, but never changes or masks source facts.
    remediation = {
        "sync-health": ("Open Connections and test or re-authorise the failed source before syncing again.", "connections", "Open Connections"),
        "crm-coverage": ("Confirm the CRM source is enabled, then sync it. If the source itself has no records for a day, correct that at the source.", "connections", "Open Connections"),
        "revenue": ("A CRM owner must map the confirmed sale or procurement revenue field. Do not use estimated resale or procurement values as actual revenue.", "crm", "Open CRM funnel"),
        "campaign-revenue": ("A CRM owner must map confirmed revenue and keep the campaign tag on the same lead record before campaign ROAS can be calculated.", "crm", "Open CRM funnel"),
        "currency": ("Set every paid account to the reporting currency or define an approved FX policy before using financial totals.", "connections", "Open Connections"),
        "duplicates": ("Compare the duplicate source records and choose the authoritative feed. Do not delete leads merely to clear this check.", "connections", "Open Connections"),
        "funnel-order": ("A Superleap CRM owner must correct or explain the qualification status. The portal keeps raw stage counts unchanged until the source definition is confirmed.", "crm", "Open CRM funnel"),
        "crm-feed-overlap": ("A CRM owner must reconcile the direct CRM and Sheet definitions. Reports already prefer direct CRM where the records can be matched.", "connections", "Open Connections"),
        "channels": ("Classify the source rows marked Other in the originating Sheet or CRM. Do not guess a paid platform from a campaign name.", "connections", "Open Connections"),
    }
    for check in checks:
        message, route, label = remediation[check["id"]]
        check["next_step"] = message
        check["action_route"] = route
        check["action_label"] = label
    return {"range": {"start": s, "end": e}, "checks": checks,
            "summary": {status: sum(1 for item in checks if item["status"] == status)
                        for status in ("pass", "warn", "fail")}}


def _rows_held(connection_id: int) -> int:
    from .db import PROVENANCE_TABLES

    return sum(
        scalar(f"SELECT COUNT(*) FROM {t} WHERE connection_id = ?", (connection_id,))
        for t in PROVENANCE_TABLES
    )


def source_view(connection_id: int, start: str | None, end: str | None,
                limit: int = 100, offset: int = 0, search: str = "") -> dict[str, Any]:
    """A safe, generic detail view for any saved connection.

    Connector pages are generated from the table a provider writes to, so a
    newly installed provider gets a useful portal page without front-end work.
    """
    from .db import PROVENANCE_TABLES

    conn = conns.get(connection_id)
    if not conn:
        raise KeyError(f"no connection with id {connection_id}")
    provider = REGISTRY.get(conn.provider)
    if not provider:
        raise KeyError(f"unknown provider: {conn.provider}")
    table = table_for(provider, conn)
    if table not in PROVENANCE_TABLES:
        raise ValueError(f"The provider table '{table}' cannot be displayed.")

    s, e = date_range(start, end)
    column_rows = query(f"PRAGMA table_info({table})")
    columns = [dict(row) for row in column_rows
               if row["name"] not in {"id", "connection_id"}]
    column_names = {row["name"] for row in column_rows}
    date_column = "created_date" if "created_date" in column_names else "date"
    where = "connection_id = ?"
    params: list[Any] = [connection_id]
    if date_column in column_names:
        where += f" AND {date_column} BETWEEN ? AND ?"
        params.extend([s, e])
    text_columns = [r["name"] for r in column_rows if str(r["type"] or "").upper().startswith("TEXT")]
    if search.strip() and text_columns:
        where += " AND (" + " OR ".join(f"{name} LIKE ?" for name in text_columns) + ")"
        params.extend([f"%{search.strip()}%"] * len(text_columns))
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    rows = [dict(row) for row in query(
        f"SELECT * FROM {table} WHERE {where} "
        f"ORDER BY {date_column if date_column in column_names else 'id'} DESC, id DESC LIMIT ? OFFSET ?",
        (*params, safe_limit, safe_offset),
    )]

    total = int(scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", params) or 0)
    sum_candidates = (
        "spend", "impressions", "reach", "clicks", "conversions", "conversion_value",
        "sessions", "total_users", "new_users", "engaged_sessions", "leads",
        "contacted", "qualified", "booked", "completed", "won", "revenue",
        "organic_keywords", "organic_traffic", "live_backlinks", "live_refdomains",
    )
    metric_names = [name for name in sum_candidates if name in column_names]
    totals: dict[str, float] = {}
    if metric_names:
        expressions = ",".join(f"COALESCE(SUM({name}),0) AS {name}" for name in metric_names)
        summary = query(f"SELECT {expressions} FROM {table} WHERE {where}", params)[0]
        totals = {name: summary[name] for name in metric_names}

    card = next((item for item in connections() if item["id"] == connection_id),
                conns.public(conn, provider))
    return {
        "connection": card, "table": table, "date_column": date_column,
        "range": {"start": s, "end": e}, "row_count": total,
        "shown": len(rows), "offset": safe_offset, "search": search,
        "columns": columns, "rows": rows, "totals": totals,
    }


def _provider_label(provider_id: str) -> str:
    provider = REGISTRY.get(provider_id)
    return provider.label if provider else provider_id


def syncs(limit: int = 30) -> list[dict[str, Any]]:
    """Recent pull attempts, for connections that still exist.

    Runs from a connection that has since been removed — or from an older
    configuration entirely — describe a setup that is no longer there, so
    showing them just sends you looking for settings that don't exist.
    """
    rows = query(
        "SELECT r.* FROM sync_runs r "
        "JOIN connections c ON c.id = r.connection_id "
        "ORDER BY r.id DESC LIMIT ?", (min(limit, 200),))
    return [
        {
            "id": r["id"], "connector": r["connector"],
            "connection_id": r["connection_id"],
            "label": _provider_label(r["connector"]),
            "status": r["status"], "rows": r["rows_written"],
            "started_at": r["started_at"], "finished_at": r["finished_at"],
            "message": r["message"],
        }
        for r in rows
    ]


def paid_platform_totals(start: str | None, end: str | None, source: str) -> dict[str, Any]:
    """Return precise totals for one paid provider without blending currencies."""
    if source not in PAID_SOURCES:
        raise ValueError("Unsupported paid platform.")
    s, e = date_range(start, end)
    row = query(
        "SELECT COUNT(*) n, COUNT(DISTINCT date) days, MIN(date) lo, MAX(date) hi, "
        "COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency, SUM(spend) spend, SUM(conversion_value) conversion_value, "
        "SUM(impressions) impressions, SUM(clicks) clicks, SUM(conversions) conversions "
        "FROM paid_metrics WHERE source = ? AND date BETWEEN ? AND ?", (source, s, e)
    )[0]
    available = bool(row["n"])
    total_days = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1
    currency_ok = (
        int(row["currencies"] or 0) == 1
        and not int(row["missing_currency"] or 0)
        and str(row["currency"] or "").upper() == APP.currency.upper()
    )
    return {
        "range": {"start": s, "end": e},
        "currency": str(row["currency"] or "").upper() or None,
        "availability": available,
        "coverage": {
            "days": int(row["days"] or 0), "total_days": total_days,
            "data_from": row["lo"], "data_to": row["hi"],
            "complete": int(row["days"] or 0) >= total_days,
        },
        "spend": float(row["spend"]) if available and currency_ok else None,
        "conversion_value": float(row["conversion_value"] or 0) if available and currency_ok else None,
        "impressions": float(row["impressions"]) if available else None,
        "clicks": float(row["clicks"]) if available else None,
        "conversions": float(row["conversions"]) if available else None,
    }


def budget_pacing(start: str | None, end: str | None,
                  source: str | None = None) -> dict[str, Any]:
    """Compare paid spend with daily campaign budgets saved for each campaign-day.

    Ad set rows repeat their parent campaign budget, so collapse to one campaign
    budget per day before summing. Missing budgets reduce the stated coverage.
    """
    s, e = date_range(start, end)
    source_filter = " AND source = ?" if source in PAID_SOURCES else ""
    params = (s, e, source) if source in PAID_SOURCES else (s, e)
    raw = query(
        "SELECT date, source, account_id, "
        "COALESCE(NULLIF(TRIM(campaign_id), ''), NULLIF(TRIM(campaign_name), ''), "
        "NULLIF(TRIM(adset_id), ''), 'row:' || id) campaign_key, "
        "SUM(spend) spend, MAX(daily_budget) daily_budget "
        "FROM paid_metrics WHERE date BETWEEN ? AND ?" + source_filter + " "
        "GROUP BY date, source, account_id, campaign_key", params)
    currency = query(
        "SELECT COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency "
        "FROM paid_metrics WHERE date BETWEEN ? AND ?" + source_filter, params)[0]
    has_rows = bool(raw)
    spend = sum(float(row["spend"] or 0) for row in raw)
    budgeted = [row for row in raw if row["daily_budget"] is not None]
    planned = sum(float(row["daily_budget"] or 0) for row in budgeted)
    budgeted_spend = sum(float(row["spend"] or 0) for row in budgeted)
    currency_ok = (int(currency["currencies"] or 0) == 1
                   and not int(currency["missing_currency"] or 0)
                   and str(currency["currency"] or "").upper() == APP.currency.upper())
    coverage = budgeted_spend / spend * 100 if spend else 0.0
    value_ok = has_rows and currency_ok
    return {
        "range": {"start": s, "end": e},
        "currency": str(currency["currency"] or "").upper() or None,
        "availability": has_rows,
        "spend": spend if value_ok else None,
        "budgeted_spend": budgeted_spend if value_ok else None,
        "planned_budget": planned if value_ok and planned > 0 else None,
        "pacing_percent": budgeted_spend / planned * 100 if value_ok and planned > 0 else None,
        "budget_coverage_percent": coverage if value_ok else None,
        "campaign_days": len(raw),
        "budgeted_campaign_days": len(budgeted),
        "complete": bool(value_ok and len(raw) and len(budgeted) == len(raw)),
    }


def overview(start: str | None, end: str | None) -> dict[str, Any]:
    s, e = date_range(start, end)
    ps, pe = _previous(s, e)
    (cur, availability, coverage), (prev, previous_availability, previous_coverage) = _snapshot(s, e), _snapshot(ps, pe)
    metric_sources = {
        "spend": "paid", "impressions": "paid", "clicks": "paid",
        "platform_conversions": "paid", "conversion_value": "paid",
        "cpc": "paid", "ctr": "paid", "leads": "crm", "qualified": "crm",
        "sales": "crm", "revenue": "crm", "cpl": ("paid", "crm"),
        "cac": ("paid", "crm"), "lead_to_sale": "crm",
        "roas": ("paid", "crm"), "sessions": "web",
        "ga4_conversions": "web", "organic_clicks": "search",
        "organic_impressions": "search",
    }
    comparable = {
        source: bool(coverage[source]["complete"] and previous_coverage[source]["complete"])
        for source in coverage
    }
    return {
        "range": {"start": s, "end": e},
        "compare_range": {"start": ps, "end": pe},
        "current": cur,
        "previous": prev,
        "availability": availability,
        "coverage": coverage,
        "previous_availability": previous_availability,
        "previous_coverage": previous_coverage,
        "comparison_available": comparable,
        "delta": {
            k: _delta(cur[k], prev.get(k)) if all(
                comparable.get(source, False)
                for source in ((metric_sources.get(k),) if isinstance(metric_sources.get(k), str)
                               else metric_sources.get(k, ()))
            ) else None
            for k in cur
        },
    }


def timeseries(start: str | None, end: str | None) -> dict[str, Any]:
    s, e = date_range(start, end)
    days = []
    d, last = date.fromisoformat(s), date.fromisoformat(e)
    while d <= last:
        days.append(d.isoformat())
        d += timedelta(days=1)
    idx = {day: i for i, day in enumerate(days)}
    series: dict[str, list[float | None]] = {}

    def bucket(key: str) -> list[float | None]:
        # Null means the source has no row for that date. A real zero remains
        # zero, which prevents stale feeds from drawing a misleading plunge.
        return series.setdefault(key, [None] * len(days))

    for r in query(
        "SELECT date, source, SUM(spend) spend, SUM(clicks) clicks, SUM(conversions) conv, "
        "COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency "
        "FROM paid_metrics WHERE date BETWEEN ? AND ? GROUP BY date, source", (s, e)
    ):
        i = idx.get(r["date"])
        if i is None:
            continue
        compatible = (
            int(r["currencies"] or 0) == 1
            and not int(r["missing_currency"] or 0)
            and str(r["currency"] or "").upper() == APP.currency.upper()
        )
        bucket(f"spend_{r['source']}")[i] = round(float(r["spend"] or 0), 2) if compatible else None
        bucket(f"clicks_{r['source']}")[i] = float(r["clicks"] or 0)
        bucket(f"conv_{r['source']}")[i] = round(float(r["conv"] or 0), 2)

    for r in query(
        "SELECT date d, COALESCE(SUM(leads),0) leads, COALESCE(SUM(won),0) sales "
        "FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? GROUP BY date", (s, e)
    ):
        i = idx.get(r["d"])
        if i is not None:
            bucket("crm_leads")[i] = float(r["leads"])
            bucket("crm_sales")[i] = float(r["sales"])

    for r in query(
        "SELECT date, SUM(clicks) clicks FROM search_metrics "
        "WHERE date BETWEEN ? AND ? GROUP BY date", (s, e)
    ):
        i = idx.get(r["date"])
        if i is not None:
            bucket("organic_clicks")[i] = float(r["clicks"] or 0)

    return {"days": days, "series": series}


def channels(start: str | None, end: str | None) -> list[dict[str, Any]]:
    """The blended table — ad spend joined to CRM outcomes on a channel key.

    This is the number the hosted tools charge for: real cost per lead and cost
    per sale, because spend and outcomes live in the same database.
    """
    s, e = date_range(start, end)
    rows: dict[str, dict[str, Any]] = {}
    crm_available = bool(scalar(
        "SELECT COUNT(*) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?", (s, e)))
    revenue_available = bool(scalar(
        "SELECT COUNT(*) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? AND revenue != 0", (s, e)))

    def row(key: str) -> dict[str, Any]:
        return rows.setdefault(key, {
            "channel": key, "spend": 0.0, "impressions": 0, "clicks": 0,
            "platform_conversions": 0.0, "sessions": 0,
            "leads": 0, "qualified": 0, "sales": 0, "revenue": 0.0,
            "crm_available": crm_available, "is_paid": False,
            "revenue_available": revenue_available,
        })

    for r in query(
        "SELECT source, SUM(spend) spend, SUM(impressions) imp, SUM(clicks) clicks, "
        "SUM(conversions) conv, "
        "COUNT(DISTINCT CASE WHEN TRIM(currency) != '' THEN UPPER(TRIM(currency)) END) currencies, "
        "SUM(CASE WHEN TRIM(currency) = '' THEN 1 ELSE 0 END) missing_currency, "
        "MIN(UPPER(TRIM(currency))) currency "
        "FROM paid_metrics WHERE date BETWEEN ? AND ? GROUP BY source",
        (s, e),
    ):
        t = row(platform.transform("channel", r["source"]))
        t["is_paid"] = True
        compatible = (
            int(r["currencies"] or 0) == 1
            and not int(r["missing_currency"] or 0)
            and str(r["currency"] or "").upper() == APP.currency.upper()
        )
        t["spend"] = round(float(r["spend"] or 0), 2) if compatible else None
        t["currency_compatible"] = compatible
        t["impressions"] = int(r["imp"] or 0)
        t["clicks"] = int(r["clicks"] or 0)
        t["platform_conversions"] = round(float(r["conv"] or 0), 1)

    for r in query(
        "SELECT channel, COALESCE(SUM(leads),0) leads, COALESCE(SUM(qualified),0) qualified, "
        "COALESCE(SUM(won),0) sales, COALESCE(SUM(revenue),0) revenue "
        "FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? GROUP BY channel", (s, e)
    ):
        key = channel_from_label(r["channel"], {"others": "other", "superleap": "other"})
        key = platform.transform("channel", key)
        t = row(key)
        t["leads"] += int(r["leads"])
        t["qualified"] += int(r["qualified"])
        t["sales"] += int(r["sales"])
        t["revenue"] = round(t["revenue"] + float(r["revenue"] or 0), 2)

    for r in query(
        "SELECT session_source src, session_medium med, SUM(sessions) sessions "
        "FROM web_metrics WHERE date BETWEEN ? AND ? GROUP BY session_source, session_medium",
        (s, e),
    ):
        t = row(platform.transform("channel", normalise_channel(r["src"], r["med"])))
        t["sessions"] += int(r["sessions"] or 0)

    out = []
    for r in rows.values():
        # No ad spend means there is no cost per lead to report — an organic
        # channel shows a blank here, never a misleading zero.
        r["cpl"] = _ratio(r["spend"], r["leads"]) if r["spend"] else None
        r["cac"] = _ratio(r["spend"], r["sales"]) if r["spend"] else None
        r["cpc"] = (_ratio(r["spend"], r["clicks"])
                    if r["spend"] is not None and r["spend"] > 0 else None)
        r["lead_to_sale"] = round(r["sales"] / r["leads"] * 100, 1) if r["leads"] else None
        r["is_paid"] = bool(r["is_paid"] or r["channel"] in PAID_SOURCES)
        out.append(r)
    return sorted(out, key=lambda x: (-(x["spend"] or 0), -(x["leads"] or 0)))


def campaigns(start: str | None, end: str | None, source: str | None = None,
              limit: int | None = 200, offset: int = 0) -> list[dict[str, Any]]:
    s, e = date_range(start, end)
    if offset < 0:
        raise ValueError("Campaign offset cannot be negative")
    if source is not None and source not in PAID_SOURCES:
        raise ValueError("Unsupported paid campaign source")
    sql = (
        "SELECT source, account_id, account_name, campaign_id, campaign_name, channel, currency, "
        "MAX(campaign_status) campaign_status, MAX(daily_budget) daily_budget, "
        "MAX(bidding_strategy) bidding_strategy, SUM(spend) spend, "
        "SUM(impressions) imp, SUM(reach) reach, AVG(frequency) frequency, SUM(clicks) clicks, "
        "SUM(conversions) conv, SUM(all_conversions) all_conv, SUM(conversion_value) val, "
        "AVG(impression_share) impression_share, "
        "AVG(budget_lost_impression_share) budget_lost_impression_share, "
        "AVG(rank_lost_impression_share) rank_lost_impression_share, "
        "SUM(video_views) video_views, AVG(video_completion_rate) video_completion_rate "
        "FROM paid_metrics WHERE date BETWEEN ? AND ?"
    )
    params: list[Any] = [s, e]
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    sql += (
        " GROUP BY source, account_id, account_name, campaign_id, campaign_name, channel, currency"
        " ORDER BY spend DESC, source, account_id, campaign_id, campaign_name, channel, currency"
    )
    if limit is not None:
        if limit < 1:
            raise ValueError("Campaign limit must be positive")
        sql += " LIMIT ? OFFSET ?"
        params.extend((min(limit, 1000), offset))
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        params.append(offset)

    # Budgets are daily values, not period totals. Aggregate each campaign day
    # once so campaign-level pacing does not multiply a daily budget by the
    # selected calendar days or double-count it across ad sets.
    budget_daily = query(
        "SELECT source, account_id, account_name, campaign_id, campaign_name, channel, currency, date, "
        "SUM(spend) spend, MAX(daily_budget) daily_budget FROM paid_metrics "
        "WHERE date BETWEEN ? AND ?" + (" AND source = ?" if source is not None else "") +
        " GROUP BY source, account_id, account_name, campaign_id, campaign_name, channel, currency, date",
        (s, e, source) if source is not None else (s, e),
    )
    budget_totals: dict[tuple[str, str, str, str, str, str, str], dict[str, float | int]] = {}
    for day in budget_daily:
        key = (day["source"], day["account_id"], day["account_name"], day["campaign_id"],
               day["campaign_name"], day["channel"], day["currency"])
        totals = budget_totals.setdefault(key, {
            "period_budget": 0.0, "budgeted_spend": 0.0, "reported_days": 0,
            "budgeted_days": 0,
        })
        totals["reported_days"] += 1
        if day["daily_budget"] is not None and float(day["daily_budget"]) > 0:
            totals["period_budget"] += float(day["daily_budget"])
            totals["budgeted_spend"] += float(day["spend"] or 0)
            totals["budgeted_days"] += 1
    selected_days = (date.fromisoformat(e) - date.fromisoformat(s)).days + 1

    attribution_rows = _crm_campaign_reporting_rows(s, e)
    by_id: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_name: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in attribution_rows:
        keys = []
        for campaign_id in r["campaign_ids"]:
            keys.append((by_id, (r["channel"], campaign_id, r["lead_side"])))
        if not r["campaign_key"].startswith(("id:", "row:")):
            keys.append((by_name, (r["channel"], r["campaign_key"], r["lead_side"])))
        for bucket, key in keys:
            totals = bucket.setdefault(key, {"leads": 0, "sales": 0, "sources": set()})
            totals["leads"] += r["leads"]
            totals["sales"] += r["won"]
            totals["sources"].add(r["source"])

    crm_revenue_by_name: dict[tuple[str, str, str], float] = {}
    crm_revenue_by_id: dict[tuple[str, str, str], float] = {}
    for r in query(
        "SELECT channel, TRIM(utm_campaign) campaign, lead_type, COALESCE(SUM(revenue),0) revenue "
        "FROM " + CRM_LEADS_FROM + " WHERE created_date BETWEEN ? AND ? AND TRIM(utm_campaign) != '' "
        "GROUP BY channel, LOWER(TRIM(utm_campaign)), lead_type", (s, e),
    ):
        lead_side = _lead_side(r["lead_type"])
        campaign_value = str(r["campaign"] or "")
        revenue = float(r["revenue"] or 0)
        if re.fullmatch(r"\d+", campaign_value):
            crm_revenue_by_id[(r["channel"], campaign_value, lead_side)] = revenue
        crm_revenue_by_name[(r["channel"], _campaign_key(campaign_value), lead_side)] = revenue

    out = []
    for r in query(sql, params):
        spend = float(r["spend"] or 0)
        imp = float(r["imp"] or 0)
        clicks = float(r["clicks"] or 0)
        conv = float(r["conv"] or 0)
        campaign_name = (r["campaign_name"] or "").strip()
        lowered = campaign_name.lower()
        side = ("sell" if "supply" in lowered or "procurement" in lowered
                else "buy" if "demand" in lowered or "sale" in lowered else "")
        crm = (by_id.get((r["source"], r["campaign_id"], side))
               or by_name.get((r["source"], _campaign_key(campaign_name), side))
               or {})
        crm_leads = int(crm["leads"] if crm else 0)
        crm_sales = int(crm["sales"] if crm else 0)
        crm_revenue = crm_revenue_by_id.get((r["source"], r["campaign_id"], side))
        if crm_revenue is None:
            crm_revenue = crm_revenue_by_name.get((r["source"], _campaign_key(campaign_name), side))
        budget = budget_totals.get((r["source"], r["account_id"], r["account_name"],
                                    r["campaign_id"], r["campaign_name"], r["channel"], r["currency"]), {})
        period_budget = float(budget.get("period_budget", 0) or 0)
        budgeted_spend = float(budget.get("budgeted_spend", 0) or 0)
        budget_coverage_percent = (round(budgeted_spend / spend * 100, 1)
                                   if spend > 0 else None)
        out.append({
            "source": platform.transform("source", r["source"]), "account_id": r["account_id"], "account": r["account_name"],
            "campaign_id": r["campaign_id"], "campaign": platform.transform("campaign_name", campaign_name), "channel": platform.transform("channel", r["channel"]),
            "campaign_status": r["campaign_status"] or "",
            "daily_budget": round(float(r["daily_budget"]), 2) if r["daily_budget"] is not None else None,
            "period_budget": round(period_budget, 2) if period_budget > 0 else None,
            "budgeted_spend": round(budgeted_spend, 2) if budget.get("budgeted_days") else None,
            "reported_days": int(budget.get("reported_days", 0)),
            "budgeted_days": int(budget.get("budgeted_days", 0)),
            "selected_days": selected_days,
            "budget_coverage_percent": budget_coverage_percent,
            "pacing_percent": round(budgeted_spend / period_budget * 100, 1)
                              if period_budget > 0 else None,
            "bidding_strategy": r["bidding_strategy"] or "",
            "spend": round(spend, 2), "currency": str(r["currency"] or "").upper(),
            "impressions": int(imp), "clicks": int(clicks),
            "reach": int(r["reach"]) if r["reach"] is not None else None,
            "frequency": round(float(r["frequency"]), 2) if r["frequency"] is not None else None,
            "conversions": round(conv, 1), "conversion_value": round(float(r["val"] or 0), 2),
            "all_conversions": round(float(r["all_conv"]), 1) if r["all_conv"] is not None else None,
            "impression_share": round(float(r["impression_share"]), 2) if r["impression_share"] is not None else None,
            "budget_lost_impression_share": round(float(r["budget_lost_impression_share"]), 2) if r["budget_lost_impression_share"] is not None else None,
            "rank_lost_impression_share": round(float(r["rank_lost_impression_share"]), 2) if r["rank_lost_impression_share"] is not None else None,
            "video_views": int(r["video_views"]) if r["video_views"] is not None else None,
            "video_completion_rate": round(float(r["video_completion_rate"]), 2) if r["video_completion_rate"] is not None else None,
            "ctr": round(clicks / imp * 100, 2) if imp else None,
            "cpc": _ratio(spend, clicks),
            "cpa": _ratio(spend, conv),
            "cpl": _ratio(spend, crm_leads) if crm_leads else None,
            "cac": _ratio(spend, crm_sales) if crm_sales else None,
            "crm_leads": crm_leads,
            "crm_sales": crm_sales,
            "crm_leads_source": ", ".join(sorted(crm["sources"])) if crm else None,
            "crm_revenue": round(crm_revenue, 2) if crm_revenue is not None else None,
            "crm_revenue_currency": APP.currency.upper() if crm_revenue is not None else None,
        })
    return out


def search(start: str | None, end: str | None, dim: str = "query",
           limit: int = 50) -> list[dict[str, Any]]:
    s, e = date_range(start, end)
    col = "page" if dim == "page" else "query"
    rows = query(
        f"SELECT {col} k, SUM(clicks) clicks, SUM(impressions) imp, AVG(position) pos "
        f"FROM search_metrics WHERE date BETWEEN ? AND ? GROUP BY {col} "
        f"ORDER BY clicks DESC LIMIT ?", (s, e, min(limit, 500))
    )
    return [
        {
            "key": r["k"], "clicks": int(r["clicks"] or 0), "impressions": int(r["imp"] or 0),
            "ctr": round(float(r["clicks"] or 0) / float(r["imp"]) * 100, 2) if r["imp"] else 0,
            "position": round(float(r["pos"] or 0), 1),
        }
        for r in rows
    ]


def ahrefs(end: str | None = None) -> dict[str, Any]:
    """Latest Ahrefs snapshot on or before the selected reporting date."""
    report_end = end or portal_today().isoformat()
    rows = query(
        "SELECT * FROM ahrefs_metrics WHERE date <= ? ORDER BY date DESC, id DESC LIMIT 1",
        (report_end,),
    )
    if not rows:
        return {"available": False}
    r = rows[0]
    return {"available": True, **{
        key: r[key] for key in (
            "date", "target", "country", "domain_rating", "ahrefs_rank",
            "organic_keywords", "top3_keywords", "organic_traffic",
            "organic_traffic_value", "live_backlinks", "live_refdomains",
            "all_time_backlinks", "all_time_refdomains", "ingested_at",
        )
    }}


def web(start: str | None, end: str | None, dim: str = "channel") -> list[dict[str, Any]]:
    s, e = date_range(start, end)
    col = {
        "channel": "channel_group",
        "source": "session_source",
        "campaign": "session_campaign",
    }.get(dim, "channel_group")
    rows = query(
        f"SELECT {col} k, SUM(sessions) sessions, SUM(total_users) users, "
        f"SUM(engaged_sessions) engaged, SUM(conversions) conv "
        f"FROM web_metrics WHERE date BETWEEN ? AND ? GROUP BY {col} "
        f"ORDER BY sessions DESC LIMIT 100", (s, e)
    )
    return [
        {
            "key": r["k"], "sessions": int(r["sessions"] or 0), "users": int(r["users"] or 0),
            "engaged_sessions": int(r["engaged"] or 0),
            "conversions": round(float(r["conv"] or 0), 1),
            "engagement_rate": round(float(r["engaged"] or 0) / float(r["sessions"]) * 100, 1)
            if r["sessions"] else 0,
        }
        for r in rows
    ]


FUNNEL_COLUMNS = ("leads", "contacted", "qualified", "booked", "completed", "won")


def funnel_shape(lead_type: str | None = None) -> list[tuple[str, str]]:
    """(column, label) pairs in the order this business runs its funnel."""
    order = [c.strip() for c in APP.funnel_order.split(",") if c.strip() in FUNNEL_COLUMNS]
    labels = [l.strip() for l in APP.funnel_labels.split(",")]
    if not order:
        order = list(FUNNEL_COLUMNS)
    shape = [(col, labels[i] if i < len(labels) else col.title())
             for i, col in enumerate(order)
             if not (lead_type == "buy" and col == "qualified")]
    if lead_type == "sell":
        columns = [column for column, _ in shape]
        if "qualified" in columns and "contacted" in columns:
            qualified_index, contacted_index = columns.index("qualified"), columns.index("contacted")
            if qualified_index > contacted_index:
                qualified = shape.pop(qualified_index)
                shape.insert(contacted_index, qualified)
    return shape


def _invalid_funnel_rows(start: str, end: str, side: str,
                         channel: str | None = None) -> int:
    ordered = [column for column, _ in funnel_shape(side)]
    if len(ordered) < 2:
        return 0
    expression = " OR ".join(
        f"{later} > {earlier}" for earlier, later in zip(ordered, ordered[1:]))
    where = "date BETWEEN ? AND ? AND lead_type = ?"
    params: tuple[str, ...] = (start, end, side)
    if channel is not None:
        where += " AND channel = ?"
        params += (channel,)
    return int(scalar(
        f"SELECT COUNT(*) FROM {CRM_DAILY_FROM} WHERE {where} AND ({expression})",
        params, default=0) or 0)


def crm(start: str | None, end: str | None) -> dict[str, Any]:
    """Funnels, sources and cities — one funnel per lead type present in the data."""
    s, e = date_range(start, end)

    lead_types = [r["lead_type"] for r in query(
        "SELECT lead_type, SUM(leads) n FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? "
        "GROUP BY lead_type HAVING n > 0 ORDER BY n DESC", (s, e))]
    if not lead_types:
        lead_types = [""]

    funnels = []
    for side in lead_types:
        side_shape = funnel_shape(side)
        side_cols = ", ".join(f"COALESCE(SUM({c}),0) {c}" for c, _ in side_shape)
        row = query(
            f"SELECT {side_cols} FROM {CRM_DAILY_FROM} WHERE date BETWEEN ? AND ? AND lead_type = ?",
            (s, e, side),
        )[0]
        raw_counts = [int(row[col]) for col, _ in side_shape]
        invalid_row_count = _invalid_funnel_rows(s, e, side)
        invalid_order = bool(invalid_row_count) or any(
            later > earlier for earlier, later in zip(raw_counts, raw_counts[1:]))
        top = raw_counts[0] or 1
        # Never inflate a source count to make an invalid sequence look valid.
        # Keep zero stages visible when they explain the disagreement.
        stages = [
            {"stage": label, "count": raw_counts[i], "raw_count": raw_counts[i],
             "adjusted": False,
             "pct_of_leads": round(raw_counts[i] / top * 100, 1)}
            for i, (col, label) in enumerate(side_shape)
            if invalid_order or col == "leads" or raw_counts[i]
        ]
        funnels.append({
            "side": side or "all",
            "invalid_stage_order": invalid_order,
            "source_rows_out_of_order": invalid_row_count,
            "adjusted_for_stage_overlap": False,
            "label": (side.replace("_", " ").title() + " leads") if side else "Lead funnel",
            "stages": stages,
        })

    channel_funnels = []
    for channel, label in (("google_ads", "Google Ads leads"), ("meta_ads", "Meta Ads leads"),
                           ("linkedin_ads", "LinkedIn Ads leads"), ("tiktok_ads", "TikTok Ads leads")):
        for side in lead_types:
            side_shape = funnel_shape(side)
            side_cols = ", ".join(f"COALESCE(SUM({c}),0) {c}" for c, _ in side_shape)
            row = query(
                f"SELECT {side_cols} FROM {CRM_DAILY_FROM} "
                "WHERE date BETWEEN ? AND ? AND channel = ? AND lead_type = ?",
                (s, e, channel, side),
            )[0]
            raw_counts = [int(row[col]) for col, _ in side_shape]
            if not any(raw_counts):
                continue
            invalid_row_count = _invalid_funnel_rows(s, e, side, channel)
            invalid_order = bool(invalid_row_count) or any(
                later > earlier for earlier, later in zip(raw_counts, raw_counts[1:]))
            top = raw_counts[0] or 1
            stages = [
                {"stage": stage_label, "count": raw_counts[i], "raw_count": raw_counts[i],
                 "adjusted": False,
                 "pct_of_leads": round(raw_counts[i] / top * 100, 1)}
                for i, (col, stage_label) in enumerate(side_shape)
                if invalid_order or col == "leads" or raw_counts[i]
            ]
            side_label = {"buy": "demand", "sell": "supply"}.get(side, side or "all")
            channel_funnels.append({
                "channel": channel, "side": side,
                "label": label.replace(" leads", f" {side_label} leads"),
                "invalid_stage_order": invalid_order,
                "source_rows_out_of_order": invalid_row_count,
                "adjusted_for_stage_overlap": False,
                "stages": stages,
            })

    by_city = [
        {"key": r["city"] or "(unknown)", "leads": int(r["leads"]), "sales": int(r["won"])}
        for r in query(
            "SELECT city, COALESCE(SUM(leads),0) leads, COALESCE(SUM(won),0) won "
            "FROM " + CRM_CITY_DAILY_FROM + " WHERE date BETWEEN ? AND ? "
            "GROUP BY city ORDER BY leads DESC LIMIT 25", (s, e))
    ]
    by_type = [
        {"key": r["lead_type"] or "(unspecified)", "leads": int(r["leads"]),
         "sales": int(r["won"])}
        for r in query(
            "SELECT lead_type, COALESCE(SUM(leads),0) leads, COALESCE(SUM(won),0) won "
            "FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? "
            "GROUP BY lead_type ORDER BY leads DESC", (s, e))
    ]
    by_source = [
        {"key": r["source"] or "(unknown)", "channel": r["channel"],
         "leads": int(r["leads"]), "sales": int(r["won"])}
        for r in query(
            "SELECT source, channel, COALESCE(SUM(leads),0) leads, COALESCE(SUM(won),0) won "
            "FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ? "
            "GROUP BY source, channel ORDER BY leads DESC LIMIT 30", (s, e))
    ]
    campaign_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _crm_campaign_reporting_rows(s, e):
        key = (row["lead_side"], row["channel"])
        summary = campaign_groups.setdefault(key, {
            "lead_type": row["lead_side"] or "(unspecified)",
            "channel": row["channel"] or "(unknown)", "sources": set(),
            "campaign_keys": set(), "leads": 0, "data_to": row["date"],
        })
        summary["sources"].add(row["source"])
        summary["campaign_keys"].add(row["campaign_key"])
        summary["leads"] += row["leads"]
        summary["data_to"] = max(summary["data_to"], row["date"])
    campaign_leads = [
        {"lead_type": item["lead_type"], "channel": item["channel"],
         "source": ", ".join(sorted(item["sources"])),
         "campaigns": len(item["campaign_keys"]),
         "leads": item["leads"], "data_to": item["data_to"]}
        for item in sorted(campaign_groups.values(), key=lambda x: -x["leads"])
    ]
    return {
        "funnels": funnels,
        "channel_funnels": channel_funnels,
        "by_city": by_city,
        "by_type": by_type,
        "by_source": by_source,
        "campaign_leads": campaign_leads,
        "campaign_feed_policy": (
            "Matching Superleap campaign/day/lead-side rows are counted once. "
            "Direct Superleap API rows are preferred; CSV and Google Sheets fill "
            "campaigns or dates with no matching direct row. Source labels identify "
            "the selected feed. Matching by campaign name is approximate and cannot "
            "replace lead-level reconciliation."
        ),
        "campaign_data_to": scalar(
            "SELECT MAX(date) FROM crm_campaign_daily WHERE date BETWEEN ? AND ?",
            (s, e), default=None,
        ),
        "funnel_data_to": scalar(
            "SELECT MAX(date) FROM " + CRM_DAILY_FROM + " WHERE date BETWEEN ? AND ?",
            (s, e), default=None,
        ),
    }
