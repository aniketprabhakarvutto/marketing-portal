from __future__ import annotations

import http.client
import json
import threading
import secrets
import unittest
from unittest.mock import patch
from email.message import Message
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory

from portal import auth, db, server
from portal.config import APP, effective_secure_cookie


class AuthBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_path = APP.db_path
        self.old_cookie_secure = APP.secure_cookie
        self.old_trusted_proxy_ips = APP.trusted_proxy_ips
        APP.db_path = f"{self.temp.name}/portal.db"
        APP.secure_cookie = False
        db.init_db()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        db.close_connection()
        APP.db_path = self.old_path
        APP.secure_cookie = self.old_cookie_secure
        APP.trusted_proxy_ips = self.old_trusted_proxy_ips
        self.temp.cleanup()

    def request(self, method: str, path: str, payload=None, cookie="", csrf="", origin=None,
                content_type="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.httpd.server_port}"}
        if content_type:
            headers["Content-Type"] = content_type
        if cookie:
            headers["Cookie"] = cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if origin is not None:
            headers["Origin"] = origin
        body = json.dumps(payload).encode() if payload is not None else None
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, response.getheaders(), data)
        conn.close()
        return result

    def login_admin(self):
        payload = {
            "email": "admin@example.com", "display_name": "Portal Admin",
            "password": "correct horse battery staple 42",
        }
        status, headers, body = self.request("POST", "/api/auth/setup", payload, origin=self.origin)
        self.assertEqual(status, 200, body)
        cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        status, _, body = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertEqual(status, 200, body)
        csrf = json.loads(body)["csrf_token"]
        return cookie, csrf

    def test_unauthenticated_api_denied_and_login_page_is_csp_compatible(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"/static/login.js", body)
        self.assertIn(b"/static/login.css", body)
        self.assertIn(b'id="togglePassword"', body)
        self.assertIn(b'aria-label="Show password"', body)
        self.assertNotIn(b"<script>", body)
        csp = next(v for k, v in headers if k.lower() == "content-security-policy")
        self.assertIn("script-src 'self'", csp)
        self.assertEqual(self.request("GET", "/api/overview")[0], 401)
        self.assertEqual(self.request("GET", "/api/auth/status")[0], 200)
        self.assertEqual(self.request("GET", "/static/login.js")[0], 200)

        login_script = (server.WEB / "login.js").read_text()
        self.assertNotIn("waitForAuthenticatedSession", login_script)
        self.assertIn("location.replace(`/${location.hash}`)", login_script)

    def test_loopback_http_never_emits_secure_session_cookies(self) -> None:
        self.assertFalse(effective_secure_cookie("127.0.0.1", True))
        self.assertFalse(effective_secure_cookie("::1", True))
        self.assertFalse(effective_secure_cookie("localhost", True))
        self.assertTrue(effective_secure_cookie("0.0.0.0", True))

    def test_readiness_reports_database_failure_without_leaking_details(self) -> None:
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        self.assertEqual(self.request("GET", "/readyz")[0], 200)
        with patch("portal.server.query", side_effect=RuntimeError("private database detail")):
            status, _, body = self.request("GET", "/readyz")
        self.assertEqual(status, 503)
        self.assertNotIn(b"private database detail", body)

    def test_setup_role_limits_csrf_and_logout(self) -> None:
        cookie, csrf = self.login_admin()
        status, _, body = self.request("POST", "/api/auth/setup", {
            "email": "second@example.com", "display_name": "Second", "password": "very long password 1234",
        }, origin=self.origin)
        self.assertEqual(status, 409, body)

        status, _, body = self.request("POST", "/api/users", {
            "email": "viewer@example.com", "display_name": "Viewer", "role": "viewer",
            "enabled": True, "password": "another very long password 123",
        }, cookie=cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)

        status, headers, body = self.request("POST", "/api/auth/login", {
            "email": "viewer@example.com", "password": "another very long password 123",
        }, origin=self.origin)
        self.assertEqual(status, 200, body)
        viewer_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        status, _, body = self.request("GET", "/api/auth/me", cookie=viewer_cookie)
        viewer_csrf = json.loads(body)["csrf_token"]
        self.assertEqual(self.request("GET", "/api/overview", cookie=viewer_cookie)[0], 200)
        self.assertEqual(self.request("GET", "/", cookie=viewer_cookie)[0], 200)
        self.assertEqual(self.request("GET", "/static/app.js", cookie=viewer_cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/workspace", cookie=viewer_cookie)[0], 403)
        self.assertEqual(self.request("GET", "/api/connections/1", cookie=viewer_cookie)[0], 403)
        self.assertEqual(self.request("POST", "/api/connections", {}, cookie=viewer_cookie,
                                      csrf=viewer_csrf, origin=self.origin)[0], 403)
        self.assertEqual(self.request("POST", "/api/explorer/query", {
            "start": "2026-10-01", "end": "2026-10-02", "dimensions": ["campaign"],
            "metrics": ["spend"], "limit": 10,
        }, cookie=viewer_cookie, csrf=viewer_csrf, origin=self.origin)[0], 200)
        self.assertEqual(self.request("POST", "/api/auth/logout", {}, cookie=viewer_cookie,
                                      csrf=viewer_csrf, origin=self.origin)[0], 200)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=viewer_cookie)[0], 401)

    def test_logout_then_login_page_on_reused_connection(self) -> None:
        cookie, csrf = self.login_admin()
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        headers = {
            "Host": f"127.0.0.1:{self.httpd.server_port}",
            "Origin": self.origin,
            "Content-Type": "application/json",
            "Cookie": cookie,
            "X-CSRF-Token": csrf,
        }
        conn.request("POST", "/api/auth/logout", body="{}", headers=headers)
        logout = conn.getresponse()
        self.assertEqual(logout.status, 200)
        logout.read()
        conn.request("GET", "/login", headers={"Host": f"127.0.0.1:{self.httpd.server_port}"})
        page = conn.getresponse()
        body = page.read()
        conn.close()
        self.assertEqual(page.status, 200, body)
        self.assertIn(b"Sign in", body)

    def test_state_changes_require_origin_csrf_and_json(self) -> None:
        cookie, csrf = self.login_admin()
        self.assertEqual(self.request("POST", "/api/automation", {}, cookie=cookie,
                                      csrf=csrf, origin=None)[0], 403)
        self.assertEqual(self.request("POST", "/api/automation", {}, cookie=cookie,
                                      csrf="wrong", origin=self.origin)[0], 403)
        self.assertEqual(self.request("POST", "/api/automation", {}, cookie=cookie,
                                      csrf=csrf, origin=self.origin,
                                      content_type="text/plain")[0], 415)

    def test_rejected_body_does_not_corrupt_next_request(self) -> None:
        cookie, csrf = self.login_admin()
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.httpd.server_port}", "Origin": self.origin,
                   "Content-Type": "application/json", "Cookie": cookie}
        conn.request("POST", "/api/assistant/ask", body='{"question":"hello"}', headers=headers)
        rejected = conn.getresponse()
        self.assertEqual(rejected.status, 403)
        self.assertEqual(rejected.getheader("Connection"), "close")
        rejected.read()
        conn.request("GET", "/api/auth/me", headers={"Cookie": cookie})
        following = conn.getresponse()
        self.assertEqual(following.status, 200, following.read())
        conn.close()

    def test_password_change_rotates_and_revokes_the_old_session(self) -> None:
        old_cookie, csrf = self.login_admin()
        status, headers, body = self.request("POST", "/api/auth/password", {
            "current_password": "correct horse battery staple 42",
            "new_password": "a new and longer password 987",
        }, cookie=old_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        new_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=old_cookie)[0], 401)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=new_cookie)[0], 200)
        status, _, body = self.request("POST", "/api/auth/login", {
            "email": "admin@example.com", "password": "a new and longer password 987",
        }, origin=self.origin)
        self.assertEqual(status, 200, body)

    def test_profile_preferences_are_per_account_and_validated(self) -> None:
        cookie, csrf = self.login_admin()
        status, _, body = self.request("GET", "/api/auth/profile", cookie=cookie)
        self.assertEqual(status, 200, body)
        initial = json.loads(body)
        self.assertEqual(initial["preferences"]["theme"], "system")
        self.assertEqual(initial["preferences"]["accent"], "")
        status, _, body = self.request("POST", "/api/auth/profile", {
            "display_name": "Portal Owner", "theme": "dark",
            "accent": "#7c3aed",
            "notifications": {"browser": True, "sync_failures": True, "sync_complete": False},
        }, cookie=cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        saved = json.loads(body)
        self.assertEqual(saved["user"]["display_name"], "Portal Owner")
        self.assertEqual(saved["preferences"]["theme"], "dark")
        self.assertEqual(saved["preferences"]["accent"], "#7C3AED")
        self.assertTrue(saved["preferences"]["notifications"]["browser"])
        self.assertEqual(self.request("POST", "/api/auth/profile", {"theme": "rainbow"},
                                     cookie=cookie, csrf=csrf, origin=self.origin)[0], 400)
        self.assertEqual(self.request("POST", "/api/auth/profile", {"accent": "red"},
                                     cookie=cookie, csrf=csrf, origin=self.origin)[0], 400)

    def test_other_sessions_can_be_revoked_without_revoking_current_session(self) -> None:
        current_cookie, csrf = self.login_admin()
        status, headers, body = self.request("POST", "/api/auth/login", {
            "email": "admin@example.com", "password": "correct horse battery staple 42",
        }, origin=self.origin)
        self.assertEqual(status, 200, body)
        other_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        status, _, body = self.request("POST", "/api/auth/sessions/revoke-others", {},
                                       cookie=current_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["revoked"], 1)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=other_cookie)[0], 401)
        self.assertEqual(self.request("GET", "/api/auth/me", cookie=current_cookie)[0], 200)

    def test_viewer_can_manage_own_profile_but_cannot_access_gmail_sender(self) -> None:
        admin_cookie, csrf = self.login_admin()
        status, headers, body = self.request("POST", "/api/users", {
            "email": "viewer@example.com", "display_name": "Viewer", "role": "viewer",
            "enabled": True, "password": "another very long password 123",
        }, cookie=admin_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        status, headers, body = self.request("POST", "/api/auth/login", {
            "email": "viewer@example.com", "password": "another very long password 123",
        }, origin=self.origin)
        self.assertEqual(status, 200, body)
        viewer_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        status, _, body = self.request("GET", "/api/auth/profile", cookie=viewer_cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.request("GET", "/api/auth/gmail", cookie=viewer_cookie)[0], 403)

    def test_admin_can_create_list_and_revoke_viewer_invitation(self) -> None:
        admin_cookie, csrf = self.login_admin()
        status, _, body = self.request("GET", "/api/invitations", cookie=admin_cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), [])

        status, _, body = self.request("POST", "/api/invitations", {
            "email": "manager@example.com", "display_name": "Marketing Manager",
        }, cookie=admin_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 201, body)
        invite = json.loads(body)
        self.assertEqual(invite["role"], "viewer")
        self.assertTrue(invite["token"])
        invite_id = invite["id"]

        status, _, body = self.request("GET", "/api/invitations", cookie=admin_cookie)
        self.assertEqual(status, 200, body)
        listed = json.loads(body)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["status"], "pending")
        self.assertNotIn("token", listed[0])

        status, _, body = self.request("POST", f"/api/invitations/{invite_id}/revoke", {},
                                       cookie=admin_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["revoked"])
        status, _, body = self.request("GET", "/api/invitations", cookie=admin_cookie)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), [])

    def test_viewer_cannot_manage_invitations(self) -> None:
        admin_cookie, csrf = self.login_admin()
        status, _, body = self.request("POST", "/api/users", {
            "email": "viewer@example.com", "display_name": "Viewer", "role": "viewer",
            "enabled": True, "password": "another very long password 123",
        }, cookie=admin_cookie, csrf=csrf, origin=self.origin)
        self.assertEqual(status, 200, body)
        status, headers, body = self.request("POST", "/api/auth/login", {
            "email": "viewer@example.com", "password": "another very long password 123",
        }, origin=self.origin)
        self.assertEqual(status, 200, body)
        viewer_cookie = next(value.split(";", 1)[0] for key, value in headers if key.lower() == "set-cookie")
        status, _, _ = self.request("GET", "/api/auth/me", cookie=viewer_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/invitations", cookie=viewer_cookie)[0], 403)

    def test_setup_requires_direct_local_origin_and_password_policy(self) -> None:
        status, _, _ = self.request("POST", "/api/auth/setup", {
            "email": "admin@example.com", "display_name": "Admin", "password": "short",
        }, origin="https://attacker.invalid")
        self.assertEqual(status, 403)
        status, _, body = self.request("POST", "/api/auth/setup", {
            "email": "admin@example.com", "display_name": "Admin", "password": "short",
        }, origin=self.origin)
        self.assertEqual(status, 409, body)
        self.assertEqual(self.request("GET", "/api/auth/status")[0], 200)

    def test_parallel_login_reservations_cannot_exceed_account_limit(self) -> None:
        keys = auth._login_attempt_keys("198.51.100.10", "admin@example.com")
        other_ip_keys = auth._login_attempt_keys("198.51.100.11", "admin@example.com")
        self.assertNotEqual(keys[0], other_ip_keys[0])
        self.assertEqual(keys[1], other_ip_keys[1])
        barrier = threading.Barrier(32)
        accepted = []
        accepted_lock = threading.Lock()

        def attempt():
            barrier.wait(timeout=3)
            reserved = auth._reserve_login_attempt(keys, (60, 10), 1000.0)
            if reserved:
                with accepted_lock:
                    accepted.append(True)
                auth._finish_login_attempt(keys, 1000.0, failed=True)

        workers = [threading.Thread(target=attempt) for _ in range(32)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=4)
        self.assertEqual(len(accepted), 10)
        with auth._login_lock:
            for key in (*keys, *other_ip_keys):
                auth._login_failures.pop(key, None)

    def test_login_throttle_uses_client_ip_only_from_trusted_proxy(self) -> None:
        handler = server.Handler.__new__(server.Handler)
        handler.client_address = ("127.0.0.1", 12345)
        APP.trusted_proxy_ips = ()
        handler.headers = {"CF-Connecting-IP": "203.0.113.12"}
        self.assertEqual(handler._login_remote_address(), "127.0.0.1")
        APP.trusted_proxy_ips = ("127.0.0.1",)
        self.assertEqual(handler._login_remote_address(), "203.0.113.12")
        handler.headers = {"X-Forwarded-For": "203.0.113.13"}
        self.assertEqual(handler._login_remote_address(), "203.0.113.13")
        handler.headers = {"X-Forwarded-For": "203.0.113.13, 127.0.0.1"}
        self.assertEqual(handler._login_remote_address(), "127.0.0.1")
        duplicate_headers = Message()
        duplicate_headers.add_header("X-Forwarded-For", "203.0.113.13")
        duplicate_headers.add_header("X-Forwarded-For", "203.0.113.14")
        handler.headers = duplicate_headers
        self.assertEqual(handler._login_remote_address(), "127.0.0.1")
        handler.headers = {
            "CF-Connecting-IP": "203.0.113.12",
            "X-Forwarded-For": "203.0.113.13",
        }
        self.assertEqual(handler._login_remote_address(), "127.0.0.1")
        handler.client_address = ("10.0.0.9", 12345)
        handler.headers = {"CF-Connecting-IP": "203.0.113.12"}
        self.assertEqual(handler._login_remote_address(), "10.0.0.9")

    def test_local_setup_can_explicitly_trust_docker_loopback_forwarding(self) -> None:
        handler = server.Handler.__new__(server.Handler)
        handler.client_address = ("192.168.65.1", 12345)
        handler.headers = {"Host": "127.0.0.1:8788"}
        original = APP.trust_loopback_host_for_setup
        try:
            APP.trust_loopback_host_for_setup = False
            self.assertFalse(handler._trusted_local_request())
            APP.trust_loopback_host_for_setup = True
            self.assertTrue(handler._trusted_local_request())
            handler.headers = {"Host": "portal.example.com"}
            self.assertFalse(handler._trusted_local_request())
            handler.headers = {
                "Host": "127.0.0.1:8788",
                "X-Forwarded-For": "127.0.0.1",
            }
            self.assertFalse(handler._trusted_local_request())
        finally:
            APP.trust_loopback_host_for_setup = original


if __name__ == "__main__":
    unittest.main()
