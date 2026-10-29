"""HTTP server — the API, the OAuth callback and the dashboard.

A localhost single-user portal does not need a web framework, and not needing
one means there is nothing to install: system Python runs this as-is.
"""
from __future__ import annotations

import json
import hmac
import ipaddress
import logging
import mimetypes
import re
import sys
import threading
import traceback
from http.cookies import SimpleCookie
from html import escape
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from . import api
from . import assistant
from . import auth
from . import automation
from . import connections as conns
from . import marketing_analytics
from . import oauth
from . import mcp
from . import platform
from .config import APP, ROOT
from . import db
from .db import close_connection, init_db, query
from .providers import REGISTRY

log = logging.getLogger("portal.server")
WEB = Path(__file__).parent / "web"

LOGIN_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Vortex Veri</title><link rel="icon" type="image/png" href="/static/assets/vortex-veri-mark.png?v=2"><link rel="stylesheet" href="/static/login.css?v=2"><script src="/static/login.js?v=4" defer></script></head><body><main><div class="brand"><img class="mark" src="/static/assets/vortex-veri-mark.png" alt=""><strong>Vortex Veri</strong></div>
<p class="eyebrow" id="eyebrow">Private workspace</p><h1 id="title">Sign in</h1><p id="hint">Use your portal account to continue.</p>
<form id="authForm" method="post" action="/api/auth/login" autocomplete="on"><div id="nameField" hidden><label for="displayName">Your name<input id="displayName" name="name" autocomplete="name" maxlength="100"></label></div>
<label for="email">Work email<input id="email" name="email" type="email" autocomplete="username" required maxlength="254"></label>
<label for="password" id="passwordLabel">Password<span class="password-input-wrap"><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024"><button id="togglePassword" class="password-toggle" type="button" aria-label="Show password" aria-pressed="false"><svg class="eye-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.7"/></svg></button></span></label>
<button id="submit" type="submit">Sign in</button></form><p id="message" role="status" aria-live="polite"></p><p class="note" id="note">Access is restricted to accounts created by your portal administrator.</p></main>
</body></html>"""


def _p(params: dict, key: str, default=None):
    vals = params.get(key)
    return vals[0] if vals else default


def _enabled_query_flag(params: dict, *keys: str) -> bool:
    """Return whether a request explicitly opts in to an expensive action."""
    return any(str(_p(params, key, "")).lower() in {"1", "true", "yes"} for key in keys)


def _int(params: dict, key: str, default: int) -> int:
    try:
        return int(_p(params, key, default))
    except (TypeError, ValueError):
        return default


def _window(params: dict) -> tuple[date, date]:
    end = date.fromisoformat(_p(params, "end") or api.portal_today().isoformat())
    lookback_days = max(1, APP.sync_lookback_days)
    start = date.fromisoformat(
        _p(params, "start") or (end - timedelta(days=lookback_days - 1)).isoformat()
    )
    return start, end


# ------------------------------------------------------------------ sync actions
def start_sync(params: dict, body: dict) -> dict:
    """Run a sync now or queue it for callers that do not need the result."""
    from .sync import run_connection

    start, end = _window(params)
    cid = _p(params, "connection") or body.get("connection_id")
    targets = ([conns.get(int(cid))] if cid else conns.all_connections(enabled_only=True))
    targets = [c for c in targets if c]
    if cid and targets and REGISTRY.get(targets[0].provider) and not REGISTRY[targets[0].provider].syncable:
        raise ValueError("This connection sends actions and does not sync data rows.")
    targets = [c for c in targets if REGISTRY.get(c.provider) and REGISTRY[c.provider].syncable]
    if not targets:
        raise ValueError("No enabled connections to sync.")

    def work() -> list[dict]:
        results = []
        for conn in targets:
            try:
                results.append(run_connection(conn, start, end))
            except Exception:  # noqa: BLE001 — one bad account must not stop the rest
                log.error("sync thread failed\n%s", traceback.format_exc())
                results.append({
                    "connection_id": conn.id, "provider": conn.provider,
                    "name": conn.name, "status": "error", "rows": 0,
                    "message": "The sync stopped unexpectedly. Check the server log.",
                })
        return results

    queued = [{"id": c.id, "provider": c.provider, "name": c.name} for c in targets]
    window = {"start": start.isoformat(), "end": end.isoformat()}
    wait = body.get("wait") in (True, 1, "1", "true", "yes")
    if wait:
        return {"queued": queued, "window": window, "complete": True, "results": work()}

    threading.Thread(target=work, daemon=True, name="sync").start()
    return {"queued": queued, "window": window, "complete": False}


# ------------------------------------------------------------- connection actions
def create_connection(params: dict, body: dict) -> dict:
    provider_id = str(body.get("provider") or "")
    provider = REGISTRY.get(provider_id)
    if not provider:
        raise KeyError(f"unknown provider: {provider_id}")
    name = str(body.get("name") or "").strip() or provider.label
    config, secrets = _split_fields(provider, body.get("values") or {})
    conn = conns.create(provider_id, name, config, secrets)
    api.invalidate_report_cache()
    db.audit("create", "connection", conn.id, {"provider": provider_id, "name": name})
    return conns.public(conn, provider)


def update_connection(connection_id: int, body: dict) -> dict:
    conn = conns.get(connection_id)
    if not conn:
        raise KeyError(f"no connection with id {connection_id}")
    provider = REGISTRY.get(conn.provider)
    config, secrets = _split_fields(provider, body.get("values") or {})
    updated = conns.update(
        connection_id,
        name=(str(body["name"]).strip() if body.get("name") else None),
        config=config, secrets=secrets,
        enabled=body.get("enabled"),
    )
    api.invalidate_report_cache()
    db.audit("update", "connection", connection_id, {"provider": conn.provider, "name": updated.name})
    return conns.public(updated, provider)


def _split_fields(provider, values: dict) -> tuple[dict, dict]:
    """Secrets go in the secrets column; everything else is plain config."""
    secret_keys = {f.key for f in getattr(provider, "fields", ())if f.secret}
    config = {k: v for k, v in values.items() if k not in secret_keys}
    secrets = {k: v for k, v in values.items() if k in secret_keys}
    return config, secrets


def test_connection(params: dict, body: dict) -> dict:
    """Validate credentials without saving data or changing the warehouse."""
    connection_id = body.get("connection_id")
    existing = conns.get(int(connection_id)) if connection_id else None
    provider_id = existing.provider if existing else str(body.get("provider") or "")
    provider = REGISTRY.get(provider_id)
    if not provider:
        raise KeyError(f"unknown provider: {provider_id}")
    config, secrets = _split_fields(provider, body.get("values") or {})
    if existing:
        config = {**existing.config, **config}
        secrets = {**existing.secrets, **{k: v for k, v in secrets.items() if v}}
    from .providers.base import Connection
    candidate = Connection(0, provider_id, str(body.get("name") or provider.label), config, secrets)
    missing = provider.missing(candidate)
    if missing:
        raise ValueError("Complete: " + ", ".join(missing))
    custom_test = getattr(provider, "test_connection", None)
    if callable(custom_test):
        return {"ok": True, "rows": 0, "message": custom_test(candidate)}
    today = api.portal_today()
    rows = provider.fetch(candidate, today - timedelta(days=2), today)
    return {"ok": True, "rows": len(rows),
            "message": f"Connection verified. {len(rows)} recent row(s) returned."}


def delete_connection(connection_id: int, params: dict) -> dict:
    connection = conns.get(connection_id)
    drop = _p(params, "drop_data") in ("1", "true", "yes")
    if not conns.delete(connection_id, drop_data=drop):
        raise KeyError(f"no connection with id {connection_id}")
    api.invalidate_report_cache()
    if drop:
        api.invalidate_campaign_cache()
    oauth.forget(connection_id)
    db.audit("delete", "connection", connection_id,
             {"provider": connection.provider, "name": connection.name, "data_removed": drop})
    return {"deleted": connection_id, "data_removed": drop}


# ---------------------------------------------------------------------- routing
GET_ROUTES = {
    "/api/meta": lambda p: api.meta(),
    "/api/assistant/status": lambda p: assistant.status(),
    "/api/providers": lambda p: api.providers(),
    "/api/connections": lambda p: api.connections(),
    "/api/source": lambda p: api.source_view(
        _int(p, "connection_id", 0), _p(p, "start"), _p(p, "end"),
        _int(p, "limit", 100), _int(p, "offset", 0), _p(p, "q", "")),
    "/api/oauth/redirect-uri": lambda p: {"redirect_uri": oauth.redirect_uri()},
    "/api/syncs": lambda p: api.syncs(_int(p, "limit", 30)),
    "/api/overview": lambda p: api.overview(_p(p, "start"), _p(p, "end")),
    "/api/timeseries": lambda p: api.timeseries(_p(p, "start"), _p(p, "end")),
    "/api/channels": lambda p: api.channels(_p(p, "start"), _p(p, "end")),
    "/api/campaigns": lambda p: api.campaigns(
        _p(p, "start"), _p(p, "end"), _p(p, "source"),
        _int(p, "limit", 200), _int(p, "offset", 0)),
    "/api/search": lambda p: api.search(
        _p(p, "start"), _p(p, "end"), _p(p, "dim", "query"), _int(p, "limit", 50)),
    "/api/ahrefs": lambda p: api.ahrefs(_p(p, "end")),
    "/api/web": lambda p: api.web(_p(p, "start"), _p(p, "end"), _p(p, "dim", "channel")),
    "/api/crm": lambda p: api.crm(_p(p, "start"), _p(p, "end")),
    "/api/data-quality": lambda p: api.data_quality(_p(p, "start"), _p(p, "end")),
    "/api/marketing-analytics/workbooks": lambda p: {
        "workbooks": marketing_analytics.workbooks(
            discover=_enabled_query_flag(p, "discover", "refresh")
        )
    },
    "/api/marketing-analytics/mapping-status": lambda p: marketing_analytics.mapping_status(_p(p, "month")),
    "/api/marketing-analytics/tab": lambda p: marketing_analytics.read_tab(
        _int(p, "connection_id", 0), _p(p, "tab"), _p(p, "range", "A1:ZZ500"),
        _p(p, "spreadsheet_id")),
    "/api/automation": lambda p: automation.get_settings(),
    "/api/automation/rules": lambda p: {"rules": automation.get_rules()},
    "/api/mcp": lambda p: {
        "enabled": True,
        "http_enabled": bool(APP.mcp_token),
        "http_path": "/mcp",
        "stdio_command": sys.executable,
        "stdio_args": [str(ROOT / "mcp_server.py")],
        "tools": [definition["name"] for definition, _ in mcp.TOOLS],
    },
    "/api/workspace": lambda p: {
        "schema": platform.explorer_schema(),
        "governance": platform.governance(),
        "dashboards": platform.list_objects("dashboards"),
        "views": platform.list_objects("saved_explorer_views"),
        "destinations": platform.list_objects("data_destinations"),
        "jobs": platform.list_objects("scheduled_jobs"),
        "job_runs": [dict(r) for r in query("SELECT * FROM job_runs ORDER BY id DESC LIMIT 30")],
        "audit": platform.audit_entries(50),
    },
    "/api/planning": lambda p: platform.planning(_p(p, "start"), _p(p, "end")),
}

# Paths with an id in them, matched in order.
GET_PATTERNS = [
    (re.compile(r"^/api/connections/(\d+)$"),
     lambda m, p: _one_connection(int(m.group(1)))),
]

POST_ROUTES = {
    "/api/sync": start_sync,
    "/api/connections": create_connection,
    "/api/connections/test": test_connection,
    "/api/automation": lambda p, b: automation.save_settings(b),
    "/api/automation/rules": lambda p, b: automation.save_rules(b),
    "/api/explorer/query": lambda p, b: platform.explore(b),
    "/api/explorer/views": lambda p, b: platform.save_object("saved_explorer_views", b),
    "/api/dashboards": lambda p, b: platform.save_object("dashboards", b),
    "/api/destinations": lambda p, b: platform.save_object("data_destinations", b),
    "/api/jobs": lambda p, b: platform.save_object("scheduled_jobs", b),
    "/api/transformations": lambda p, b: platform.save_rule(b),
    "/api/marketing-analytics/stats": lambda p, b: marketing_analytics.selection_stats(
        b.get("values") or []),
    "/api/users": lambda p, b: platform.save_user(b),
}

POST_PATTERNS = [
    (re.compile(r"^/api/jobs/(\d+)/run$"),
     lambda m, p, b: platform.run_job(int(m.group(1)))),
    (re.compile(r"^/api/dashboards/(\d+)/delete$"),
     lambda m, p, b: platform.delete_dashboard(int(m.group(1)))),
    (re.compile(r"^/api/explorer/views/(\d+)/delete$"),
     lambda m, p, b: platform.delete_object("saved_explorer_views", int(m.group(1)))),
    (re.compile(r"^/api/transformations/(\d+)/delete$"),
     lambda m, p, b: platform.delete_object("transformation_rules", int(m.group(1)))),
    (re.compile(r"^/api/destinations/(\d+)/delete$"),
     lambda m, p, b: platform.delete_object("data_destinations", int(m.group(1)))),
    (re.compile(r"^/api/jobs/(\d+)/delete$"),
     lambda m, p, b: platform.delete_object("scheduled_jobs", int(m.group(1)))),
    (re.compile(r"^/api/connections/(\d+)$"),
     lambda m, p, b: update_connection(int(m.group(1)), b)),
    (re.compile(r"^/api/connections/(\d+)/delete$"),
     lambda m, p, b: delete_connection(int(m.group(1)), p)),
    (re.compile(r"^/api/connections/(\d+)/sync$"),
     lambda m, p, b: start_sync({**p, "connection": [m.group(1)]}, b)),
]


def _one_connection(connection_id: int) -> dict:
    conn = conns.get(connection_id)
    if not conn:
        raise KeyError(f"no connection with id {connection_id}")
    return conns.public(conn, REGISTRY.get(conn.provider))


def _gmail_status() -> dict:
    provider = REGISTRY["gmail"]
    saved = conns.all_connections(provider="gmail")
    conn = saved[0] if saved else None
    if not conn:
        return {"exists": False, "connected": False, "account_email": "",
                "needs_setup": True, "needs_authorization": False}
    missing = provider.missing(conn)
    return {
        "exists": True,
        "connected": "Authorisation" not in missing,
        "account_email": str(conn.config.get("account_email") or ""),
        "needs_setup": not bool(conn.get("client_id") and conn.get("client_secret")),
        "needs_authorization": "Authorisation" in missing,
        "connection_id": conn.id,
        "provider_label": provider.label,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "MarketingDataPortal/2.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing -----------------------------------------------------------
    def handle_one_request(self):
        """Return remote PostgreSQL connections after every HTTP request."""
        try:
            super().handle_one_request()
        except db.DatabaseUnavailable:
            # Connection acquisition is bounded.  Return a retryable response
            # instead of leaving a handler thread blocked or dropping the socket.
            log.warning("database temporarily unavailable while serving %s", getattr(self, "path", "request"))
            self._error(503, "Database temporarily unavailable. Please retry.")
        finally:
            if db.using_postgres():
                close_connection()
                db.set_audit_actor(None)

    def handle(self):
        """Serve this connection, then release its database handle.

        ThreadingHTTPServer runs one thread per connection and each thread opens
        its own SQLite connection (they are not thread-safe), so closing here
        keeps a long-running portal from accumulating open handles.
        """
        try:
            super().handle()
        finally:
            close_connection()
            db.set_audit_actor(None)

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; "
                         "style-src 'self'; script-src 'self'; connect-src 'self'; "
                         "object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser navigated away mid-response

    def _json(self, payload, status: int = 200, headers: dict | None = None) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json", headers)

    def _error(self, status: int, message: str) -> None:
        # A rejected request can still have an unread body. Do not let those
        # bytes become the next request on an HTTP/1.1 keep-alive connection.
        headers = None
        if self.command in {"POST", "PUT", "PATCH", "DELETE"}:
            self.close_connection = True
            headers = {"Connection": "close"}
        self._json({"error": message}, status, headers)

    def _html(self, body: str, status: int = 200) -> None:
        self._send(status, body.encode(), "text/html; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Content-Length must be a valid number.") from None
        if length < 0:
            raise ValueError("Request body length is invalid.")
        if length > 1_048_576:
            raise OverflowError("Request body is too large (maximum 1 MB).")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode())
            if not isinstance(parsed, dict):
                raise ValueError("Request body must be a JSON object.")
            return parsed
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must contain valid JSON.") from None

    def _trusted_local_request(self) -> bool:
        """Allow first-admin setup only through a direct loopback address.

        Docker Desktop replaces the socket peer with its bridge gateway. The
        local-only Compose profile can explicitly trust that translation, but
        the request must still use a loopback Host header and no proxy headers.
        """
        if self._proxied_request():
            return False
        client = self.client_address[0]
        host = urlsplit(f"//{self.headers.get('Host', '')}").hostname or ""

        def is_loopback(value: str) -> bool:
            lowered = value.lower().rstrip(".")
            if lowered == "localhost":
                return True
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                return False
            return address.is_loopback

        if not is_loopback(host):
            return False
        return is_loopback(client) or APP.trust_loopback_host_for_setup

    def _login_remote_address(self) -> str:
        """Use one validated forwarded IP only from an explicitly trusted proxy.

        Multi-value forwarding chains are rejected. In particular, accepting
        the leftmost X-Forwarded-For entry lets clients evade IP throttles when
        an append-style proxy preserves an untrusted value supplied by a client.
        """
        peer = self.client_address[0]
        try:
            peer_address = ipaddress.ip_address(peer).compressed
        except ValueError:
            return peer
        trusted = set()
        for item in APP.trusted_proxy_ips:
            try:
                trusted.add(ipaddress.ip_address(item).compressed)
            except ValueError:
                continue
        if peer_address not in trusted:
            return peer_address
        forwarded: dict[str, str] = {}
        for header in ("CF-Connecting-IP", "X-Forwarded-For"):
            get_all = getattr(self.headers, "get_all", None)
            values = get_all(header) if get_all else None
            if values is None:
                candidate = self.headers.get(header)
                values = [candidate] if candidate is not None else []
            if not values:
                continue
            if len(values) != 1:
                return peer_address
            candidate = str(values[0]).strip()
            if not candidate or "," in candidate:
                return peer_address
            try:
                forwarded[header] = ipaddress.ip_address(candidate).compressed
            except ValueError:
                return peer_address
        if len(set(forwarded.values())) > 1:
            return peer_address
        if forwarded:
            return next(iter(forwarded.values()))
        return peer_address

    def _proxied_request(self) -> bool:
        return any(self.headers.get(name) for name in (
            "CF-Connecting-IP", "X-Forwarded-For", "Forwarded", "X-Real-IP"))

    def _session(self) -> dict | None:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        saved = cookie.get("portal_session")
        return auth.session(saved.value) if saved else None

    def _set_session_cookie(self, token: str, max_age: int = auth.SESSION_HOURS * 3600) -> str:
        return (f"portal_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
                + ("; Secure" if APP.secure_cookie else ""))

    def _clear_session_cookie(self) -> str:
        return self._set_session_cookie("", max_age=0)

    def _role_allows(self, role: str, method: str, path: str) -> bool:
        if role == "admin":
            return True
        if method == "GET":
            if path in {"/", "/index.html"} or path.startswith("/static/"):
                return True
            common = {
                "/api/auth/me", "/api/meta", "/api/providers", "/api/connections",
                "/api/bootstrap",
                "/api/assistant/status",
                "/api/auth/profile", "/api/auth/gmail", "/api/auth/mfa", "/api/ui-preferences",
                "/api/overview", "/api/timeseries", "/api/channels", "/api/campaigns",
                "/api/search", "/api/ahrefs", "/api/web", "/api/crm",
                "/api/data-quality", "/api/automation", "/api/planning",
                "/api/marketing-analytics/workbooks", "/api/marketing-analytics/tab", "/api/marketing-analytics/mapping-status",
            }
            analyst = {
                "/api/workspace", "/api/mcp", "/api/syncs", "/api/source",
            }
            if path in common:
                return True
            if role == "analyst" and path in analyst:
                return True
            return False
        if method == "POST":
            if path == "/api/ui-preferences":
                return True
            if path == "/api/explorer/query":
                return True
            if path == "/api/marketing-analytics/stats":
                return True
            if path in {"/api/explorer/views", "/api/dashboards"}:
                return role == "analyst"
        return False

    def _current_token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        saved = cookie.get("portal_session")
        return saved.value if saved else ""

    def _same_origin(self) -> bool:
        """Require an explicit same-origin browser request for state changes."""
        origin = self.headers.get("Origin")
        if not origin:
            return False
        origin_parts = urlsplit(origin)
        request_host = (self.headers.get("Host") or "").lower()
        return (origin_parts.scheme in {"http", "https"} and origin_parts.netloc.lower() == request_host
                and not origin_parts.path and not origin_parts.query and not origin_parts.fragment)

    def _json_content_type(self) -> bool:
        return (self.headers.get_content_type() or "").lower() == "application/json"

    def _read_body(self) -> dict | None:
        if not self._json_content_type():
            self._error(415, "Send this request as application/json.")
            return None
        try:
            return self._body()
        except OverflowError as exc:
            self.close_connection = True
            self._error(413, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        return None

    def _static(self, path: str) -> None:
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            self._error(404, "not found")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- OAuth --------------------------------------------------------------
    def _oauth_start(self, params: dict, user: dict) -> None:
        cid = _p(params, "connection_id")
        conn = conns.get(int(cid)) if cid and cid.isdigit() else None
        if not conn:
            self._error(404, "unknown connection")
            return
        provider = REGISTRY.get(conn.provider)
        if not provider or not provider.oauth:
            self._error(400, f"{conn.provider} does not use OAuth")
            return
        try:
            url = oauth.begin(conn, provider.oauth, int(user["id"]))
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _oauth_callback(self, params: dict) -> None:
        error = _p(params, "error_description") or _p(params, "error")
        state = _p(params, "state") or ""
        code = _p(params, "code") or _p(params, "auth_code") or ""
        state_context = oauth.claim_state(state)

        if error:
            self._html(_result_page(f"Authorisation was declined: {error}", ok=False))
            return
        if not state_context or not code:
            self._html(_result_page(
                "That authorisation link has expired or was already used. "
                "Press Connect again.", ok=False))
            return

        connection_id, user_id = state_context
        if not auth.active_admin(user_id):
            self._html(_result_page("The account that started this connection is no longer an active administrator. Sign in and connect again.", ok=False))
            return

        conn = conns.get(connection_id)
        provider = REGISTRY.get(conn.provider) if conn else None
        if not conn or not provider or not provider.oauth:
            self._html(_result_page("That connection no longer exists.", ok=False))
            return

        try:
            # A couple of platforms deviate from the standard grant shape.
            if hasattr(provider, "exchange"):
                payload = provider.exchange(conn, code)
                data = payload.get("data") or payload
            else:
                data = oauth.exchange_code(conn, provider.oauth, code)
        except Exception as exc:  # noqa: BLE001 — shown to the person in the page
            log.error("oauth exchange failed\n%s", traceback.format_exc())
            self._html(_result_page(f"Could not complete authorisation: {exc}", ok=False))
            return

        secrets = {}
        if data.get("refresh_token"):
            secrets["refresh_token"] = data["refresh_token"]
        if data.get("access_token"):
            secrets["access_token"] = data["access_token"]
        updated = conns.update(connection_id, secrets=secrets)
        if provider.id == "gmail" and updated:
            try:
                account_email = provider.account_email(updated)
                conns.update(connection_id, config={**updated.config, "account_email": account_email})
            except Exception as exc:  # authorization remains usable if identity lookup is unavailable
                log.warning("Gmail account label lookup failed: %s", exc)
        oauth.forget(connection_id)
        self._html(_result_page(f"{provider.label} connected. You can close this tab."))

    # -- routes -------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        route = parts.path
        if route == "/healthz":
            self._json({"status": "ok"})
            return
        if route == "/readyz":
            try:
                query("SELECT key FROM portal_settings LIMIT 1")
            except Exception:
                log.exception("database readiness check failed")
                self._json({"status": "unavailable"}, 503)
                return
            self._json({"status": "ok"})
            return
        if route == "/api/auth/status":
            self._json({"setup_required": auth.setup_required(), "can_setup": self._trusted_local_request()})
            return
        if route in {"/static/login.css", "/static/login.js"}:
            self._static(route[len("/static/"):])
            return
        if route == "/invite":
            self._send(200, (WEB / "invite.html").read_bytes(), "text/html; charset=utf-8")
            return
        if route in {"/static/invite.css", "/static/invite.js",
                     "/static/assets/vortex-veri-mark.png"}:
            self._static(route[len("/static/"):])
            return
        if route == "/oauth/callback":
            self._oauth_callback(parse_qs(parts.query))
            return

        session = self._session()
        user = session["user"] if session else None
        if route == "/login":
            if user:
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._html(LOGIN_HTML)
            return
        if not user:
            if route == "/invitations":
                self.send_response(302)
                self.send_header("Location", "/login")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if route in {"/", "/index.html"}:
                self._html(LOGIN_HTML)
            else:
                self._error(401, "Sign in to access the portal.")
            return
        if not self._role_allows(user["role"], "GET", route):
            self._error(403, "Your portal role does not allow this action.")
            return

        db.set_audit_actor(user["email"])
        route, params = parts.path, parse_qs(parts.query)

        if route == "/invitations":
            if user["role"] != "admin":
                self._error(403, "Only an administrator can manage portal invitations.")
                return
            self._send(200, (WEB / "invitations.html").read_bytes(), "text/html; charset=utf-8")
            return
        if route == "/api/invitations":
            if user["role"] != "admin":
                self._error(403, "Only an administrator can manage portal invitations.")
                return
            self._json(auth.list_invitations())
            return
        if route in ("/", "/index.html"):
            self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if route == "/api/auth/me":
            self._json({"user": user, "csrf_token": session["csrf_token"]})
            return
        if route == "/api/bootstrap":
            bootstrap_meta = api.meta()
            dashboard_end = bootstrap_meta.get("data_to") or api.portal_today().isoformat()
            dashboard_start = (date.fromisoformat(dashboard_end) - timedelta(days=29)).isoformat()
            account = auth.bootstrap_account(int(user["id"]))
            self._json({
                "user": user,
                "csrf_token": session["csrf_token"],
                "meta": bootstrap_meta,
                "connections": api.connections(),
                "automation_rules": (account["automation_rules"]
                                     if user["role"] == "admin" else {"rules": []}),
                "profile": account["profile"],
                "ui_preferences": account["ui_preferences"],
                "overview_bundle": {
                    "overview": api.overview(dashboard_start, dashboard_end),
                    "timeseries": api.timeseries(dashboard_start, dashboard_end),
                    "channels": api.channels(dashboard_start, dashboard_end),
                },
            })
            return
        if route == "/api/auth/profile":
            try:
                self._json(auth.profile(int(user["id"])))
            except ValueError as exc:
                self._error(404, str(exc))
            return
        if route == "/api/auth/mfa":
            try:
                self._json(auth.mfa_status(int(user["id"])))
            except ValueError as exc:
                self._error(404, str(exc))
            return
        if route == "/api/ui-preferences":
            try:
                self._json(auth.ui_preferences(int(user["id"])))
            except ValueError as exc:
                self._error(404, str(exc))
            return
        if route == "/api/auth/gmail":
            if user["role"] != "admin":
                self._error(403, "Only an administrator can manage the portal's Gmail sender.")
                return
            self._json(_gmail_status())
            return
        if route == "/api/providers" and user["role"] != "admin":
            self._json([provider for provider in api.providers() if provider.get("id") != "gmail"])
            return
        if route == "/api/connections" and user["role"] != "admin":
            safe_fields = {
                "id", "provider", "provider_label", "category", "name", "enabled", "ready",
                "last_status", "last_run_at", "last_success_at", "rows_held",
            }
            self._json([{key: value for key, value in connection.items() if key in safe_fields}
                        for connection in api.connections() if connection["provider"] != "gmail"])
            return
        if route == "/api/workspace" and user["role"] == "analyst":
            workspace = GET_ROUTES[route](params)
            workspace["governance"]["users"] = []
            workspace["audit"] = []
            workspace["destinations"] = []
            workspace["jobs"] = []
            workspace["job_runs"] = []
            self._json(workspace)
            return
        if route == "/mcp":
            self._send(405, b"", "application/json", {"Allow": "POST"})
            return
        if route.startswith("/static/"):
            self._static(route[len("/static/"):])
            return
        if route == "/oauth/start":
            self._oauth_start(params, user)
            return
        artifact_match = re.fullmatch(r"/api/job-runs/(\d+)/download", route)
        if artifact_match:
            try:
                artifact = platform.artifact_for_run(int(artifact_match.group(1)))
                self._send(200, artifact.read_bytes(), "application/octet-stream",
                           {"Content-Disposition": f'attachment; filename="{artifact.name}"'})
            except ValueError as exc:
                self._error(404, str(exc))
            return

        handler = GET_ROUTES.get(route)
        try:
            if handler:
                self._json(handler(params))
                return
            for pattern, fn in GET_PATTERNS:
                m = pattern.match(route)
                if m:
                    self._json(fn(m, params))
                    return
            self._error(404, f"no such endpoint: {route}")
        except KeyError as exc:
            self._error(404, str(exc).strip("'"))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.error("GET %s failed\n%s", route, traceback.format_exc())
            self._error(500, "The portal could not complete this request. Check the server log for details.")

    def do_POST(self):  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/mcp":
            if not APP.mcp_token:
                self._error(503, "HTTP MCP is disabled. Set PORTAL_MCP_TOKEN to enable it.")
                return
            authorization = self.headers.get("Authorization") or ""
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, APP.mcp_token):
                self._error(401, "A valid MCP bearer token is required.")
                return
            body = self._read_body()
            if body is None:
                return
            response = mcp.handle_message(body)
            if response is None:
                self._send(202, b"", "application/json")
            else:
                self._json(response)
            return

        route = parts.path
        if route == "/api/invitations/accept":
            if not self._same_origin():
                self._error(403, "The invitation request must come from this portal.")
                return
            body = self._read_body()
            if body is None:
                return
            try:
                result = auth.accept_invitation(
                    body.get("token"), body.get("email"), body.get("display_name"),
                    str(body.get("password") or ""),
                )
            except ValueError as exc:
                status = 409 if "already exists" in str(exc) else 400
                self._error(status, str(exc))
                return
            self._json({"user": result["user"]},
                       headers={"Set-Cookie": self._set_session_cookie(result["token"])})
            return
        if route in {"/api/auth/login", "/api/auth/setup"}:
            if not self._same_origin():
                self._error(403, "The sign-in request must come from this portal.")
                return
            body = self._read_body()
            if body is None:
                return
            if route == "/api/auth/setup":
                if not self._trusted_local_request():
                    self._error(403, "Create the first administrator from the computer running the portal.")
                    return
                try:
                    result = auth.create_first_admin(body.get("email"), body.get("display_name"), str(body.get("password") or ""))
                except ValueError as exc:
                    self._error(409, str(exc))
                    return
                self._json({"user": result["user"]}, headers={"Set-Cookie": self._set_session_cookie(result["token"])})
                return
            result = auth.login(body.get("email"), str(body.get("password") or ""), self._login_remote_address())
            if not result:
                self._error(401, "Email or password is incorrect, or sign-in is temporarily unavailable.")
                return
            if result.get("mfa_required"):
                self._json({"mfa_required": True, "challenge": result["challenge"]})
                return
            self._json({"user": result["user"]}, headers={"Set-Cookie": self._set_session_cookie(result["token"])})
            return

        if route == "/api/auth/mfa/verify":
            if not self._same_origin():
                self._error(403, "The verification request must come from this portal.")
                return
            body = self._read_body()
            if body is None:
                return
            try:
                result = auth.verify_mfa(body.get("challenge"), body.get("code"))
            except ValueError as exc:
                self._error(401, str(exc))
                return
            self._json({"user": result["user"]}, headers={"Set-Cookie": self._set_session_cookie(result["token"])})
            return

        session = self._session()
        if not session:
            self._error(401, "Sign in to access the portal.")
            return
        user = session["user"]
        csrf = self.headers.get("X-CSRF-Token") or ""
        if not csrf or not hmac.compare_digest(csrf, session["csrf_token"]):
            self._error(403, "This request expired. Reload the portal and try again.")
            return
        if not self._same_origin():
            self._error(403, "This write request did not come from the portal.")
            return
        db.set_audit_actor(user["email"])
        if route == "/api/invitations" or re.fullmatch(r"/api/invitations/\d+/revoke", route):
            if user["role"] != "admin":
                self._error(403, "Only an administrator can manage portal invitations.")
                return
            body = self._read_body()
            if body is None:
                return
            try:
                if route == "/api/invitations":
                    invite = auth.create_invitation(
                        body.get("email"), body.get("display_name"), actor=user["email"])
                    self._json(invite, status=201)
                else:
                    match = re.fullmatch(r"/api/invitations/(\d+)/revoke", route)
                    self._json(auth.revoke_invitation(int(match.group(1)), actor=user["email"]))
            except KeyError as exc:
                self._error(404, str(exc).strip("'"))
            except ValueError as exc:
                self._error(409 if "already" in str(exc).lower() else 400,
                            str(exc))
            return
        if route == "/api/auth/logout":
            # Consume the JSON body before keeping this HTTP/1.1 connection
            # alive. Otherwise the next request can be parsed as "{}GET".
            if self._read_body() is None:
                return
            auth.revoke_session(self._current_token(), actor=user["email"])
            self._json({"ok": True}, headers={"Set-Cookie": self._clear_session_cookie()})
            return
        if route == "/api/auth/mfa/enroll":
            body = self._read_body()
            if body is None:
                return
            try:
                self._json(auth.begin_mfa_enrollment(int(user["id"]), body.get("current_password")))
            except ValueError as exc:
                self._error(400, str(exc))
            return
        if route == "/api/auth/mfa/enable":
            body = self._read_body()
            if body is None:
                return
            try:
                result = auth.complete_mfa_enrollment(body.get("challenge"), body.get("code"))
            except ValueError as exc:
                self._error(400, str(exc))
                return
            self._json({"user": result["user"], "csrf_token": result["csrf_token"], "recovery_codes": result["recovery_codes"]}, headers={"Set-Cookie": self._set_session_cookie(result["token"])})
            return
        if route == "/api/auth/password":
            body = self._read_body()
            if body is None:
                return
            try:
                result = auth.change_password(
                    int(user["id"]), str(body.get("current_password") or ""),
                    str(body.get("new_password") or ""), user["email"],
                )
            except ValueError as exc:
                self._error(400, str(exc))
                return
            self._json({"user": result["user"], "csrf_token": result["csrf_token"]},
                       headers={"Set-Cookie": self._set_session_cookie(result["token"])})
            return
        if route == "/api/auth/profile":
            body = self._read_body()
            if body is None:
                return
            try:
                self._json(auth.update_profile(int(user["id"]), body, actor=user["email"]))
            except ValueError as exc:
                self._error(400, str(exc))
            return
        if route == "/api/ui-preferences":
            body = self._read_body()
            if body is None:
                return
            try:
                if "key" not in body or "value" not in body:
                    raise ValueError("Send a preference key and value.")
                self._json(auth.update_ui_preference(
                    int(user["id"]), body["key"], body["value"], actor=user["email"]))
            except ValueError as exc:
                self._error(400, str(exc))
            return
        if route == "/api/assistant/ask":
            body = self._read_body()
            if body is None:
                return
            try:
                self._json(assistant.ask(int(user["id"]), body.get("question"),
                                         body.get("start"), body.get("end")))
            except assistant.AssistantRateLimited as exc:
                self._error(429, str(exc))
            except ValueError as exc:
                self._error(400, str(exc))
            return
        if route == "/api/auth/sessions/revoke-others":
            if self._read_body() is None:
                return
            try:
                revoked = auth.revoke_other_sessions(int(user["id"]), self._current_token(), actor=user["email"])
                self._json({"revoked": revoked})
            except ValueError as exc:
                self._error(400, str(exc))
            return
        if route in {"/api/auth/gmail/test", "/api/auth/gmail/disconnect"}:
            if user["role"] != "admin":
                self._error(403, "Only an administrator can manage the portal's Gmail sender.")
                return
            if self._read_body() is None:
                return
            saved = conns.all_connections(provider="gmail")
            conn = saved[0] if saved else None
            if not conn:
                self._error(404, "Set up a Gmail sender first.")
                return
            provider = REGISTRY["gmail"]
            if route.endswith("/test"):
                if not provider.ready(conn):
                    self._error(400, "Finish Gmail setup and authorisation first.")
                    return
                try:
                    sent = provider.send_test(conn, user["email"])
                    db.audit("gmail_test_email_sent", "connection", conn.id,
                             {"recipient": user["email"]}, actor=user["email"])
                    self._json({"sent": True, "recipient": user["email"],
                                "message_id": sent.get("message_id")})
                except Exception as exc:  # noqa: BLE001
                    log.warning("Gmail test email failed: %s", exc)
                    self._error(502, f"Gmail could not send the test email: {exc}")
                return
            oauth.forget(conn.id)
            conns.delete(conn.id, drop_data=False)
            db.audit("gmail_sender_disconnected", "connection", conn.id, {}, actor=user["email"])
            self._json({"removed": True, "google_grant_revoked": False})
            return
        if not self._role_allows(user["role"], "POST", route):
            self._error(403, "Your portal role does not allow this action.")
            return
        route, params = parts.path, parse_qs(parts.query)
        body = self._read_body()
        if body is None:
            return

        try:
            handler = POST_ROUTES.get(route)
            if handler:
                self._json(handler(params, body))
                return
            for pattern, fn in POST_PATTERNS:
                m = pattern.match(route)
                if m:
                    self._json(fn(m, params, body))
                    return
            self._error(404, f"no such endpoint: {route}")
        except KeyError as exc:
            self._error(404, str(exc).strip("'"))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.error("POST %s failed\n%s", route, traceback.format_exc())
            self._error(500, "The portal could not complete this request. Check the server log for details.")


def _result_page(message: str, ok: bool = True) -> str:
    tone = "#0ca30c" if ok else "#d03b3b"
    safe_message = escape(message)
    return f"""<!doctype html><meta charset="utf-8">
<title>{'Connected' if ok else 'Not connected'}</title>
<body style="font:16px system-ui;margin:0;display:grid;place-items:center;height:100vh;
             background:#f9f9f7;color:#0b0b0b">
  <div style="max-width:34rem;padding:2rem;text-align:center">
    <div style="width:12px;height:12px;border-radius:50%;background:{tone};
                margin:0 auto 1.25rem"></div>
    <p style="margin:0 0 1.5rem;line-height:1.5">{safe_message}</p>
    <a href="/#connections" style="color:#2a78d6">Back to the portal</a>
  </div>
</body>"""


def build_server() -> ThreadingHTTPServer:
    try:
        local_only = ipaddress.ip_address(APP.host).is_loopback
    except ValueError:
        local_only = APP.host.lower().rstrip(".") == "localhost"
    # Docker binds the application to all container interfaces, while the
    # local Compose file publishes that port only on the host loopback address.
    # Allow HTTP cookies there only when the operator explicitly opted into the
    # local-Docker setting. Production deployments must keep secure cookies.
    local_docker_http = (
        APP.trust_loopback_host_for_setup
        and APP.host.strip().lower() in {"0.0.0.0", "::"}
    )
    if not local_only and not APP.secure_cookie and not local_docker_http:
        raise RuntimeError(
            "PORTAL_COOKIE_SECURE must be enabled before the portal can listen on a network address. Put the HTTP server behind HTTPS."
        )
    init_db()
    if not local_only and not local_docker_http:
        if not APP.mfa_encryption_key:
            raise RuntimeError("PORTAL_MFA_ENCRYPTION_KEY is required before the portal can listen on a network address.")
        unenrolled = db.scalar(
            "SELECT COUNT(*) FROM portal_users WHERE enabled=1 AND password_hash!='' AND mfa_enabled=0",
            default=0,
        )
        if unenrolled:
            raise RuntimeError(
                "Every enabled portal account must enroll MFA before the portal can listen on a network address."
            )
    from .sync import recover_interrupted_runs
    # Bind first. A second process that cannot own this port must not mark
    # the live process's syncs or scheduled jobs as interrupted.
    httpd = ThreadingHTTPServer((APP.host, APP.port), Handler)
    try:
        recovered = recover_interrupted_runs()
        if recovered:
            log.warning("closed %s interrupted sync run(s)", recovered)
        recovered_jobs = platform.recover_interrupted_jobs()
        if recovered_jobs:
            log.warning("closed %s interrupted scheduled job run(s)", recovered_jobs)
        if APP.demo_mode:
            from .seed import seed_if_empty
            seed_if_empty()
        return httpd
    except Exception:
        httpd.server_close()
        raise


def serve() -> None:
    from .scheduler import start_scheduler, stop_scheduler

    httpd = build_server()
    if APP.scheduler_enabled:
        start_scheduler()

    print(f"\n  Vortex Veri → http://localhost:{APP.port}\n  Ctrl+C to stop.\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopping…")
    finally:
        stop_scheduler()
        httpd.server_close()
