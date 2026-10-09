from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
from unittest.mock import patch
from zoneinfo import ZoneInfo

from portal import db, platform
from portal.config import APP


class ScheduledExecutionTests(unittest.TestCase):
    def setUp(self):
        db.close_connection()
        self.old_path = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        db.init_db()

    def tearDown(self):
        db.close_connection()
        APP.db_path = self.old_path
        self.temp.cleanup()

    def test_failed_scheduled_job_is_reported_and_not_retried_each_minute(self):
        job = platform.save_object("scheduled_jobs", {
            "name": "Unsupported alert delivery", "job_type": "alert",
            "schedule": "daily", "run_at": "00:00", "enabled": True,
        })
        today = datetime.now(ZoneInfo(APP.timezone)).date().isoformat()

        first = platform.run_due_jobs(today, "23:59")
        second = platform.run_due_jobs(today, "23:59")

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["status"], "error")
        self.assertIn("not implemented", first[0]["message"])
        self.assertEqual(second, [])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM job_runs WHERE job_id=?", (job["id"],)), 1)
        self.assertEqual(db.scalar("SELECT status FROM job_runs WHERE job_id=?", (job["id"],)), "error")
        self.assertIsNotNone(db.scalar("SELECT last_run_at FROM scheduled_jobs WHERE id=?", (job["id"],)))

    def test_failed_manual_job_does_not_consume_scheduled_attempt(self):
        job = platform.save_object("scheduled_jobs", {
            "name": "Unsupported manual alert", "job_type": "alert",
            "schedule": "daily", "run_at": "00:00", "enabled": True,
        })

        with self.assertRaisesRegex(ValueError, "not implemented"):
            platform.run_job(job["id"])

        self.assertIsNone(db.query("SELECT last_run_at FROM scheduled_jobs WHERE id=?", (job["id"],))[0]["last_run_at"])

    def test_each_report_run_keeps_its_original_file(self):
        job = platform.save_object("scheduled_jobs", {
            "name":"Historical report", "job_type":"report", "schedule":"daily",
            "run_at":"09:00", "definition":{}, "enabled":True,
        })
        with patch.object(platform, "ROOT", Path(self.temp.name)), \
             patch("portal.automation.build_csv", side_effect=[("report.csv",b"first"),("report.csv",b"second")]):
            first = platform.run_job(job["id"])
            second = platform.run_job(job["id"])
        self.assertNotEqual(first["artifact"],second["artifact"])
        self.assertEqual(Path(first["artifact"]).read_bytes(),b"first")
        self.assertEqual(Path(second["artifact"]).read_bytes(),b"second")

    def test_manual_and_scheduled_overlap_execute_job_once(self):
        job = platform.save_object("scheduled_jobs", {
            "name":"Concurrent report", "job_type":"report", "schedule":"daily",
            "run_at":"00:00", "definition":{}, "enabled":True,
        })
        entered,release = threading.Event(),threading.Event()
        results,errors = [],[]
        def build(*args):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test report did not release")
            return "report.csv",b"once"
        def first():
            try:
                results.append(platform.run_job(job["id"]))
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close_connection()
        with patch.object(platform,"ROOT",Path(self.temp.name)), \
             patch("portal.automation.build_csv",side_effect=build) as build_report:
            worker = threading.Thread(target=first)
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                with self.assertRaisesRegex(ValueError,"already running"):
                    platform.run_job(job["id"])
                today = datetime.now(ZoneInfo(APP.timezone)).date().isoformat()
                self.assertEqual(platform.run_due_jobs(today,"23:59"),[])
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertEqual(build_report.call_count,1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(len(results),1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM job_runs"),1)

    def test_interrupted_run_recovery_marks_error_without_repeating_delivery(self):
        job = platform.save_object("scheduled_jobs", {
            "name":"Interrupted delivery", "job_type":"report", "schedule":"daily",
            "run_at":"00:00", "definition":{}, "enabled":True,
        })
        db.execute("INSERT INTO job_runs(job_id,status,message) VALUES(?,'running','Starting')",(job["id"],))
        self.assertEqual(platform.recover_interrupted_jobs(),1)
        run = db.query("SELECT * FROM job_runs")[0]
        self.assertEqual(run["status"],"error")
        self.assertIn("interrupted",run["message"])
        today = datetime.now(ZoneInfo(APP.timezone)).date().isoformat()
        self.assertEqual(platform.run_due_jobs(today,"23:59"),[])
        self.assertEqual(platform.recover_interrupted_jobs(),0)

    def test_second_server_cannot_recover_the_live_servers_jobs(self):
        from portal import server
        with patch.object(server,"init_db"), \
             patch.object(server,"ThreadingHTTPServer",side_effect=OSError("Address in use")), \
             patch("portal.sync.recover_interrupted_runs") as sync_recovery, \
             patch.object(platform,"recover_interrupted_jobs") as job_recovery:
            with self.assertRaisesRegex(OSError,"Address in use"):
                server.build_server()
        sync_recovery.assert_not_called()
        job_recovery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
