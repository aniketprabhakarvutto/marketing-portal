from __future__ import annotations

import unittest
import sqlite3
import shutil
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, db, mcp, oauth, platform, server
from portal.config import APP
from portal.providers.linkedin import VERSION as LINKEDIN_VERSION
from portal.httpclient import validate_public_https_url
from portal.providers.generic import IMPORT_ROOT, _local_csv_paths
from portal.providers.meta import MetaAdsProvider
from portal.providers.base import Connection


class PortalSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.start = "2026-08-27"
        cls.end = "2026-09-25"

    def setUp(self) -> None:
        db.close_connection()
        self._old_db_path = APP.db_path
        self._temp = TemporaryDirectory()
        APP.db_path = str(Path(self._temp.name) / "portal.db")
        db.init_db()

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self._old_db_path
        self._temp.cleanup()

    def test_future_facts_do_not_advance_the_portal_reporting_date(self) -> None:
        today = api.portal_today()
        yesterday = today - timedelta(days=1)
        future = today + timedelta(days=365)
        db.upsert("paid_metrics", [
            {"date": today.isoformat(), "source": "google_ads", "account_id": "date-test",
             "campaign_id": "today", "campaign_name": "Today", "spend": 10,
             "currency": "INR", "connection_id": 998001},
            {"date": future.isoformat(), "source": "google_ads", "account_id": "date-test",
             "campaign_id": "future", "campaign_name": "Future test row", "spend": 10,
             "currency": "INR", "connection_id": 998001},
        ])

        metadata = api.meta()
        google_ads = next(source for source in metadata["sources"] if source["id"] == "google_ads")

        self.assertEqual(metadata["data_to"], today.isoformat())
        self.assertEqual(google_ads["data_to"], today.isoformat())
        self.assertEqual(api.date_range(None, None)[1], yesterday.isoformat())
        self.assertEqual(api.date_range(yesterday.isoformat(), future.isoformat()),
                         (yesterday.isoformat(), today.isoformat()))
        rows = api.campaigns(today.isoformat(), future.isoformat(), "google_ads")
        self.assertEqual([row["campaign"] for row in rows], ["Today"])

    def test_core_reports_render(self) -> None:
        self.assertIn("current", api.overview(self.start, self.end))
        self.assertIn("series", api.timeseries(self.start, self.end))
        self.assertIsInstance(api.channels(self.start, self.end), list)
        self.assertIsInstance(api.campaigns(self.start, self.end), list)
        self.assertIn("funnels", api.crm(self.start, self.end))

    def test_crm_freshness_is_scoped_to_the_selected_range(self) -> None:
        result = api.crm(self.start, self.end)
        if result["campaign_data_to"] is not None:
            self.assertLessEqual(result["campaign_data_to"], self.end)
        if result["funnel_data_to"] is not None:
            self.assertLessEqual(result["funnel_data_to"], self.end)

    def test_delayed_source_explains_missing_ahrefs_connection(self) -> None:
        stale = (api.portal_today() - timedelta(days=15)).isoformat()
        db.upsert("ahrefs_metrics", [{"date": stale, "target": "example.com", "country": "in",
                                      "connection_id": 990001}])
        source = next(x for x in api.meta()["sources"] if x["id"] == "ahrefs")
        self.assertEqual(source["status"], "delayed")
        self.assertIn("No active Ahrefs connection", source["detail"])

    def test_failed_crm_file_connection_overrides_delayed_age(self) -> None:
        stale = (api.portal_today() - timedelta(days=5)).isoformat()
        db.upsert("crm_daily", [{"date": stale, "channel": "test", "source": "test",
                                 "lead_type": "demand", "connection_id": 990002}])
        saved = [{"provider": "csv", "name": "Funnel export", "enabled": True,
                  "config": {"target_table": "crm_daily"}, "last_status": "error",
                  "last_message": "CSV file missing"}]
        with patch("portal.api.connections", return_value=saved):
            source = next(x for x in api.meta()["sources"] if x["id"] == "crm")
        self.assertEqual(source["status"], "error")
        self.assertIn("CSV file missing", source["detail"])

    def test_quality_checks_have_known_statuses(self) -> None:
        result = api.data_quality(self.start, self.end)
        self.assertEqual(len(result["checks"]), 9)
        self.assertTrue(all(check["status"] in {"pass", "warn", "fail"}
                            for check in result["checks"]))
        self.assertTrue(all(check.get("next_step") and check.get("action_route")
                            and check.get("action_label") for check in result["checks"]))
        self.assertEqual(sum(result["summary"].values()), len(result["checks"]))

    def test_missing_revenue_is_not_reported_as_zero_roas(self) -> None:
        result = api.overview(self.start, self.end)
        if not any(check["id"] == "revenue" and check["status"] == "pass"
                   for check in api.data_quality(self.start, self.end)["checks"]):
            self.assertIsNone(result["current"]["revenue"])
            self.assertIsNone(result["current"]["roas"])

    def test_frontend_contains_production_controls(self) -> None:
        html = Path("portal/web/index.html").read_text(encoding="utf-8")
        script = Path("portal/web/app.js").read_text(encoding="utf-8")
        self.assertIn('id="sourceSelect"', html)
        self.assertIn('id="qualityList"', html)
        self.assertIn('id="automationScheduleLabel"', html)
        self.assertIn('data-quality-route', script)
        self.assertIn('quality-next', script)
        self.assertIn('"Paused · background scheduler is off"', script)
        self.assertIn('Background schedule paused. Manual sync is available below.', script)
        self.assertIn("Planned feature. Execution service required.", script)
        self.assertIn("/api/job-runs/${x.id}/download", script)
        self.assertIn("Sync succeeded", script)
        self.assertIn("API or file template", script)
        self.assertIn('id="homeIconButton" class="home-icon-button" href="#overview"', html)
        self.assertIn('aria-label="Home"', html)
        self.assertIn('id="nav-overview"', html)
        self.assertIn('"Portal report answer"', script)
        self.assertIn('id="settingsInviteForm"', html)
        self.assertIn('data-settings-content="security"', html)
        self.assertIn("does not grant access to the Performance Gmail sender", html)
        self.assertIn("localhost only works on this computer", html)
        self.assertIn('"/api/invitations"', script)
        self.assertIn('data-settings-revoke-invite', script)

    def test_governed_explorer_and_planning_are_queryable(self) -> None:
        result = platform.explore({"start": self.start, "end": self.end,
                                   "dimensions": ["campaign", "platform"],
                                   "metrics": ["spend", "clicks"], "limit": 10})
        self.assertEqual(result["columns"], ["campaign", "platform", "currency", "spend", "clicks"])
        self.assertLessEqual(result["count"], 10)
        planning = platform.planning(self.start, self.end)
        self.assertEqual(planning["model"], "run-rate")
        self.assertIn("not a causal prediction", planning["warning"])

    def test_explorer_merges_campaigns_after_applying_transformation_rules(self) -> None:
        day = (api.portal_today() - timedelta(days=1)).isoformat()
        db.upsert("paid_metrics", [
            {"date": day, "source": "google_ads", "account_id": "rules-test",
             "campaign_id": "rules-a", "campaign_name": "Demand Test A",
             "spend": 100, "currency": "INR", "connection_id": 997001},
            {"date": day, "source": "google_ads", "account_id": "rules-test",
             "campaign_id": "rules-b", "campaign_name": "Demand Test B",
             "spend": 250, "currency": "INR", "connection_id": 997001},
        ])
        platform.save_rule({"name": "Group demand tests", "field": "campaign_name",
                            "match_type": "contains", "pattern": "Demand Test",
                            "output": "Standard demand campaign"})

        result = platform.explore({"start": day, "end": day, "dimensions": ["campaign"],
                                   "metrics": ["spend"], "limit": 20})

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["campaign"], "Standard demand campaign")
        self.assertEqual(result["rows"][0]["spend"], 350)

    def test_channel_transformation_keeps_paid_and_crm_totals_together(self) -> None:
        day = (api.portal_today() - timedelta(days=1)).isoformat()
        db.upsert("paid_metrics", [{
            "date": day, "source": "google_ads", "account_id": "channel-rule-test",
            "campaign_id": "channel-rule-campaign", "campaign_name": "Channel Rule",
            "spend": 100, "currency": "INR", "connection_id": 997002,
        }])
        db.upsert("crm_daily", [{
            "date": day, "channel": "google_ads", "source": "google_ads",
            "lead_type": "buy", "leads": 5, "connection_id": 997003,
        }])
        platform.save_rule({"name": "Standard Google channel", "field": "channel",
                            "match_type": "equals", "pattern": "google_ads",
                            "output": "Google Paid"})

        rows = api.channels(day, day)
        google = next(row for row in rows if row["channel"] == "Google Paid")

        self.assertEqual(google["spend"], 100)
        self.assertEqual(google["leads"], 5)
        self.assertTrue(google["is_paid"])

    def test_campaign_attribution_is_not_described_as_multi_touch(self) -> None:
        result = platform.planning(self.start, self.end)

        self.assertEqual(result["attribution"]["status"], "campaign_level_only")
        self.assertIn("multi-touch", result["attribution"]["note"].lower())

    def test_workspace_exposes_multi_select_explorer_and_custom_dashboard_cards(self) -> None:
        html = Path("portal/web/index.html").read_text(encoding="utf-8")
        script = Path("portal/web/app.js").read_text(encoding="utf-8")
        styles = Path("portal/web/styles.css").read_text(encoding="utf-8")

        self.assertIn('data-explorer-dimension', html)
        self.assertIn('data-explorer-metric', html)
        self.assertIn('id="explorerSort"', html)
        self.assertIn('data-dashboard-metric value="roas"', html)
        self.assertIn("selectedExplorerDimensions", script)
        self.assertIn("selectedDashboardMetrics", script)
        self.assertIn(".workspace-actions select", styles)

    def test_created_dashboard_can_be_removed_and_audited(self) -> None:
        dashboard = platform.save_object("dashboards", {
            "name": "Removable dashboard", "description": "Test", "layout": ["spend"],
            "shared": True,
        })

        result = platform.delete_dashboard(dashboard["id"])

        self.assertEqual(result["deleted"], dashboard["id"])
        self.assertFalse(any(x["id"] == dashboard["id"] for x in platform.list_objects("dashboards")))
        self.assertEqual(platform.audit_entries(1)[0]["action"], "delete")

    def test_saved_workspace_items_can_be_removed(self) -> None:
        view = platform.save_object("saved_explorer_views", {"name": "Temporary view"})
        destination = platform.save_object("data_destinations", {
            "name": "Temporary download", "kind": "download", "config": {},
        })
        job = platform.save_object("scheduled_jobs", {
            "name": "Temporary report", "job_type": "report", "schedule": "daily",
            "definition": {},
        })
        rule = platform.save_rule({
            "name": "Temporary rule", "field": "channel", "match_type": "equals",
            "pattern": "Old", "output": "New",
        })
        for table, item in (("saved_explorer_views", view),
                            ("data_destinations", destination),
                            ("scheduled_jobs", job),
                            ("transformation_rules", rule)):
            self.assertEqual(platform.delete_object(table, item["id"])["deleted"], item["id"])
            self.assertFalse(db.query(f"SELECT id FROM {table} WHERE id=?", (item["id"],)))

    def test_destination_used_by_job_cannot_be_removed(self) -> None:
        destination = platform.save_object("data_destinations", {
            "name": "In use", "kind": "download", "config": {},
        })
        platform.save_object("scheduled_jobs", {
            "name": "Uses destination", "job_type": "report", "schedule": "daily",
            "definition": {"destination_id": destination["id"]},
        })
        with self.assertRaisesRegex(ValueError, "scheduled job"):
            platform.delete_object("data_destinations", destination["id"])

    def test_rule_name_is_saved_once(self) -> None:
        data = {"name": "Channel mapping", "field": "channel", "match_type": "equals",
                "pattern": "Old", "output": "New"}
        first = platform.save_rule(data)
        second = platform.save_rule({**data, "output": "Current"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(db.query("SELECT id FROM transformation_rules WHERE name=?", (data["name"],))), 1)

    def test_backup_job_can_be_saved(self) -> None:
        job = platform.save_object("scheduled_jobs", {
            "name": "Daily backup", "job_type": "backup", "schedule": "daily",
            "definition": {},
        })
        self.assertEqual(job["job_type"], "backup")

    def test_existing_jobs_and_history_survive_backup_job_migration(self) -> None:
        conn = db.connect()
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE scheduled_jobs")
        conn.execute("""CREATE TABLE scheduled_jobs (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL CHECK(job_type IN ('sync','report','alert','quality','snapshot','webhook')),
            schedule TEXT NOT NULL DEFAULT 'daily', run_at TEXT NOT NULL DEFAULT '09:00',
            definition TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT, next_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")
        conn.execute("INSERT INTO scheduled_jobs(name,job_type) VALUES('Existing report','report')")
        conn.execute("INSERT INTO job_runs(job_id,status) VALUES(1,'ok')")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")

        db._migrate_backup_job_type(conn)

        self.assertEqual(db.scalar("SELECT COUNT(*) FROM job_runs WHERE job_id=1"), 1)
        self.assertEqual(db.scalar("SELECT name FROM scheduled_jobs WHERE id=1"), "Existing report")
        self.assertTrue(Path(APP.db_path + ".before-backup-job-migration").is_file())
        platform.save_object("scheduled_jobs", {"name": "New backup", "job_type": "backup"})

    def test_connector_icons_do_not_depend_on_remote_image_urls(self) -> None:
        script = Path("portal/web/app.js").read_text(encoding="utf-8")
        self.assertNotIn("api.iconify.design", script)
        self.assertNotIn("google.com/s2/favicons", script)

    def test_meta_system_user_token_is_ready_without_oauth(self) -> None:
        provider = MetaAdsProvider()
        connection = Connection(1, "meta_ads", "Meta account", {"account_ids": "act_123"},
                                {"access_token": "test-token"})
        self.assertEqual(provider.missing(connection), [])
        self.assertTrue(provider.ready(connection))

    def test_duplicate_connection_name_is_rejected_cleanly(self) -> None:
        from portal import connections
        connections.create("csv", "Company export", {"path": "dummy.csv"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            connections.create("csv", "company export", {"path": "dummy.csv"})

    def test_dashboard_removal_is_admin_only_and_has_a_confirmation_action(self) -> None:
        script = Path("portal/web/app.js").read_text(encoding="utf-8")
        handler = object.__new__(server.Handler)

        self.assertIn("data-delete-dashboard", script)
        self.assertIn("confirmDashboardDelete", script)
        self.assertTrue(any(pattern.fullmatch("/api/dashboards/7/delete") for pattern, _ in server.POST_PATTERNS))
        self.assertTrue(handler._role_allows("admin", "POST", "/api/dashboards/7/delete"))
        self.assertFalse(handler._role_allows("analyst", "POST", "/api/dashboards/7/delete"))

    def test_workspace_marks_unimplemented_destinations_unavailable(self) -> None:
        html = Path("portal/web/index.html").read_text(encoding="utf-8")
        script = Path("portal/web/app.js").read_text(encoding="utf-8")

        self.assertRegex(html, r'<option value="google_sheets"[^>]*disabled')
        self.assertRegex(html, r'<option value="webhook"[^>]*disabled')
        self.assertRegex(html, r'<option value="warehouse"[^>]*disabled')
        self.assertIn("Not available yet", script)

    def test_unimplemented_destination_is_rejected_by_server(self) -> None:
        with self.assertRaisesRegex(ValueError, "not implemented"):
            platform.save_object("data_destinations", {
                "name": "Unavailable Sheets destination", "kind": "google_sheets",
                "config": {"spreadsheet_url": "https://docs.google.com/spreadsheets/d/example"},
                "enabled": True,
            })

    def test_existing_unimplemented_destination_is_labelled_unavailable(self) -> None:
        db.execute("INSERT INTO data_destinations(name,kind,config,enabled,status) VALUES(?,?,?,?,?)",
                   ("Old webhook destination", "webhook", "{}", 1, "ready"))

        item = next(x for x in platform.list_objects("data_destinations") if x["name"] == "Old webhook destination")

        self.assertEqual(item["status"], "not_available")

    def test_email_destination_readiness_uses_gmail_and_recipient(self) -> None:
        destination = platform.save_object("data_destinations", {
            "name": "Readiness email", "kind": "email",
            "config": {"recipient": "manager@example.test"}, "enabled": True,
        })

        with patch("portal.connections.all_connections", return_value=[]):
            item = next(x for x in platform.list_objects("data_destinations") if x["id"] == destination["id"])

        self.assertEqual(item["status"], "needs Gmail access")

    def test_scheduled_report_can_deliver_to_configured_gmail_sender(self) -> None:
        destination = platform.save_object("data_destinations", {
            "name": "Test report email", "kind": "email",
            "config": {"recipient": "manager@example.test"}, "enabled": True,
        })
        job = platform.save_object("scheduled_jobs", {
            "name": "Test emailed report", "job_type": "report", "schedule": "daily",
            "run_at": "09:00", "definition": {"destination_id": destination["id"]},
            "enabled": True,
        })
        from portal.providers.base import Connection
        gmail = Connection(1, "gmail", "Portal report sender",
                           {"client_id": "test-client", "client_secret": "test-secret"},
                           {"refresh_token": "test"})
        with patch("portal.connections.all_connections", return_value=[gmail]), \
             patch("portal.providers.gmail.GmailProvider.send_report", create=True,
                   return_value={"message_id": "test-message"}) as send_report:
            result = platform.run_job(job["id"])

        self.assertEqual(result["status"], "ok")
        self.assertIn("manager@example.test", result["message"])
        self.assertEqual(send_report.call_args.args[1], "manager@example.test")
        self.assertTrue(send_report.call_args.args[4])
        self.assertTrue(send_report.call_args.args[5])

    def test_failed_email_job_history_explains_missing_gmail_setup(self) -> None:
        destination = platform.save_object("data_destinations", {
            "name": "Unconfigured report email", "kind": "email",
            "config": {"recipient": "manager@example.test"}, "enabled": True,
        })
        job = platform.save_object("scheduled_jobs", {
            "name": "Unconfigured email report", "job_type": "report", "schedule": "daily",
            "run_at": "09:00", "definition": {"destination_id": destination["id"]},
            "enabled": True,
        })

        with self.assertRaisesRegex(ValueError, "send-only Gmail account"):
            platform.run_job(job["id"])

        failed = db.query("SELECT status,message FROM job_runs WHERE job_id=?", (job["id"],))[0]
        self.assertEqual(failed["status"], "error")
        self.assertIn("Connect and authorise a send-only Gmail account", failed["message"])

    def test_unimplemented_webhook_job_does_not_report_success(self) -> None:
        job = platform.save_object("scheduled_jobs", {
            "name": "Not implemented webhook", "job_type": "webhook", "schedule": "daily",
            "run_at": "09:00", "definition": {}, "enabled": True,
        })

        with self.assertRaisesRegex(ValueError, "not implemented"):
            platform.run_job(job["id"])

        failed = db.query("SELECT status FROM job_runs WHERE job_id=?", (job["id"],))[0]
        self.assertEqual(failed["status"], "error")

    def test_scheduled_sync_records_partial_failures_instead_of_success(self) -> None:
        job = platform.save_object("scheduled_jobs", {
            "name": "Test partial sync", "job_type": "sync", "schedule": "daily",
            "run_at": "09:00", "definition": {}, "enabled": True,
        })
        results = [
            {"provider": "google_ads", "name": "Ads", "status": "ok", "rows": 12},
            {"provider": "ga4", "name": "Analytics", "status": "error", "rows": 0},
        ]

        with patch("portal.sync.run_all", return_value=results), \
             self.assertRaisesRegex(ValueError, "Analytics"):
            platform.run_job(job["id"])

        run = db.query("SELECT status,message FROM job_runs WHERE job_id=?", (job["id"],))[0]
        self.assertEqual(run["status"], "error")
        self.assertIn("1 of 2", run["message"])

    def test_scheduled_sync_reports_actual_completed_rows(self) -> None:
        job = platform.save_object("scheduled_jobs", {
            "name": "Test successful sync", "job_type": "sync", "schedule": "daily",
            "run_at": "09:00", "definition": {}, "enabled": True,
        })

        with patch("portal.sync.run_all", return_value=[
            {"provider": "google_ads", "name": "Ads", "status": "ok", "rows": 12},
        ]):
            result = platform.run_job(job["id"])

        self.assertEqual(result["status"], "ok")
        self.assertIn("12 rows", result["message"])

    def test_saved_dashboard_names_are_case_insensitive(self) -> None:
        first = platform.save_object("dashboards", {"name": "Executive report", "layout": []})
        second = platform.save_object("dashboards", {"name": "executive REPORT", "layout": []})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM dashboards"), 1)

    def test_scheduler_does_not_repeat_a_job_after_utc_midnight_in_local_day(self) -> None:
        from portal import scheduler

        old_timezone = APP.timezone
        try:
            APP.timezone = "Asia/Kolkata"
            job = {"last_run_at": "2026-10-07 19:35:00", "schedule": "daily", "run_at": "01:00"}

            due = platform._job_due(job, "2026-10-08", "01:05")
            full_sync_date = scheduler._local_run_date("2026-10-07 19:35:00")

            self.assertFalse(due)
            self.assertEqual(full_sync_date.isoformat(), "2026-10-08")
        finally:
            APP.timezone = old_timezone

    def test_mcp_exposes_governed_read_only_tools(self) -> None:
        names = {definition["name"] for definition, _ in mcp.TOOLS}
        self.assertTrue({"marketing_data_quality", "marketing_planning", "marketing_explorer"} <= names)
        response = mcp.handle_message({"id": 1, "method": "tools/call", "params": {
            "name": "marketing_explorer", "arguments": {"start": self.start, "end": self.end, "limit": 2}}})
        self.assertFalse(response["result"]["isError"])

    def test_online_database_backup_is_readable(self) -> None:
        with TemporaryDirectory() as temp:
            backup = Path(temp) / "portal-backup.db"
            db.execute("INSERT INTO portal_settings(key, value) VALUES (?, ?)", ("restore_test", "preserved"))
            db.backup_database(str(backup))
            self.assertTrue(backup.exists())
            with sqlite3.connect(backup) as restored:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(restored.execute("SELECT value FROM portal_settings WHERE key=?", ("restore_test",)).fetchone()[0], "preserved")
            restored_path = Path(temp) / "restored.db"
            shutil.copyfile(backup, restored_path)
            with sqlite3.connect(restored_path) as restored:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(restored.execute("SELECT value FROM portal_settings WHERE key=?", ("restore_test",)).fetchone()[0], "preserved")
            self.assertEqual(db.scalar("SELECT 1"), 1)

    def test_generic_connector_blocks_private_network_targets(self) -> None:
        for url in ("http://example.com/data", "https://127.0.0.1/data", "https://localhost/data"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_https_url(url)
        with self.assertRaises(ValueError):
            validate_public_https_url("https://example.com/data", ["api.vendor.test"])

    def test_csv_imports_are_confined_to_extracts(self) -> None:
        with self.assertRaises(RuntimeError):
            _local_csv_paths("/etc/passwd")
        self.assertTrue(IMPORT_ROOT.name == "extracts")

    def test_web_api_requires_an_authenticated_role(self) -> None:
        script = Path("portal/server.py").read_text(encoding="utf-8")
        handler = server.Handler.__new__(server.Handler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"CF-Connecting-IP": "203.0.113.10"}
        self.assertFalse(handler._trusted_local_request())
        self.assertIn("portal_session", script)
        self.assertTrue(handler._role_allows("viewer", "GET", "/"))
        self.assertTrue(handler._role_allows("viewer", "GET", "/static/app.js"))
        self.assertTrue(handler._role_allows("viewer", "GET", "/api/overview"))
        self.assertFalse(handler._role_allows("viewer", "GET", "/api/workspace"))
        self.assertFalse(handler._role_allows("analyst", "POST", "/api/connections"))
        self.assertFalse(handler._role_allows("viewer", "GET", "/api/new-future-endpoint"))

    def test_default_sync_window_uses_portal_date_and_inclusive_days(self) -> None:
        with patch.object(api, "portal_today", return_value=date(2026, 10, 19)), \
             patch.object(APP, "sync_lookback_days", 30):
            self.assertEqual(
                server._window({}),
                (date(2026, 9, 20), date(2026, 10, 19)),
            )

    def test_derived_cost_comparisons_require_paid_and_crm_coverage(self) -> None:
        script = Path("portal/api.py").read_text(encoding="utf-8")
        self.assertIn('"cpl": ("paid", "crm")', script)
        self.assertIn('t["leads"] += int(r["leads"])', script)

    def test_paid_platform_filters_show_only_currently_used_platforms(self) -> None:
        expected = {"google_ads", "meta_ads", "linkedin_ads", "tiktok_ads"}
        self.assertEqual(set(api.PAID_SOURCES), expected)
        html = Path("portal/web/index.html").read_text(encoding="utf-8")
        for source in {"google_ads", "meta_ads"}:
            self.assertIn(f'data-src="{source}"', html)
        for source in {"linkedin_ads", "tiktok_ads"}:
            self.assertNotIn(f'data-src="{source}"', html)

    def test_calculator_separates_crm_roas_from_platform_roas(self) -> None:
        script = Path("portal/web/app.js").read_text(encoding="utf-8")
        self.assertIn('roas: { name: "CRM ROAS"', script)
        self.assertIn('platform_roas: { name: "Platform ROAS"', script)
        self.assertIn('numerator: "crm_revenue"', script)
        self.assertIn('"campaign-revenue"', Path("portal/api.py").read_text(encoding="utf-8"))

    def test_linkedin_uses_current_versioned_api_header(self) -> None:
        self.assertEqual(LINKEDIN_VERSION, "202609")

    def test_production_oauth_redirect_override_requires_https(self) -> None:
        previous = APP.oauth_redirect_uri
        try:
            APP.oauth_redirect_uri = "https://portal.example.com/oauth/callback"
            self.assertEqual(oauth.redirect_uri(), APP.oauth_redirect_uri)
            APP.oauth_redirect_uri = "http://portal.example.com/oauth/callback"
            with self.assertRaises(ValueError):
                oauth.redirect_uri()
        finally:
            APP.oauth_redirect_uri = previous

    def test_mixed_paid_currencies_withhold_blended_money_metrics(self) -> None:
        start = end = (api.portal_today() - timedelta(days=1)).isoformat()
        db.upsert("paid_metrics", [
            {"date": start, "source": "google_ads", "account_id": "currency-usd",
             "campaign_id": "currency-usd-campaign", "campaign_name": "Currency USD",
             "spend": 100, "impressions": 1000, "clicks": 50, "currency": "USD",
             "connection_id": 993001},
            {"date": start, "source": "meta_ads", "account_id": "currency-inr",
             "campaign_id": "currency-inr-campaign", "campaign_name": "Currency INR",
             "spend": 500, "impressions": 1000, "clicks": 40, "currency": "INR",
             "connection_id": 993002},
        ])
        totals, available, _ = api._snapshot(start, end)
        self.assertTrue(available["paid"])
        self.assertIsNone(totals["spend"])
        self.assertIsNone(totals["cpc"])
        check = next(item for item in api.data_quality(start, end)["checks"]
                     if item["id"] == "currency")
        self.assertEqual(check["status"], "warn")

    def test_paid_platform_totals_sum_before_rounding_and_keep_sources_separate(self) -> None:
        db.upsert("paid_metrics", [
            {"date": "2026-10-07", "source": "google_ads", "account_id": "precision",
             "campaign_id": "g1", "campaign_name": "Google 1", "spend": 10.004,
             "conversion_value": 5, "clicks": 2, "currency": "INR", "connection_id": 997003},
            {"date": "2026-10-07", "source": "google_ads", "account_id": "precision",
             "campaign_id": "g2", "campaign_name": "Google 2", "spend": 10.004,
             "conversion_value": 7, "clicks": 3, "currency": "INR", "connection_id": 997003},
            {"date": "2026-10-07", "source": "meta_ads", "account_id": "precision",
             "campaign_id": "m1", "campaign_name": "Meta 1", "spend": 7,
             "conversion_value": 2, "clicks": 4, "currency": "INR", "connection_id": 997004},
        ])
        google = api.paid_platform_totals("2026-10-07", "2026-10-07", "google_ads")
        meta = api.paid_platform_totals("2026-10-07", "2026-10-07", "meta_ads")
        self.assertAlmostEqual(google["spend"], 20.008)
        self.assertEqual(google["clicks"], 5)
        self.assertEqual(google["conversion_value"], 12)
        self.assertEqual(meta["spend"], 7)

    def test_budget_pacing_deduplicates_daily_budget_across_adsets_and_sums_days(self) -> None:
        db.upsert("paid_metrics", [
            {"date": "2026-10-06", "source": "google_ads", "account_id": "budget",
             "campaign_id": "c1", "campaign_name": "Campaign 1", "adset_id": "a1",
             "daily_budget": 100, "spend": 30, "currency": "INR", "connection_id": 997005},
            {"date": "2026-10-06", "source": "google_ads", "account_id": "budget",
             "campaign_id": "c1", "campaign_name": "Campaign 1", "adset_id": "a2",
             "daily_budget": 100, "spend": 20, "currency": "INR", "connection_id": 997005},
            {"date": "2026-10-06", "source": "meta_ads", "account_id": "budget",
             "campaign_id": "c2", "campaign_name": "Campaign 2", "adset_id": "",
             "daily_budget": 50,
             "spend": 50, "currency": "INR", "connection_id": 997006},
            {"date": "2026-10-07", "source": "google_ads", "account_id": "budget",
             "campaign_id": "c1", "campaign_name": "Campaign 1", "adset_id": "a1",
             "daily_budget": 120, "spend": 70, "currency": "INR", "connection_id": 997005},
        ])
        pacing = api.budget_pacing("2026-10-06", "2026-10-07")
        self.assertAlmostEqual(pacing["spend"], 170)
        self.assertAlmostEqual(pacing["planned_budget"], 270)
        self.assertAlmostEqual(pacing["pacing_percent"], 170 / 270 * 100)

    def test_quality_flags_crm_ids_repeated_across_connections(self) -> None:
        day = (api.portal_today() - timedelta(days=1)).isoformat()
        db.upsert("crm_leads", [
            {"external_id": "shared-crm-lead", "created_date": day,
             "utm_campaign": "Demand Test", "connection_id": 995001},
            {"external_id": "shared-crm-lead", "created_date": day,
             "utm_campaign": "Demand Test", "connection_id": 995002},
        ])
        check = next(item for item in api.data_quality(day, day)["checks"]
                     if item["id"] == "duplicates")
        self.assertEqual(check["status"], "warn")
        self.assertIn("multiple CRM connections", check["detail"])

    def test_campaign_revenue_uses_campaign_tagged_crm_revenue(self) -> None:
        day = (api.portal_today() - timedelta(days=1)).isoformat()
        campaign = "Demand Metric Test Campaign"
        db.upsert("paid_metrics", [{
            "date": day, "source": "google_ads", "account_id": "metric-test-account",
            "campaign_id": "metric-test-campaign", "campaign_name": campaign,
            "channel": "SEARCH", "spend": 1000, "impressions": 1000, "clicks": 100,
            "conversions": 5, "conversion_value": 2200, "currency": "INR",
            "connection_id": 994001,
        }])
        db.upsert("crm_campaign_daily", [{
            "date": day, "channel": "google_ads", "campaign_id": "metric-test-campaign",
            "campaign_name": campaign, "lead_type": "demand", "leads": 10, "won": 2,
            "connection_id": 994002,
        }])
        db.upsert("crm_leads", [{
            "external_id": "metric-test-lead", "created_date": day,
            "utm_campaign": campaign, "channel": "google_ads", "lead_type": "demand",
            "is_sale": 1, "revenue": 12000, "connection_id": 994002,
        }])
        rows = api.campaigns(day, day, "google_ads")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["crm_revenue"], 12000)
        self.assertEqual(rows[0]["crm_revenue_currency"], APP.currency.upper())


if __name__ == "__main__":
    unittest.main()
