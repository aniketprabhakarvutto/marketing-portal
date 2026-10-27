from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portal import db
from portal.config import APP
from scripts.data_quality_worklist import build_worklists, write_worklists


class QualityWorklistTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_path = APP.db_path
        self.old_database_url = APP.database_url
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()
        db.execute("INSERT INTO connections (id,provider,name) VALUES (14,'superleap_crm','Direct CRM')")
        db.execute("INSERT INTO connections (id,provider,name) VALUES (8,'google_sheets','CRM Sheet')")

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        APP.database_url = self.old_database_url
        self.temp.cleanup()

    def test_conflicts_preserve_both_counts_and_selected_source(self) -> None:
        day = "2026-10-08"
        db.upsert("crm_campaign_daily", [
            {"date": day, "channel": "google_ads", "campaign_name": "Jaipur Demand",
             "lead_type": "buy", "leads": 12, "won": 1, "connection_id": 14},
            {"date": day, "channel": "google_ads", "campaign_name": "Jaipur Demand",
             "lead_type": "demand", "leads": 6, "won": 0, "connection_id": 8},
        ])

        result = build_worklists(day, day)

        self.assertEqual(len(result["feed_conflicts"]), 2)
        self.assertEqual({row["leads"] for row in result["feed_conflicts"]}, {12, 6})
        selected = [row for row in result["feed_conflicts"] if row["selected_for_reporting"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source"], "Direct CRM")

    def test_unqualified_supply_and_other_channel_rows_remain_traceable(self) -> None:
        day = "2026-10-08"
        db.upsert("crm_leads", [{
            "external_id": "lead-42", "created_date": day, "lead_type": "sell",
            "stage": "Inspection Done", "is_qualified": 0, "is_visit_done": 1,
            "utm_campaign": "Supply Test", "connection_id": 14,
        }])
        db.upsert("crm_campaign_daily", [{
            "date": day, "channel": "other", "campaign_name": "Supply Test",
            "lead_type": "sell", "leads": 1, "connection_id": 14,
        }])

        result = build_worklists(day, day)

        self.assertEqual(result["unqualified_supply"][0]["external_id"], "lead-42")
        self.assertEqual(result["unqualified_supply"][0]["stage"], "Inspection Done")
        self.assertEqual(result["other_channels"][0]["campaign"], "Supply Test")

    def test_export_explains_counts_and_does_not_claim_the_source_was_fixed(self) -> None:
        day = "2026-10-08"
        output = Path(self.temp.name) / "quality"

        counts = write_worklists(day, day, output)

        self.assertEqual(counts, {"feed_conflicts": 0, "other_channels": 0,
                                  "unqualified_supply": 0})
        notes = (output / "README.md").read_text(encoding="utf-8")
        self.assertIn("2026-10-08", notes)
        self.assertIn("No source records were changed", notes)
        self.assertIn("feed_conflicts: 0", notes)


if __name__ == "__main__":
    unittest.main()
