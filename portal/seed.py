"""Sample data.

So the portal is a working product the moment you start it, before a single
account is connected. Generates 90 days of plausible, deliberately generic
marketing data — no company, industry or region implied. Every seeded row
carries a DEMO- marker, and `portal reset-demo` removes them without touching
anything real.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from .db import execute, query, scalar, upsert

log = logging.getLogger("portal.seed")
DAYS = 90
rng = random.Random(20260905)

GOOGLE_CAMPAIGNS = [
    ("DEMO-Search | Core terms", "SEARCH", 0.34),
    ("DEMO-Search | Long tail", "SEARCH", 0.22),
    ("DEMO-Performance Max | Catalogue", "PERFORMANCE_MAX", 0.24),
    ("DEMO-Search | Brand", "SEARCH", 0.08),
    ("DEMO-Demand Gen | Awareness", "DEMAND_GEN", 0.12),
]
META_CAMPAIGNS = [
    ("DEMO-Lead gen | Main offer", "OUTCOME_LEADS", 0.40),
    ("DEMO-Traffic | Catalogue carousel", "OUTCOME_TRAFFIC", 0.25),
    ("DEMO-Lead gen | Retargeting", "OUTCOME_LEADS", 0.25),
    ("DEMO-Awareness | Reach", "OUTCOME_AWARENESS", 0.10),
]
QUERIES = [
    "example product", "example product reviews", "example product price",
    "buy example product online", "best example product 2026", "example brand",
    "example product near me", "example product alternatives",
    "cheap example product", "example product delivery",
]
PAGES = ["/", "/products", "/pricing", "/products/category-a", "/products/category-b",
         "/locations", "/blog/buyers-guide"]
CITIES = ["North region", "South region", "East region", "West region", "Central region"]
STORES = ["Branch 1", "Branch 2", "Branch 3", "Branch 4"]
STAGES = [
    ("New", 0.30), ("Contacted", 0.20), ("Qualified", 0.16),
    ("Booked", 0.13), ("Completed", 0.11), ("Won", 0.10),
]


def _weekday_factor(d: date) -> float:
    return {5: 1.18, 6: 1.10}.get(d.weekday(), 1.0)


def _seasonality(i: int) -> float:
    return 1.0 + 0.25 * (i / DAYS)  # gentle growth across the window


def _paid_rows(start: date) -> list[dict]:
    rows = []
    for i in range(DAYS):
        d = start + timedelta(days=i)
        factor = _weekday_factor(d) * _seasonality(i) * rng.uniform(0.85, 1.15)
        for src, campaigns, daily_budget, cpc_base in (
            ("google_ads", GOOGLE_CAMPAIGNS, 42000, 14.0),
            ("meta_ads", META_CAMPAIGNS, 26000, 6.5),
        ):
            for idx, (name, channel, share) in enumerate(campaigns):
                spend = daily_budget * share * factor * rng.uniform(0.9, 1.1)
                cpc = cpc_base * rng.uniform(0.75, 1.3)
                clicks = max(1, int(spend / cpc))
                ctr = rng.uniform(0.018, 0.075) if src == "google_ads" else rng.uniform(0.008, 0.022)
                impressions = int(clicks / ctr)
                cvr = rng.uniform(0.035, 0.11) if "Leadgen" in name or "Search" in name else rng.uniform(0.01, 0.04)
                conversions = round(clicks * cvr, 1)
                rows.append(
                    {
                        "date": d.isoformat(), "source": src,
                        "account_id": "DEMO-1234567890" if src == "google_ads" else "DEMO-act_998877",
                        "account_name": "DEMO Example Account",
                        "campaign_id": f"DEMO{src[:1].upper()}{idx:03d}",
                        "campaign_name": name, "adset_id": "", "adset_name": "",
                        "channel": channel,
                        "impressions": impressions, "clicks": clicks,
                        "spend": round(spend, 2), "conversions": conversions,
                        "conversion_value": round(conversions * rng.uniform(1200, 2600), 2),
                        "currency": "INR",
                    }
                )
    return rows


def _web_rows(start: date) -> list[dict]:
    mix = [
        ("Paid Search", "google", "cpc", "DEMO-Search | Core terms", 0.24),
        ("Paid Social", "facebook", "paid_social", "DEMO-Lead gen | Main offer", 0.18),
        ("Organic Search", "google", "organic", "(not set)", 0.31),
        ("Direct", "(direct)", "(none)", "(not set)", 0.17),
        ("Referral", "partner-site.example", "referral", "(not set)", 0.06),
        ("Organic Social", "instagram", "social", "(not set)", 0.04),
    ]
    rows = []
    for i in range(DAYS):
        d = start + timedelta(days=i)
        total = int(3800 * _weekday_factor(d) * _seasonality(i) * rng.uniform(0.88, 1.12))
        for ch, src, med, camp, share in mix:
            sessions = int(total * share * rng.uniform(0.9, 1.1))
            engaged = int(sessions * rng.uniform(0.42, 0.68))
            rows.append(
                {
                    "date": d.isoformat(), "property_id": "DEMO-000000000",
                    "channel_group": ch, "session_source": src,
                    "session_medium": med, "session_campaign": camp,
                    "sessions": sessions,
                    "total_users": int(sessions * rng.uniform(0.78, 0.94)),
                    "new_users": int(sessions * rng.uniform(0.45, 0.7)),
                    "engaged_sessions": engaged,
                    "conversions": round(sessions * rng.uniform(0.02, 0.06), 1),
                }
            )
    return rows


def _search_rows(start: date) -> list[dict]:
    rows = []
    for i in range(DAYS):
        d = start + timedelta(days=i)
        for q in QUERIES:
            for page in rng.sample(PAGES, 2):
                impressions = int(rng.uniform(80, 2400) * _seasonality(i))
                position = rng.uniform(2.2, 28.0)
                ctr = max(0.004, 0.32 * (1 / max(1.0, position)) ** 0.65)
                rows.append(
                    {
                        "date": d.isoformat(), "site_url": "sc-domain:demo-example.com",
                        "query": q, "page": f"https://demo-example.com{page}",
                        "device": rng.choice(["MOBILE", "MOBILE", "DESKTOP", "TABLET"]),
                        "country": "ind",
                        "clicks": int(impressions * ctr),
                        "impressions": impressions,
                        "position": round(position, 1),
                    }
                )
    return rows


def _crm_rows(start: date) -> list[dict]:
    attribution = [
        ("google", "cpc", "DEMO-Search | Core terms", 0.26),
        ("google", "cpc", "DEMO-Search | Long tail", 0.15),
        ("facebook", "paid_social", "DEMO-Lead gen | Main offer", 0.21),
        ("google", "organic", "(not set)", 0.16),
        ("(direct)", "(none)", "(not set)", 0.14),
        ("partner-site.example", "referral", "(not set)", 0.08),
    ]
    stage_names = [s for s, _ in STAGES]
    stage_weights = [w for _, w in STAGES]
    rows, seq = [], 0
    for i in range(DAYS):
        d = start + timedelta(days=i)
        n = int(rng.uniform(110, 190) * _weekday_factor(d) * _seasonality(i))
        for _ in range(n):
            seq += 1
            src, med, camp, _w = rng.choices(
                attribution, weights=[a[3] for a in attribution], k=1
            )[0]
            stage = rng.choices(stage_names, weights=stage_weights, k=1)[0]
            order = stage_names.index(stage)
            is_sale = stage == "Won"
            from .providers.shape import normalise_channel

            rows.append(
                {
                    "external_id": f"DEMO-LEAD-{seq:07d}",
                    "created_date": d.isoformat(),
                    "utm_source": src, "utm_medium": med, "utm_campaign": camp,
                    "channel": normalise_channel(src, med),
                    "lead_type": rng.choices(["new", "returning"], weights=[0.58, 0.42], k=1)[0],
                    "city": rng.choice(CITIES), "store": rng.choice(STORES),
                    "stage": stage,
                    "is_contacted": int(order >= 1), "is_qualified": int(order >= 2),
                    "is_visit_booked": int(order >= 3), "is_visit_done": int(order >= 4),
                    "is_sale": int(is_sale),
                    "revenue": round(rng.uniform(48000, 132000), 0) if is_sale else 0.0,
                }
            )
    return rows


# Every table sample data would touch. If any of them already holds something,
# this warehouse is in use and must not be seeded — an aggregate CRM import
# leaves crm_leads empty, so checking that alone would wrongly look empty.
OCCUPIED_TABLES = ("paid_metrics", "web_metrics", "search_metrics",
                   "crm_leads", "crm_daily", "crm_city_daily")


def seed_if_empty() -> bool:
    """Populate sample data only when the warehouse is completely empty."""
    if any(scalar(f"SELECT COUNT(*) FROM {t}") for t in OCCUPIED_TABLES):
        return False
    start = date.today() - timedelta(days=DAYS)
    log.info("seeding %s days of demo data (DEMO_MODE=true)", DAYS)
    upsert("paid_metrics", _paid_rows(start))
    upsert("web_metrics", _web_rows(start))
    upsert("search_metrics", _search_rows(start))
    upsert("crm_leads", _crm_rows(start))
    from .rollup import rollup_daily

    rollup_daily(start, date.today())
    log.info("demo data ready")
    return True


def wipe_demo() -> int:
    """Delete the seeded rows, leaving real synced or imported data untouched.

    Run this before importing real CRM aggregates: the daily rollup is rebuilt
    from whatever lead-level rows survive, so clearing demo data afterwards
    would also clear an aggregate import covering the same dates.
    """
    from .rollup import rollup_daily

    span = query(
        "SELECT MIN(created_date) lo, MAX(created_date) hi FROM crm_leads "
        "WHERE external_id LIKE 'DEMO-%'"
    )[0]

    removed = 0
    removed += execute("DELETE FROM paid_metrics WHERE campaign_name LIKE 'DEMO-%'")
    removed += execute("DELETE FROM web_metrics WHERE property_id LIKE 'DEMO-%'")
    removed += execute("DELETE FROM search_metrics WHERE site_url LIKE 'sc-domain:demo-%'")
    removed += execute("DELETE FROM crm_leads WHERE external_id LIKE 'DEMO-%'")

    if span["lo"] and span["hi"]:
        rollup_daily(date.fromisoformat(span["lo"]), date.fromisoformat(span["hi"]))
    return removed
