"""Password authentication, opaque web sessions, and account administration."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from typing import Any

from . import db

PASSWORD_ITERATIONS = 600_000
SESSION_HOURS = 8
INVITATION_HOURS = 72
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 10
LOGIN_IP_MAX_FAILURES = 60
_login_lock = threading.Lock()
_login_failures: dict[str, list[float]] = {}
_login_inflight: dict[str, int] = {}
_login_hash_slots = threading.BoundedSemaphore(4)

UI_PREFERENCE_KEYS = frozenset({
    "savedPortalViews", "marketingDashboardViews", "customMetrics", "calendarDatePresets", "campaignColumns",
    "portalTableColumns", "portalTableWidths", "portalTableHeights",
    "portalTableRowHeights", "campaignWidths", "campaignRowHeights",
    "campaignRowHeight", "portalTableDensity", "portalFreezeFirstColumn",
    "portalTableStripes",
})
_UI_ARRAY_KEYS = frozenset({"savedPortalViews", "marketingDashboardViews", "customMetrics", "calendarDatePresets", "campaignColumns"})
_UI_OBJECT_KEYS = frozenset({"portalTableColumns", "portalTableWidths", "portalTableHeights",
                             "portalTableRowHeights", "campaignWidths", "campaignRowHeights"})


def _saved_preferences(user_id: int) -> dict[str, Any]:
    rows = db.query("SELECT preferences_json FROM portal_users WHERE id=? AND enabled=1", (user_id,))
    if not rows:
        raise ValueError("This account is unavailable.")
    try:
        saved = json.loads(rows[0]["preferences_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        saved = {}
    return saved if isinstance(saved, dict) else {}


def ui_preferences(user_id: int) -> dict[str, Any]:
    saved = _saved_preferences(user_id)
    ui = saved.get("ui")
    return {"preferences": {key: value for key, value in ui.items()
                            if key in UI_PREFERENCE_KEYS}} if isinstance(ui, dict) else {"preferences": {}}


def _validate_ui_value(key: str, value: Any) -> str:
    if key not in UI_PREFERENCE_KEYS:
        raise ValueError("Unknown UI preference.")
    if key in _UI_ARRAY_KEYS and not isinstance(value, list):
        raise ValueError("This UI preference must be a list.")
    if key in _UI_OBJECT_KEYS and not isinstance(value, dict):
        raise ValueError("This UI preference must be an object.")
    if key == "portalTableDensity" and (not isinstance(value, str) or value not in {"compact", "comfortable", "spacious"}):
        raise ValueError("Unknown table density.")
    if key in {"portalFreezeFirstColumn", "portalTableStripes"} and (
        not isinstance(value, (str, bool)) or value not in {"true", "false", True, False}
    ):
        raise ValueError("This UI preference must be on or off.")
    if key == "campaignRowHeight":
        try:
            height = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Campaign row height is invalid.") from None
        if str(value) not in {str(height), str(float(height))} or not 28 <= height <= 160:
            raise ValueError("Campaign row height is invalid.")
    if key in {"savedPortalViews", "marketingDashboardViews"} and len(value) > 30:
        raise ValueError("Too many saved views.")
    if key == "calendarDatePresets" and len(value) > 8:
        raise ValueError("Too many date presets.")
    if key == "customMetrics" and len(value) > 100:
        raise ValueError("Too many custom metrics.")
    if key == "campaignColumns" and (len(value) > 150 or any(not isinstance(v, str) for v in value)):
        raise ValueError("Campaign columns must be a list of column names.")
    if key in _UI_OBJECT_KEYS and len(value) > 1000:
        raise ValueError("Too many table settings.")
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("This UI preference must contain valid JSON values.") from None
    if len(payload.encode("utf-8")) > 65_536:
        raise ValueError("This UI preference is too large (maximum 64 KB).")
    return payload


def update_ui_preference(user_id: int, key: str, value: Any, actor: str = "") -> dict[str, Any]:
    if not isinstance(key, str) or key not in UI_PREFERENCE_KEYS:
        raise ValueError("Unknown UI preference.")
    encoded = None if value is None else _validate_ui_value(key, value)
    with db._write_lock:
        conn = db.connect()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT preferences_json FROM portal_users WHERE id=? AND enabled=1",
                               (user_id,)).fetchone()
            if not row:
                raise ValueError("This account is unavailable.")
            try:
                saved = json.loads(row["preferences_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                saved = {}
            if not isinstance(saved, dict):
                saved = {}
            ui = saved.get("ui") if isinstance(saved.get("ui"), dict) else {}
            if encoded is None:
                ui.pop(key, None)
            else:
                ui[key] = json.loads(encoded)
            saved["ui"] = ui
            payload = json.dumps(saved, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(payload.encode("utf-8")) > 262_144:
                raise ValueError("Total UI preferences exceed 256 KB.")
            conn.execute("UPDATE portal_users SET preferences_json=?,updated_at=datetime('now') "
                         "WHERE id=? AND enabled=1", (payload, user_id))
    db.audit("ui_preference_updated", "user", user_id, {"key": key, "removed": encoded is None}, actor=actor)
    return ui_preferences(user_id)


def _password_error(password: str) -> str | None:
    if len(password) < 14:
        return "Use a password with at least 14 characters."
    if len(password) > 1024:
        return "Password is too long."
    return None


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def _password_matches(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        iterations = int(iterations_text)
        if scheme != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False


def _valid_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Enter a valid work email address.")
    return email


def setup_required() -> bool:
    configured = db.scalar("SELECT value FROM portal_settings WHERE key='auth_setup_complete'", default="")
    return configured != "1"


def _safe_user(row) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "email", "display_name", "role", "enabled", "created_at", "updated_at")}


def users() -> list[dict[str, Any]]:
    return [_safe_user(row) for row in db.query(
        "SELECT id,email,display_name,role,enabled,created_at,updated_at "
        "FROM portal_users WHERE password_hash != '' ORDER BY id")]


def active_admin(user_id: int) -> bool:
    return bool(db.scalar(
        "SELECT COUNT(*) FROM portal_users WHERE id=? AND role='admin' AND enabled=1 AND password_hash!=''",
        (user_id,),
    ))


def _make_session(user_id: int) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.execute(
        "DELETE FROM auth_sessions WHERE expires_at <= datetime('now') "
        "OR (revoked_at IS NOT NULL AND revoked_at < datetime('now','-30 days'))")
    db.execute(
        "INSERT INTO auth_sessions(token_hash,csrf_token,user_id,expires_at) "
        "VALUES(?,?,?,datetime('now', ?))",
        (hashlib.sha256(token.encode()).hexdigest(), csrf, user_id, f"+{SESSION_HOURS} hours"),
    )
    row = db.query(
        "SELECT id,email,display_name,role,enabled,created_at,updated_at "
        "FROM portal_users WHERE id=? AND enabled=1", (user_id,))
    if not row:
        raise ValueError("This account is unavailable.")
    db.audit("login", "session", user_id, {"expires_in_hours": SESSION_HOURS}, actor=row[0]["email"])
    return {"token": token, "csrf_token": csrf, "user": _safe_user(row[0])}


def create_first_admin(email: str, display_name: str, password: str) -> dict[str, Any]:
    if not setup_required():
        raise ValueError("Administrator setup has already been completed.")
    email = _valid_email(email)
    password_problem = _password_error(password)
    if password_problem:
        raise ValueError(password_problem)
    display_name = str(display_name or "").strip()[:100] or email
    password_hash = _password_hash(password)
    with db._write_lock:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        complete = conn.execute("SELECT value FROM portal_settings WHERE key='auth_setup_complete'").fetchone()
        if complete and complete[0] == "1":
            conn.rollback()
            raise ValueError("Administrator setup has already been completed.")
        existing = conn.execute("SELECT id FROM portal_users WHERE email=?", (email,)).fetchone()
        if existing:
            user_id = existing[0]
            conn.execute(
                "UPDATE portal_users SET display_name=?, role='admin', enabled=1, password_hash=?, "
                "updated_at=datetime('now') WHERE id=?",
                (display_name, password_hash, user_id),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO portal_users(email,display_name,role,enabled,password_hash) VALUES(?,?, 'admin',1,?)",
                (email, display_name, password_hash),
            )
            user_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO portal_settings(key,value) VALUES('auth_setup_complete','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=datetime('now')")
        conn.commit()
    session = _make_session(int(user_id))
    db.audit("initial_admin_created", "user", user_id, {"email": email}, actor=email)
    return session


def _failure_key(remote_addr: str) -> str:
    return hashlib.sha256((remote_addr or "unknown").encode()).hexdigest()


def _login_attempt_keys(remote_addr: str, email: str) -> tuple[str, str]:
    """Throttle each account across all IPs, and each source IP across accounts."""
    normalized = str(email or "").strip().lower()[:254]
    return (
        _failure_key("ip:" + (remote_addr or "unknown")),
        _failure_key("account:" + normalized),
    )


def _reserve_login_attempt(keys: tuple[str, ...], limits: tuple[int, ...], now: float) -> bool:
    with _login_lock:
        for candidate in list(_login_failures):
            attempts = [at for at in _login_failures[candidate] if now - at < LOGIN_WINDOW_SECONDS]
            if attempts:
                _login_failures[candidate] = attempts
            else:
                _login_failures.pop(candidate, None)
        if any(len(_login_failures.get(key, [])) + _login_inflight.get(key, 0) >= limit
               for key, limit in zip(keys, limits)):
            return False
        for key in keys:
            _login_inflight[key] = _login_inflight.get(key, 0) + 1
        return True


def _finish_login_attempt(keys: tuple[str, ...], now: float, failed: bool) -> None:
    with _login_lock:
        for key in keys:
            remaining = _login_inflight.get(key, 0) - 1
            if remaining > 0:
                _login_inflight[key] = remaining
            else:
                _login_inflight.pop(key, None)
            if failed:
                _login_failures.setdefault(key, []).append(now)
        if len(_login_failures) > 4096:
            candidates = [key for key in _login_failures if key not in _login_inflight]
            oldest = sorted(candidates, key=lambda item: _login_failures[item][0])[:1024]
            for item in oldest:
                _login_failures.pop(item, None)


def login(email: str, password: str, remote_addr: str) -> dict[str, Any] | None:
    now = time.time()
    normalized = str(email or "").strip().lower()
    password = str(password or "")
    keys = _login_attempt_keys(remote_addr, normalized)
    if not _login_hash_slots.acquire(blocking=False):
        return None
    if not _reserve_login_attempt(keys, (LOGIN_IP_MAX_FAILURES, LOGIN_MAX_FAILURES), now):
        _login_hash_slots.release()
        return None
    failed = True
    try:
        if len(normalized) > 254 or len(password) > 1024:
            _password_matches("invalid", _DUMMY_PASSWORD_HASH)
            return None
        row = db.query(
            "SELECT id,email,password_hash FROM portal_users WHERE email=? AND enabled=1",
            (normalized,),
        )
        encoded = row[0]["password_hash"] if row else ""
        # Do comparable work for unknown accounts to reduce account enumeration by timing.
        if not encoded:
            encoded = _DUMMY_PASSWORD_HASH
        valid = _password_matches(password, encoded)
        if not valid or not row:
            return None
        failed = False
        return _make_session(int(row[0]["id"]))
    finally:
        _finish_login_attempt(keys, time.time(), failed)
        _login_hash_slots.release()


_DUMMY_PASSWORD_HASH = _password_hash("not-a-real-portal-password")


def session(token: str) -> dict[str, Any] | None:
    if not token or len(token) > 256:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = db.query(
        "SELECT s.csrf_token,u.id,u.email,u.display_name,u.role,u.enabled,u.created_at,u.updated_at "
        "FROM auth_sessions s JOIN portal_users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at > datetime('now') AND u.enabled=1",
        (token_hash,),
    )
    if not rows:
        return None
    row = rows[0]
    return {"csrf_token": row["csrf_token"], "user": _safe_user(row)}


def profile(user_id: int) -> dict[str, Any]:
    rows = db.query(
        "SELECT id,email,display_name,role,enabled,created_at,updated_at,preferences_json "
        "FROM portal_users WHERE id=? AND enabled=1", (user_id,))
    if not rows:
        raise ValueError("This account is unavailable.")
    row = rows[0]
    try:
        saved = json.loads(row["preferences_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        saved = {}
    notifications = saved.get("notifications") if isinstance(saved.get("notifications"), dict) else {}
    return {
        "user": _safe_user(row),
        "preferences": {
            "theme": saved.get("theme") if saved.get("theme") in {"system", "light", "dark"} else "system",
            "accent": saved.get("accent") if isinstance(saved.get("accent"), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", saved.get("accent", "")) else "",
            "notifications": {
                "browser": bool(notifications.get("browser", False)),
                "sync_failures": bool(notifications.get("sync_failures", True)),
                "sync_complete": bool(notifications.get("sync_complete", False)),
            },
        },
    }


def update_profile(user_id: int, body: dict[str, Any], actor: str = "") -> dict[str, Any]:
    current = profile(user_id)
    display_name = str(body.get("display_name", current["user"]["display_name"]) or "").strip()
    if not display_name or len(display_name) > 100:
        raise ValueError("Name must be between 1 and 100 characters.")
    old = current["preferences"]
    theme = body.get("theme", old["theme"])
    if theme not in {"system", "light", "dark"}:
        raise ValueError("Choose system, light, or dark appearance.")
    accent = body.get("accent", old["accent"])
    if not isinstance(accent, str) or (accent and not re.fullmatch(r"#[0-9a-fA-F]{6}", accent)):
        raise ValueError("Choose a valid accent colour in #RRGGBB format.")
    supplied = body.get("notifications", old["notifications"])
    if not isinstance(supplied, dict) or any(
        key in supplied and not isinstance(supplied[key], bool)
        for key in ("browser", "sync_failures", "sync_complete")
    ):
        raise ValueError("Notification preferences must be on or off.")
    notifications = {
        key: supplied.get(key, old["notifications"][key])
        for key in ("browser", "sync_failures", "sync_complete")
    }
    with db._write_lock:
        conn = db.connect()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT preferences_json FROM portal_users WHERE id=? AND enabled=1",
                               (user_id,)).fetchone()
            if not row:
                raise ValueError("This account is unavailable.")
            try:
                preferences = json.loads(row["preferences_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                preferences = {}
            if not isinstance(preferences, dict):
                preferences = {}
            preferences.update({"theme": theme, "accent": accent.upper(),
                                "notifications": notifications})
            conn.execute(
                "UPDATE portal_users SET display_name=?,preferences_json=?,updated_at=datetime('now') "
                "WHERE id=? AND enabled=1",
                (display_name, json.dumps(preferences, separators=(",", ":")), user_id),
            )
    db.audit("profile_updated", "user", user_id,
             {"theme": theme, "notification_preferences_changed": True}, actor=actor)
    return profile(user_id)


def revoke_other_sessions(user_id: int, current_token: str, actor: str = "") -> int:
    if not current_token:
        raise ValueError("Your current session could not be verified. Sign in again.")
    current_hash = hashlib.sha256(current_token.encode()).hexdigest()
    active = db.scalar(
        "SELECT COUNT(*) FROM auth_sessions WHERE user_id=? AND token_hash=? "
        "AND revoked_at IS NULL AND expires_at > datetime('now')",
        (user_id, current_hash), default=0)
    if not active:
        raise ValueError("Your current session has expired. Sign in again.")
    count = db.execute(
        "UPDATE auth_sessions SET revoked_at=datetime('now') WHERE user_id=? AND token_hash!=? "
        "AND revoked_at IS NULL AND expires_at > datetime('now')",
        (user_id, current_hash),
    )
    db.audit("other_sessions_revoked", "user", user_id, {"sessions": max(0, count)}, actor=actor)
    return max(0, count)


def revoke_session(token: str, actor: str = "") -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    rows = db.query("SELECT user_id FROM auth_sessions WHERE token_hash=? AND revoked_at IS NULL", (token_hash,))
    db.execute("UPDATE auth_sessions SET revoked_at=datetime('now') WHERE token_hash=? AND revoked_at IS NULL", (token_hash,))
    if rows:
        db.audit("logout", "session", rows[0]["user_id"], {}, actor=actor)


def save_user(body: dict[str, Any], actor: str = "") -> dict[str, Any]:
    email = _valid_email(body.get("email"))
    requested_role = body.get("role") if "role" in body else None
    if "role" in body and requested_role not in {"admin", "analyst", "viewer"}:
        raise ValueError("Choose administrator, analyst, or viewer.")
    requested_enabled = body.get("enabled") if "enabled" in body else None
    if "enabled" in body and not isinstance(requested_enabled, bool):
        raise ValueError("Account status must be enabled or disabled.")
    password = str(body.get("password") or "")
    password_hash = None
    if password:
        password_problem = _password_error(password)
        if password_problem:
            raise ValueError(password_problem)
        password_hash = _password_hash(password)
    with db._write_lock:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id,display_name,role,enabled,password_hash FROM portal_users WHERE email=?", (email,)
        ).fetchone()
        if not existing and password_hash is None:
            conn.rollback()
            raise ValueError("Set a temporary password when creating an account.")
        display_name = str(body.get("display_name") if "display_name" in body
                           else existing["display_name"] if existing else "").strip()[:100] or email
        role = requested_role if requested_role is not None else existing["role"] if existing else "viewer"
        enabled = requested_enabled if requested_enabled is not None else bool(existing["enabled"]) if existing else True
        actor_email = _valid_email(actor) if actor else None
        actor_row = conn.execute("SELECT id FROM portal_users WHERE lower(email)=?", (actor_email,)).fetchone() if actor_email else None
        if existing and not existing["password_hash"] and password_hash is None:
            conn.rollback()
            raise ValueError("Set a temporary password before activating this account.")
        if existing and actor_row and int(existing["id"]) == int(actor_row["id"]):
            if not enabled or role != existing["role"]:
                conn.rollback()
                raise ValueError("You cannot disable or change the role of your own account.")
            if password_hash is not None:
                conn.rollback()
                raise ValueError("Use Change password to update your own password.")
        if existing and existing["role"] == "admin" and bool(existing["enabled"]) and (role != "admin" or not enabled):
            other_admins = conn.execute(
                "SELECT COUNT(*) FROM portal_users WHERE role='admin' AND enabled=1 AND password_hash!='' AND id!=?",
                (existing["id"],),
            ).fetchone()[0]
            if not other_admins:
                conn.rollback()
                raise ValueError("You cannot disable or demote the last active administrator.")
        if existing:
            new_hash = password_hash if password_hash is not None else existing["password_hash"]
            conn.execute(
                "UPDATE portal_users SET display_name=?,role=?,enabled=?,password_hash=?,updated_at=datetime('now') WHERE id=?",
                (display_name, role, int(enabled), new_hash, existing["id"]),
            )
            user_id = int(existing["id"])
            action = "account_updated"
            if password_hash is not None or not enabled or role != existing["role"]:
                conn.execute("UPDATE auth_sessions SET revoked_at=datetime('now') WHERE user_id=? AND revoked_at IS NULL", (user_id,))
        else:
            cursor = conn.execute(
                "INSERT INTO portal_users(email,display_name,role,enabled,password_hash) VALUES(?,?,?,?,?)",
                (email, display_name, role, int(enabled), password_hash),
            )
            user_id = int(cursor.lastrowid)
            action = "account_created"
        conn.commit()
    db.audit(action, "user", user_id, {"email": email, "role": role, "enabled": enabled}, actor=actor)
    rows = db.query(
        "SELECT id,email,display_name,role,enabled,created_at,updated_at FROM portal_users WHERE id=?",
        (user_id,),
    )
    return _safe_user(rows[0])


def list_invitations() -> list[dict[str, Any]]:
    """List unaccepted, unrevoked invitations without exposing token hashes."""
    rows = db.query(
        "SELECT id,email,display_name,role,created_by,created_at,expires_at,"
        "CASE WHEN expires_at > datetime('now') THEN 'pending' ELSE 'expired' END AS status "
        "FROM access_invitations WHERE accepted_at IS NULL AND revoked_at IS NULL "
        "ORDER BY id DESC LIMIT 100")
    return [dict(row) for row in rows]


def create_invitation(email: str, display_name: str = "", actor: str = "") -> dict[str, Any]:
    """Create a one-time, viewer-only invitation. Return the raw token once."""
    email = _valid_email(email)
    display_name = str(display_name or "").strip()[:100]
    actor = str(actor or "system").strip()[:254] or "system"
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    with db._write_lock:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        existing_user = conn.execute(
            "SELECT id FROM portal_users WHERE email=?", (email,)).fetchone()
        if existing_user:
            conn.rollback()
            raise ValueError("An account already exists for this email address.")
        pending = conn.execute(
            "SELECT id FROM access_invitations WHERE email=? AND accepted_at IS NULL "
            "AND revoked_at IS NULL AND expires_at > datetime('now') LIMIT 1", (email,)
        ).fetchone()
        if pending:
            conn.rollback()
            raise ValueError("A valid invitation already exists for this email address.")
        cursor = conn.execute(
            "INSERT INTO access_invitations(email,display_name,role,token_hash,created_by,expires_at) "
            "VALUES(?,?,'viewer',?,?,datetime('now', '+72 hours'))",
            (email, display_name, token_hash, actor),
        )
        invitation_id = int(cursor.lastrowid)
        invitation = conn.execute(
            "SELECT id,email,display_name,role,created_at,expires_at FROM access_invitations WHERE id=?",
            (invitation_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO audit_log(actor,action,object_type,object_id,detail) VALUES(?,?,?,?,?)",
            (actor, "invitation_created", "invitation", str(invitation_id),
             json.dumps({"email": email, "role": "viewer", "expires_at": invitation["expires_at"]})),
        )
        conn.commit()
    return {**dict(invitation), "token": token, "role": "viewer"}


def revoke_invitation(invitation_id: int, actor: str = "") -> dict[str, Any]:
    """Revoke an unaccepted invitation; revocation is one-way."""
    try:
        invitation_id = int(invitation_id)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Invitation ID is invalid.") from None
    if invitation_id < 1:
        raise ValueError("Invitation ID is invalid.")
    actor = str(actor or "system").strip()[:254] or "system"
    with db._write_lock:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id,email,accepted_at,revoked_at FROM access_invitations WHERE id=?",
            (invitation_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            raise KeyError("Invitation not found.")
        if row["accepted_at"]:
            conn.rollback()
            raise ValueError("This invitation has already been accepted.")
        if row["revoked_at"]:
            conn.rollback()
            raise ValueError("This invitation has already been revoked.")
        conn.execute("UPDATE access_invitations SET revoked_at=datetime('now') WHERE id=?",
                     (invitation_id,))
        conn.execute(
            "INSERT INTO audit_log(actor,action,object_type,object_id,detail) VALUES(?,?,?,?,?)",
            (actor, "invitation_revoked", "invitation", str(invitation_id),
             json.dumps({"email": row["email"]})),
        )
        conn.commit()
    return {"id": invitation_id, "revoked": True}


def accept_invitation(token: str, email: str, display_name: str, password: str) -> dict[str, Any]:
    """Atomically consume an invitation, create its viewer account and session."""
    token = str(token or "")
    if len(token) != 43 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise ValueError("This invite link is invalid, expired, already used or revoked.")
    email = _valid_email(email)
    password = str(password or "")
    password_problem = _password_error(password)
    if password_problem:
        raise ValueError(password_problem)
    display_name = str(display_name or "").strip()[:100]
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()

    # Validate the bearer token before the expensive password hash so random
    # requests cannot force repeated PBKDF2 work.
    candidate = db.query(
        "SELECT email FROM access_invitations WHERE token_hash=? AND accepted_at IS NULL "
        "AND revoked_at IS NULL AND expires_at > datetime('now')", (token_hash,))
    if not candidate:
        raise ValueError("This invite link is invalid, expired, already used or revoked.")
    if not hmac.compare_digest(str(candidate[0]["email"]), email):
        raise ValueError("This invitation was issued to a different email address.")
    password_hash = _password_hash(password)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session_hash = hashlib.sha256(session_token.encode("ascii")).hexdigest()
    final_name = display_name or email
    actor = email

    with db._write_lock:
        conn = db.connect()
        conn.execute("BEGIN IMMEDIATE")
        invitation = conn.execute(
            "SELECT id,email,display_name,role FROM access_invitations WHERE token_hash=? "
            "AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > datetime('now')",
            (token_hash,),
        ).fetchone()
        if not invitation:
            conn.rollback()
            raise ValueError("This invite link is invalid, expired, already used or revoked.")
        if not hmac.compare_digest(str(invitation["email"]), email):
            conn.rollback()
            raise ValueError("This invitation was issued to a different email address.")
        if conn.execute("SELECT id FROM portal_users WHERE email=?", (email,)).fetchone():
            conn.rollback()
            raise ValueError("An account already exists for this email address.")
        final_name = display_name or str(invitation["display_name"] or email)
        cursor = conn.execute(
            "INSERT INTO portal_users(email,display_name,role,enabled,password_hash) "
            "VALUES(?,?, 'viewer',1,?)", (email, final_name, password_hash),
        )
        user_id = int(cursor.lastrowid)
        consumed = conn.execute(
            "UPDATE access_invitations SET accepted_at=datetime('now'),accepted_user_id=? "
            "WHERE id=? AND accepted_at IS NULL AND revoked_at IS NULL "
            "AND expires_at > datetime('now')", (user_id, invitation["id"]),
        )
        if consumed.rowcount != 1:
            conn.rollback()
            raise ValueError("This invite link is invalid, expired, already used or revoked.")
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,csrf_token,user_id,expires_at) "
            "VALUES(?,?,?,datetime('now', ?))",
            (session_hash, csrf_token, user_id, f"+{SESSION_HOURS} hours"),
        )
        user_row = conn.execute(
            "SELECT id,email,display_name,role,enabled,created_at,updated_at FROM portal_users WHERE id=?",
            (user_id,),
        ).fetchone()
        for action, object_type, object_id, detail in (
            ("invitation_accepted", "invitation", str(invitation["id"]),
             {"email": email, "user_id": user_id}),
            ("account_created", "user", str(user_id), {"email": email, "role": "viewer"}),
            ("login", "session", str(user_id), {"expires_in_hours": SESSION_HOURS}),
        ):
            conn.execute(
                "INSERT INTO audit_log(actor,action,object_type,object_id,detail) VALUES(?,?,?,?,?)",
                (actor, action, object_type, object_id, json.dumps(detail)),
            )
        conn.commit()
    return {"token": session_token, "csrf_token": csrf_token, "user": _safe_user(user_row)}


def change_password(user_id: int, old_password: str, new_password: str, actor: str) -> dict[str, Any]:
    rows = db.query("SELECT password_hash FROM portal_users WHERE id=? AND enabled=1", (user_id,))
    if not rows or not _password_matches(str(old_password or ""), rows[0]["password_hash"]):
        raise ValueError("Current password is incorrect.")
    password_problem = _password_error(new_password)
    if password_problem:
        raise ValueError(password_problem)
    password_hash = _password_hash(new_password)
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with db._write_lock:
        conn = db.connect()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM portal_users WHERE id=? AND enabled=1", (user_id,)).fetchone()
            # A concurrent password change or account update invalidates the
            # original verification. Never overwrite that newer password.
            if not row or row["password_hash"] != rows[0]["password_hash"]:
                raise ValueError("Current password is incorrect. Sign in again before changing it.")
            conn.execute("UPDATE portal_users SET password_hash=?,updated_at=datetime('now') WHERE id=?", (password_hash, user_id))
            conn.execute("UPDATE auth_sessions SET revoked_at=datetime('now') WHERE user_id=? AND revoked_at IS NULL", (user_id,))
            conn.execute(
                "INSERT INTO auth_sessions(token_hash,csrf_token,user_id,expires_at) "
                "VALUES(?,?,?,datetime('now', ?))",
                (hashlib.sha256(token.encode()).hexdigest(), csrf, user_id, f"+{SESSION_HOURS} hours"),
            )
            for action, object_type, detail in (
                ("password_changed", "user", {}),
                ("login", "session", {"expires_in_hours": SESSION_HOURS}),
            ):
                conn.execute(
                    "INSERT INTO audit_log(actor,action,object_type,object_id,detail) VALUES(?,?,?,?,?)",
                    (actor, action, object_type, str(user_id), json.dumps(detail)),
                )
            row = conn.execute("SELECT * FROM portal_users WHERE id=?", (user_id,)).fetchone()
    return {"token": token, "csrf_token": csrf, "user": _safe_user(row)}
