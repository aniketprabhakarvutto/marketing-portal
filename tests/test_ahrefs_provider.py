"""Ahrefs snapshots must use a date Ahrefs has reached in UTC."""

import unittest
from datetime import date
from unittest.mock import patch

from portal.providers.ahrefs import AhrefsProvider
from portal.providers.base import Connection


class AhrefsProviderTests(unittest.TestCase):
    def test_future_local_day_uses_current_utc_day(self) -> None:
        requested_dates = []

        def fake_get(url, *, headers, params):
            requested_dates.append(params["date"])
            if url.endswith("domain-rating"):
                return {"domain_rating": {"domain_rating": 42}}
            return {"metrics": {}}

        conn = Connection(12, "ahrefs", "Site", {"target": "example.com", "country": "in"},
                          {"api_key": "test"})
        with patch("portal.providers.ahrefs._utc_today", return_value=date(2026, 10, 8),
                   create=True), patch("portal.providers.ahrefs.get", side_effect=fake_get):
            rows = AhrefsProvider().fetch(conn, date(2026, 10, 6), date(2026, 10, 9))

        self.assertEqual(["2026-10-08"] * 3, requested_dates)
        self.assertEqual("2026-10-08", rows[0]["date"])


if __name__ == "__main__":
    unittest.main()
