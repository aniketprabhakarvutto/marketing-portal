"""TikTok Ads — the integrated reporting endpoint.

TikTok's OAuth is non-standard: it hands back a long-lived access token with no
refresh token, and the exchange wants JSON rather than form fields. Both quirks
are handled here so the rest of the portal sees an ordinary provider.
"""
from __future__ import annotations

import json
from datetime import date

from ..httpclient import get, post
from .base import Connection, Field, OAuth2, Provider

API = "https://business-api.tiktok.com/open_api/v1.3"

METRICS = ["spend", "impressions", "clicks", "conversion", "total_complete_payment_rate"]


class TikTokAdsProvider(Provider):
    id = "tiktok_ads"
    label = "TikTok Ads"
    category = "ads"
    table = "paid_metrics"
    summary = "Campaign spend, clicks and conversions by day."
    docs_url = "https://business-api.tiktok.com/portal/docs"
    oauth = OAuth2(
        auth_url="https://business-api.tiktok.com/portal/auth",
        token_url=f"{API}/oauth2/access_token/",
        scopes=[],
    )
    fields = (
        Field("client_id", "App ID",
              help="TikTok for Business → Developers → your app. Add this portal's "
                   "callback as the redirect URL."),
        Field("client_secret", "App secret", secret=True),
        Field("advertiser_ids", "Advertiser IDs", kind="list", placeholder="7012345678901234567"),
        Field("access_token", "Access token", secret=True, required=False,
              help="Filled in automatically when you press Connect."),
    )

    def missing(self, conn: Connection) -> list[str]:
        out = [f.label for f in self.fields if f.required and not conn.get(f.key)]
        if not conn.get("access_token"):
            out.append("Authorisation")
        return out

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        token = str(conn.get("access_token") or "")
        if not token:
            raise RuntimeError(f"'{conn.name}' is not authorised yet — press Connect.")
        headers = {"Access-Token": token}
        rows: list[dict] = []
        for advertiser in conn.list_of("advertiser_ids"):
            page = 1
            while page <= 100:
                body = get(f"{API}/report/integrated/get/", headers=headers, params={
                    "advertiser_id": advertiser,
                    "report_type": "BASIC",
                    "data_level": "AUCTION_CAMPAIGN",
                    "dimensions": json.dumps(["campaign_id", "stat_time_day"]),
                    "metrics": json.dumps(METRICS + ["campaign_name"]),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "page": page, "page_size": 1000,
                })
                if body.get("code") not in (0, None):
                    raise RuntimeError(f"TikTok API error {body.get('code')}: {body.get('message')}")
                data = body.get("data") or {}
                items = data.get("list") or []
                for r in items:
                    dims, met = r.get("dimensions") or {}, r.get("metrics") or {}
                    day = str(dims.get("stat_time_day") or "")[:10]
                    if not day:
                        continue
                    rows.append({
                        "date": day,
                        "source": self.id,
                        "account_id": advertiser,
                        "account_name": "",
                        "campaign_id": str(dims.get("campaign_id") or ""),
                        "campaign_name": met.get("campaign_name") or "",
                        "adset_id": "", "adset_name": "", "channel": "TIKTOK",
                        "impressions": int(float(met.get("impressions") or 0)),
                        "clicks": int(float(met.get("clicks") or 0)),
                        "spend": float(met.get("spend") or 0),
                        "conversions": float(met.get("conversion") or 0),
                        "conversion_value": 0.0,
                        "currency": "",
                    })
                info = data.get("page_info") or {}
                if page >= int(info.get("total_page") or 1):
                    break
                page += 1
        return rows

    # TikTok's token exchange does not follow the standard grant shape.
    def exchange(self, conn: Connection, code: str) -> dict:
        return post(f"{API}/oauth2/access_token/", json_body={
            "app_id": conn.get("client_id"),
            "secret": conn.get("client_secret"),
            "auth_code": code,
            "grant_type": "authorization_code",
        })
