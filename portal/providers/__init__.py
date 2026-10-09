"""Provider registry — the one place the app discovers what it can connect to.

To add a platform: write a module in this folder and add it to REGISTRY. The
setup form, OAuth flow, scheduling and storage all follow from the spec, with
no other file to touch.
"""
from __future__ import annotations

from .base import Connection, Field, OAuth2, Provider
from .ahrefs import AhrefsProvider
from .generic import CsvProvider, RestProvider
from .google import GA4Provider, GoogleAdsProvider, GoogleSheetsProvider, SearchConsoleProvider
from .gmail import GmailProvider
from .linkedin import LinkedInAdsProvider
from .meta import MetaAdsProvider
from .superleap import SuperleapCRMProvider
from .tiktok import TikTokAdsProvider

REGISTRY: dict[str, Provider] = {
    p.id: p
    for p in (
        AhrefsProvider(),
        GoogleAdsProvider(),
        MetaAdsProvider(),
        GA4Provider(),
        SearchConsoleProvider(),
        GoogleSheetsProvider(),
        GmailProvider(),
        SuperleapCRMProvider(),
        LinkedInAdsProvider(),
        TikTokAdsProvider(),
        RestProvider(),
        CsvProvider(),
    )
}


def get(provider_id: str) -> Provider | None:
    return REGISTRY.get(provider_id)


def table_for(provider: Provider, conn: Connection) -> str:
    """Which fact table a connection writes to.

    Purpose-built providers know their own table; the generic ones are told,
    because the same REST endpoint could be spend data for one company and
    lead data for another.
    """
    return str(conn.get("target_table") or provider.table)


def catalogue() -> list[dict]:
    return [p.spec() for p in REGISTRY.values()]


__all__ = ["REGISTRY", "Connection", "Field", "OAuth2", "Provider",
           "get", "table_for", "catalogue"]
