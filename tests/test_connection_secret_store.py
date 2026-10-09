"""Credential persistence must leave SQLite without recoverable secret values."""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from portal import connections, db, secret_store
from portal.config import APP


class FakeKeychain:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def put(self, reference: str, values: dict) -> None:
        self.items[reference] = values.copy()

    def get(self, reference: str) -> dict:
        if reference not in self.items:
            raise secret_store.SecretStoreError("Credential unavailable")
        return self.items[reference].copy()

    def delete(self, reference: str) -> None:
        if reference not in self.items:
            raise secret_store.SecretStoreError("Credential unavailable")
        del self.items[reference]


class ConnectionSecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.previous_db = APP.db_path
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        db.init_db()
        self.keychain = FakeKeychain()
        self.patches = [
            patch("portal.secret_store.put", side_effect=self.keychain.put),
            patch("portal.secret_store.get", side_effect=self.keychain.get),
            patch("portal.secret_store.delete", side_effect=self.keychain.delete),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        db.close_connection()
        APP.db_path = self.previous_db
        self.temp.cleanup()

    def raw(self, connection_id: int) -> str:
        return db.query("SELECT secrets FROM connections WHERE id=?", (connection_id,))[0]["secrets"]

    def test_create_update_and_delete_keep_secrets_out_of_sqlite(self) -> None:
        created = connections.create("superleap_crm", "CRM", {}, {"api_key": "private-first"})
        self.assertEqual(created.secrets, {"api_key": "private-first"})
        self.assertNotIn("private-first", self.raw(created.id))
        first_reference = secret_store.reference(self.raw(created.id))
        self.assertIn(first_reference, self.keychain.items)

        updated = connections.update(created.id, secrets={"api_key": "private-second"})
        self.assertEqual(updated.secrets, {"api_key": "private-second"})
        self.assertNotIn("private-second", self.raw(created.id))
        self.assertNotIn(first_reference, self.keychain.items)
        new_reference = secret_store.reference(self.raw(created.id))
        self.assertIn(new_reference, self.keychain.items)

        self.assertTrue(connections.delete(created.id))
        self.assertNotIn(new_reference, self.keychain.items)

    def test_keychain_failure_does_not_write_plaintext_or_change_connection(self) -> None:
        created = connections.create("ahrefs", "SEO", {}, {"api_key": "original"})
        original_row = self.raw(created.id)
        with patch("portal.secret_store.put", side_effect=secret_store.SecretStoreError("locked")):
            with self.assertRaises(secret_store.SecretStoreError):
                connections.update(created.id, secrets={"api_key": "replacement"})
        self.assertEqual(self.raw(created.id), original_row)
        self.assertEqual(connections.get(created.id).secrets, {"api_key": "original"})

    def test_create_failure_does_not_persist_connection(self) -> None:
        with patch("portal.secret_store.put", side_effect=secret_store.SecretStoreError("locked")):
            with self.assertRaises(secret_store.SecretStoreError):
                connections.create("ahrefs", "Cannot save", {}, {"api_key": "private"})
        self.assertEqual(db.query("SELECT id FROM connections"), [])

    def test_sqlite_create_failure_cleans_new_keychain_item(self) -> None:
        db.execute("CREATE TRIGGER deny_secret_create BEFORE INSERT ON connections "
                   "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            connections.create("ahrefs", "Cannot save", {}, {"api_key": "private"})
        self.assertEqual(self.keychain.items, {})
        self.assertEqual(db.query("SELECT id FROM connections"), [])

    def test_sqlite_update_failure_keeps_previous_keychain_item(self) -> None:
        created = connections.create("ahrefs", "SEO", {}, {"api_key": "first"})
        original = self.raw(created.id)
        db.execute("CREATE TRIGGER deny_secret_update BEFORE UPDATE ON connections "
                   "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            connections.update(created.id, secrets={"api_key": "second"})
        self.assertEqual(self.raw(created.id), original)
        self.assertEqual(len(self.keychain.items), 1)
        self.assertEqual(connections.get(created.id).secrets["api_key"], "first")

    def test_missing_keychain_item_does_not_fall_back_to_sqlite(self) -> None:
        created = connections.create("ahrefs", "SEO", {}, {"api_key": "private"})
        self.keychain.items.clear()
        with self.assertRaises(secret_store.SecretStoreError):
            connections.get(created.id)
        self.assertNotIn("private", self.raw(created.id))

    def test_google_oauth_client_reuse_reads_keychain(self) -> None:
        first = connections.create("google_ads", "Ads", {"client_id": "oauth-app"},
                                   {"client_secret": "private-app-secret"})
        second = connections.create("ga4", "Analytics", {}, {})
        self.assertEqual(second.config["client_id"], "oauth-app")
        self.assertEqual(second.secrets["client_secret"], "private-app-secret")
        self.assertNotIn("private-app-secret", self.raw(first.id))
        self.assertNotIn("private-app-secret", self.raw(second.id))

    def test_misplaced_secret_in_config_is_not_exposed(self) -> None:
        created = connections.create("ahrefs", "Misplaced", {"api_key": "hidden", "target": "site.test"}, {})
        row = db.query("SELECT config,secrets FROM connections WHERE id=?", (created.id,))[0]
        self.assertNotIn("hidden", row["config"])
        self.assertNotIn("hidden", row["secrets"])
        self.assertEqual(created.get("api_key"), "hidden")
        self.assertEqual(created.config, {"target": "site.test"})

    def test_delete_failure_preserves_database_row(self) -> None:
        created = connections.create("ahrefs", "SEO", {}, {"api_key": "private"})
        with patch("portal.secret_store.delete", side_effect=secret_store.SecretStoreError("locked")):
            with self.assertRaises(secret_store.SecretStoreError):
                connections.delete(created.id)
        self.assertEqual(connections.get(created.id).secrets["api_key"], "private")

    def test_legacy_plaintext_can_be_migrated_explicitly(self) -> None:
        db.execute("INSERT INTO connections(provider,name,secrets) VALUES (?,?,?)",
                   ("ahrefs", "Old", json.dumps({"api_key": "old-secret"})))
        old = connections.all_connections()[0]
        self.assertEqual(old.secrets["api_key"], "old-secret")
        migrated = connections.migrate_legacy_secrets()
        self.assertEqual(migrated, 1)
        self.assertNotIn("old-secret", self.raw(old.id))
        self.assertEqual(connections.get(old.id).secrets["api_key"], "old-secret")
        self.assertNotIn(b"old-secret", Path(APP.db_path).read_bytes())
        wal_path = Path(APP.db_path + "-wal")
        if wal_path.exists():
            self.assertNotIn(b"old-secret", wal_path.read_bytes())

    def test_migration_removes_legacy_config_secret(self) -> None:
        db.execute("INSERT INTO connections(provider,name,config) VALUES (?,?,?)",
                   ("ahrefs", "Old config", json.dumps({"target": "site.test", "api_key": "misplaced"})))
        connection = connections.all_connections()[0]
        self.assertNotIn("api_key", connection.config)
        self.assertEqual(connection.secrets["api_key"], "misplaced")
        self.assertEqual(connections.migrate_legacy_secrets(), 1)
        row = db.query("SELECT config,secrets FROM connections WHERE id=?", (connection.id,))[0]
        self.assertNotIn("misplaced", row["config"] + row["secrets"])
        self.assertEqual(connections.get(connection.id).secrets["api_key"], "misplaced")

class SecretCommandTests(unittest.TestCase):
    def test_native_keychain_receives_complete_secret_buffer(self) -> None:
        security = Mock()
        core = Mock()
        security.SecKeychainAddGenericPassword.return_value = 0
        value = "private-credential" * 100
        with patch("portal.secret_store._frameworks", return_value=(security, core)):
            secret_store.put("safe-reference", {"api_key": value})
        args = security.SecKeychainAddGenericPassword.call_args.args
        payload = json.dumps({"api_key": value}, separators=(",", ":")).encode()
        self.assertEqual(args[5], len(payload))
        self.assertEqual(args[6].value, payload)

    def test_reference_marker_with_plaintext_is_rejected(self) -> None:
        with self.assertRaises(secret_store.SecretStoreError):
            secret_store.resolve('{"$keychain_ref":"ref","api_key":"private"}')


if __name__ == "__main__":
    unittest.main()
