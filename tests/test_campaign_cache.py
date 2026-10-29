from __future__ import annotations

import unittest
from unittest.mock import patch

from portal import api


class CampaignCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        api.invalidate_campaign_cache()

    def tearDown(self) -> None:
        api.invalidate_campaign_cache()

    def test_identical_reads_are_cached_and_results_are_isolated(self) -> None:
        expected = [{"campaign": "Cached", "spend": 10}]
        with patch.object(api, "_campaigns_uncached", return_value=expected) as report:
            first = api.campaigns("2026-10-01", "2026-10-28", limit=1000)
            first[0]["spend"] = 999
            second = api.campaigns("2026-10-01", "2026-10-28", limit=1000)
        self.assertEqual(second[0]["spend"], 10)
        report.assert_called_once()

    def test_invalidation_forces_recalculation(self) -> None:
        with patch.object(api, "_campaigns_uncached", side_effect=[[{"campaign": "A"}], [{"campaign": "B"}]]) as report:
            self.assertEqual(api.campaigns("2026-10-01", "2026-10-28")[0]["campaign"], "A")
            api.invalidate_campaign_cache()
            self.assertEqual(api.campaigns("2026-10-01", "2026-10-28")[0]["campaign"], "B")
        self.assertEqual(report.call_count, 2)


class ReportCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        api.invalidate_report_cache()

    def tearDown(self) -> None:
        api.invalidate_report_cache()

    def test_overview_reads_are_cached_and_results_are_isolated(self) -> None:
        expected = {"current": {"spend": 10}}
        with patch.object(api, "_overview_uncached", return_value=expected) as report:
            first = api.overview("2026-10-01", "2026-10-28")
            first["current"]["spend"] = 999
            second = api.overview("2026-10-01", "2026-10-28")
        self.assertEqual(second["current"]["spend"], 10)
        report.assert_called_once()

    def test_report_invalidation_forces_recalculation(self) -> None:
        with patch.object(api, "_channels_uncached", side_effect=[[{"channel": "A"}], [{"channel": "B"}]]) as report:
            self.assertEqual(api.channels("2026-10-01", "2026-10-28")[0]["channel"], "A")
            api.invalidate_report_cache()
            self.assertEqual(api.channels("2026-10-01", "2026-10-28")[0]["channel"], "B")
        self.assertEqual(report.call_count, 2)
