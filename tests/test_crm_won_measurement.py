"""Campaign feed conflicts compare sales only when both feeds supply sales."""
from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from portal import api, db
from portal.config import APP
from scripts.data_quality_worklist import build_worklists, write_worklists


class CampaignWonMeasurementTests(unittest.TestCase):
    day = "2026-10-08"

    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_path = APP.db_path
        self.old_database_url = APP.database_url
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()
        self._connection(14, "superleap_crm", "Direct CRM")
        self._connection(8, "google_sheets", "Lead-only Sheet", {"leads": "F"})
        self._connection(7, "csv", "Sales CSV", {"leads": "Leads", "won": "Sales"})

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        APP.database_url = self.old_database_url
        self.temp.cleanup()

    @staticmethod
    def _connection(connection_id: int, provider: str, name: str,
                    field_map: dict[str, str] | None = None) -> None:
        config = {"target_table": "crm_campaign_daily", "field_map": json.dumps(field_map or {})}
        db.execute("INSERT INTO connections (id,provider,name,config) VALUES (?,?,?,?)",
                   (connection_id, provider, name, json.dumps(config)))

    def _campaign(self, connection_id: int, leads: int, won: int,
                  campaign: str = "Demand Test") -> None:
        db.upsert("crm_campaign_daily", [{"date": self.day, "channel": "google_ads",
            "campaign_name": campaign, "lead_type": "buy", "leads": leads,
            "won": won, "connection_id": connection_id}])

    def _overlap_check(self) -> dict:
        return next(check for check in api.data_quality(self.day, self.day)["checks"]
                    if check["id"] == "crm-feed-overlap")

    def test_unmeasured_sheet_zero_does_not_conflict_with_direct_sales(self) -> None:
        self._campaign(14, 5, 2)
        self._campaign(8, 5, 0)

        check = self._overlap_check()
        self.assertEqual(check["status"], "pass")
        self.assertIn("identical on comparable fields", check["detail"])
        self.assertEqual(build_worklists(self.day, self.day)["feed_conflicts"], [])

    def test_two_measured_feeds_still_conflict_on_sales(self) -> None:
        self._campaign(14, 5, 2)
        self._campaign(7, 5, 0)

        check = self._overlap_check()
        self.assertEqual(check["status"], "warn")
        self.assertIn("1 campaign/day/lead-side group", check["detail"])
        rows = build_worklists(self.day, self.day)["feed_conflicts"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["won"] for row in rows}, {0, 2})
        self.assertTrue(all(row["won_measured"] for row in rows))

    def test_lead_disagreement_remains_and_unmeasured_sales_export_blank(self) -> None:
        self._campaign(14, 5, 2)
        self._campaign(8, 3, 0)

        self.assertEqual(self._overlap_check()["status"], "warn")
        rows = build_worklists(self.day, self.day)["feed_conflicts"]
        self.assertEqual(len(rows), 2)
        sheet = next(row for row in rows if row["connection_id"] == 8)
        self.assertFalse(sheet["won_measured"])
        self.assertIsNone(sheet["won"])
        output = Path(self.temp.name) / "worklist"
        write_worklists(self.day, self.day, output)
        with (output / "feed_conflicts.csv").open(newline="", encoding="utf-8") as handle:
            exported = list(csv.DictReader(handle))
        sheet_export = next(row for row in exported if row["connection_id"] == "8")
        self.assertEqual(sheet_export["won"], "")
        self.assertEqual(sheet_export["won_measured"], "False")

    def test_one_measured_feed_does_not_conflict_with_two_unmeasured_feeds(self) -> None:
        self._connection(9, "google_sheets", "Second Lead-only Sheet", {"leads": "F"})
        self._campaign(14, 5, 2)
        self._campaign(8, 5, 0)
        self._campaign(9, 5, 0)
        self.assertEqual(self._overlap_check()["status"], "pass")


if __name__ == "__main__":
    unittest.main()
