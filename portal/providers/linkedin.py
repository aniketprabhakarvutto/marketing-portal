"""LinkedIn Ads — campaign analytics via the Marketing API.

LinkedIn's reporting endpoint returns date ranges as split-out day/month/year
parts rather than an ISO string, and money as a decimal string, so most of the
work here is normalising its shapes into the same row every other ads platform
produces.
"""
from __future__ import annotations

from datetime import date

from ..httpclient import get
from ..oauth import access_token
from .base import Connection, Field, OAuth2, Provider

API = "https://api.linkedin.com/rest"
VERSION = "202609"

METRIC_FIELDS = (
    "impressions,clicks,costInLocalCurrency,externalWebsiteConversions,"
    "conversionValueInLocalCurrency,dateRange,pivotValues"
)


class LinkedInAdsProvider(Provider):
    id = "linkedin_ads"
    label = "LinkedIn Ads"
    category = "ads"
    table = "paid_metrics"
    summary = "Campaign spend, clicks and website conversions by day."
    docs_url = "https://learn.microsoft.com/en-us/linkedin/marketing/integrations/ads-reporting/ads-reporting"
    oauth = OAuth2(
        auth_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        scopes=["r_ads_reporting", "r_ads"],
    )
    fields = (
        Field("client_id", "Client ID",
              help="LinkedIn Developer app → Auth. Add this portal's callback as a "
                   "redirect URL, and request the Advertising API product."),
        Field("client_secret", "Client secret", secret=True),
        Field("account_ids", "Ad account IDs", kind="list", placeholder="512345678",
              help="Numeric sponsored-account IDs, comma-separated."),
    )

    def _headers(self, conn: Connection) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token(conn, self.oauth)}",
            "LinkedIn-Version": str(conn.get("api_version") or VERSION),
            "X-Restli-Protocol-Version": "2.0.0",
        }

    @staticmethod
    def _iso(part: dict) -> str:
        """LinkedIn returns {'year': 2026, 'month': 9, 'day': 5}."""
        try:
            return date(int(part["year"]), int(part["month"]), int(part["day"])).isoformat()
        except (KeyError, TypeError, ValueError):
            return ""

    @staticmethod
    def _campaign_id(pivot_values) -> str:
        # Pivot values arrive as URNs like urn:li:sponsoredCampaign:123456
        for v in pivot_values or []:
            if isinstance(v, str) and ":" in v:
                return v.rsplit(":", 1)[-1]
        return ""

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        rows: list[dict] = []
        headers = self._headers(conn)
        for account in conn.list_of("account_ids"):
            acct = account.rsplit(":", 1)[-1]
            account_info = get(f"{API}/adAccounts/{acct}", headers=headers)
            currency = str(account_info.get("currency") or "").upper()
            params = {
                "q": "analytics",
                "pivot": "CAMPAIGN",
                "timeGranularity": "DAILY",
                "accounts": f"List(urn%3Ali%3AsponsoredAccount%3A{acct})",
                "dateRange": (
                    f"(start:(year:{start.year},month:{start.month},day:{start.day}),"
                    f"end:(year:{end.year},month:{end.month},day:{end.day}))"
                ),
                "fields": METRIC_FIELDS,
            }
            # LinkedIn's Rest.li query syntax must not be re-encoded, so the URL
            # is assembled by hand rather than via urlencode.
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            body = get(f"{API}/adAnalytics?{qs}", headers=headers)
            for r in body.get("elements") or []:
                day = self._iso((r.get("dateRange") or {}).get("start") or {})
                if not day:
                    continue
                rows.append({
                    "date": day,
                    "source": self.id,
                    "account_id": acct,
                    "account_name": "",
                    "campaign_id": self._campaign_id(r.get("pivotValues")),
                    "campaign_name": "",
                    "adset_id": "", "adset_name": "", "channel": "LINKEDIN",
                    "impressions": int(float(r.get("impressions") or 0)),
                    "clicks": int(float(r.get("clicks") or 0)),
                    "spend": float(r.get("costInLocalCurrency") or 0),
                    "conversions": float(r.get("externalWebsiteConversions") or 0),
                    "conversion_value": float(r.get("conversionValueInLocalCurrency") or 0),
                    "currency": currency,
                })
        return rows
