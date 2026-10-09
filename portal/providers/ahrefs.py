"""Ahrefs Site Explorer connector."""
from __future__ import annotations

from datetime import date, datetime, timezone

from ..httpclient import get
from .base import Connection, Field, Provider


def _utc_today() -> date:
    """Ahrefs report dates follow UTC, not the portal host's local calendar."""
    return datetime.now(timezone.utc).date()


class AhrefsProvider(Provider):
    id = "ahrefs"
    label = "Ahrefs"
    category = "search"
    table = "ahrefs_metrics"
    summary = "Domain Rating, organic visibility, backlink and referring-domain metrics."
    docs_url = "https://docs.ahrefs.com/en/api/reference/site-explorer"
    fields = (
        Field("target", "Website", placeholder="vutto.in",
              help="Domain or URL to analyse."),
        Field("country", "Country", placeholder="in",
              help="Two-letter country code used for organic keyword and traffic estimates."),
        Field("api_key", "Ahrefs API key", secret=True,
              help="Created in Ahrefs API settings and stored only in this portal."),
    )

    @staticmethod
    def _object(body: dict, key: str) -> dict:
        value = body.get(key) or {}
        return value if isinstance(value, dict) else {}

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        base = "https://api.ahrefs.com/v3/site-explorer"
        headers = {"Authorization": f"Bearer {conn.get('api_key')}"}
        snapshot_date = min(end, _utc_today())
        common = {
            "date": snapshot_date.isoformat(), "target": str(conn.get("target")),
            "mode": "subdomains", "protocol": "both", "output": "json",
        }
        country = str(conn.get("country") or "in").lower()
        overview = self._object(get(f"{base}/metrics", headers=headers,
                                    params={**common, "country": country}), "metrics")
        rating = self._object(get(f"{base}/domain-rating", headers=headers,
                                  params=common), "domain_rating")
        links = self._object(get(f"{base}/backlinks-stats", headers=headers,
                                 params=common), "metrics")
        return [{
            "date": snapshot_date.isoformat(), "target": str(conn.get("target")), "country": country,
            "domain_rating": rating.get("domain_rating"),
            "ahrefs_rank": rating.get("ahrefs_rank"),
            "organic_keywords": int(overview.get("org_keywords") or 0),
            "top3_keywords": int(overview.get("org_keywords_1_3") or 0),
            "organic_traffic": int(overview.get("org_traffic") or 0),
            "organic_traffic_value": float(overview.get("org_cost") or 0),
            "live_backlinks": int(links.get("live") or 0),
            "live_refdomains": int(links.get("live_refdomains") or 0),
            "all_time_backlinks": int(links.get("all_time") or 0),
            "all_time_refdomains": int(links.get("all_time_refdomains") or 0),
        }]
