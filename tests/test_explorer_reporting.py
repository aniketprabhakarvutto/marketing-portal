from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import db, platform
from portal.config import APP


class ExplorerReportingTests(unittest.TestCase):
    def setUp(self):
        db.close_connection()
        self.old_path = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        db.init_db()

    def tearDown(self):
        db.close_connection()
        APP.db_path = self.old_path
        self.temp.cleanup()

    def insert(self, currencies):
        db.upsert("paid_metrics", [
            {"date": "2026-09-15", "source": "google_ads", "account_id": str(index),
             "campaign_id": str(index), "campaign_name": "Shared campaign name",
             "currency": currency, "spend": spend, "clicks": clicks,
             "conversions": 2, "conversion_value": spend * 2, "connection_id": 100 + index}
            for index, (currency, spend, clicks) in enumerate(currencies)
        ])

    def explore(self, metrics=None):
        return platform.explore({"start": "2026-09-15", "end": "2026-09-15",
                                 "dimensions": ["campaign"],
                                 "metrics": metrics or ["spend", "clicks", "cpc"]})

    def test_money_is_grouped_by_currency_without_fx_conversion(self):
        self.insert([("INR", 500, 50), ("USD", 100, 20)])

        result = self.explore()

        self.assertIn("currency", result["columns"])
        self.assertEqual(result["count"], 2)
        rows = {row["currency"]: row for row in result["rows"]}
        self.assertEqual((rows["INR"]["spend"], rows["INR"]["cpc"]), (500, 10))
        self.assertEqual((rows["USD"]["spend"], rows["USD"]["cpc"]), (100, 5))

    def test_currency_case_and_whitespace_do_not_split_same_currency(self):
        self.insert([("INR", 500, 50), (" inr ", 100, 20)])

        result = self.explore()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0].get("currency"), "INR")
        self.assertEqual(result["rows"][0]["spend"], 600)
        self.assertAlmostEqual(result["rows"][0]["cpc"], 600 / 70)

    def test_missing_currency_preserves_counts_but_withholds_money(self):
        self.insert([("", 100, 20)])

        result = self.explore(["spend", "clicks", "cpc", "cpa", "conversion_value"])

        row = result["rows"][0]
        self.assertEqual(row["clicks"], 20)
        for metric in ("spend", "cpc", "cpa", "conversion_value"):
            self.assertIsNone(row[metric], metric)
        self.assertTrue(result["warnings"])

    def test_nonfinancial_metrics_can_combine_accounts_without_currency_grouping(self):
        self.insert([("INR", 500, 50), ("USD", 100, 20)])

        result = self.explore(["clicks"])

        self.assertEqual(result["columns"], ["campaign", "clicks"])
        self.assertEqual(result["rows"], [{"campaign": "Shared campaign name", "clicks": 70}])

    def test_dates_use_same_reporting_timezone_and_bounds_as_main_reports(self):
        with patch("portal.api.portal_today", return_value=date(2026, 10, 9)):
            default = platform.explore({})
            future = platform.explore({"start": "2026-10-01", "end": "2035-12-31"})

        self.assertEqual(default["range"], {"start": "2026-09-09", "end": "2026-10-08"})
        self.assertEqual(future["range"], {"start": "2026-10-01", "end": "2026-10-09"})

    def test_invalid_and_reversed_dates_are_rejected(self):
        for start, end in (("2026-10-09", "2026-10-08"), ("invalid", "2026-10-08")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                platform.explore({"start": start, "end": end})


if __name__ == "__main__":
    unittest.main()
