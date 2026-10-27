from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import connections, db, oauth
from portal.config import APP
from portal.providers.base import Connection, OAuth2


class OAuthRefreshRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_db_path = APP.db_path
        self.old_database_url = APP.database_url
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        db.init_db()
        self.keychain = {}
        self.patches = [
            patch("portal.secret_store.put", side_effect=lambda ref, values:
                  self.keychain.__setitem__(ref, values.copy())),
            patch("portal.secret_store.get", side_effect=lambda ref: self.keychain[ref].copy()),
            patch("portal.secret_store.delete", side_effect=lambda ref: self.keychain.pop(ref)),
        ]
        for item in self.patches:
            item.start()
        self.spec = OAuth2("https://example.invalid/auth", "https://example.invalid/token", ["scope"])
        oauth._tokens.clear()
        oauth._generations.clear()

    def tearDown(self) -> None:
        oauth._tokens.clear()
        oauth._generations.clear()
        for item in reversed(self.patches):
            item.stop()
        db.close_connection()
        APP.db_path = self.old_db_path
        APP.database_url = self.old_database_url
        self.temp.cleanup()

    def _start_old_refresh(self, original: Connection):
        started = threading.Event()
        release = threading.Event()
        result = []
        failures = []

        def refresh(_url, **kwargs):
            token = kwargs["data"]["refresh_token"]
            if token == "old-refresh":
                started.set()
                if not release.wait(5):
                    raise AssertionError("Timed out waiting to release old refresh")
            return {"access_token": token, "expires_in": 3600}

        def work():
            try:
                result.append(oauth.access_token(original, self.spec))
            except Exception as exc:
                failures.append(exc)

        mocked_post = patch("portal.oauth.post", side_effect=refresh)
        post = mocked_post.start()
        self.addCleanup(mocked_post.stop)
        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(started.wait(5), "Old refresh never started")
        return release, thread, result, failures, post

    def _finish_old_refresh(self, release, thread, result, failures):
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "Old refresh did not finish")
        self.assertEqual(failures, [])
        self.assertEqual(result, ["old-refresh"])

    def test_update_does_not_cache_refresh_started_with_old_credentials(self) -> None:
        original = connections.create("ga4", "Analytics", {"client_id": "test-client"},
                                      {"client_secret": "test-secret", "refresh_token": "old-refresh"})
        release, thread, result, failures, post = self._start_old_refresh(original)
        try:
            rotated = connections.update(original.id, secrets={"refresh_token": "new-refresh"})
        finally:
            release.set()
        self._finish_old_refresh(release, thread, result, failures)
        self.assertEqual(oauth.access_token(rotated, self.spec), "new-refresh")
        self.assertEqual(post.call_count, 2)

    def test_delete_does_not_cache_refresh_for_reused_connection_id(self) -> None:
        original = connections.create("ga4", "Analytics", {"client_id": "test-client"},
                                      {"client_secret": "test-secret", "refresh_token": "old-refresh"})
        release, thread, result, failures, post = self._start_old_refresh(original)
        try:
            self.assertTrue(connections.delete(original.id))
        finally:
            release.set()
        self._finish_old_refresh(release, thread, result, failures)
        replacement = Connection(original.id, "ga4", "Replacement", original.config,
                                 {"client_secret": "test-secret", "refresh_token": "new-refresh"})
        self.assertEqual(oauth.access_token(replacement, self.spec), "new-refresh")
        self.assertEqual(post.call_count, 2)

    def test_stale_connection_snapshot_cannot_poison_cache_after_update(self) -> None:
        original = connections.create("ga4", "Analytics", {"client_id": "test-client"},
                                      {"client_secret": "test-secret", "refresh_token": "old-refresh"})
        rotated = connections.update(original.id, secrets={"refresh_token": "new-refresh"})

        def refresh(_url, **kwargs):
            return {"access_token": kwargs["data"]["refresh_token"], "expires_in": 3600}

        with patch("portal.oauth.post", side_effect=refresh) as post:
            self.assertEqual(oauth.access_token(original, self.spec), "old-refresh")
            self.assertEqual(oauth.access_token(rotated, self.spec), "new-refresh")
        self.assertEqual(post.call_count, 2)

    def test_missing_access_token_does_not_expose_provider_payload(self) -> None:
        connection = Connection(
            1, "ga4", "Analytics", {"client_id": "test-client"},
            {"client_secret": "test-secret", "refresh_token": "test-refresh"},
        )
        with patch("portal.oauth.post", return_value={"refresh_token": "provider-secret"}):
            with self.assertRaisesRegex(RuntimeError, r"^Refresh returned no access token\.$") as error:
                oauth.access_token(connection, self.spec)
        self.assertNotIn("provider-secret", str(error.exception))

        with patch("portal.oauth.post", return_value={"refresh_token": "exchange-secret"}):
            with self.assertRaisesRegex(RuntimeError, r"^Token exchange returned no access token\.$") as error:
                oauth.exchange_code(connection, self.spec, "one-time-code")
        self.assertNotIn("exchange-secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
