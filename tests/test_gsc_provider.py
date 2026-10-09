from __future__ import annotations

import unittest
from unittest.mock import patch

from portal import server
from portal.httpclient import HttpError
from portal.providers.base import Connection
from portal.providers.google import SearchConsoleProvider


class SearchConsoleConnectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = SearchConsoleProvider()
        self.conn = Connection(
            12, "gsc", "Organic search",
            {"site_urls": "sc-domain:example.com, https://www.example.com/"},
            {"refresh_token": "test-refresh"},
        )

    def test_checks_all_exact_properties_without_fetching_reports(self) -> None:
        with patch("portal.providers.google.forget") as clear_token, \
             patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", return_value={"siteEntry": [
                 {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteFullUser"},
                 {"siteUrl": "https://www.example.com/", "permissionLevel": "siteRestrictedUser"},
             ]}) as sites_list, patch.object(self.provider, "_day") as report:
            message = self.provider.test_connection(self.conn)

        self.assertIn("2 configured property(s)", message)
        clear_token.assert_called_once_with(self.conn.id)
        sites_list.assert_called_once_with(
            "https://www.googleapis.com/webmasters/v3/sites",
            headers={"Authorization": "Bearer test-token"},
        )
        report.assert_not_called()

    def test_missing_exact_property_names_fail_even_if_similar_property_is_visible(self) -> None:
        with patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", return_value={"siteEntry": [
                 {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                 {"siteUrl": "https://www.example.com", "permissionLevel": "siteFullUser"},
             ]}):
            with self.assertRaisesRegex(ValueError, "https://www.example.com/") as caught:
                self.provider.test_connection(self.conn)

        self.assertIn("Settings > Users and permissions > Add user", str(caught.exception))

    def test_unverified_property_fails_with_owner_setup_steps(self) -> None:
        with patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", return_value={"siteEntry": [
                 {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteUnverifiedUser"},
                 {"siteUrl": "https://www.example.com/", "permissionLevel": "siteOwner"},
             ]}):
            with self.assertRaisesRegex(ValueError, "unverified") as caught:
                self.provider.test_connection(self.conn)

        self.assertIn("sc-domain:example.com", str(caught.exception))
        self.assertIn("Google account used for Connect", str(caught.exception))

    def test_quota_403_is_not_diagnosed_as_property_permission_failure(self) -> None:
        error = HttpError(403, '{"error":{"errors":[{"reason":"quotaExceeded"}]}}',
                          "https://www.googleapis.com/webmasters/v3/sites")
        with patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", side_effect=error):
            with self.assertRaisesRegex(ValueError, "quota or rate limit") as caught:
                self.provider.test_connection(self.conn)

        self.assertIn("property access has not been verified", str(caught.exception))
        self.assertNotIn("Add user", str(caught.exception))

    def test_other_403_does_not_guess_property_permission(self) -> None:
        error = HttpError(403, '{"error":{"errors":[{"reason":"forbidden"}]}}',
                          "https://www.googleapis.com/webmasters/v3/sites")
        with patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", side_effect=error):
            with self.assertRaisesRegex(ValueError, "site list request") as caught:
                self.provider.test_connection(self.conn)

        self.assertNotIn("Add user", str(caught.exception))

    def test_token_failure_allows_for_a_transient_error(self) -> None:
        with patch("portal.providers.google.access_token", side_effect=RuntimeError("temporary network failure")), \
             patch("portal.providers.google.get") as sites_list:
            with self.assertRaisesRegex(ValueError, "Retry the test") as caught:
                self.provider.test_connection(self.conn)

        self.assertIn("if the problem persists", str(caught.exception))
        sites_list.assert_not_called()

    def test_server_uses_permission_check_instead_of_recent_report(self) -> None:
        with patch("portal.providers.google.access_token", return_value="test-token"), \
             patch("portal.providers.google.get", return_value={"siteEntry": [
                 {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
             ]}), patch.object(SearchConsoleProvider, "fetch") as report:
            result = server.test_connection({}, {"provider": "gsc", "values": {
                "client_id": "test-client", "client_secret": "test-secret",
                "refresh_token": "test-refresh", "site_urls": "sc-domain:example.com",
            }})

        self.assertTrue(result["ok"])
        self.assertEqual(result["rows"], 0)
        self.assertIn("access verified", result["message"])
        report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
