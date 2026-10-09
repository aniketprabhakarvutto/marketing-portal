from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from portal.httpclient import HttpError
from portal.providers.base import Connection
from portal.providers.google import GA4_METRICS_FALLBACK, GA4Provider
from portal.providers.meta import MetaAdsProvider


def ga4_record(campaign: str) -> dict:
    return {
        "dimensionValues": [
            {"value": "20261008"}, {"value": "Paid Search"},
            {"value": "google"}, {"value": "cpc"}, {"value": campaign},
        ],
        "metricValues": [
            {"value": "10"}, {"value": "8"}, {"value": "4"},
            {"value": "6"}, {"value": "2"},
        ],
    }


class GA4PaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = GA4Provider()
        self.connection = Connection(
            10, "ga4", "Analytics", {"property_ids": "123456789"},
            {"refresh_token": "test-refresh"},
        )

    def test_fetch_requests_every_report_page_using_offset(self) -> None:
        requests = []

        def fake_post(url, *, headers, json_body):
            requests.append(json_body)
            offset = json_body["offset"]
            page = [ga4_record("one"), ga4_record("two")] if offset == 0 else [ga4_record("three")]
            return {"rowCount": 3, "rows": page}

        with patch("portal.providers.google.GA4_PAGE_SIZE", 2), \
             patch("portal.providers.google.access_token", return_value="token"), \
             patch("portal.providers.google.post", side_effect=fake_post):
            rows = self.provider.fetch(self.connection, date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual([request["offset"] for request in requests], [0, 2])
        self.assertEqual([row["session_campaign"] for row in rows], ["one", "two", "three"])

    def test_conversions_fallback_is_used_across_all_pages(self) -> None:
        requests = []

        def fake_post(url, *, headers, json_body):
            requests.append(json_body)
            metrics = [metric["name"] for metric in json_body["metrics"]]
            if "conversions" in metrics:
                raise HttpError(400, "Invalid metric: conversions", url)
            offset = json_body["offset"]
            page = [ga4_record("one"), ga4_record("two")] if offset == 0 else [ga4_record("three")]
            return {"rowCount": 3, "rows": page}

        with patch("portal.providers.google.GA4_PAGE_SIZE", 2), \
             patch("portal.providers.google.access_token", return_value="token"), \
             patch("portal.providers.google.post", side_effect=fake_post):
            rows = self.provider.fetch(self.connection, date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual(len(rows), 3)
        self.assertEqual([request["offset"] for request in requests], [0, 0, 2])
        self.assertTrue(all(request["metrics"][-1]["name"] == GA4_METRICS_FALLBACK[-1]
                            for request in requests[1:]))

    def test_empty_page_before_report_row_count_fails_instead_of_returning_partial_data(self) -> None:
        calls = []

        def fake_post(url, *, headers, json_body):
            calls.append(json_body["offset"])
            if json_body["offset"] == 0:
                return {"rowCount": 3, "rows": [ga4_record("one"), ga4_record("two")]}
            return {"rowCount": 3, "rows": []}

        with patch("portal.providers.google.GA4_PAGE_SIZE", 2), \
             patch("portal.providers.google.access_token", return_value="token"), \
             patch("portal.providers.google.post", side_effect=fake_post):
            with self.assertRaisesRegex(RuntimeError, "refusing to save incomplete results"):
                self.provider.fetch(self.connection, date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual(calls, [0, 2])

    def test_short_page_continues_when_row_count_says_more_rows_remain(self) -> None:
        calls = []

        def fake_post(url, *, headers, json_body):
            calls.append(json_body["offset"])
            if json_body["offset"] == 0:
                return {"rowCount": 3, "rows": [ga4_record("one")]}
            return {"rowCount": 3, "rows": [ga4_record("two"), ga4_record("three")]}

        with patch("portal.providers.google.GA4_PAGE_SIZE", 2), \
             patch("portal.providers.google.access_token", return_value="token"), \
             patch("portal.providers.google.post", side_effect=fake_post):
            rows = self.provider.fetch(self.connection, date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual(calls, [0, 1])
        self.assertEqual([row["session_campaign"] for row in rows], ["one", "two", "three"])


class MetaPaginationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MetaAdsProvider()
        self.connection = Connection(
            11, "meta_ads", "Meta", {"account_ids": "123"}, {"access_token": "test-token"},
        )

    def test_insights_returns_rows_from_all_pages(self) -> None:
        calls = []

        def fake_get(url, *, params):
            calls.append((url, params))
            if len(calls) == 1:
                return {"data": [{"campaign_id": "one"}], "paging": {"next": "https://next.invalid/page2"}}
            return {"data": [{"campaign_id": "two"}], "paging": {}}

        with patch("portal.providers.meta.META_INSIGHTS_MAX_PAGES", 2), \
             patch("portal.providers.meta.get", side_effect=fake_get):
            rows = self.provider._insights(self.connection, "123", date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual([row["campaign_id"] for row in rows], ["one", "two"])
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1][1])

    def test_insights_fails_if_page_cap_is_reached_with_another_page_available(self) -> None:
        calls = []

        def fake_get(url, *, params):
            calls.append(url)
            return {"data": [{"campaign_id": str(len(calls))}],
                    "paging": {"next": f"https://next.invalid/page{len(calls) + 1}"}}

        with patch("portal.providers.meta.META_INSIGHTS_MAX_PAGES", 2), \
             patch("portal.providers.meta.get", side_effect=fake_get):
            with self.assertRaisesRegex(RuntimeError, "stopped before saving incomplete results"):
                self.provider._insights(self.connection, "123", date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual(len(calls), 2)

    def test_campaign_metadata_fails_if_page_cap_is_reached_with_another_page(self) -> None:
        calls = []

        def fake_get(url, *, params):
            calls.append(url)
            return {"data": [{"id": str(len(calls))}],
                    "paging": {"next": f"https://next.invalid/page{len(calls) + 1}"}}

        with patch("portal.providers.meta.META_CAMPAIGN_MAX_PAGES", 2), \
             patch("portal.providers.meta.get", side_effect=fake_get):
            with self.assertRaisesRegex(RuntimeError, "stopped before saving incomplete results"):
                self.provider._campaign_metadata(self.connection, "123")

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
