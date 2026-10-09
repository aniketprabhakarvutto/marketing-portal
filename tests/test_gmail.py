from __future__ import annotations

import base64
from email import message_from_bytes
from unittest import TestCase
from unittest.mock import patch

from portal.providers.base import Connection
from portal.providers.gmail import GmailProvider


class GmailProviderTests(TestCase):
    def test_gmail_is_send_only_and_requests_narrow_send_scope(self) -> None:
        provider = GmailProvider()
        self.assertFalse(provider.syncable)
        self.assertEqual(provider.table, "")
        self.assertIn("https://www.googleapis.com/auth/gmail.send", provider.oauth.scopes)
        self.assertNotIn("https://www.googleapis.com/auth/gmail.readonly", provider.oauth.scopes)
        self.assertNotIn("https://mail.google.com/", provider.oauth.scopes)
        self.assertEqual(provider.oauth.extra_auth_params["prompt"], "select_account consent")

    @patch("portal.providers.gmail.post", return_value={"id": "gmail-test-123"})
    @patch("portal.providers.gmail.access_token", return_value="access-token")
    def test_test_email_uses_gmail_send_api_and_sends_to_the_supplied_recipient(self, token, send):
        provider = GmailProvider()
        conn = Connection(1, "gmail", "Report sender", {}, {"refresh_token": "stored"})
        result = provider.send_test(conn, "admin@example.com")
        self.assertEqual(result["message_id"], "gmail-test-123")
        args, kwargs = send.call_args
        self.assertEqual(args[0], "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer access-token")
        raw = kwargs["json_body"]["raw"]
        raw += "=" * (-len(raw) % 4)
        message = message_from_bytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(message["To"], "admin@example.com")
        self.assertIn("connection test", message["Subject"].lower())

    def test_rejects_header_injection_in_recipient(self):
        provider = GmailProvider()
        conn = Connection(1, "gmail", "Report sender", {}, {"refresh_token": "stored"})
        with self.assertRaisesRegex(ValueError, "valid recipient"):
            provider.send_test(conn, "admin@example.com\r\nBcc: attacker@example.com")

    @patch("portal.providers.gmail.post", return_value={"id": "gmail-report-456"})
    @patch("portal.providers.gmail.access_token", return_value="access-token")
    def test_scheduled_report_email_contains_the_csv_attachment(self, token, send):
        provider = GmailProvider()
        conn = Connection(1, "gmail", "Report sender", {}, {"refresh_token": "stored"})
        content = b"Campaign,Spend\nSpring,1200\n"

        result = provider.send_report(conn, "manager@example.com", "weekly report.csv",
                                      content, "2026-10-01", "2026-10-07")

        self.assertEqual(result["message_id"], "gmail-report-456")
        raw = send.call_args.kwargs["json_body"]["raw"]
        raw += "=" * (-len(raw) % 4)
        message = message_from_bytes(base64.urlsafe_b64decode(raw))
        self.assertEqual(message["To"], "manager@example.com")
        self.assertIn("2026-10-01 to 2026-10-07", message["Subject"])
        attachment = next(part for part in message.walk() if part.get_filename())
        self.assertEqual(attachment.get_filename(), "weekly_report.csv")
        self.assertEqual(attachment.get_payload(decode=True), content)
