from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory

from portal import auth, db, server
from portal.config import APP


class UIPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.temp = TemporaryDirectory()
        self.old_db_path = APP.db_path
        APP.db_path = f"{self.temp.name}/portal.db"
        db.init_db()
        db.execute("INSERT INTO portal_users(id,email,display_name,role,enabled,password_hash) "
                   "VALUES(1,'one@example.com','One','admin',1,'test')")
        db.execute("INSERT INTO portal_users(id,email,display_name,role,enabled,password_hash) "
                   "VALUES(2,'two@example.com','Two','viewer',1,'test')")
        self.first = auth._make_session(1)
        self.second = auth._make_session(2)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        db.close_connection()
        APP.db_path = self.old_db_path
        self.temp.cleanup()

    def request(self, method: str, route: str, payload=None, identity=None,
                csrf=True, origin=True):
        client = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.httpd.server_port}",
                   "Content-Type": "application/json"}
        if identity:
            headers["Cookie"] = f"portal_session={identity['token']}"
            if csrf:
                headers["X-CSRF-Token"] = identity["csrf_token"]
        if origin:
            headers["Origin"] = self.origin
        client.request(method, route, json.dumps(payload).encode() if payload is not None else None,
                       headers=headers)
        response = client.getresponse()
        data = response.read()
        client.close()
        return response.status, json.loads(data) if data else {}

    def test_authenticated_round_trip_and_per_user_isolation(self) -> None:
        self.assertEqual(self.request("GET", "/api/ui-preferences")[0], 401)
        status, data = self.request("POST", "/api/ui-preferences",
                                    {"key": "savedPortalViews", "value": [{"name": "My view"}]},
                                    self.first)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["preferences"]["savedPortalViews"], [{"name": "My view"}])
        self.assertEqual(self.request("GET", "/api/ui-preferences", identity=self.first)[1], data)
        self.assertEqual(self.request("GET", "/api/ui-preferences", identity=self.second)[1],
                         {"preferences": {}})
        self.assertEqual(self.request("POST", "/api/ui-preferences",
                                      {"key": "campaignColumns", "value": ["spend"]},
                                      self.second)[0], 200)
        self.assertNotIn("campaignColumns", self.request(
            "GET", "/api/ui-preferences", identity=self.first)[1]["preferences"])

    def test_profile_update_preserves_ui_and_null_removes_key(self) -> None:
        self.assertEqual(self.request("POST", "/api/ui-preferences",
                                      {"key": "campaignWidths", "value": {"spend": 180}},
                                      self.first)[0], 200)
        status, _ = self.request("POST", "/api/auth/profile",
                                 {"display_name": "Updated", "theme": "dark"}, self.first)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/ui-preferences", identity=self.first)[1]
                         ["preferences"]["campaignWidths"], {"spend": 180})
        status, data = self.request("POST", "/api/ui-preferences",
                                    {"key": "campaignWidths", "value": None}, self.first)
        self.assertEqual(status, 200, data)
        self.assertNotIn("campaignWidths", data["preferences"])
        self.assertEqual(auth.profile(1)["preferences"]["theme"], "dark")

    def test_validation_and_write_guards(self) -> None:
        cases = [
            {"key": "unknown", "value": []},
            {"key": ["campaignColumns"], "value": []},
            {"key": "campaignColumns", "value": {"spend": True}},
            {"key": "savedPortalViews", "value": [{"name": "x" * 66_000}]},
            {"key": "portalTableDensity", "value": "invalid"},
        ]
        for body in cases:
            self.assertEqual(self.request("POST", "/api/ui-preferences", body, self.first)[0],
                             400, body)
        valid = {"key": "calendarDatePresets", "value": []}
        self.assertEqual(self.request("POST", "/api/ui-preferences", valid, self.first,
                                      csrf=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/ui-preferences", valid, self.first,
                                      origin=False)[0], 403)


if __name__ == "__main__":
    unittest.main()
