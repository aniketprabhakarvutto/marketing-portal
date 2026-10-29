from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from portal import db
from portal.config import APP


class _FakePostgresConnection:
    def __init__(self) -> None:
        self.closed = False
        self.rollbacks = 0
        self.commits = 0

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


class PostgresPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_database_url = APP.database_url
        APP.database_url = "postgresql://portal.test/portal"
        db.close_connection()
        db._reset_postgres_pool_for_testing()
        self.created: list[_FakePostgresConnection] = []
        self.connect_kwargs: list[dict] = []
        self.psycopg = types.ModuleType("psycopg")
        self.rows = types.ModuleType("psycopg.rows")
        self.rows.dict_row = object()

        def connect(*_args, **kwargs):
            self.connect_kwargs.append(kwargs)
            raw = _FakePostgresConnection()
            self.created.append(raw)
            return raw

        self.psycopg.connect = connect
        self.modules = {"psycopg": self.psycopg, "psycopg.rows": self.rows}
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()

    def tearDown(self) -> None:
        db.close_connection()
        db._reset_postgres_pool_for_testing()
        APP.database_url = self.old_database_url
        self.module_patch.stop()

    def test_caps_total_postgres_connections_and_times_out_when_all_are_checked_out(self) -> None:
        ready = threading.Barrier(5)
        release = threading.Event()

        def checkout_connection() -> None:
            db.connect()
            ready.wait()
            release.wait(1)
            db.close_connection()

        workers = [threading.Thread(target=checkout_connection) for _ in range(4)]
        for worker in workers:
            worker.start()
        ready.wait()

        with patch.object(db, "_POSTGRES_ACQUIRE_TIMEOUT_SECONDS", 0.05):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "Database temporarily unavailable"):
                db.connect()
            self.assertLess(time.monotonic() - started, 0.5)

        release.set()
        for worker in workers:
            worker.join(1)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(self.created), 4)

    def test_returns_healthy_connection_for_reuse_and_closes_it_when_pool_is_reset(self) -> None:
        first = db.connect()
        raw = first._connection
        db.close_connection()

        second = db.connect()
        self.assertIs(second._connection, raw)
        db.close_connection()

        db._reset_postgres_pool_for_testing()
        self.assertTrue(raw.closed)

    def test_new_postgres_connections_use_the_acquisition_timeout(self) -> None:
        with patch.object(db, "_POSTGRES_ACQUIRE_TIMEOUT_SECONDS", 5):
            db.connect()
        self.assertEqual(self.connect_kwargs[0]["connect_timeout"], 5)

    def test_closes_connection_when_rollback_fails_before_returning_its_slot(self) -> None:
        first = db.connect()
        raw = first._connection

        def failed_rollback() -> None:
            raise RuntimeError("connection lost")

        raw.rollback = failed_rollback
        db.close_connection()
        self.assertTrue(raw.closed)

        second = db.connect()
        self.assertIsNot(second._connection, raw)
        db.close_connection()

    def test_init_db_returns_its_startup_connection_to_the_pool(self) -> None:
        with patch.object(db, "apply_migrations") as migrate:
            db.init_db()

        self.assertEqual(migrate.call_count, 1)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0].commits, 1)
        self.assertEqual(db._postgres_pool.qsize(), db._POSTGRES_POOL_SIZE)


if __name__ == "__main__":
    unittest.main()
