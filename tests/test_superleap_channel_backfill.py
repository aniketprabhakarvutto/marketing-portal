from __future__ import annotations

import sqlite3
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from portal import db
from portal.config import APP
from portal.rollup import rollup_campaign_daily, rollup_daily
from scripts.backfill_superleap_source_channels import backfill


class SuperleapChannelBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_path = APP.db_path
        self.path = Path(self.temp.name) / "portal.db"
        APP.db_path = str(self.path)
        db.init_db()
        db.execute("INSERT INTO connections (id,provider,name) VALUES (14,'superleap_crm','Direct CRM')")
        db.execute("INSERT INTO connections (id,provider,name) VALUES (8,'google_sheets','Legacy sheet')")
        day = "2026-10-08"
        db.upsert("crm_leads", [
            {"external_id": "blank-google", "created_date": day, "utm_source": "google",
             "utm_medium": "", "utm_campaign": "Campaign A", "channel": "google/none",
             "lead_type": "buy", "connection_id": 14},
            {"external_id": "blank-facebook", "created_date": day, "utm_source": "facebook",
             "utm_medium": "", "utm_campaign": "Campaign B", "channel": "facebook/none",
             "lead_type": "sell", "connection_id": 14},
            {"external_id": "paid-google", "created_date": day, "utm_source": "google",
             "utm_medium": "Paid", "utm_campaign": "Campaign A", "channel": "google_ads",
             "lead_type": "buy", "connection_id": 14},
            {"external_id": "organic-google", "created_date": day, "utm_source": "google",
             "utm_medium": "Organic", "utm_campaign": "Campaign A", "channel": "organic_search",
             "lead_type": "buy", "connection_id": 14},
        ])
        rollup_daily(date.fromisoformat(day), date.fromisoformat(day))
        rollup_campaign_daily(date.fromisoformat(day), date.fromisoformat(day), 14)
        db.upsert("crm_campaign_daily", [{"date": day, "channel": "other",
            "campaign_name": "Legacy Campaign", "lead_type": "sell", "leads": 7,
            "connection_id": 8}])
        db.close_connection()

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        self.temp.cleanup()

    def test_backfill_makes_verified_backup_and_preserves_totals_and_legacy_feed(self) -> None:
        preview = backfill(self.path, 14)
        self.assertEqual(preview["candidates"], 2)
        self.assertIsNone(preview["backup"])

        result = backfill(self.path, 14, apply=True, expected_count=2)
        backup_path = result["backup"]
        self.assertTrue(backup_path.is_file())
        self.assertEqual(backup_path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(backup.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE channel IN ('google/none','facebook/none')"
            ).fetchone()[0], 2)

        with sqlite3.connect(self.path) as stored:
            channels = dict(stored.execute("SELECT external_id,channel FROM crm_leads"))
            self.assertEqual(channels, {"blank-google": "google_ads",
                                        "blank-facebook": "meta_ads",
                                        "paid-google": "google_ads",
                                        "organic-google": "organic_search"})
            self.assertEqual(stored.execute(
                "SELECT SUM(leads) FROM crm_daily WHERE connection_id=14"
            ).fetchone()[0], 4)
            self.assertEqual(stored.execute(
                "SELECT SUM(leads) FROM crm_campaign_daily WHERE connection_id=14"
            ).fetchone()[0], 4)
            self.assertEqual(stored.execute(
                "SELECT channel,leads FROM crm_campaign_daily WHERE connection_id=8"
            ).fetchone(), ("other", 7))
            self.assertEqual(stored.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_count_guard_rejects_unreviewed_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "Expected 3 candidate rows, found 2"):
            backfill(self.path, 14, apply=True, expected_count=3)
        self.assertEqual(len(list(Path(self.temp.name).glob("*.before-source-channel-backfill-*"))), 0)
        with sqlite3.connect(self.path) as stored:
            self.assertEqual(stored.execute(
                "SELECT COUNT(*) FROM crm_leads WHERE channel IN ('google/none','facebook/none')"
            ).fetchone()[0], 2)

    def test_named_source_repair_preserves_raw_values_overrides_and_other_connections(self) -> None:
        day = "2026-10-08"
        db.upsert("crm_leads", [
            {"external_id": "store-organic", "created_date": day, "utm_source": "store",
             "utm_medium": "Organic", "channel": "organic_search", "lead_type": "buy", "connection_id": 14},
            {"external_id": "cross-funnel-paid", "created_date": day, "utm_source": "cross_funnel",
             "utm_medium": "Paid", "channel": "cross_funnel/paid", "lead_type": "sell", "connection_id": 14},
            {"external_id": "explicit-channel", "created_date": day, "utm_source": "GMB",
             "utm_medium": "Organic", "channel": "approved_custom", "lead_type": "buy", "connection_id": 14},
            {"external_id": "sheet-store", "created_date": day, "utm_source": "store",
             "utm_medium": "Organic", "channel": "organic_search", "lead_type": "buy", "connection_id": 8},
        ])
        rollup_daily(date.fromisoformat(day), date.fromisoformat(day))
        rollup_campaign_daily(date.fromisoformat(day), date.fromisoformat(day), 14)
        db.close_connection()
        self.assertEqual(backfill(self.path, 14, named_sources=True)["candidates"], 2)
        backfill(self.path, 14, named_sources=True, apply=True, expected_count=2)
        with sqlite3.connect(self.path) as stored:
            values = dict(stored.execute("SELECT external_id,channel FROM crm_leads"))
            self.assertEqual(values["store-organic"], "store")
            self.assertEqual(values["cross-funnel-paid"], "cross_funnel")
            self.assertEqual(values["explicit-channel"], "approved_custom")
            self.assertEqual(values["sheet-store"], "organic_search")
            self.assertEqual(values["organic-google"], "organic_search")
            self.assertEqual(stored.execute("SELECT utm_source,utm_medium FROM crm_leads WHERE external_id='store-organic'").fetchone(), ("store", "Organic"))

    def test_named_source_repair_rejects_connection_channel_override(self) -> None:
        with sqlite3.connect(self.path) as stored:
            stored.execute("UPDATE connections SET config=? WHERE id=14",
                           ('{"field_map":{"channel":"approved_channel"}}',))
        with self.assertRaisesRegex(ValueError, "no explicit channel override"):
            backfill(self.path, 14, named_sources=True)


if __name__ == "__main__":
    unittest.main()
