"""Meta Ads — Facebook and Instagram, via the Graph API.

Meta reports conversions as a list of typed actions rather than one number, so
which action types count is a business decision. It is a field on the
connection, not a constant in the code.
"""
from __future__ import annotations

import json
from datetime import date

from ..httpclient import get
from ..oauth import access_token
from .base import Connection, Field, OAuth2, Provider

FIELDS = ("account_id,account_name,campaign_id,campaign_name,objective,"
          "account_currency,impressions,reach,frequency,clicks,spend,results,actions,"
          "action_values,video_p100_watched_actions")
META_CAMPAIGN_PAGE_LIMIT = 500
META_CAMPAIGN_MAX_PAGES = 200
META_INSIGHTS_PAGE_LIMIT = 500
META_INSIGHTS_MAX_PAGES = 200


class MetaAdsProvider(Provider):
    id = "meta_ads"
    label = "Meta Ads"
    category = "ads"
    table = "paid_metrics"
    summary = "Facebook and Instagram campaign spend and conversions."
    docs_url = "https://developers.facebook.com/docs/marketing-api/insights"
    oauth = OAuth2(
        auth_url="https://www.facebook.com/v21.0/dialog/oauth",
        token_url="https://graph.facebook.com/v21.0/oauth/access_token",
        scopes=["ads_read"],
    )
    fields = (
        Field("client_id", "App ID",
              help="developers.facebook.com → your app → Settings → Basic."),
        Field("client_secret", "App secret", secret=True),
        Field("account_ids", "Ad account IDs", kind="list", placeholder="act_123456789",
              help="With or without the act_ prefix, comma-separated."),
        Field("conversion_actions", "Conversion action types", required=False, kind="list",
              placeholder="lead,purchase",
              help="Leave blank to match the Results column in Ads Manager. Enter Meta "
                   "action types only when you want a custom conversion definition."),
        Field("api_version", "Graph API version", required=False, placeholder="v21.0"),
        Field("access_token", "System User token", secret=True, required=False,
              help="Optional. A System User token never expires and skips the Connect "
                   "step entirely — useful for unattended syncs."),
    )

    @staticmethod
    def _sum(actions, wanted: set[str]) -> float:
        if not actions:
            return 0.0
        return sum(float(a.get("value") or 0) for a in actions
                   if a.get("action_type") in wanted)

    @staticmethod
    def _sum_results(results) -> float:
        """Return the campaign result Meta selects for Ads Manager.

        Unlike ``actions``, ``results`` already respects each campaign's chosen
        optimisation event and avoids double-counting aliases for one event.
        """
        return sum(
            float(value.get("value") or 0)
            for result in (results or [])
            for value in (result.get("values") or [])
        )

    def _token(self, conn: Connection) -> str:
        direct = conn.get("access_token")
        return str(direct) if direct else access_token(conn, self.oauth)

    @staticmethod
    def _sum_values(values) -> float:
        return sum(float(item.get("value") or 0) for item in (values or []))

    def _campaign_metadata(self, conn: Connection, account: str) -> dict[str, dict]:
        ver = str(conn.get("api_version") or "v21.0")
        acct = account if account.startswith("act_") else f"act_{account}"
        url = f"https://graph.facebook.com/{ver}/{acct}/campaigns"
        params = {
            "fields": "id,effective_status,daily_budget,bid_strategy",
            "limit": META_CAMPAIGN_PAGE_LIMIT, "access_token": self._token(conn),
        }
        out: dict[str, dict] = {}
        for page_number in range(META_CAMPAIGN_MAX_PAGES):
            body = get(url, params=params)
            for item in body.get("data") or []:
                out[str(item.get("id") or "")] = item
            nxt = (body.get("paging") or {}).get("next")
            if not nxt:
                return out
            if page_number + 1 == META_CAMPAIGN_MAX_PAGES:
                raise RuntimeError(
                    f"Meta Ads campaign metadata exceeded {META_CAMPAIGN_MAX_PAGES} pages "
                    f"for account {account}; the sync stopped before saving incomplete results. "
                    "Check the account scope and retry."
                )
            url, params = nxt, None
        raise RuntimeError("Meta Ads campaign metadata pagination stopped unexpectedly; retry the sync.")

    def _insights(self, conn: Connection, account: str, start: date, end: date) -> list[dict]:
        ver = str(conn.get("api_version") or "v21.0")
        acct = account if account.startswith("act_") else f"act_{account}"
        url = f"https://graph.facebook.com/{ver}/{acct}/insights"
        params = {
            "level": "campaign", "fields": FIELDS, "time_increment": 1,
            "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
            "limit": META_INSIGHTS_PAGE_LIMIT, "access_token": self._token(conn),
        }
        out: list[dict] = []
        for page_number in range(META_INSIGHTS_MAX_PAGES):  # bound requests against paging loops
            body = get(url, params=params)
            out.extend(body.get("data") or [])
            nxt = (body.get("paging") or {}).get("next")
            if not nxt:
                return out
            if page_number + 1 == META_INSIGHTS_MAX_PAGES:
                raise RuntimeError(
                    f"Meta Ads insights exceeded {META_INSIGHTS_MAX_PAGES} pages for "
                    f"account {account}; the sync stopped before saving incomplete results. "
                    "Narrow the date range and retry."
                )
            url, params = nxt, None  # `next` is a fully-formed signed URL
        raise RuntimeError("Meta Ads insights pagination stopped unexpectedly; retry the sync.")

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        wanted = set(conn.list_of("conversion_actions"))
        rows: list[dict] = []
        for account in conn.list_of("account_ids"):
            metadata = self._campaign_metadata(conn, account)
            for r in self._insights(conn, account, start, end):
                campaign = metadata.get(str(r.get("campaign_id") or ""), {})
                reach = int(float(r.get("reach") or 0)) if r.get("reach") is not None else None
                conversions = (self._sum(r.get("actions"), wanted)
                               if wanted else self._sum_results(r.get("results")))
                video_views = self._sum(r.get("actions"), {"video_view", "video_play"})
                video_completions = self._sum_values(r.get("video_p100_watched_actions"))
                rows.append({
                    "date": r.get("date_start", ""),
                    "source": self.id,
                    "account_id": str(r.get("account_id") or account),
                    "account_name": r.get("account_name") or "",
                    "campaign_id": str(r.get("campaign_id") or ""),
                    "campaign_name": r.get("campaign_name") or "",
                    "adset_id": "", "adset_name": "",
                    "campaign_status": campaign.get("effective_status") or "",
                    "daily_budget": (float(campaign.get("daily_budget") or 0) / 100
                                     if campaign.get("daily_budget") else None),
                    "bidding_strategy": campaign.get("bid_strategy") or "",
                    "channel": r.get("objective") or "",
                    "impressions": int(float(r.get("impressions") or 0)),
                    "reach": reach,
                    "frequency": float(r.get("frequency")) if r.get("frequency") is not None else None,
                    "clicks": int(float(r.get("clicks") or 0)),
                    "spend": float(r.get("spend") or 0),
                    "conversions": conversions,
                    # Meta has no Google-style bidding/all-conversions split.
                    # Use the configured Results definition for both so aliases
                    # in the actions array are never double-counted.
                    "all_conversions": conversions,
                    "conversion_value": self._sum(r.get("action_values"), wanted),
                    "impression_share": None,
                    "budget_lost_impression_share": None,
                    "rank_lost_impression_share": None,
                    "video_views": int(video_views),
                    "video_completion_rate": (video_completions / video_views * 100
                                              if video_views else None),
                    "currency": r.get("account_currency") or "",
                })
        return rows

    def missing(self, conn: Connection) -> list[str]:
        direct = bool(conn.get("access_token"))
        # A System User token stands in for the Meta app credentials and the
        # whole OAuth round trip, which is ideal for unattended local syncing.
        oauth_only = {"client_id", "client_secret"} if direct else set()
        out = [f.label for f in self.fields
               if f.required and f.key not in oauth_only and not conn.get(f.key)]
        if not direct and not conn.get("refresh_token"):
            out.append("Authorisation")
        return out
