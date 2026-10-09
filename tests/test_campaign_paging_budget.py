from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portal import api, db
from portal.config import APP


class CampaignPagingBudgetTests(unittest.TestCase):
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

    def test_campaign_pages_are_stable_and_include_every_campaign(self):
        db.upsert("paid_metrics", [
            {"date": "2026-10-08", "source": "google_ads", "account_id": "a",
             "campaign_id": str(i), "campaign_name": f"Campaign {i:04}",
             "currency": "INR", "spend": float(2000 - i), "connection_id": 1}
            for i in range(1003)
        ])

        first = api.campaigns("2026-10-08", "2026-10-08", "google_ads", 1000)
        second = api.campaigns("2026-10-08", "2026-10-08", "google_ads", 1000, 1000)

        self.assertEqual(len(first), 1000)
        self.assertEqual(len(second), 3)
        self.assertEqual(len({row["campaign_id"] for row in first + second}), 1003)
        self.assertEqual(api.campaigns("2026-10-08", "2026-10-08", "google_ads", None).__len__(), 1003)

    def test_campaign_budget_sums_observed_budget_days_and_exposes_coverage(self):
        db.upsert("paid_metrics", [
            {"date": "2026-10-06", "source": "google_ads", "account_id": "a",
             "campaign_id": "budgeted", "campaign_name": "Budgeted Supply", "currency": "INR",
             "spend": 50, "daily_budget": 100, "connection_id": 1},
            {"date": "2026-10-07", "source": "google_ads", "account_id": "a",
             "campaign_id": "budgeted", "campaign_name": "Budgeted Supply", "currency": "INR",
             "spend": 80, "daily_budget": None, "connection_id": 1},
            {"date": "2026-10-08", "source": "google_ads", "account_id": "a",
             "campaign_id": "budgeted", "campaign_name": "Budgeted Supply", "currency": "INR",
             "spend": 70, "daily_budget": 200, "connection_id": 1},
        ])

        row = api.campaigns("2026-10-06", "2026-10-08", "google_ads", None)[0]

        self.assertEqual(row["selected_days"], 3)
        self.assertEqual(row["reported_days"], 3)
        self.assertEqual(row["budgeted_days"], 2)
        self.assertEqual(row["period_budget"], 300)
        self.assertEqual(row["budgeted_spend"], 120)
        self.assertEqual(row["budget_coverage_percent"], 60)
        self.assertEqual(row["pacing_percent"], 40)

    def test_campaign_pagination_rejects_negative_offset(self):
        with self.assertRaises(ValueError):
            api.campaigns("2026-10-08", "2026-10-08", offset=-1)
        with self.assertRaises(ValueError):
            api.campaigns("2026-10-08", "2026-10-08", source="typo")


if __name__ == "__main__":
    unittest.main()
