from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from portal import marketing_analytics as analytics
from portal import server
from portal.providers import REGISTRY
from portal.providers.base import Connection


class MarketingAnalyticsTests(unittest.TestCase):
    def setUp(self):
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

    def test_selection_stats_match_spreadsheet_status_bar(self):
        result = analytics.selection_stats([["1", "text", "₹2.50"], [3, "", None]])
        self.assertEqual(result, {
            "sum": 6.5, "avg": 6.5 / 3, "min": 1.0, "max": 3.0,
            "count": 4, "count_numbers": 3,
        })

    def test_month_key_supports_named_and_numeric_months(self):
        self.assertEqual(analytics._month_key("Marketing Analytics October 2026"), "2026-10")
        self.assertEqual(analytics._month_key("Marketing Analytics 10-2026"), "2026-10")
        self.assertEqual(analytics._month_key("Marketing Analytics"), "")

    @patch("portal.marketing_analytics.request")
    @patch("portal.marketing_analytics._token", return_value="access")
    @patch("portal.marketing_analytics.conns.get")
    def test_sheet_write_is_restricted_to_tagged_workbook(self, get_conn, _token, request):
        get_conn.return_value = Connection(
            9, "google_sheets", "Demand Marketing Analytics",
            {"spreadsheet_url": "https://docs.google.com/spreadsheets/d/12345678901234567890/edit",
             "analytics_role": "demand"}, {}, True,
        )
        request.return_value = {"updatedRange": "__vortex_cost_test!A1:E3", "updatedRows": 3}
        result = analytics.write_cost_tab(9, "__vortex_cost_test", [["Date"], ["2026-10-21"]])
        self.assertEqual(result["updated_rows"], 3)
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


if __name__ == "__main__":
    unittest.main()
