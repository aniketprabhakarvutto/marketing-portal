from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, db, mcp
from portal.config import APP


class MCPReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_path = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "reporting.db")
        db.init_db()
        db.upsert("paid_metrics", [
            {"date": "2026-10-08", "source": "google_ads", "account_id": "g-1",
             "campaign_id": "jaipur", "campaign_name": "Google Supply Jaipur",
             "currency": "INR", "spend": 200.0, "clicks": 10, "impressions": 1000,
             "connection_id": 1},
            {"date": "2026-10-08", "source": "google_ads", "account_id": "g-1",
             "campaign_id": "delhi", "campaign_name": "Google Supply Delhi",
             "currency": "INR", "spend": 900.0, "clicks": 100, "impressions": 5000,
             "connection_id": 1},
            {"date": "2026-10-08", "source": "meta_ads", "account_id": "m-1",
             "campaign_id": "meta-jaipur", "campaign_name": "Meta Supply Jaipur",
             "currency": "INR", "spend": 500.0, "clicks": 20, "impressions": 2000,
             "connection_id": 2},
        ])

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        self.temp.cleanup()

    def call_campaigns(self, **arguments):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                       "params": {"name": "marketing_campaigns",
                                                  "arguments": {"start": "2026-10-08",
                                                                "end": "2026-10-08", **arguments}}})
        self.assertFalse(response["result"]["isError"], response)
        return response["result"]["structuredContent"]

    def test_campaign_query_filters_reported_name_and_retains_metric_values(self):
        report_rows = api.campaigns("2026-10-08", "2026-10-08", "google_ads", 1000)
        self.assertEqual({row["campaign"] for row in report_rows},
                         {"Google Supply Jaipur", "Google Supply Delhi"})
        result = self.call_campaigns(source="google_ads", query="jaipur", sort_by="cpc")
        self.assertEqual(result["count"], 1)
        self.assertEqual([row["campaign"] for row in result["campaigns"]],
                         ["Google Supply Jaipur"])
        self.assertEqual(result["campaigns"][0]["cpc"], 20.0)
        self.assertEqual(result["campaigns"][0]["spend"], 200.0)

    def test_unknown_source_does_not_silently_return_every_platform(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                                       "params": {"name": "marketing_campaigns",
                                                  "arguments": {"start": "2026-10-08", "end": "2026-10-08",
                                                                "source": "invalid_ads"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Unsupported paid source", response["result"]["content"][0]["text"])

    def test_unsupported_metric_sort_does_not_return_spend_order(self):
        response = mcp.handle_message({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                       "params": {"name": "marketing_campaigns",
                                                  "arguments": {"start": "2026-10-08", "end": "2026-10-08",
                                                                "sort_by": "roas"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Unsupported campaign sort field: roas",
                      response["result"]["content"][0]["text"])

    def test_ascending_cpl_puts_missing_attribution_after_measured_campaign(self):
        db.upsert("crm_campaign_daily", [{
            "date": "2026-10-08", "channel": "google_ads", "campaign_id": "jaipur",
            "campaign_name": "Google Supply Jaipur", "lead_type": "supply",
            "leads": 2, "won": 0, "connection_id": 3,
        }])
        result = self.call_campaigns(source="google_ads", sort_by="cpl", descending=False)
        self.assertEqual([row["campaign"] for row in result["campaigns"]],
                         ["Google Supply Jaipur", "Google Supply Delhi"])
        self.assertEqual(result["campaigns"][0]["cpl"], 100.0)
        self.assertIsNone(result["campaigns"][1]["cpl"])

    def test_result_limit_reports_matches_separately_from_rows_returned(self):
        result = self.call_campaigns(source="google_ads", query="supply", limit=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total_matches"], 2)
        self.assertTrue(result["search_complete"])

    def test_search_runs_across_campaigns_beyond_old_1000_row_cap(self):
        rows = [{"campaign": f"Campaign {index}", "source": "google_ads",
                 "spend": float(index), "currency": "INR"} for index in range(1001)]
        rows[-1]["campaign"] = "Campaign 1000 Needle"
        with patch("portal.mcp.api.campaigns", return_value=rows) as campaigns:
            result = self.call_campaigns(source="google_ads", query="needle", limit=10)
        campaigns.assert_called_once_with("2026-10-08", "2026-10-08", "google_ads", None)
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["campaigns"][0]["campaign"], "Campaign 1000 Needle")
        self.assertTrue(result["search_complete"])


if __name__ == "__main__":
    unittest.main()
