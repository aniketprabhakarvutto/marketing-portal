"""Saved accounts: create, read, update, delete.

SQLite stores non-secret account settings and Keychain references. The current
user's macOS Keychain stores API keys and OAuth tokens. The browser sees only
which secret fields are populated.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from typing import Any

from .config import APP
from .db import PROVENANCE_TABLES, _write_lock, connect, query
from .providers.base import Connection
from . import secret_store

GOOGLE_PROVIDERS = ("google_ads", "ga4", "gsc", "google_sheets")
_DUPLICATE_NAME_MESSAGE = (
    "A connection with this name already exists. Edit the existing connection or choose another name."
)


def _is_unique_violation(exc: Exception) -> bool:
    """Recognize duplicate-key errors without tying callers to one driver."""
    return isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23505"


def _reuse_google_oauth(provider: str, config: dict, secrets: dict) -> tuple[dict, dict]:
    """Reuse one saved Google OAuth app across Ads, GA4 and Search Console.

    Google expects the same web client and callback URL for all three APIs. Once
    a person has entered that client in any Google connection, asking for it
    again is both tedious and an easy way to introduce a mismatched redirect.
    Tokens remain separate because each provider requests a different scope.
    """
    if provider not in GOOGLE_PROVIDERS or (config.get("client_id") and secrets.get("client_secret")):
        return config, secrets
    marks = ",".join("?" for _ in GOOGLE_PROVIDERS)
    rows = query(
        f"SELECT config, secrets FROM connections WHERE provider IN ({marks}) ORDER BY id LIMIT 1",
        GOOGLE_PROVIDERS,
    )
    if not rows:
        return config, secrets
    saved_config, saved_secrets = secret_store.promote_config(
        json.loads(rows[0]["config"] or "{}"), secret_store.resolve(rows[0]["secrets"])
    )
    config = {**config}
    secrets = {**secrets}
    if not config.get("client_id") and saved_config.get("client_id"):
        config["client_id"] = saved_config["client_id"]
    if not secrets.get("client_secret") and saved_secrets.get("client_secret"):
        secrets["client_secret"] = saved_secrets["client_secret"]
    return config, secrets


def _secure_db_file() -> None:
    """Keep account metadata and any not-yet-migrated legacy rows private."""
    try:
        os.chmod(APP.db_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # best effort; some filesystems don't support it


def all_connections(provider: str | None = None, enabled_only: bool = False) -> list[Connection]:
    sql = "SELECT * FROM connections"
    where, params = [], []
    if provider:
        where.append("provider = ?")
        params.append(provider)
    if enabled_only:
        where.append("enabled = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY provider, name"
    return [Connection.from_row(r) for r in query(sql, params)]


def get(connection_id: int) -> Connection | None:
    rows = query("SELECT * FROM connections WHERE id = ?", (connection_id,))
    return Connection.from_row(rows[0]) if rows else None


def create(provider: str, name: str, config: dict, secrets: dict | None = None) -> Connection:
    config, secrets = secret_store.promote_config(config or {}, secrets or {})
    config, secrets = _reuse_google_oauth(provider, config, secrets)
    name = name.strip()
    if not name:
        raise ValueError("Connection name is required")
    if query("SELECT id FROM connections WHERE provider=? AND lower(name)=lower(?)", (provider, name)):
        raise ValueError(_DUPLICATE_NAME_MESSAGE)
    stored_secrets, new_ref = secret_store.stage(secrets)
    try:
        with _write_lock:
            conn = connect()
            try:
                cur = conn.execute(
                    "INSERT INTO connections (provider, name, config, secrets) VALUES (?, ?, ?, ?)",
                    (provider, name, json.dumps(config or {}), stored_secrets),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except Exception as exc:
        if new_ref:
            secret_store.delete(new_ref)
        if _is_unique_violation(exc) and query(
            "SELECT id FROM connections WHERE provider=? AND lower(name)=lower(?)", (provider, name)
        ):
            raise ValueError(_DUPLICATE_NAME_MESSAGE) from None
        raise
    _secure_db_file()
    return Connection(cur.lastrowid, provider, name, config, secrets)


def update(connection_id: int, *, name: str | None = None, config: dict | None = None,
           secrets: dict | None = None, enabled: bool | None = None) -> Connection | None:
    existing = get(connection_id)
    if not existing:
        return None
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Connection name is required")
        if query("SELECT id FROM connections WHERE provider=? AND lower(name)=lower(?) AND id<>?",
                 (existing.provider, name, connection_id)):
            raise ValueError(_DUPLICATE_NAME_MESSAGE)
    # Merge rather than replace, so a form that omits an unchanged secret does
    # not silently wipe it.
    incoming_config, incoming_secrets = secret_store.promote_config(config or {}, secrets or {})
    new_config = {**existing.config, **incoming_config}
    new_secrets = {**existing.secrets, **{k: v for k, v in incoming_secrets.items() if v != ""}}
    old_raw = query("SELECT secrets FROM connections WHERE id=?", (connection_id,))[0]["secrets"]
    old_ref = secret_store.reference(old_raw)
    # A settings-only edit need not replace an already secured Keychain item.
    needs_store = new_secrets != existing.secrets or (old_ref is None and bool(new_secrets))
    stored_secrets, new_ref = (secret_store.stage(new_secrets) if needs_store else (old_raw, None))
    try:
        with _write_lock:
            conn = connect()
            try:
                conn.execute(
                    "UPDATE connections SET name = ?, config = ?, secrets = ?, enabled = ?, "
                    "updated_at = datetime('now') WHERE id = ?",
                    (
                        name if name is not None else existing.name,
                        json.dumps(new_config), stored_secrets,
                        int(existing.enabled if enabled is None else enabled),
                        connection_id,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except Exception as exc:
        if new_ref:
            secret_store.delete(new_ref)
        final_name = name if name is not None else existing.name
        if _is_unique_violation(exc) and query(
            "SELECT id FROM connections WHERE provider=? AND lower(name)=lower(?) AND id<>?",
            (existing.provider, final_name, connection_id),
        ):
            raise ValueError(_DUPLICATE_NAME_MESSAGE) from None
        raise
    # The cached token is keyed by connection ID, so a saved credential change
    # must take effect before another request can reuse the old token.
    from . import oauth
    oauth.forget(connection_id)
    if old_ref and new_ref:
        try:
            secret_store.delete(old_ref)
        except secret_store.SecretStoreError as exc:
            raise secret_store.SecretStoreError(
                "Connection saved, but obsolete Keychain item could not be removed"
            ) from exc
    _secure_db_file()
    return Connection(connection_id, existing.provider,
                      name if name is not None else existing.name,
                      new_config, new_secrets,
                      existing.enabled if enabled is None else enabled)


def delete(connection_id: int, drop_data: bool = False) -> bool:
    rows = query("SELECT secrets FROM connections WHERE id=?", (connection_id,))
    if not rows:
        return False
    old_ref = secret_store.reference(rows[0]["secrets"])
    old_values = secret_store.get(old_ref) if old_ref else None
    keychain_deleted = False
    with _write_lock:
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Keychain deletion failure leaves SQLite untouched. If the SQL
            # transaction fails afterwards, restore the same reference.
            if old_ref:
                secret_store.delete(old_ref)
                keychain_deleted = True
            if drop_data:
                for table in PROVENANCE_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE connection_id = ?", (connection_id,))
            conn.execute("DELETE FROM connections WHERE id = ?", (connection_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            if keychain_deleted and old_ref and old_values is not None:
                secret_store.put(old_ref, old_values)
            raise
    from . import oauth
    oauth.forget(connection_id)
    return True


def migrate_legacy_secrets() -> int:
    """Move plaintext legacy rows to Keychain after an operator makes a backup.

    Run with other portal processes stopped. This function also checkpoints
    WAL and vacuums SQLite so deleted plaintext is not left in database pages.
    Existing database backups must be handled separately by the operator.
    """
    rows = query("SELECT id, config, secrets FROM connections ORDER BY id")
    staged: list[tuple[int, str, str, str, str, str | None, str | None]] = []
    try:
        for row in rows:
            old_raw = row["secrets"] or "{}"
            old_config = row["config"] or "{}"
            old_ref = secret_store.reference(old_raw)
            original_config = json.loads(old_config)
            config, values = secret_store.promote_config(
                original_config, secret_store.resolve(old_raw)
            )
            if old_ref is not None and config == original_config:
                continue
            if not values:
                continue
            new_raw, new_ref = secret_store.stage(values)
            staged.append((row["id"], old_config, old_raw, json.dumps(config), new_raw, new_ref, old_ref))
    except Exception:
        for *_, ref, _ in staged:
            if ref:
                secret_store.delete(ref)
        raise
    if not staged:
        return 0
    with _write_lock:
        conn = connect()
        try:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("BEGIN IMMEDIATE")
            for connection_id, old_config, old_raw, new_config, new_raw, _, _ in staged:
                cur = conn.execute(
                    "UPDATE connections SET config=?, secrets=?, updated_at=datetime('now') "
                    "WHERE id=? AND config=? AND secrets=?",
                    (new_config, new_raw, connection_id, old_config, old_raw),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("Connection changed during credential migration")
            conn.commit()
        except Exception:
            conn.rollback()
            for *_, ref, _ in staged:
                if ref:
                    secret_store.delete(ref)
            raise
        # These operations are intentionally after commit and require an
        # offline migration. A failure is surfaced rather than claimed safe.
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or checkpoint[0] != 0:
            raise RuntimeError("Credential migration committed but WAL checkpoint was blocked")
        conn.execute("VACUUM")
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or checkpoint[0] != 0:
            raise RuntimeError("Credential migration committed but WAL checkpoint was blocked")
    for *_, old_ref in staged:
        if old_ref:
            secret_store.delete(old_ref)
    return len(staged)


def public(conn: Connection, provider) -> dict[str, Any]:
    """The shape the browser sees — config in the clear, secrets only as flags."""
    return {
        "id": conn.id,
        "provider": conn.provider,
        "provider_label": getattr(provider, "label", conn.provider),
        "category": getattr(provider, "category", "other"),
        "name": conn.name,
        "enabled": conn.enabled,
        "config": conn.config,
        "secrets_set": sorted(k for k, v in conn.secrets.items() if v),
        "ready": provider.ready(conn) if provider else False,
        "missing": provider.missing(conn) if provider else ["Unknown provider"],
        "needs_oauth": bool(getattr(provider, "oauth", None)),
        "syncable": bool(getattr(provider, "syncable", True)),
    }
