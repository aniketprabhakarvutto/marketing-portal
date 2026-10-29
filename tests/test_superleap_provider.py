from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import db
from portal.config import APP
from portal.httpclient import HttpError
from portal.providers.base import Connection
from portal.providers.superleap import DEFAULT_FIELD_MAP, SuperleapCRMProvider
from portal.rollup import rollup_campaign_daily, rollup_daily
from scripts.rebuild_superleap_flags import rebuild_stage_flags


class SuperleapProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_db_path = APP.db_path
        self.old_database_url = APP.database_url
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()
        self.provider = SuperleapCRMProvider()
        self.conn = Connection(
            id=7, provider="superleap_crm", name="Vutto Superleap",
            config={"object_slug": "opportunity", "field_map": json.dumps(DEFAULT_FIELD_MAP)},
            secrets={"api_key": "test-key"},
        )

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_db_path
        APP.database_url = self.old_database_url
        self.temp.cleanup()

    def test_connection_check_verifies_access_without_fetching_records(self) -> None:
        with patch.object(self.provider, "_request", side_effect=[
            {"status": "ok"},
            {"data": [{"entity_identifier": "opportunity"}]},
        ]) as request:
            message = self.provider.test_connection(self.conn)

        self.assertIn("opportunity object is available", message)
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("/records", str(request.call_args_list))

    def test_request_accepts_a_bearer_prefixed_key_without_double_prefixing(self) -> None:
        self.conn.secrets["api_key"] = "Bearer test-key"
        with patch("portal.providers.superleap.get", return_value={"status": "ok"}) as get:
            self.provider._request("GET", "/appflow/verify/", self.conn)

        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_request_accepts_a_pasted_authorization_header(self) -> None:
        self.conn.secrets["api_key"] = "Authorization: Bearer test-key"
        with patch("portal.providers.superleap.get", return_value={"status": "ok"}) as get:
            self.provider._request("GET", "/appflow/verify/", self.conn)

        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_invalid_key_error_gives_a_clear_recovery_step(self) -> None:
        with patch("portal.providers.superleap.get", side_effect=HttpError(
                401, '{"error":"invalid token"}', "https://app.superleap.com/api/v1/appflow/verify/")):
            with self.assertRaisesRegex(RuntimeError, "Superleap rejected this API key \(401\)"):
                self.provider.test_connection(self.conn)

    def test_fetch_reads_all_cursor_pages_and_normalises_vutto_opportunities(self) -> None:
        schema = {"data": {"fields": [{"field_name": field}
                                      for field in set(DEFAULT_FIELD_MAP.values()) | {"updated_at", "utm_source", "utm_medium"}]}}
        page_one = {"data": {"records": [{
            "id": "lead-1", "created_at": "2026-10-07T19:30:00Z", "vutto_source": "Google",
            "lead_type": "Paid", "utm_campaign": "Jaipur Demand", "lead_category": "buy",
            "customer_city": "Jaipur", "sales_store": "Vutto Jaipur", "stage1": "Store Visit Planned",
            "event_qualification_status": "Qualified",
        }], "next_token": "page-2"}}
        page_two = {"data": {"records": [{
            "id": "lead-2", "created_at": "2026-10-08T04:00:00+05:30", "vutto_source": "Facebook",
            "lead_type": "Organic", "utm_campaign": "", "lead_category": "sell",
            "customer_city": "Delhi", "sales_store": "", "stage1": "Procured",
            "event_qualification_status": True,
        }], "next_token": None}}
        with patch.object(self.provider, "_request", side_effect=[
            {"status": "ok"}, schema, page_one, page_two,
        ]) as request:
            rows = self.provider.fetch(self.conn, date(2026, 10, 1), date(2026, 10, 8))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["created_date"], "2026-10-08")  # UTC instant converted to portal IST date
        self.assertEqual(rows[0]["lead_type"], "buy")
        self.assertEqual(rows[0]["is_qualified"], 0)
        self.assertEqual(rows[0]["is_visit_booked"], 1)
        self.assertEqual(rows[1]["lead_type"], "sell")
        self.assertEqual(rows[1]["is_sale"], 1)
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[2].kwargs["body"]["next_token"], None)
        self.assertIn("utm_source", request.call_args_list[2].kwargs["body"]["query"]["fields"])
        self.assertIn("utm_medium", request.call_args_list[2].kwargs["body"]["query"]["fields"])
        self.assertEqual(request.call_args_list[3].kwargs["body"]["next_token"], "page-2")

    def test_not_connected_opportunity_is_not_counted_as_contacted(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "unreached", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "buy", "stage1": "Not Connected",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual(row["is_contacted"], 0)

    def test_demand_sale_counts_prior_visit_milestones(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "sale", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "buy", "stage1": "Sales Done",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual((row["is_visit_booked"], row["is_visit_done"], row["is_sale"]),
                         (1, 1, 1))

    def test_supply_procurement_counts_prior_inspection_milestones(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "procurement", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "sell", "stage1": "Procured",
            "event_qualification_status": "Qualified",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual((row["is_visit_booked"], row["is_visit_done"], row["is_sale"]),
                         (1, 1, 1))

    def test_paid_source_classifies_when_crm_lead_type_is_blank(self) -> None:
        for source, expected in ((" Google ", "google_ads"), ("Facebook", "meta_ads")):
            with self.subTest(source=source):
                row = self.provider._normalise(self.conn, {
                    "id": "lead-blank-medium", "created_at": "2026-10-08T12:00:00Z",
                    "lead_category": "sell", "stage1": "Not Assigned",
                    "vutto_source": source, "lead_type": "",
                }, DEFAULT_FIELD_MAP)
                self.assertEqual(row["channel"], expected)

        explicitly_organic = self.provider._normalise(self.conn, {
            "id": "lead-organic-medium", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "sell", "stage1": "Not Assigned",
            "vutto_source": "google", "lead_type": "Organic",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual(explicitly_organic["channel"], "organic_search")

    def test_named_vutto_sources_do_not_collapse_into_organic_search(self) -> None:
        cases = (
            ("GMB", "Organic", "gmb"),
            ("store", "Organic", "store"),
            ("olx", "Organic", "olx"),
            ("flipkart", "Organic", "flipkart"),
            ("Influencer", "Organic", "influencer"),
            ("others", "Organic", "others"),
            ("superleap", "Organic", "others"),
            ("cross_funnel", "Paid", "cross_funnel"),
            ("WhatsApp", "Paid", "whatsapp"),
        )
        for source, lead_type, channel in cases:
            with self.subTest(source=source):
                row = self.provider._normalise(self.conn, {
                    "id": "source-lead", "created_at": "2026-10-08T12:00:00Z",
                    "lead_category": "sell", "stage1": "Not Assigned",
                    "vutto_source": source, "lead_type": lead_type,
                }, DEFAULT_FIELD_MAP)
                self.assertEqual(row["channel"], channel)
                self.assertEqual(row["utm_source"], source)
                self.assertEqual(row["utm_medium"], lead_type)

    def test_unclassified_source_uses_paid_utm_only_with_matching_evidence(self) -> None:
        base = {"id": "tagged", "created_at": "2026-10-08T12:00:00Z",
                "lead_category": "buy", "stage1": "Not Assigned",
                "vutto_source": "others", "lead_type": "paid", "utm_source": "ig"}
        row = self.provider._normalise(self.conn, {**base, "utm_medium": "paid"}, DEFAULT_FIELD_MAP)
        self.assertEqual(row["channel"], "meta_ads")
        self.assertEqual(row["utm_source"], "others")
        for changes in ({"utm_medium": "organic"}, {"utm_medium": ""},
                        {"utm_medium": "paid", "lead_type": "organic"}):
            with self.subTest(changes=changes):
                row = self.provider._normalise(self.conn, {**base, **changes}, DEFAULT_FIELD_MAP)
                self.assertEqual(row["channel"], "others")

    def test_supply_contacted_uses_the_documented_stage_pipeline(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "unqualified", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "sell", "stage1": "Follow Up Planned",
            "event_qualification_status": "Invalid",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual(row["is_contacted"], 1)

    def test_underscore_stage_variant_keeps_inspection_milestones(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "inspection", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "sell", "stage1": "inspection_done",
            "event_qualification_status": "Qualified",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual((row["is_visit_booked"], row["is_visit_done"]), (1, 1))

    def test_un_manned_procurement_planned_counts_as_booked(self) -> None:
        row = self.provider._normalise(self.conn, {
            "id": "unmanned", "created_at": "2026-10-08T12:00:00Z",
            "lead_category": "sell", "stage1": "Un-Manned Procurement Planned",
            "event_qualification_status": "Qualified",
        }, DEFAULT_FIELD_MAP)
        self.assertEqual(row["is_visit_booked"], 1)
        self.assertEqual(row["is_visit_done"], 0)

    def test_records_page_accepts_list_data_and_camel_case_cursor(self) -> None:
        records, cursor = self.provider._records_page({
            "success": True,
            "data": {"items": [{"id": "lead-1"}], "nextToken": "page-2"},
        })
        self.assertEqual(records, [{"id": "lead-1"}])
        self.assertEqual(cursor, "page-2")

        records, cursor = self.provider._records_page({"data": [{"id": "lead-2"}]})
        self.assertEqual(records, [{"id": "lead-2"}])
        self.assertIsNone(cursor)

    def test_records_page_reports_safe_shape_details_for_unknown_envelope(self) -> None:
        with self.assertRaisesRegex(RuntimeError, r"payload type: dict; keys: message, status"):
            self.provider._records_page({"data": {"status": "ok", "message": "No records"}})

    def test_records_page_does_not_misreport_null_records_as_empty_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "records=null with a pagination token"):
            self.provider._records_page({"data": {"records": None, "next_token": "opaque-cursor"}})

    def test_fetch_accepts_null_records_only_as_terminal_cursor_page(self) -> None:
        schema = {"data": {"fields": [{"field_name": field}
                                    for field in set(DEFAULT_FIELD_MAP.values()) | {"updated_at"}]}}
        first = {"success": True, "data": {"records": [{
            "id": "lead-1", "created_at": "2026-10-08T12:00:00Z", "stage1": "Not Assigned",
        }], "next_token": "page-2"}}
        terminal = {"success": True, "data": {"records": None, "next_token": ""}}
        with patch.object(self.provider, "_request", side_effect=[
            {"status": "ok"}, schema, first, terminal,
        ]) as request:
            rows = self.provider.fetch(self.conn, date(2026, 10, 8), date(2026, 10, 8))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "lead-1")
        self.assertEqual(request.call_count, 4)

        with self.assertRaisesRegex(RuntimeError, "records=null"):
            self.provider._records_page(terminal)

    def test_sync_rollups_create_campaign_facts_without_deleting_other_crm_feeds(self) -> None:
        day = "2026-10-07"
        db.execute("INSERT INTO connections (id, provider, name) VALUES (7, 'superleap_crm', 'Direct CRM')")
        db.upsert("crm_leads", [{
            "external_id": "lead-1", "created_date": day, "utm_source": "Google",
            "utm_medium": "Paid", "utm_campaign": "Jaipur Demand", "channel": "google_ads",
            "lead_type": "buy", "city": "Jaipur", "stage": "Sales Done", "is_sale": 1, "revenue": 1200,
            "connection_id": 7,
        }])
        db.upsert("crm_daily", [{
            "date": day, "channel": "legacy", "source": "sheet", "lead_type": "sell",
            "leads": 4, "connection_id": 99,
        }])

        rollup_daily(date.fromisoformat(day), date.fromisoformat(day))
        rollup_campaign_daily(date.fromisoformat(day), date.fromisoformat(day), 7)

        daily = db.query("SELECT leads FROM crm_daily WHERE connection_id=7 AND date=?", (day,))
        legacy = db.query("SELECT leads FROM crm_daily WHERE connection_id=99 AND date=?", (day,))
        campaign = db.query("SELECT campaign_name, leads, won FROM crm_campaign_daily WHERE connection_id=7")
        revenue = db.query("SELECT campaign_name, revenue FROM crm_campaign_revenue_daily WHERE connection_id=7")
        self.assertEqual(daily[0]["leads"], 1)
        self.assertEqual(legacy[0]["leads"], 4)
        self.assertEqual((campaign[0]["campaign_name"], campaign[0]["leads"], campaign[0]["won"]),
                         ("Jaipur Demand", 1, 1))
        self.assertEqual((revenue[0]["campaign_name"], revenue[0]["revenue"]), ("Jaipur Demand", 1200))

    def test_stage_rebuild_repairs_direct_rows_and_preserves_legacy_aggregate(self) -> None:
        day = "2026-10-07"
        db.execute("INSERT INTO connections (id, provider, name, config) VALUES (?, ?, ?, ?)",
                   (7, "superleap_crm", "Direct CRM",
                    json.dumps({"field_map": DEFAULT_FIELD_MAP})))
        db.upsert("crm_leads", [
            {"external_id": "unreached", "created_date": day, "lead_type": "buy",
             "stage": "Not Connected", "is_contacted": 1, "connection_id": 7},
            {"external_id": "sale", "created_date": day, "lead_type": "buy",
             "stage": "Sales Done", "is_contacted": 1, "is_sale": 1,
             "connection_id": 7},
        ])
        db.upsert("crm_daily", [{
            "date": day, "channel": "legacy", "source": "sheet",
            "lead_type": "sell", "leads": 4, "connection_id": 99,
        }])
        repaired = rebuild_stage_flags(7)
        leads = db.query("SELECT external_id, is_contacted, is_visit_booked, is_visit_done "
                         "FROM crm_leads WHERE connection_id=7 ORDER BY external_id")
        self.assertEqual(repaired, 2)
        self.assertEqual([(r["external_id"], r["is_contacted"], r["is_visit_booked"], r["is_visit_done"])
                          for r in leads], [("sale", 1, 1, 1), ("unreached", 0, 0, 0)])
        self.assertEqual(db.scalar("SELECT SUM(leads) FROM crm_daily WHERE connection_id=7"), 2)
        self.assertEqual(db.scalar("SELECT SUM(leads) FROM crm_daily WHERE connection_id=99"), 4)


if __name__ == "__main__":
    unittest.main()
