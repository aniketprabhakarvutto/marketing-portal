from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import auth, db, server
from portal.config import APP


ADMIN_PASSWORD = "correct horse battery staple 42"
USER_PASSWORD = "another very long password 123"


class AccessWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_path = APP.db_path
        self.old_secure_cookie = APP.secure_cookie
        APP.db_path = f"{self.temp.name}/portal.db"
        APP.secure_cookie = False
        db.init_db()
        self.admin = auth.create_first_admin("admin@example.com", "Admin", ADMIN_PASSWORD)

    def tearDown(self) -> None:
        db.close_connection()
        APP.db_path = self.old_path
        APP.secure_cookie = self.old_secure_cookie
        self.temp.cleanup()

    def save_user(self, email: str, role: str = "viewer", enabled: bool = True):
        return auth.save_user({
            "email": email, "display_name": "Original name", "role": role,
            "enabled": enabled, "password": USER_PASSWORD,
        }, actor="admin@example.com")

    def test_partial_update_preserves_disabled_role_and_display_name(self) -> None:
        created = self.save_user("analyst@example.com", "analyst", False)

        updated = auth.save_user({"email": "analyst@example.com"}, actor="admin@example.com")

        self.assertEqual(updated["id"], created["id"])
        self.assertEqual(updated["role"], "analyst")
        self.assertEqual(updated["enabled"], 0)
        self.assertEqual(updated["display_name"], "Original name")
        self.assertIsNone(auth.login("analyst@example.com", USER_PASSWORD, "127.0.0.2"))

    def test_disabling_revokes_session_and_reenabling_does_not_restore_it(self) -> None:
        self.save_user("viewer@example.com")
        signed_in = auth.login("viewer@example.com", USER_PASSWORD, "127.0.0.3")
        self.assertIsNotNone(signed_in)
        self.assertIsNotNone(auth.session(signed_in["token"]))

        auth.save_user({"email": "viewer@example.com", "enabled": False},
                       actor="admin@example.com")
        self.assertIsNone(auth.session(signed_in["token"]))
        auth.save_user({"email": "viewer@example.com", "enabled": True},
                       actor="admin@example.com")
        self.assertIsNone(auth.session(signed_in["token"]))

    def test_expired_session_is_rejected(self) -> None:
        db.execute("UPDATE auth_sessions SET expires_at=datetime('now','-1 second') "
                   "WHERE user_id=?", (self.admin["user"]["id"],))
        self.assertIsNone(auth.session(self.admin["token"]))

    def test_concurrent_password_changes_cannot_both_use_the_old_password(self) -> None:
        barrier = threading.Barrier(2)
        real_hash = auth._password_hash
        successes = []
        errors = []

        def synchronized_hash(password):
            barrier.wait(timeout=5)
            return real_hash(password)

        def change(new_password):
            try:
                successes.append(auth.change_password(
                    self.admin["user"]["id"], ADMIN_PASSWORD, new_password,
                    "admin@example.com"))
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                db.close_connection()

        with patch.object(auth, "_password_hash", side_effect=synchronized_hash):
            workers = [threading.Thread(target=change, args=(f"new long password {index} 987",))
                       for index in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=8)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("Current password", errors[0])

    def test_expired_and_revoked_invitations_cannot_be_accepted(self) -> None:
        first = auth.create_invitation("new@example.com", "New person", actor="admin@example.com")
        self.assertEqual(first["role"], "viewer")
        self.assertNotIn("token", auth.list_invitations()[0])
        db.execute("UPDATE access_invitations SET expires_at=datetime('now','-1 second') "
                   "WHERE id=?", (first["id"],))
        self.assertEqual(auth.list_invitations()[0]["status"], "expired")
        with self.assertRaisesRegex(ValueError, "expired"):
            auth.accept_invitation(first["token"], "new@example.com", "", USER_PASSWORD)

        replacement = auth.create_invitation("new@example.com", actor="admin@example.com")
        auth.revoke_invitation(replacement["id"], actor="admin@example.com")
        with self.assertRaisesRegex(ValueError, "revoked"):
            auth.accept_invitation(replacement["token"], "new@example.com", "", USER_PASSWORD)
        self.assertFalse(db.query("SELECT id FROM portal_users WHERE email=?", ("new@example.com",)))

    def test_analyst_and_viewer_http_permissions(self) -> None:
        self.save_user("analyst@example.com", "analyst")
        self.save_user("viewer@example.com")
        analyst = auth.login("analyst@example.com", USER_PASSWORD, "127.0.0.4")
        viewer = auth.login("viewer@example.com", USER_PASSWORD, "127.0.0.5")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{httpd.server_port}"

        def request(identity, method, path, body=None):
            client = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            headers = {"Host": f"127.0.0.1:{httpd.server_port}",
                       "Cookie": f"portal_session={identity['token']}"}
            if method == "POST":
                headers.update({"Content-Type": "application/json", "Origin": origin,
                                "X-CSRF-Token": identity["csrf_token"]})
            client.request(method, path, body=json.dumps(body or {}).encode() if method == "POST" else None,
                           headers=headers)
            response = client.getresponse()
            status = response.status
            response.read()
            client.close()
            return status

        try:
            self.assertEqual(request(analyst, "GET", "/api/workspace"), 200)
            self.assertEqual(request(viewer, "GET", "/api/workspace"), 403)
            query = {"start": "2026-09-01", "end": "2026-09-02",
                     "dimensions": ["campaign"], "metrics": ["clicks"]}
            self.assertEqual(request(analyst, "POST", "/api/explorer/query", query), 200)
            self.assertEqual(request(viewer, "POST", "/api/explorer/query", query), 200)
            for identity in (analyst, viewer):
                self.assertEqual(request(identity, "GET", "/api/invitations"), 403)
                self.assertEqual(request(identity, "POST", "/api/users", {}), 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
