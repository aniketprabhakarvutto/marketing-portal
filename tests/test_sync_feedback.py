from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, connections, db, sync
from portal.config import APP
from portal.providers.base import Connection, Provider


class FeedbackProvider(Provider):
    id = "feedback_test"
    label = "Feedback test"
    table = "paid_metrics"

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        return [row.copy() for row in self.rows]


class SyncFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.original_db_path = APP.db_path
        self.original_database_url = APP.database_url
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()
        self.provider = FeedbackProvider()
        self.registry_patch = patch.dict(sync.REGISTRY, {self.provider.id: self.provider})
        self.registry_patch.start()
        self.connection = connections.create(self.provider.id, "Zero-row source", {}, {})

    def tearDown(self) -> None:
        self.registry_patch.stop()
        db.close_connection()
        APP.db_path = self.original_db_path
        APP.database_url = self.original_database_url
        self.temp.cleanup()

    def test_empty_fetch_does_not_claim_success_or_remove_existing_rows(self) -> None:
        day = date(2026, 10, 6)
        self.provider.rows = [{
            "date": day.isoformat(), "source": "google_ads", "account_id": "test",
            "campaign_id": "campaign", "campaign_name": "Campaign", "spend": 100,
        }]
        first = sync.run_connection(self.connection, day, day)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["rows"], 1)
        first_success_at = next(c for c in api.connections() if c["id"] == self.connection.id)["last_success_at"]

        self.provider.rows = []
        second = sync.run_connection(self.connection, day, day)
        self.assertEqual(second["status"], "empty")
        self.assertEqual(second["rows"], 0)
        self.assertIn("No rows returned", second["message"])
        saved = next(c for c in api.connections() if c["id"] == self.connection.id)
        self.assertEqual(saved["last_status"], "empty")
        self.assertEqual(saved["last_success_at"], first_success_at)
        self.assertEqual(saved["rows_held"], 1)

    def test_never_successful_empty_fetch_has_no_success_time(self) -> None:
        day = date(2026, 10, 6)
        result = sync.run_connection(self.connection, day, day)
        self.assertEqual(result["status"], "empty")
        saved = next(c for c in api.connections() if c["id"] == self.connection.id)
        self.assertIsNone(saved["last_success_at"])
        runs = api.syncs(limit=1)
        self.assertEqual(runs[0]["status"], "empty")
        self.assertEqual(runs[0]["rows"], 0)

    def test_run_all_uses_portal_date_and_inclusive_day_count(self) -> None:
        with patch("portal.api.portal_today", return_value=date(2026, 10, 19)), \
             patch.object(self.provider, "fetch", wraps=self.provider.fetch) as fetch:
            sync.run_all(lookback_days=3, connection_id=self.connection.id)

        fetch.assert_called_once_with(
            self.connection, date(2026, 10, 17), date(2026, 10, 19)
        )


if __name__ == "__main__":
    unittest.main()
