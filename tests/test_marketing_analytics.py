from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from portal import marketing_analytics as analytics
from portal import server, auth
from portal.providers import REGISTRY
from portal.providers.base import Connection


class MarketingAnalyticsTests(unittest.TestCase):
    def setUp(self):
        analytics._discovery_cache.clear()
        self.accounts = {"google_ads": {"1"}, "meta_ads": {"2"}}
        self.rows = [
            {"source": "google_ads", "account_id": "1", "date": "2026-10-21",
             "currency": "INR", "campaign_name": "Google campaign", "spend": "1.234"},
            {"source": "meta_ads", "account_id": "2", "date": "2026-10-21",
             "currency": "INR", "campaign_name": "Meta campaign", "spend": "2.345"},
        ]
        self.mappings = [("Google campaign", "Google CRM"), ("Meta campaign", "Meta CRM")]

    def test_campaign_costs_map_both_sources_without_rounding(self):
        result = analytics.prepare_cost_rows(self.rows, self.mappings, "2026-10-21", self.accounts)
        self.assertEqual([row[4] for row in result["rows"]], [Decimal("1.234"), Decimal("2.345")])
        self.assertEqual(result["unmapped"], [])

    def test_campaign_costs_reject_conflict_and_missing_platform(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            analytics.prepare_cost_rows(
                self.rows, self.mappings + [("GOOGLE CAMPAIGN", "Wrong")],
                "2026-10-21", self.accounts,
            )
        with self.assertRaisesRegex(ValueError, "meta_ads"):
            analytics.prepare_cost_rows(self.rows[:1], self.mappings, "2026-10-21", self.accounts)

    def test_campaign_costs_skip_explicitly_ignored_campaigns(self):
        result = analytics.prepare_cost_rows(self.rows, self.mappings[:1], "2026-10-21", self.accounts,
                                             ignored_campaigns=["Meta campaign"])
        self.assertEqual(result["unmapped"], [])
        self.assertEqual(len(result["rows"]), 1)

    def test_selection_stats_match_spreadsheet_status_bar(self):
        result = analytics.selection_stats([["1", "text", "₹2.50"], [3, "", None]])
        self.assertEqual(result, {
            "sum": 6.5, "avg": 6.5 / 3, "min": 1.0, "max": 3.0,
            "count": 4, "count_numbers": 3,
        })

    def test_mapping_rows_split_demand_supply_and_flag_unknown(self):
        result = analytics.mapping_pairs([
            ["Source", "Target", "Side"],
            ["G Demand", "Google_Demand_Search", ""],
            ["M Supply", "Meta_Supply_Lead", "sell"],
            ["Unknown", "Renamed", ""],
        ])
        self.assertEqual(result["demand"], [("G Demand", "Google_Demand_Search")])
        self.assertEqual(result["supply"], [("M Supply", "Meta_Supply_Lead")])
        self.assertEqual(result["unclassified"], ["Unknown"])

    def test_mapping_rows_ignore_platform_name_header(self):
        result = analytics.mapping_pairs([
            ["Platform Name", "SuperLeap Name"],
            ["Demand campaign", "Google_Demand_Delhi"],
        ])
        self.assertEqual(result["demand"], [("Demand campaign", "Google_Demand_Delhi")])
        self.assertEqual(result["unclassified"], [])

    def test_demandgen_is_a_format_not_a_demand_funnel(self):
        result = analytics.mapping_pairs([
            ["Google_Supply_DemandGen_Jaipur", "Google_Supply_DemandGen_Jaipur"],
            ["Google_DemandGen_Supply_HR_Outside", "Google_DemandGen_Supply_HR_Outside"],
            ["DemandGen-only", "DemandGen-only"],
        ])
        self.assertEqual(len(result["supply"]), 2)
        self.assertEqual(result["unclassified"], ["DemandGen-only"])

    def test_mapping_funnel_column_accepts_named_campaign_classification(self):
        result = analytics.mapping_pairs([
            ["Source A", "Mapped A", "", "Google_Demand_Search_Delhi"],
            ["Source B", "Mapped B", "", "Google_Supply_Search_Delhi"],
        ], side_column="D")
        self.assertEqual(result["demand"], [("Source A", "Mapped A")])
        self.assertEqual(result["supply"], [("Source B", "Mapped B")])

    def test_ambiguous_explicit_funnel_fails_closed(self):
        result = analytics.mapping_pairs([
            ["Google_Demand_Search", "Google_Demand_Search", "", "Demand Supply"],
        ], side_column="D")
        self.assertEqual(result["demand"], [])
        self.assertEqual(result["supply"], [])
        self.assertEqual(result["unclassified"], ["Google_Demand_Search"])

    @patch("portal.marketing_analytics.conns.all_connections")
    @patch("portal.marketing_analytics.query")
    def test_mapping_status_does_not_claim_success_without_campaign_data(self, query, connections):
        query.return_value = []
        connections.return_value = [Connection(1, "google_sheets", "Mapping", {
            "analytics_role": "mapping", "sheet_name": "Sheet2"}, {}, True)]
        result = analytics.mapping_status("2026-10")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["campaign_count"], 0)
        self.assertIn("No saved advertising campaign data", result["message"])
        self.assertIn("account_id", query.call_args.args[0])

    @patch("portal.marketing_analytics.conns.all_connections")
    @patch("portal.marketing_analytics.query")
    def test_mapping_status_does_not_claim_success_when_source_is_disabled(self, query, connections):
        query.return_value = [{"source": "google_ads", "campaign_name": "A"}]
        connections.return_value = [Connection(1, "google_sheets", "Mapping", {"analytics_role": "mapping"}, {}, False)]
        result = analytics.mapping_status("2026-10")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["campaign_count"], 1)

    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._metadata")
    @patch("portal.marketing_analytics._token", return_value="access")
    @patch("portal.marketing_analytics.conns.all_connections")
    @patch("portal.marketing_analytics.query")
    def test_mapping_status_reports_missing_and_conflicting_campaigns(self, query, connections, _token, metadata, get):
        query.return_value = [{"source":"google_ads", "campaign_name":"Mapped"},
                              {"source":"meta_ads", "campaign_name":"Missing"},
                              {"source":"google_ads", "campaign_name":"Conflict"}]
        connections.return_value = [Connection(1, "google_sheets", "Mapping", {
            "analytics_role":"mapping", "sheet_name":"Sheet2", "spreadsheet_url":"12345678901234567890"}, {}, True)]
        metadata.return_value = {"sheets":[{"properties":{"title":"Sheet2"}}]}
        get.return_value = {"values":[["Mapped", "Google_Demand"], ["Conflict", "Google_Demand"],
                                      ["Conflict", "Google_Supply"]]}
        result = analytics.mapping_status("2026-10")
        self.assertEqual(result["status"], "checked")
        self.assertEqual([x["campaign"] for x in result["unmapped"]], ["Missing"])
        self.assertEqual([x["campaign"] for x in result["ambiguous"]], ["Conflict"])

    def test_mapping_uses_target_funnel_and_skips_other_funnel_costs(self):
        result = analytics.mapping_pairs([["Demand remarketing", "Meta_Supply_REM"]])
        self.assertEqual(result["supply"], [("Demand remarketing", "Meta_Supply_REM")])
        prepared = analytics.prepare_cost_rows(
            self.rows, [("Google campaign", "Google_Demand")], "2026-10-21",
            self.accounts, other_side_campaigns=["Meta campaign"],
        )
        self.assertEqual(prepared["unmapped"], [])
        self.assertEqual(len(prepared["rows"]), 1)
        with self.assertRaisesRegex(ValueError, "both demand and supply"):
            analytics.prepare_cost_rows(self.rows, self.mappings, "2026-10-21", self.accounts,
                                        other_side_campaigns=["Meta campaign"])

    def test_month_key_supports_named_and_numeric_months(self):
        self.assertEqual(analytics._month_key("Marketing Analytics October 2026"), "2026-10")
        self.assertEqual(analytics._month_key("Marketing Analytics 10-2026"), "2026-10")
        self.assertEqual(analytics._month_key("Marketing Analytics (Demand)-Sep'26- Aniket"), "2026-09")
        self.assertEqual(analytics._month_key("Marketing Analytics (Supply) - Apr -Nayan"), "")
        self.assertEqual(analytics._month_key("Marketing Analytics"), "")

    def test_disabled_workbook_requires_explicit_view_only_flag(self):
        conn = Connection(3, "google_sheets", "Demand Test", {}, {}, False)
        self.assertFalse(analytics._viewable(conn))
        conn.config["analytics_view_only"] = True
        self.assertTrue(analytics._viewable(conn))

    @patch("portal.marketing_analytics._drive_files")
    @patch("portal.marketing_analytics._metadata")
    @patch("portal.marketing_analytics.conns.all_connections")
    def test_workbook_listing_uses_saved_records_without_google_requests(self, connections, metadata, drive_files):
        connections.return_value = [Connection(3, "google_sheets", "Demand October 2026", {
            "spreadsheet_url": "12345678901234567890", "analytics_role": "demand",
            "sheet_name": "Backend",
        }, {}, True)]
        result = analytics.workbooks()
        self.assertEqual(result, [{
            "connection_id": 3, "name": "Demand October 2026", "role": "demand",
            "month": "2026-10", "spreadsheet_id": "12345678901234567890", "tabs": [{"id": 0, "title": "Backend"}],
            "status": "ready", "message": "",
        }])
        metadata.assert_not_called()
        drive_files.assert_not_called()

    @patch("portal.marketing_analytics._drive_files", return_value=[])
    @patch("portal.marketing_analytics._metadata")
    @patch("portal.marketing_analytics.conns.all_connections")
    def test_explicit_discovery_hydrates_workbook_and_uses_short_cache(self, connections, metadata, _drive_files):
        connections.return_value = [Connection(3, "google_sheets", "Demand", {
            "spreadsheet_url": "12345678901234567890", "analytics_role": "demand",
        }, {}, True)]
        metadata.return_value = {"properties": {"title": "Marketing Analytics (Demand)-Oct'26"},
                                 "sheets": [{"properties": {"sheetId": 7, "title": "Control Room"}}]}
        first = analytics.workbooks(discover=True)
        second = analytics.workbooks(discover=True)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["tabs"], [{"id": 7, "title": "Control Room"}])
        metadata.assert_called_once()
        _drive_files.assert_called_once()

    @patch("portal.marketing_analytics._token", return_value="access")
    @patch("portal.marketing_analytics.get")
    def test_google_discovery_uses_interactive_timeout(self, get, _token):
        conn = Connection(3, "google_sheets", "Demand", {"analytics_role": "demand"}, {}, True)
        get.return_value = {"files": []}
        analytics._drive_files(conn)
        self.assertEqual(get.call_args.kwargs["timeout"], analytics.GOOGLE_API_TIMEOUT)

    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._token", return_value="access")
    @patch("portal.marketing_analytics.conns.get")
    def test_read_tab_reports_workbook_modification_and_handles_unavailable_metadata(self, get_conn, _token, get):
        get_conn.return_value = Connection(3, "google_sheets", "Demand Test", {
            "spreadsheet_url": "12345678901234567890", "analytics_role": "demand",
        }, {}, True)
        get.side_effect = [{"values": [["Leads", "10"]]}, {}, {"modifiedTime": "2026-10-22T12:00:00Z"}]
        result = analytics.read_tab(3, "Control Room", "A1:B1")
        self.assertEqual(result["rows"], [["Leads", "10"]])
        self.assertEqual(result["workbook_modified_time"], "2026-10-22T12:00:00Z")
        self.assertEqual(result["workbook_modified_status"], "available")
        get.side_effect = [{"values": [["Leads", "10"]]}, {}, RuntimeError("Drive unavailable")]
        result = analytics.read_tab(3, "Control Room", "A1:B1")
        self.assertIsNone(result["workbook_modified_time"])
        self.assertEqual(result["workbook_modified_status"], "unavailable")
        permission_error = analytics.HttpError(403, '{"error":{"errors":[{"reason":"insufficientPermissions"}]}}', 'https://www.googleapis.com')
        get.side_effect = [{"values": [["Leads", "10"]]}, {}, permission_error]
        result = analytics.read_tab(3, "Control Room", "A1:B1")
        self.assertEqual(result["workbook_modified_status"], "permission_required")
        other_error = analytics.HttpError(403, '{"error":{"errors":[{"reason":"accessNotConfigured"}]}}', 'https://www.googleapis.com')
        get.side_effect = [{"values": [["Leads", "10"]]}, {}, other_error]
        result = analytics.read_tab(3, "Control Room", "A1:B1")
        self.assertEqual(result["workbook_modified_status"], "api_not_enabled")
        self.assertEqual(result["rows"], [["Leads", "10"]])

    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._token", return_value="access")
    def test_drive_discovery_is_bounded_to_configured_folder(self, _token, get):
        conn = Connection(3, "google_sheets", "Demand Google Sheet", {
            "spreadsheet_url": "12345678901234567890", "drive_folder_id": "folder-1",
        }, {}, True)
        get.return_value = {"files": [{"id": "sheet-1", "name": "Marketing Analytics (Demand)-Oct'26"}]}
        self.assertEqual(analytics._drive_files(conn)[0]["id"], "sheet-1")
        params = get.call_args.kwargs["params"]
        self.assertIn("'folder-1' in parents", params["q"])

    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._token", return_value="access")
    def test_drive_discovery_paginates_and_excludes_other_roles_and_test_copies(self, _token, get):
        conn = Connection(3, "google_sheets", "Demand", {"analytics_role": "demand"}, {}, True)
        get.side_effect = [
            {"files": [{"id": "1", "name": "Marketing Analytics (Demand)-Sep'26"}, {"id": "2", "name": "Marketing Analytics (Supply)-Sep'26"}], "nextPageToken": "next"},
            {"files": [{"id": "3", "name": "TEST ONLY - Marketing Analytics Demand"}, {"id": "4", "name": "Marketing Analytics (Demand)-Oct'26"}]},
        ]
        self.assertEqual([item["id"] for item in analytics._drive_files(conn)], ["1", "4"])
        self.assertEqual(get.call_args.kwargs["params"]["pageToken"], "next")

    @patch("portal.marketing_analytics.request")
    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._token", return_value="access")
    @patch("portal.marketing_analytics.conns.get")
    def test_sheet_write_is_restricted_to_tagged_workbook(self, get_conn, _token, get, request):
        get_conn.return_value = Connection(
            9, "google_sheets", "Demand Marketing Analytics",
            {"spreadsheet_url": analytics.TEST_WORKBOOKS["demand"],
             "analytics_role": "demand"}, {}, True,
        )
        get.return_value = {"sheets": [{"properties": {"title": "__vortex_cost_test", "sheetId": 8,
                            "gridProperties": {"rowCount": 100, "columnCount": 10}}}]}
        request.return_value = {}
        result = analytics.write_cost_tab(9, "__vortex_cost_test", [["Date"], ["2026-10-21"]])
        self.assertEqual(result["updated_rows"], 2)
        self.assertIn("sheets.googleapis.com", request.call_args.args[1])

    @patch("portal.marketing_analytics.conns.get")
    def test_sheet_write_rejects_unclassified_google_sheet(self, get_conn):
        get_conn.return_value = Connection(
            7, "google_sheets", "Other", {"spreadsheet_url": "12345678901234567890"}, {}, True,
        )
        with self.assertRaises(KeyError):
            analytics.write_cost_tab(7, "Costs", [[1]])

    def test_routes_are_readable_and_stats_are_available_to_viewers(self):
        handler = object.__new__(server.Handler)
        self.assertTrue(handler._role_allows("viewer", "GET", "/api/marketing-analytics/workbooks"))
        self.assertTrue(handler._role_allows("viewer", "GET", "/api/marketing-analytics/tab"))
        self.assertTrue(handler._role_allows("viewer", "POST", "/api/marketing-analytics/stats"))

    def test_google_sheet_grant_supports_write_and_month_discovery(self):
        scopes = set(REGISTRY["google_sheets"].oauth.scopes)
        self.assertIn("https://www.googleapis.com/auth/spreadsheets", scopes)
        self.assertIn("https://www.googleapis.com/auth/drive.metadata.readonly", scopes)

    def test_read_only_workbook_view_is_present(self):
        root = Path(__file__).resolve().parents[1] / "portal" / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="tab-marketing-analytics"', html)
        self.assertIn('id="tblMarketingAnalytics"', html)
        self.assertIn('"marketing-analytics": loadMarketingAnalytics', script)
        self.assertIn('/api/marketing-analytics/tab?', script)

    def test_dashboard_views_are_account_preferences_with_a_limit(self):
        self.assertIn("marketingDashboardViews", auth.UI_PREFERENCE_KEYS)
        self.assertEqual(auth._validate_ui_value("marketingDashboardViews", [{"name": "Google"}]), '[{"name":"Google"}]')
        with self.assertRaises(ValueError):
            auth._validate_ui_value("marketingDashboardViews", [{}] * 31)


if __name__ == "__main__":
    unittest.main()
