"""Encrypted TOTP MFA state for portal accounts."""
from __future__ import annotations


def up(connection, dialect: str, sqlite_schema: str) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    connection.execute("ALTER TABLE portal_users ADD COLUMN IF NOT EXISTS mfa_secret_encrypted TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE portal_users ADD COLUMN IF NOT EXISTS mfa_recovery_hashes TEXT NOT NULL DEFAULT '[]'")
    connection.execute("ALTER TABLE portal_users ADD COLUMN IF NOT EXISTS mfa_enabled INTEGER NOT NULL DEFAULT 0")


def down(connection, dialect: str, sqlite_schema: str, *, force: bool = False) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    connection.execute("ALTER TABLE portal_users DROP COLUMN IF EXISTS mfa_enabled")
    connection.execute("ALTER TABLE portal_users DROP COLUMN IF EXISTS mfa_recovery_hashes")
    connection.execute("ALTER TABLE portal_users DROP COLUMN IF EXISTS mfa_secret_encrypted")
