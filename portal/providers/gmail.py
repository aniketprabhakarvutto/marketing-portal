"""Send-only Gmail connection for portal test messages and future reports."""
from __future__ import annotations

import base64
import re
from datetime import date
from email.message import EmailMessage

from ..httpclient import get, post
from ..oauth import access_token
from .base import Connection, Field, OAuth2, Provider

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailProvider(Provider):
    id = "gmail"
    label = "Gmail (send-only)"
    category = "other"
    table = ""
    syncable = False
    summary = "Authorise the portal to send report emails. It cannot read your inbox."
    docs_url = "https://developers.google.com/gmail/api/guides/sending"
    oauth = OAuth2(
        auth_url=AUTH_URL,
        token_url=TOKEN_URL,
        scopes=["openid", "email", "https://www.googleapis.com/auth/gmail.send"],
        extra_auth_params={"access_type": "offline", "prompt": "select_account consent"},
    )
    fields = (
        Field("client_id", "Google OAuth client ID", help=(
            "Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs → "
            "Web application. Enable the Gmail API and add this portal's callback URL.")),
        Field("client_secret", "Google OAuth client secret", secret=True),
    )

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        raise ValueError("Gmail is send-only. It does not import or sync inbox data.")

    def account_email(self, conn: Connection) -> str:
        token = access_token(conn, self.oauth)
        payload = get("https://openidconnect.googleapis.com/v1/userinfo",
                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
        email = str(payload.get("email") or "").strip().lower()
        if not email or payload.get("email_verified") is False:
            raise RuntimeError("Google did not return a verified Gmail account address.")
        return email

    def send_test(self, conn: Connection, recipient: str) -> dict:
        recipient = str(recipient or "").strip()
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", recipient) or "\r" in recipient or "\n" in recipient:
            raise ValueError("A valid recipient email address is required.")
        token = access_token(conn, self.oauth)
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = "Vortex Veri Gmail connection test"
        message.set_content(
            "This is a test email from your Vortex Veri workspace.\n\n"
            "It confirms the configured Google account can send email. "
            "The portal has Gmail send permission only and cannot read your inbox."
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        result = post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json_body={"raw": raw}, headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        return {"message_id": str(result.get("id") or "")}

    def send_report(self, conn: Connection, recipient: str, filename: str,
                    content: bytes, start: str, end: str) -> dict:
        recipient = str(recipient or "").strip()
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", recipient) or "\r" in recipient or "\n" in recipient:
            raise ValueError("A valid report recipient email address is required.")
        safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(filename))[:120] or "report.csv"
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = f"Vortex Veri report: {start} to {end}"
        message.set_content(
            f"Your scheduled Vortex Veri report for {start} to {end} is attached.\n"
            "This email was sent by the portal using its send-only Gmail permission."
        )
        message.add_attachment(content, maintype="text", subtype="csv", filename=safe_filename)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        token = access_token(conn, self.oauth)
        result = post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json_body={"raw": raw}, headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        return {"message_id": str(result.get("id") or "")}
