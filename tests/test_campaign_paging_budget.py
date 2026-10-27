from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, db, platform
from portal.config import APP


class CampaignPagingBudgetTests(unittest.TestCase):
    def setUp(self):
        db.close_connection()
        self.old_path = APP.db_path
        self.old_database_url = APP.database_url
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()

    def tearDown(self):
        db.close_connection()
        APP.db_path = self.old_path
        APP.database_url = self.old_database_url
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

    def test_campaign_report_loads_transformation_rules_once(self):
        db.upsert("paid_metrics", [
            {"date": "2026-10-08", "source": "google_ads", "account_id": "a",
             "campaign_id": str(i), "campaign_name": f"Campaign {i}",
             "channel": "search", "currency": "INR", "spend": 1,
             "connection_id": 1}
            for i in range(3)
        ])
        original_query = platform.query
        rule_queries = 0

        def counting_query(sql, params=()):
            nonlocal rule_queries
            if "transformation_rules" in sql:
                rule_queries += 1
            return original_query(sql, params)

        with patch.object(platform, "query", side_effect=counting_query):
            rows = api.campaigns("2026-10-08", "2026-10-08", "google_ads", None)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rule_queries, 1,
                         "one campaign report must not query transformation rules per cell")

    def test_campaign_pagination_rejects_negative_offset(self):
        with self.assertRaises(ValueError):
            api.campaigns("2026-10-08", "2026-10-08", offset=-1)
        with self.assertRaises(ValueError):
            api.campaigns("2026-10-08", "2026-10-08", source="typo")

    def test_crm_lead_selection_prefers_enabled_direct_source_per_day(self):
        db.execute("INSERT INTO connections(id, provider, name, enabled) VALUES (1, 'csv', 'legacy', 1)")
        db.execute("INSERT INTO connections(id, provider, name, enabled) VALUES (2, 'superleap_crm', 'direct', 1)")
        db.execute("INSERT INTO connections(id, provider, name, enabled) VALUES (3, 'superleap_crm', 'disabled', 0)")
        db.upsert("crm_leads", [
            {"external_id": "old1", "created_date": "2026-10-08", "connection_id": 1},
            {"external_id": "direct1", "created_date": "2026-10-08", "connection_id": 2},
            {"external_id": "old2", "created_date": "2026-10-09", "connection_id": 1},
            {"external_id": "disabled1", "created_date": "2026-10-09", "connection_id": 3},
            {"external_id": "unlinked", "created_date": "2026-10-10", "connection_id": 999},
        ])
        rows = db.query("SELECT external_id FROM " + api.CRM_LEADS_FROM + " ORDER BY external_id")
        self.assertEqual([row[0] for row in rows], ["direct1", "old2", "unlinked"])


if __name__ == "__main__":
    unittest.main()
