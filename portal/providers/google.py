"""Google platforms: Ads, Analytics 4 and Search Console.

They share one OAuth app, so they share one spec here and differ only in scope
and in how each turns an API response into warehouse rows.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import re
from urllib.parse import quote

from ..httpclient import HttpError, get, post
from ..oauth import access_token, forget
from .base import Connection, Field, OAuth2, Provider
from .generic import _finish, _mapping, _table
from .shape import build_row, channel_from_label

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Offline access plus a forced consent screen is what makes Google hand over a
# refresh token; without prompt=consent a re-authorisation returns none.
COMMON_AUTH_PARAMS = {"access_type": "offline", "prompt": "consent"}

CLIENT_FIELDS = (
    Field("client_id", "OAuth client ID", required=True,
          help="Google Cloud Console → Credentials → OAuth client ID (type: Web application). "
               "Add this portal's callback URL to its Authorised redirect URIs."),
    Field("client_secret", "OAuth client secret", secret=True, required=True),
)


def _google_oauth(*scopes: str) -> OAuth2:
    return OAuth2(
        auth_url=AUTH_URL, token_url=TOKEN_URL, scopes=list(scopes),
        extra_auth_params=COMMON_AUTH_PARAMS,
    )


# --------------------------------------------------------------------- Google Ads
GOOGLE_ADS_VERSIONS = ["v25", "v24", "v23", "v22"]

ADS_QUERY = """
SELECT
  segments.date, customer.id, customer.descriptive_name, customer.currency_code,
  campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,
  campaign.bidding_strategy_type, campaign_budget.amount_micros,
  metrics.impressions, metrics.clicks, metrics.cost_micros,
  metrics.conversions, metrics.all_conversions, metrics.conversions_value,
  metrics.search_impression_share, metrics.search_budget_lost_impression_share,
  metrics.search_rank_lost_impression_share, metrics.video_trueview_views,
  metrics.video_quartile_p100_rate
FROM campaign
WHERE segments.date BETWEEN '{start}' AND '{end}'
  AND metrics.impressions > 0
"""


class GoogleAdsProvider(Provider):
    id = "google_ads"
    label = "Google Ads"
    category = "ads"
    table = "paid_metrics"
    summary = "Campaign spend, clicks and conversions, day by day."
    docs_url = "https://developers.google.com/google-ads/api/docs/oauth/overview"
    oauth = _google_oauth("https://www.googleapis.com/auth/adwords")
    fields = (
        *CLIENT_FIELDS,
        Field("developer_token", "Developer token", secret=True,
              help="Google Ads → Tools → API Center. Explorer access can read production accounts within its daily limit; Basic raises the operation limit."),
        Field("customer_ids", "Customer IDs", kind="list", placeholder="1234567890, 2345678901",
              help="The accounts to pull, comma-separated. Dashes are fine."),
        Field("login_customer_id", "Manager (MCC) ID", required=False,
              help="Only if the accounts sit under a manager account."),
        Field("api_version", "API version", required=False, placeholder="auto",
              help="Leave blank to detect automatically."),
    )

    _version: str | None = None

    def _headers(self, conn: Connection) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {access_token(conn, self.oauth)}",
            "developer-token": str(conn.get("developer_token") or ""),
        }
        mcc = str(conn.get("login_customer_id") or "").replace("-", "").strip()
        if mcc:
            h["login-customer-id"] = mcc
        return h

    def _search(self, conn: Connection, customer_id: str, query: str) -> list[dict]:
        pinned = str(conn.get("api_version") or "").strip()
        versions = [pinned] if pinned else ([type(self)._version] if type(self)._version
                                            else GOOGLE_ADS_VERSIONS)
        headers, last = self._headers(conn), ""
        for ver in versions:
            url = f"https://googleads.googleapis.com/{ver}/customers/{customer_id}/googleAds:searchStream"
            try:
                body = post(url, json_body={"query": query}, headers=headers)
            except HttpError as exc:
                if exc.status == 404 and not pinned:
                    last = f"{ver} → 404"
                    continue  # version retired, try the next
                raise
            type(self)._version = ver
            chunks = body if isinstance(body, list) else [body]
            rows: list[dict] = []
            for chunk in chunks:
                rows.extend(chunk.get("results") or [])
            return rows
        raise RuntimeError(
            f"No supported Google Ads API version responded (tried {versions}; last {last}). "
            "Set an API version on this connection."
        )

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        query = ADS_QUERY.format(start=start.isoformat(), end=end.isoformat())
        rows: list[dict] = []
        for raw_id in conn.list_of("customer_ids"):
            cid = raw_id.replace("-", "")
            for r in self._search(conn, cid, query):
                cust, camp = r.get("customer", {}), r.get("campaign", {})
                m = r.get("metrics", {})
                rows.append({
                    "date": r.get("segments", {}).get("date", ""),
                    "source": self.id,
                    "account_id": str(cust.get("id", cid)),
                    "account_name": cust.get("descriptiveName") or "",
                    "campaign_id": str(camp.get("id", "")),
                    "campaign_name": camp.get("name") or "",
                    "adset_id": "", "adset_name": "",
                    "campaign_status": camp.get("status") or "",
                    "daily_budget": int(r.get("campaignBudget", {}).get("amountMicros") or 0) / 1_000_000,
                    "bidding_strategy": camp.get("biddingStrategyType") or "",
                    "channel": camp.get("advertisingChannelType") or "",
                    "impressions": int(float(m.get("impressions") or 0)),
                    "reach": None, "frequency": None,
                    "clicks": int(float(m.get("clicks") or 0)),
                    "spend": int(m.get("costMicros") or 0) / 1_000_000,
                    "conversions": float(m.get("conversions") or 0),
                    "all_conversions": float(m.get("allConversions") or 0),
                    "conversion_value": float(m.get("conversionsValue") or 0),
                    "impression_share": (float(m["searchImpressionShare"]) * 100
                                         if m.get("searchImpressionShare") is not None else None),
                    "budget_lost_impression_share": (float(m["searchBudgetLostImpressionShare"]) * 100
                                                      if m.get("searchBudgetLostImpressionShare") is not None else None),
                    "rank_lost_impression_share": (float(m["searchRankLostImpressionShare"]) * 100
                                                    if m.get("searchRankLostImpressionShare") is not None else None),
                    "video_views": int(float(m.get("videoTrueviewViews") or 0)),
                    "video_completion_rate": (float(m["videoQuartileP100Rate"]) * 100
                                              if m.get("videoQuartileP100Rate") is not None else None),
                    "currency": cust.get("currencyCode") or "",
                })
        return rows


# ---------------------------------------------------------------------------- GA4
GA4_DIMENSIONS = ["date", "sessionDefaultChannelGroup", "sessionSource",
                  "sessionMedium", "sessionCampaignName"]
GA4_METRICS = ["sessions", "totalUsers", "newUsers", "engagedSessions", "conversions"]
GA4_METRICS_FALLBACK = ["sessions", "totalUsers", "newUsers", "engagedSessions", "keyEvents"]
GA4_PAGE_SIZE = 100000


class GA4Provider(Provider):
    id = "ga4"
    label = "Google Analytics 4"
    category = "analytics"
    table = "web_metrics"
    summary = "Sessions and conversions by channel, source, medium and campaign."
    docs_url = "https://developers.google.com/analytics/devguides/reporting/data/v1"
    oauth = _google_oauth("https://www.googleapis.com/auth/analytics.readonly")
    fields = (
        *CLIENT_FIELDS,
        Field("property_ids", "Property IDs", kind="list", placeholder="123456789",
              help="GA4 Admin → Property Settings. The 9-digit number, not the G- ID."),
    )

    def _report(self, conn: Connection, prop: str, start: date, end: date,
                metrics: list[str], offset: int = 0):
        return post(
            f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport",
            headers={"Authorization": f"Bearer {access_token(conn, self.oauth)}"},
            json_body={
                "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
                "dimensions": [{"name": d} for d in GA4_DIMENSIONS],
                "metrics": [{"name": m} for m in metrics],
                "limit": GA4_PAGE_SIZE, "offset": offset, "keepEmptyRows": False,
            },
        )

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        rows: list[dict] = []
        for prop in conn.list_of("property_ids"):
            pid = prop.replace("properties/", "")
            metrics = GA4_METRICS
            offset = 0
            expected_rows: int | None = None
            while True:
                try:
                    body = self._report(conn, pid, start, end, metrics, offset)
                except HttpError as exc:
                    # 'conversions' became 'keyEvents' on newer properties.
                    # Retry the current page with the fallback metric schema.
                    if offset == 0 and metrics is GA4_METRICS and exc.status == 400 and "conversions" in exc.body:
                        metrics = GA4_METRICS_FALLBACK
                        continue
                    raise
                page = body.get("rows") or []
                has_row_count = body.get("rowCount") is not None
                if expected_rows is None and has_row_count:
                    try:
                        expected_rows = int(body["rowCount"])
                    except (TypeError, ValueError):
                        has_row_count = False
                if expected_rows is not None and offset < expected_rows and not page:
                    raise RuntimeError(
                        f"GA4 report for property {pid} stopped at row {offset} of "
                        f"{expected_rows}; refusing to save incomplete results. Retry the sync."
                    )
                for r in page:
                    d = [x.get("value", "") for x in r.get("dimensionValues", [])]
                    m = [x.get("value", "0") for x in r.get("metricValues", [])]
                    if len(d) < 5 or len(m) < 5 or len(d[0]) != 8:
                        continue
                    rows.append({
                        "date": f"{d[0][:4]}-{d[0][4:6]}-{d[0][6:8]}",
                        "property_id": pid,
                        "channel_group": d[1] or "(not set)",
                        "session_source": d[2] or "(not set)",
                        "session_medium": d[3] or "(not set)",
                        "session_campaign": d[4] or "(not set)",
                        "sessions": int(float(m[0] or 0)),
                        "total_users": int(float(m[1] or 0)),
                        "new_users": int(float(m[2] or 0)),
                        "engaged_sessions": int(float(m[3] or 0)),
                        "conversions": float(m[4] or 0),
                    })
                offset += len(page)
                if expected_rows is not None:
                    if offset >= expected_rows:
                        break
                elif len(page) < GA4_PAGE_SIZE:
                    break
        return rows


# ------------------------------------------------------------------ Search Console
GSC_PAGE = 25000


def _gsc_quota_error(body: str) -> bool:
    """A 403 can mean API quota, so inspect Google's reason before diagnosing it."""
    try:
        error = json.loads(body).get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    details = [str(error.get("message") or ""), str(error.get("status") or "")]
    details.extend(str(item.get("reason") or "") for item in (error.get("errors") or [])
                   if isinstance(item, dict))
    return bool(re.search(r"quota|rate.?limit|resource.exhausted|daily.?limit",
                          " ".join(details), re.IGNORECASE))


class SearchConsoleProvider(Provider):
    id = "gsc"
    label = "Google Search Console"
    category = "search"
    table = "search_metrics"
    summary = "Organic clicks, impressions and position by query and page."
    docs_url = "https://developers.google.com/webmaster-tools/v1/searchanalytics/query"
    oauth = _google_oauth("https://www.googleapis.com/auth/webmasters.readonly")
    fields = (
        *CLIENT_FIELDS,
        Field("site_urls", "Properties", kind="list", placeholder="sc-domain:example.com",
              help="Enter each Search Console property exactly as listed, including "
                   "sc-domain: or the URL protocol and trailing slash. The property owner "
                   "must grant the Google account used for Connect access to each property."),
    )

    def test_connection(self, conn: Connection) -> str:
        """Verify access to every configured property without relying on report rows."""
        sites = conn.list_of("site_urls")
        if not sites:
            raise ValueError("Enter at least one Search Console property before testing.")
        # The test endpoint builds a temporary Connection with id 0. Clear any
        # token cached for a previous test so this checks the current grant.
        forget(conn.id)
        try:
            token = access_token(conn, self.oauth)
        except RuntimeError:
            raise ValueError(
                "Could not obtain a Google access token for Search Console. "
                "Retry the test, and reconnect this account in Connections "
                "if the problem persists."
            ) from None
        try:
            body = get(
                "https://www.googleapis.com/webmasters/v3/sites",
                headers={"Authorization": f"Bearer {token}"},
            )
        except HttpError as exc:
            if exc.status == 429 or (exc.status == 403 and _gsc_quota_error(exc.body)):
                raise ValueError(
                    "Search Console API quota or rate limit prevented this test. "
                    "Retry later; property access has not been verified."
                ) from None
            if exc.status == 401:
                raise ValueError(
                    "Search Console rejected the Google authorisation. "
                    "Reconnect this account in Connections and test again."
                ) from None
            if exc.status == 403:
                raise ValueError(
                    "Search Console denied the site list request (HTTP 403). "
                    "Check API access and reconnect this account. "
                    "Property access has not been verified."
                ) from None
            raise
        if not isinstance(body, dict) or not isinstance(body.get("siteEntry", []), list):
            raise RuntimeError("Search Console returned an unexpected site list response.")
        permissions = {
            entry.get("siteUrl"): entry.get("permissionLevel")
            for entry in body.get("siteEntry", []) if isinstance(entry, dict)
        }
        missing = [site for site in sites if site not in permissions]
        unverified = [site for site in sites
                      if permissions.get(site) == "siteUnverifiedUser"]
        unexpected = [site for site in sites if site in permissions
                      and permissions[site] not in {
                          "siteOwner", "siteFullUser", "siteRestrictedUser", "siteUnverifiedUser"
                      }]
        problems = []
        if missing:
            problems.append(
                "The connected Google account cannot see the exact Search Console "
                "property name(s): " + ", ".join(missing) + "."
            )
        if unverified:
            problems.append(
                "Search Console lists these properties as unverified for the connected "
                "Google account: " + ", ".join(unverified) + "."
            )
        if unexpected:
            problems.append(
                "Search Console returned an unknown permission level for: "
                + ", ".join(unexpected) + "."
            )
        if problems:
            raise ValueError(
                " ".join(problems) + " Ask the property owner to open Search Console "
                "Settings > Users and permissions > Add user, grant the Google account "
                "used for Connect Full or Restricted access to each exact property, "
                "then test again."
            )
        return f"Search Console access verified for {len(sites)} configured property(s)."

    def _day(self, conn: Connection, site: str, day: date) -> list[dict]:
        from urllib.parse import quote

        url = (f"https://searchconsole.googleapis.com/webmasters/v3/sites/"
               f"{quote(site, safe='')}/searchAnalytics/query")
        headers = {"Authorization": f"Bearer {access_token(conn, self.oauth)}"}
        out: list[dict] = []
        for page in range(40):
            body = post(url, headers=headers, json_body={
                "startDate": day.isoformat(), "endDate": day.isoformat(),
                "dimensions": ["query", "page", "device"],
                "rowLimit": GSC_PAGE, "startRow": page * GSC_PAGE, "type": "web",
            })
            batch = body.get("rows") or []
            out.extend(batch)
            if len(batch) < GSC_PAGE:
                break
        return out

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        # Search Console data settles two to three days late; asking for today
        # just returns nothing.
        from ..api import portal_today

        end = min(end, portal_today() - timedelta(days=2))
        rows: list[dict] = []
        for site in conn.list_of("site_urls"):
            day = start
            while day <= end:
                for r in self._day(conn, site, day):
                    keys = (r.get("keys") or []) + ["", "", ""]
                    rows.append({
                        "date": day.isoformat(), "site_url": site,
                        "query": (keys[0] or "")[:512], "page": (keys[1] or "")[:1024],
                        "device": keys[2] or "", "country": "",
                        "clicks": int(r.get("clicks") or 0),
                        "impressions": int(r.get("impressions") or 0),
                        "position": float(r.get("position") or 0),
                    })
                day += timedelta(days=1)
        return rows


# ---------------------------------------------------------------- Google Sheets
class GoogleSheetsProvider(Provider):
    """Read a private Google Sheet directly with the Sheets API.

    This deliberately uses the same mapping contract as CSV/REST connections,
    while adding column-letter aliases (A, B, C...) for sheets whose header row
    contains merged or blank cells.
    """

    id = "google_sheets"
    label = "Google Sheets"
    category = "other"
    table = "crm_daily"
    summary = "Import private Sheet tabs directly, with automatic refreshes."
    docs_url = "https://developers.google.com/sheets/api/guides/values"
    oauth = _google_oauth(
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
    )
    fields = (
        *CLIENT_FIELDS,
        Field("spreadsheet_url", "Spreadsheet URL", kind="url",
              placeholder="https://docs.google.com/spreadsheets/d/…/edit"),
        Field("sheet_name", "Sheet tab", placeholder="Backend",
              help="The exact tab name that contains the row-level data."),
        Field("target_table", "Load into table", placeholder="crm_daily",
              help="paid_metrics, web_metrics, search_metrics, crm_leads, crm_daily, "
                   "crm_city_daily, or crm_campaign_daily."),
        Field("field_map", "Column mapping", kind="text",
              placeholder='{"date":"B","source":"E","leads":"F"}',
              help="JSON mapping from portal fields to a header name or column letter."),
        Field("header_row", "Header row", kind="number", required=False,
              placeholder="2", help="Row containing headings. Defaults to 1."),
        Field("data_start_row", "First data row", kind="number", required=False,
              placeholder="3", help="Defaults to the row immediately below the headings."),
        Field("date_order", "Slash date order", required=False,
              placeholder="month_first or day_first",
              help="For ambiguous dates such as 9/5/2026, choose month_first for September 5 "
                   "or day_first for May 9. ISO dates need no setting."),
        Field("lead_type", "Funnel tag", required=False, placeholder="demand",
              help="Use demand or supply when loading CRM data."),
        Field("source_name", "Source label", required=False,
              help="Fallback source name when the sheet does not provide one."),
        Field("constants", "Fixed values", required=False,
              placeholder='{"currency":"INR"}',
              help="Optional JSON object applied to every imported row."),
        Field("analytics_role", "Marketing Analytics role", required=False,
              placeholder="demand, supply, or mapping",
              help="Tag a workbook as demand, supply, or mapping. Only demand and supply "
                   "workbooks can receive automated campaign costs."),
        Field("drive_folder_id", "Monthly workbook folder ID", required=False,
              help="Optional Drive folder used to discover future monthly workbooks."),
        Field("campaign_cost_sheet", "Campaign cost tab", required=False,
              placeholder="Campaign Cost",
              help="Demand or Supply tab that receives verified daily campaign costs."),
        Field("mapping_source_column", "Mapping source column", required=False,
              placeholder="A"),
        Field("mapping_target_column", "Mapping target column", required=False,
              placeholder="B"),
        Field("mapping_side_column", "Mapping funnel column", required=False,
              placeholder="C", help="Optional column containing demand or supply."),
    )

    @staticmethod
    def _spreadsheet_id(value: str) -> str:
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value or "")
        if match:
            return match.group(1)
        value = (value or "").strip()
        if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", value):
            return value
        raise RuntimeError("Enter a valid Google Sheets URL or spreadsheet ID.")

    @staticmethod
    def _column_name(index: int) -> str:
        out = ""
        while index:
            index, rem = divmod(index - 1, 26)
            out = chr(65 + rem) + out
        return out

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        table, mapping = _table(conn), _mapping(conn)
        spreadsheet_id = self._spreadsheet_id(str(conn.get("spreadsheet_url") or ""))
        tab = str(conn.get("sheet_name") or "").strip()
        if not tab:
            raise RuntimeError("Choose the Sheet tab that contains the source data.")
        escaped_tab = quote(f"'{tab.replace(chr(39), chr(39) * 2)}'", safe="")
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/"
               f"{escaped_tab}")
        body = get(url, headers={"Authorization": f"Bearer {access_token(conn, self.oauth)}"},
                   params={"majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE",
                           "dateTimeRenderOption": "FORMATTED_STRING"})
        values = body.get("values") or []
        header_row = max(1, int(conn.get("header_row") or 1))
        data_start = max(header_row + 1, int(conn.get("data_start_row") or header_row + 1))
        if len(values) < data_start:
            raise RuntimeError(f"The '{tab}' tab has no rows after row {data_start - 1}.")

        headers = values[header_row - 1] if len(values) >= header_row else []
        records: list[dict] = []
        for raw in values[data_start - 1:]:
            record: dict[str, object] = {}
            for index, value in enumerate(raw, 1):
                letter = self._column_name(index)
                record[letter] = value
                heading = str(headers[index - 1]).strip() if index <= len(headers) else ""
                if heading:
                    record[heading] = value
            if any(v not in (None, "") for v in raw):
                records.append(record)

        constants: dict[str, object] = {}
        raw_constants = conn.get("constants")
        if raw_constants:
            try:
                constants.update(json.loads(raw_constants) if isinstance(raw_constants, str)
                                 else raw_constants)
            except (json.JSONDecodeError, TypeError):
                raise RuntimeError("Fixed values must be a valid JSON object.") from None
        if conn.get("lead_type"):
            constants["lead_type"] = str(conn.get("lead_type")).strip().lower()

        date_order = str(conn.get("date_order") or "").strip().lower()
        if date_order not in {"", "month_first", "day_first"}:
            raise RuntimeError("Slash date order must be month_first or day_first.")
        rows = [build_row(record, table, mapping, constants, date_order) for record in records]
        rows = _finish(conn, table, rows, records, mapping)
        if table == "crm_campaign_daily":
            paid_aliases = {
                "google": "google_ads", "google ads": "google_ads",
                "facebook": "meta_ads", "meta": "meta_ads",
                "meta ads": "meta_ads", "instagram": "meta_ads",
            }
            for row in rows:
                row["channel"] = channel_from_label(row.get("channel") or "", paid_aliases)
            rows = [row for row in rows if str(row.get("campaign_name") or "").strip()]
        key = "created_date" if table == "crm_leads" else "date"
        lo, hi = start.isoformat(), end.isoformat()
        return [row for row in rows if row.get(key) and lo <= row[key] <= hi]
