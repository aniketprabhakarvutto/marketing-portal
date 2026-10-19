from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, db
from portal.config import APP


class CRMReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_path = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        db.init_db()
        self.day = (api.portal_today() - timedelta(days=3)).isoformat()
        self.next_day = (api.portal_today() - timedelta(days=2)).isoformat()
        db.execute("INSERT INTO connections (id, provider, name) VALUES (11, 'csv', 'CRM campaign CSV')")
        db.execute("INSERT INTO connections (id, provider, name) VALUES (12, 'google_sheets', 'Demand Sheet')")

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        self.temp.cleanup()

    def test_csv_wins_overlap_and_sheet_supplies_later_date(self) -> None:
        campaign = "Vutto Demand Jaipur"
        db.upsert("paid_metrics", [{
            "date": self.day, "source": "google_ads", "account_id": "a",
            "campaign_id": "paid-1", "campaign_name": campaign, "spend": 100,
            "currency": "INR", "connection_id": 22,
        }])
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "google_ads", "campaign_name": campaign,
             "lead_type": "buy", "leads": 5, "won": 2, "connection_id": 11},
            {"date": self.day, "channel": "google_ads", "campaign_name": campaign,
             "lead_type": "demand", "leads": 9, "won": 0, "connection_id": 12},
            {"date": self.next_day, "channel": "google_ads", "campaign_name": campaign,
             "lead_type": "demand", "leads": 3, "won": 0, "connection_id": 12},
        ])

        paid = api.campaigns(self.day, self.next_day, "google_ads")[0]
        self.assertEqual(paid["crm_leads"], 8)
        self.assertEqual(paid["crm_sales"], 2)
        self.assertEqual(paid["cpl"], 12.5)
        self.assertEqual(paid["cac"], 50)
        self.assertEqual(paid["crm_leads_source"], "CRM campaign CSV, Demand Sheet")

        summary = api.crm(self.day, self.next_day)
        self.assertEqual(sum(row["leads"] for row in summary["campaign_leads"]), 8)
        self.assertEqual(len(summary["campaign_leads"]), 1)
        self.assertEqual(summary["campaign_leads"][0]["source"],
                         "CRM campaign CSV, Demand Sheet")
        self.assertIn("preferred", summary["campaign_feed_policy"])

    def test_blank_campaign_ids_count_distinct_names(self) -> None:
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "meta_ads", "campaign_name": name,
             "lead_type": "supply", "leads": 2, "connection_id": 11}
            for name in ("Supply Scooters", "Supply Commuters")
        ])
        row = api.crm(self.day, self.day)["campaign_leads"][0]
        self.assertEqual(row["campaigns"], 2)
        self.assertEqual(row["leads"], 4)

    def test_empty_platform_funnels_not_returned(self) -> None:
        self.assertEqual(api.crm(self.day, self.day)["channel_funnels"], [])

    def test_absent_funnel_is_not_reported_as_validated(self) -> None:
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "funnel-order")
        self.assertEqual(check["status"], "warn")
        self.assertIn("Not checked", check["detail"])

    def test_demand_funnel_omits_supply_only_qualified_stage(self) -> None:
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 5, "contacted": 3,
            "qualified": 0, "booked": 2, "completed": 1, "won": 1,
            "connection_id": 11,
        }])
        funnel = next(row for row in api.crm(self.day, self.day)["funnels"]
                      if row["side"] == "buy")
        self.assertNotIn("Qualified", [stage["stage"] for stage in funnel["stages"]])

    def test_supply_funnel_places_qualification_before_contact(self) -> None:
        columns = [column for column, _ in api.funnel_shape("sell")]
        self.assertLess(columns.index("qualified"), columns.index("contacted"))

    def test_paid_channel_funnels_keep_demand_and_supply_separate(self) -> None:
        db.upsert("crm_daily", [
            {"date": self.day, "channel": "google_ads", "source": "Google",
             "lead_type": "buy", "leads": 5, "qualified": 0,
             "contacted": 3, "booked": 2,
             "connection_id": 11},
            {"date": self.day, "channel": "google_ads", "source": "Google",
             "lead_type": "sell", "leads": 7, "qualified": 4,
             "contacted": 2, "booked": 0,
             "connection_id": 11},
        ])
        funnels = api.crm(self.day, self.day)["channel_funnels"]
        self.assertEqual({row["side"] for row in funnels}, {"buy", "sell"})
        demand = next(row for row in funnels if row["side"] == "buy")
        self.assertNotIn("Qualified", [stage["stage"] for stage in demand["stages"]])

    def test_quality_does_not_require_supply_qualification_for_demand(self) -> None:
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 5, "contacted": 3,
            "qualified": 0, "booked": 2, "completed": 1, "won": 1,
            "connection_id": 11,
        }])
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "funnel-order")
        self.assertEqual(check["status"], "pass")

    def test_quality_identifies_unqualified_supply_inspection(self) -> None:
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "sell", "leads": 2, "qualified": 0,
            "contacted": 0, "booked": 1, "completed": 0, "won": 0,
            "connection_id": 11,
        }])
        db.upsert("crm_leads", [{
            "external_id": "unqualified-inspection", "created_date": self.day,
            "lead_type": "sell", "is_qualified": 0, "is_visit_booked": 1,
            "connection_id": 11,
        }])
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "funnel-order")
        self.assertEqual(check["status"], "fail")
        self.assertIn("1 supply opportunity", check["detail"])
        self.assertIn("not marked qualified", check["detail"])

    def test_direct_superleap_does_not_require_a_retired_qualification_field(self) -> None:
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "sell", "leads": 2, "qualified": 0,
            "contacted": 0, "booked": 1, "connection_id": 70,
        }])
        with patch("portal.api.connections", return_value=[{
            "provider": "superleap_crm", "name": "Superleap API", "enabled": True,
            "syncable": True, "config": {}, "last_status": "ok",
        }]):
            check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                         if item["id"] == "funnel-order")
        self.assertEqual(check["status"], "pass")
        self.assertIn("no separate qualification field", check["detail"])

    def test_inconsistent_funnel_keeps_unmodified_source_counts(self) -> None:
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "sell", "leads": 2, "qualified": 0,
            "contacted": 0, "booked": 1, "completed": 0, "won": 0,
            "connection_id": 11,
        }])

        funnel = api.crm(self.day, self.day)["funnels"][0]
        stages = {stage["stage"]: stage["count"] for stage in funnel["stages"]}
        self.assertTrue(funnel["invalid_stage_order"])
        self.assertEqual(stages["Qualified"], 0)
        self.assertEqual(stages["Booked"], 1)

    def test_funnel_warns_even_when_invalid_daily_rows_cancel_in_total(self) -> None:
        db.upsert("crm_daily", [
            {"date": self.day, "channel": "google_ads", "source": "Google",
             "lead_type": "sell", "leads": 2, "qualified": 0,
             "contacted": 0, "booked": 1, "connection_id": 11},
            {"date": self.next_day, "channel": "google_ads", "source": "Google",
             "lead_type": "sell", "leads": 20, "qualified": 10,
             "contacted": 8, "booked": 0, "connection_id": 11},
        ])

        funnel = api.crm(self.day, self.next_day)["funnels"][0]

        self.assertTrue(funnel["invalid_stage_order"])
        self.assertEqual(funnel["source_rows_out_of_order"], 1)
        self.assertEqual({s["stage"]: s["count"] for s in funnel["stages"]}["Qualified"], 10)

    def test_disabled_search_console_does_not_fail_enabled_source_coverage(self) -> None:
        db.upsert("paid_metrics", [{
            "date": self.day, "source": "google_ads", "account_id": "a",
            "campaign_id": "c", "campaign_name": "Campaign", "currency": "INR",
            "connection_id": 21,
        }])
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 1, "connection_id": 11,
        }])
        db.upsert("web_metrics", [{
            "date": self.day, "property_id": "p", "session_source": "google",
            "session_medium": "organic", "channel_group": "Organic Search",
            "connection_id": 22,
        }])
        saved = [
            {"provider": provider, "name": provider, "enabled": True,
             "syncable": True, "config": {}, "last_status": "ok"}
            for provider in ("google_ads", "ga4")
        ] + [{"provider": "gsc", "name": "Search Console", "enabled": False,
              "syncable": True, "config": {}, "last_status": "error"}]
        with patch("portal.api.connections", return_value=saved):
            check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                         if item["id"] == "sync-health")
        self.assertEqual(check["status"], "pass")
        self.assertIn("enabled reporting category", check["detail"])

    def test_crm_coverage_detects_gap_before_latest_day(self) -> None:
        final_day = (api.portal_today() - timedelta(days=1)).isoformat()
        first_day = (api.portal_today() - timedelta(days=3)).isoformat()
        db.upsert("crm_daily", [
            {"date": day, "channel": "google_ads", "source": "Google",
             "lead_type": "buy", "leads": 1, "connection_id": 11}
            for day in (first_day, final_day)
        ])
        check = next(item for item in api.data_quality(first_day, final_day)["checks"]
                     if item["id"] == "crm-coverage")
        self.assertEqual(check["status"], "warn")
        self.assertIn("2/3 days", check["detail"])

    def test_other_channel_count_uses_selected_reporting_feed(self) -> None:
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "other", "campaign_name": "Supply Test",
             "lead_type": "sell", "leads": 3, "connection_id": 70},
            {"date": self.day, "channel": "other", "campaign_name": "Supply Test",
             "lead_type": "supply", "leads": 5, "connection_id": 12},
        ])
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "channels")
        self.assertEqual(check["status"], "warn")
        self.assertIn("1 reported CRM campaign group", check["detail"])

    def test_identical_campaign_feeds_are_not_reported_as_conflicting(self) -> None:
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "google_ads", "campaign_name": "Demand Test",
             "lead_type": "buy", "leads": 5, "won": 1, "connection_id": 11},
            {"date": self.day, "channel": "google_ads", "campaign_name": "Demand Test",
             "lead_type": "demand", "leads": 5, "won": 1, "connection_id": 12},
        ])
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "crm-feed-overlap")
        self.assertEqual(check["status"], "pass")
        self.assertIn("identical", check["detail"])

    def test_stale_source_explains_empty_latest_refresh(self) -> None:
        stale = (api.portal_today() - timedelta(days=8)).isoformat()
        db.upsert("crm_campaign_daily", [{
            "date": stale, "channel": "google_ads", "campaign_name": "Old",
            "lead_type": "buy", "leads": 1, "connection_id": 11,
        }])
        saved = [{"provider": "csv", "name": "CRM campaign CSV", "enabled": True,
                  "config": {"target_table": "crm_campaign_daily"},
                  "last_status": "empty", "last_message": "No rows returned"}]
        with patch("portal.api.connections", return_value=saved):
            source = next(item for item in api.meta()["sources"]
                          if item["id"] == "crm_campaign")
        self.assertEqual(source["status"], "delayed")
        self.assertIn("no new rows", source["detail"])

    def test_recent_direct_superleap_sync_is_the_authoritative_crm_feed(self) -> None:
        today = api.portal_today().isoformat()
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.upsert("crm_daily", [{
            "date": today, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 2, "connection_id": 70,
        }])
        db.execute(
            "INSERT INTO sync_runs (connector, connection_id, status, window_start, window_end, "
            "started_at, finished_at, rows_written) VALUES (?, ?, 'ok', ?, ?, datetime('now'), datetime('now'), 2)",
            ("superleap_crm", 70, today, today),
        )

        source = next(item for item in api.meta()["sources"] if item["id"] == "crm")

        self.assertEqual(source["status"], "current")
        self.assertIn("Superleap API checked recently", source["detail"])

    def test_direct_superleap_rows_replace_legacy_daily_exports_on_same_date(self) -> None:
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.execute("INSERT INTO connections (id, provider, name) VALUES (71, 'csv', 'Old funnel export')")
        day = self.day
        db.upsert("crm_daily", [
            {"date": day, "channel": "google_ads", "source": "Google", "lead_type": "buy",
             "leads": 2, "contacted": 2, "qualified": 1, "connection_id": 70},
            {"date": day, "channel": "google_ads", "source": "Google", "lead_type": "buy",
             "leads": 10, "contacted": 9, "qualified": 7, "connection_id": 71},
        ])
        db.upsert("crm_city_daily", [
            {"date": day, "city": "Jaipur", "lead_type": "buy", "leads": 2, "connection_id": 70},
            {"date": day, "city": "Jaipur", "lead_type": "buy", "leads": 10, "connection_id": 71},
        ])

        report = api.crm(day, day)

        self.assertEqual(report["funnels"][0]["stages"][0]["count"], 2)
        self.assertEqual(report["by_city"][0]["leads"], 2)
        # Older dates with no direct API rows still retain the legacy backfill.
        db.upsert("crm_daily", [{
            "date": self.next_day, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 5, "connection_id": 71,
        }])
        self.assertEqual(api.crm(self.next_day, self.next_day)["funnels"][0]["stages"][0]["count"], 5)

    def test_disabled_crm_feeds_do_not_affect_reports_or_quality_checks(self) -> None:
        db.execute("UPDATE connections SET enabled=0 WHERE id=12")
        db.upsert("crm_daily", [{
            "date": self.day, "channel": "google_ads", "source": "Google",
            "lead_type": "buy", "leads": 99, "connection_id": 12,
        }])
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "google_ads", "campaign_name": "Demand Test",
             "lead_type": "buy", "leads": 5, "connection_id": 11},
            {"date": self.day, "channel": "google_ads", "campaign_name": "Demand Test",
             "lead_type": "demand", "leads": 50, "connection_id": 12},
        ])

        funnel = api.crm(self.day, self.day)["funnels"][0]
        self.assertEqual(funnel["stages"][0]["count"], 0)
        rows = api._crm_campaign_reporting_rows(self.day, self.day)
        self.assertEqual([(row["connection_id"], row["leads"]) for row in rows], [(11, 5)])
        check = next(item for item in api.data_quality(self.day, self.day)["checks"]
                     if item["id"] == "crm-feed-overlap")
        self.assertEqual(check["status"], "pass")

    def test_direct_campaign_feed_wins_a_matching_legacy_export(self) -> None:
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.execute("INSERT INTO connections (id, provider, name) VALUES (71, 'csv', 'Old campaign export')")
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "google_ads", "campaign_name": "Vutto Demand Jaipur",
             "lead_type": "buy", "leads": 3, "connection_id": 70},
            {"date": self.day, "channel": "google_ads", "campaign_name": "Vutto Demand Jaipur",
             "lead_type": "buy", "leads": 11, "connection_id": 71},
        ])

        result = api.crm(self.day, self.day)["campaign_leads"]

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["leads"], 3)
        self.assertEqual(result[0]["source"], "Superleap API")

    def test_direct_campaign_replaces_unclassified_legacy_duplicate(self) -> None:
        db.execute("INSERT INTO connections (id, provider, name) VALUES (70, 'superleap_crm', 'Superleap API')")
        db.upsert("crm_campaign_daily", [
            {"date": self.day, "channel": "google_ads", "campaign_name": "Google Supply Jaipur",
             "lead_type": "sell", "leads": 3, "won": 1, "connection_id": 70},
            {"date": self.day, "channel": "other", "campaign_name": "Google Supply Jaipur",
             "lead_type": "supply", "leads": 9, "won": 2, "connection_id": 12},
            {"date": self.next_day, "channel": "other", "campaign_name": "Old unmatched campaign",
             "lead_type": "supply", "leads": 2, "won": 0, "connection_id": 12},
        ])

        rows = api._crm_campaign_reporting_rows(self.day, self.next_day)

        self.assertEqual(len(rows), 2)
        current = next(row for row in rows if row["date"] == self.day)
        self.assertEqual((current["channel"], current["leads"], current["connection_id"]),
                         ("google_ads", 3, 70))
        self.assertEqual(next(row for row in rows if row["date"] == self.next_day)["channel"], "other")


if __name__ == "__main__":
    unittest.main()
