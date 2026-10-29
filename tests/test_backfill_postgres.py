from __future__ import annotations

import sqlite3
import unittest

from scripts.backfill_postgres import source_counts


class BackfillPostgresTests(unittest.TestCase):
    def test_legacy_sqlite_missing_later_table_counts_as_empty(self) -> None:
        source = sqlite3.connect(":memory:")
        try:
            source.execute("CREATE TABLE paid_metrics (id INTEGER PRIMARY KEY)")
            source.execute("INSERT INTO paid_metrics VALUES (1)")
            counts = source_counts(source)
        finally:
            source.close()

        self.assertEqual(counts["paid_metrics"], 1)
        self.assertEqual(counts["crm_campaign_revenue_daily"], 0)


if __name__ == "__main__":
    unittest.main()
