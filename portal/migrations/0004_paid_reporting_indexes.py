"""Indexes matching the Paid-report aggregation and attribution reads."""
from __future__ import annotations


def up(connection, dialect: str, sqlite_schema: str) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_paid_campaign_daily ON paid_metrics (date, source, account_id, campaign_id, adset_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_crm_campaign_reporting ON crm_campaign_daily (date, connection_id, channel, campaign_id, campaign_name, lead_type)")


def down(connection, dialect: str, sqlite_schema: str, *, force: bool = False) -> None:
    if dialect != "postgresql":
        raise ValueError(f"Unsupported migration dialect: {dialect}")
    connection.execute("DROP INDEX IF EXISTS ix_paid_campaign_daily")
    connection.execute("DROP INDEX IF EXISTS ix_crm_campaign_reporting")
