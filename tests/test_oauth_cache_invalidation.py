from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import connections, db, oauth
from portal.config import APP
from portal.providers.base import Connection, OAuth2


class OAuthCacheInvalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_db_path = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
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
        self.oauth_spec = OAuth2("https://example.invalid/auth", "https://example.invalid/token", ["scope"])
        oauth._tokens.clear()

    def tearDown(self) -> None:
        oauth._tokens.clear()
        for item in reversed(self.patches):
            item.stop()
        db.close_connection()
        APP.db_path = self.old_db_path
        self.temp.cleanup()

    def _create(self):
        return connections.create("ga4", "Analytics", {"client_id": "test-client"},
                                  {"client_secret": "test-secret", "refresh_token": "old-refresh"})

    @staticmethod
    def _refresh(_url, **kwargs):
        return {"access_token": kwargs["data"]["refresh_token"], "expires_in": 3600}

    def test_rotated_refresh_token_is_used_immediately_after_update(self) -> None:
        original = self._create()
        with patch("portal.oauth.post", side_effect=self._refresh) as refresh:
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
            rotated = connections.update(original.id, secrets={"refresh_token": "new-refresh"})
            self.assertEqual(oauth.access_token(rotated, self.oauth_spec), "new-refresh")
        self.assertEqual(refresh.call_count, 2)

    def test_failed_update_keeps_existing_cached_token(self) -> None:
        original = self._create()
        with patch("portal.oauth.post", side_effect=self._refresh) as refresh:
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
            db.execute("CREATE TRIGGER block_update BEFORE UPDATE ON connections "
                       "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            with self.assertRaises(sqlite3.DatabaseError):
                connections.update(original.id, secrets={"refresh_token": "new-refresh"})
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
        self.assertEqual(refresh.call_count, 1)

    def test_delete_invalidates_cached_token_for_reused_connection_id(self) -> None:
        original = self._create()
        with patch("portal.oauth.post", side_effect=self._refresh) as refresh:
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
            self.assertTrue(connections.delete(original.id))
            replacement = Connection(original.id, "ga4", "Replacement", original.config,
                                     {"client_secret": "test-secret", "refresh_token": "new-refresh"})
            self.assertEqual(oauth.access_token(replacement, self.oauth_spec), "new-refresh")
        self.assertEqual(refresh.call_count, 2)

    def test_failed_delete_keeps_existing_cached_token(self) -> None:
        original = self._create()
        with patch("portal.oauth.post", side_effect=self._refresh) as refresh:
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
            db.execute("CREATE TRIGGER block_delete BEFORE DELETE ON connections "
                       "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            with self.assertRaises(sqlite3.DatabaseError):
                connections.delete(original.id)
            self.assertEqual(oauth.access_token(original, self.oauth_spec), "old-refresh")
        self.assertEqual(refresh.call_count, 1)


if __name__ == "__main__":
    unittest.main()
