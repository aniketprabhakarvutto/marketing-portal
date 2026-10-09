"""Configuration.

Settings come from the project .env file or the process environment. Connector
credentials live in the current macOS user's Keychain and are sent only to
their configured providers. The portal runs on the Python standard library.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_db_path() -> Path:
    """Where the warehouse lives unless PORTAL_DB_PATH says otherwise.

    On macOS this is Application Support rather than the project folder,
    because Documents and Desktop are frequently synced to iCloud and a
    cloud-synced folder cannot host a SQLite file — it fails with a disk I/O
    error the moment the database is opened. Application Support is always
    local, so the portal works wherever you unpack it.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "marketing-portal" / "portal.db"
    return ROOT / "data" / "portal.db"


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader — KEY=value, # comments, optional quotes.

    Values already present in the real environment win, so you can override
    any setting for a single run without editing the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env()


def _b(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    return default if val is None else val.strip().lower() in {"1", "true", "yes", "on"}


def _l(key: str) -> list[str]:
    return [x.strip() for x in os.getenv(key, "").split(",") if x.strip()]


def _i(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


@dataclass
class AppCfg:
    host: str = os.getenv("PORTAL_HOST", "127.0.0.1").strip()
    port: int = _i("PORTAL_PORT", 8787)
    oauth_redirect_uri: str = os.getenv("PORTAL_OAUTH_REDIRECT_URI", "").strip()
    secure_cookie: bool = _b("PORTAL_COOKIE_SECURE", True)
    mcp_token: str = os.getenv("PORTAL_MCP_TOKEN", "").strip()
    # Local macOS installs use Keychain. Linux server deployments use an
    # application-held Fernet key to encrypt connector credentials at rest.
    secret_store_backend: str = os.getenv("PORTAL_SECRET_STORE", "keychain").strip().lower()
    secret_encryption_key: str = os.getenv("PORTAL_SECRET_ENCRYPTION_KEY", "").strip()
    # Forwarded client IPs are ignored unless an operator explicitly trusts a
    # proxy that sanitizes them. Empty by default to avoid trusting local apps.
    trusted_proxy_ips: tuple[str, ...] = tuple(_l("PORTAL_TRUSTED_PROXY_IPS"))
    connector_hosts: tuple[str, ...] = tuple(_l("PORTAL_CONNECTOR_HOSTS"))
    db_path: str = str(
        Path(os.getenv("PORTAL_DB_PATH", "").strip()).expanduser()
        if os.getenv("PORTAL_DB_PATH", "").strip()
        else default_db_path()
    )
    timezone: str = os.getenv("PORTAL_TIMEZONE", "Asia/Kolkata").strip()
    currency: str = os.getenv("PORTAL_CURRENCY", "INR").strip()
    demo_mode: bool = _b("DEMO_MODE", True)
    sync_lookback_days: int = _i("SYNC_LOOKBACK_DAYS", 30)
    sync_hour: int = _i("SYNC_HOUR_LOCAL", 6)
    scheduler_enabled: bool = _b("SCHEDULER_ENABLED", True)
    topup_hours: int = _i("SYNC_TOPUP_HOURS", 4)
    # The CRM funnel differs by business, so its order and wording are settings.
    # Stages that are empty for a given lead type are hidden automatically, so
    # one order can serve several funnels.
    funnel_order: str = os.getenv(
        "FUNNEL_ORDER", "leads,contacted,qualified,booked,completed,won").strip()
    funnel_labels: str = os.getenv(
        "FUNNEL_LABELS", "Leads,Contacted,Qualified,Booked,Completed,Won").strip()


APP = AppCfg()

Path(APP.db_path).parent.mkdir(parents=True, exist_ok=True)
