"""Turning arbitrary source records into warehouse rows.

The generic providers accept any JSON or CSV shape, so the mapping from "their
fields" to "our columns" has to be data rather than code. Everything here is
driven by settings on the connection.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- field access
def dig(record: Any, path: str) -> Any:
    """Read a dotted path out of nested dicts and lists: 'a.b.0.c'."""
    cur = record
    for part in str(path).split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# -------------------------------------------------------------------- coercion
def to_date(value, date_order: str = "") -> str:
    """Best-effort ISO date from whatever the source calls a date."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        # Epoch seconds or milliseconds.
        seconds = float(value) / 1000 if float(value) > 1e11 else float(value)
        try:
            return datetime.utcfromtimestamp(seconds).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(value).strip().replace("Z", "+00:00")
    slash_formats = ("%d/%m/%Y", "%m/%d/%Y")
    if date_order == "month_first":
        slash_formats = ("%m/%d/%Y", "%d/%m/%Y")
    for parse in (
        lambda t: datetime.fromisoformat(t).date(),
        lambda t: datetime.strptime(t[:10], "%Y-%m-%d").date(),
        *(lambda t, fmt=fmt: datetime.strptime(t[:10], fmt).date()
          for fmt in slash_formats),
        lambda t: datetime.strptime(t[:8], "%Y%m%d").date(),
    ):
        try:
            return parse(text).isoformat()
        except (ValueError, TypeError):
            continue
    return ""


def to_number(value) -> float:
    if value in (None, "", False):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def to_int(value) -> int:
    return int(to_number(value))


def to_flag(value) -> int:
    if isinstance(value, bool):
        return int(value)
    return int(str(value).strip().lower() in {"1", "true", "yes", "y", "won", "closed won"})


# ------------------------------------------------------------------- stage flags
def stage_matches(stage: str, spec: str) -> int:
    """True when a stage name contains any of the comma-separated tokens.

    Substring matching on purpose: every CRM spells its stages differently, and
    "Visit Booked — Home" should still count as a booked visit.
    """
    s = (stage or "").strip().lower()
    if not s:
        return 0
    return int(any(tok.strip() and tok.strip() in s for tok in str(spec).lower().split(",")))


# ------------------------------------------------------------ channel normalising
DEFAULT_CHANNEL_RULES: list[tuple[str, str, str]] = [
    # (channel, source tokens, medium tokens) — first match wins.
    ("google_ads", "google,googleads,google_ads,adwords,gads", "cpc,ppc,paid,paidsearch,paid_search"),
    ("meta_ads", "facebook,fb,instagram,ig,meta", "cpc,ppc,paid,paid_social,paidsocial"),
    ("linkedin_ads", "linkedin", "cpc,paid,paid_social,sponsored"),
    ("tiktok_ads", "tiktok", "cpc,paid,paid_social"),
    ("microsoft_ads", "bing,microsoft,msn", "cpc,ppc,paid"),
    ("organic_search", "", "organic"),
    ("email", "", "email,newsletter"),
    ("referral", "", "referral"),
    ("social_organic", "facebook,instagram,linkedin,tiktok,twitter,x,youtube", "social,organic_social"),
]


def normalise_channel(source: str, medium: str, rules: Iterable | None = None) -> str:
    """Collapse a source/medium pair into a channel key.

    This is the join between spend and outcomes, so it has to be the same on
    both sides. Rules are overridable per connection — every company labels its
    traffic differently.
    """
    clean = lambda value: re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    s = clean(source)
    m = clean(medium)
    source_aliases = {
        "googleads": "google_ads", "google_adwords": "google", "fb": "facebook",
        "facebook_com": "facebook", "instagram_com": "instagram", "ig": "instagram",
    }
    medium_aliases = {
        "paidsearch": "paid_search", "paid_social_media": "paid_social",
        "paidsocial": "paid_social", "social_paid": "paid_social",
        "cpm": "paid_social", "cost_per_mille": "paid_social",
    }
    s = source_aliases.get(s, s)
    m = medium_aliases.get(m, m)
    known_sources = {"google", "google_ads", "facebook", "instagram", "meta", "linkedin", "tiktok", "bing", "microsoft"}
    known_mediums = {"cpc", "ppc", "cpm", "paid", "paid_search", "paid_social", "organic", "email", "referral"}
    if s in known_mediums and m in known_sources:
        s, m = m, s
    if s.startswith("facebook") or s.startswith("instagram") or s.startswith("meta_"):
        s = "facebook"
    elif s.startswith("google_ads") or s.startswith("adwords"):
        s = "google_ads"
    for channel, sources, mediums in (rules or DEFAULT_CHANNEL_RULES):
        src_values = {clean(x) for x in str(sources).split(",") if x.strip()}
        med_values = {clean(x) for x in str(mediums).split(",") if x.strip()}
        src_ok = not sources or s in src_values
        med_ok = not mediums or m in med_values
        if src_ok and med_ok:
            return channel
    if s in {"direct", "(direct)", ""} and m in {"none", "(none)", ""}:
        return "direct"
    return f"{s or 'unknown'}/{m or 'none'}"


def channel_from_label(label: str, aliases: dict[str, str] | None = None) -> str:
    """Map a single source label (no medium) onto a channel key."""
    key = (label or "").strip().lower()
    if aliases and key in aliases:
        return aliases[key]
    return key or "other"


# ------------------------------------------------------------------- table shapes
TABLE_COLUMNS = {
    "paid_metrics": [
        "date", "source", "account_id", "account_name", "campaign_id", "campaign_name",
        "adset_id", "adset_name", "campaign_status", "daily_budget", "bidding_strategy",
        "channel", "impressions", "reach", "frequency", "clicks", "spend",
        "conversions", "all_conversions", "conversion_value", "impression_share",
        "budget_lost_impression_share", "rank_lost_impression_share", "video_views",
        "video_completion_rate", "currency",
    ],
    "web_metrics": [
        "date", "property_id", "channel_group", "session_source", "session_medium",
        "session_campaign", "sessions", "total_users", "new_users",
        "engaged_sessions", "conversions",
    ],
    "search_metrics": [
        "date", "site_url", "query", "page", "country", "device",
        "clicks", "impressions", "position",
    ],
    "crm_leads": [
        "external_id", "created_date", "utm_source", "utm_medium", "utm_campaign",
        "channel", "lead_type", "city", "store", "stage", "is_contacted",
        "is_qualified", "is_visit_booked", "is_visit_done", "is_sale", "revenue",
    ],
    "crm_daily": [
        "date", "channel", "source", "lead_type", "leads", "contacted",
        "qualified", "booked", "completed", "won", "revenue",
    ],
    "crm_city_daily": [
        "date", "city", "lead_type", "leads", "won",
    ],
    "crm_campaign_daily": [
        "date", "channel", "campaign_id", "campaign_name", "lead_type", "leads", "won",
    ],
}

NUMERIC = {"impressions", "reach", "clicks", "video_views", "sessions", "total_users", "new_users",
           "engaged_sessions", "leads", "contacted", "qualified", "booked",
           "completed", "won"}
DECIMAL = {"spend", "daily_budget", "frequency", "conversions", "all_conversions",
           "conversion_value", "impression_share", "budget_lost_impression_share",
           "rank_lost_impression_share", "video_completion_rate", "position", "revenue"}
FLAGS = {"is_contacted", "is_qualified", "is_visit_booked", "is_visit_done", "is_sale"}
DATES = {"date", "created_date"}


def build_row(record: Any, table: str, mapping: dict[str, str],
              constants: dict[str, Any] | None = None,
              date_order: str = "") -> dict:
    """Assemble one warehouse row from a source record and a field mapping."""
    row: dict[str, Any] = {}
    for column in TABLE_COLUMNS[table]:
        if constants and column in constants:
            value = constants[column]
        elif column in mapping:
            value = dig(record, mapping[column])
        else:
            value = None

        if column in DATES:
            row[column] = to_date(value, date_order)
        elif column in NUMERIC:
            row[column] = to_int(value)
        elif column in DECIMAL:
            row[column] = to_number(value)
        elif column in FLAGS:
            row[column] = to_flag(value)
        else:
            row[column] = "" if value is None else str(value)
    return row
