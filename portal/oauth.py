"""Generic OAuth 2.0 authorisation-code flow.

Every OAuth platform uses the same three steps, so they are implemented once
here and each provider only declares its URLs and scopes. The redirect lands
back on this portal, which is why connecting an account is a click rather than
a copy-paste of a refresh token.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from typing import Any

from .config import APP
from .httpclient import HttpError, post
from .providers.base import Connection, OAuth2

# Short-lived CSRF state → connection id, held in memory only.
_pending: dict[str, tuple[int, int, float]] = {}
# connection id → (access token, expiry timestamp, OAuth credential fingerprint)
_tokens: dict[int, tuple[str, float, bytes]] = {}
# A refresh started before a connection changes must not repopulate its cache.
_generations: dict[int, int] = {}
_lock = threading.Lock()

STATE_TTL = 900  # 15 minutes to complete a consent screen


def redirect_uri() -> str:
    """Where platforms send the user back. Must match the app's registered URI."""
    if APP.oauth_redirect_uri:
        parsed = urllib.parse.urlparse(APP.oauth_redirect_uri)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path != "/oauth/callback" or parsed.query or parsed.fragment:
            raise ValueError("PORTAL_OAUTH_REDIRECT_URI must be an HTTPS URL ending in /oauth/callback, with no query or fragment.")
        return APP.oauth_redirect_uri.rstrip("/")
    return f"http://localhost:{APP.port}/oauth/callback"


def begin(conn: Connection, spec: OAuth2, user_id: int) -> str:
    """Return the URL to send the browser to, and remember why we sent it."""
    client_id = conn.get(spec.client_id_key)
    if not client_id:
        raise ValueError("This connection has no client ID yet — save one first.")
    state = secrets.token_urlsafe(24)
    with _lock:
        _prune()
        _pending[state] = (conn.id, user_id, time.time() + STATE_TTL)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(spec.scopes),
        "state": state,
        **spec.extra_auth_params,
    }
    return f"{spec.auth_url}?{urllib.parse.urlencode(params)}"


def _prune() -> None:
    now = time.time()
    for k in [k for k, (_, _, exp) in _pending.items() if exp < now]:
        _pending.pop(k, None)


def claim_state(state: str) -> tuple[int, int] | None:
    """Consume a state token, returning the connection and initiating user."""
    with _lock:
        _prune()
        entry = _pending.pop(state, None)
    return (entry[0], entry[1]) if entry else None


def _client_auth(spec: OAuth2, conn: Connection, body: dict) -> tuple[dict, dict]:
    """Place client credentials where the platform expects them."""
    cid = conn.get(spec.client_id_key) or ""
    csec = conn.get(spec.client_secret_key) or ""
    if spec.client_auth == "basic":
        token = base64.b64encode(f"{cid}:{csec}".encode()).decode()
        return body, {"Authorization": f"Basic {token}"}
    return {**body, "client_id": cid, "client_secret": csec}, {}


def _cache_binding(conn: Connection, spec: OAuth2) -> bytes:
    """Bind a cached token to the grant and app that obtained it."""
    values = (
        conn.provider, spec.token_url, spec.client_auth,
        str(conn.get(spec.client_id_key) or ""),
        str(conn.get(spec.client_secret_key) or ""),
        str(conn.get("refresh_token") or ""),
    )
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).digest()


def exchange_code(conn: Connection, spec: OAuth2, code: str) -> dict[str, Any]:
    """Swap the one-time code for tokens. Returns the raw token response."""
    body, headers = _client_auth(spec, conn, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
    })
    payload = post(spec.token_url, data=body, headers=headers, timeout=30)
    if not payload.get("access_token"):
        raise RuntimeError("Token exchange returned no access token.")
    return payload


def access_token(conn: Connection, spec: OAuth2) -> str:
    """A valid access token for this connection, refreshed as needed."""
    binding = _cache_binding(conn, spec)
    with _lock:
        tok, exp, cached_binding = _tokens.get(conn.id, ("", 0.0, b""))
        if tok and cached_binding == binding and time.time() < exp:
            return tok
        generation = _generations.get(conn.id, 0)

    refresh = conn.get("refresh_token")
    if not refresh:
        # Some platforms (TikTok) issue only a long-lived access token.
        direct = conn.get("access_token")
        if direct:
            return str(direct)
        raise RuntimeError(
            f"'{conn.name}' is not authorised yet — open Connections and press Connect."
        )

    body, headers = _client_auth(spec, conn, {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })
    try:
        payload = post(spec.token_url, data=body, headers=headers, timeout=30)
    except HttpError as exc:
        raise RuntimeError(
            f"Could not refresh access for '{conn.name}': {exc}. "
            "Re-connect this account from the Connections screen."
        ) from None

    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError("Refresh returned no access token.")
    with _lock:
        if _generations.get(conn.id, 0) == generation:
            _tokens[conn.id] = (token, time.time() + int(payload.get("expires_in", 3600)) - 300, binding)
    return token


def forget(connection_id: int) -> None:
    with _lock:
        _generations[connection_id] = _generations.get(connection_id, 0) + 1
        _tokens.pop(connection_id, None)
