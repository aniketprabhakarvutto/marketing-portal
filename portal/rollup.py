"""Deriving the CRM reporting tables from lead-level rows.

A busy CRM can hold hundreds of thousands of leads a quarter, so the dashboard
reads pre-aggregated daily rows rather than scanning every lead on each page
load. This keeps the two in step after a sync or an import.
"""
from __future__ import annotations

from datetime import date

from .db import execute, query, scalar


def rollup_daily(start: date, end: date) -> int:
    """Rebuild CRM reporting rows without deleting other connection feeds."""
    s, e = start.isoformat(), end.isoformat()
    connection_ids = [int(row["connection_id"]) for row in query(
        "SELECT DISTINCT connection_id FROM crm_leads WHERE created_date BETWEEN ? AND ?", (s, e))]
    if not connection_ids:
        return 0
    marks = ",".join("?" for _ in connection_ids)
    scoped = (s, e, *connection_ids)
    execute(f"DELETE FROM crm_daily WHERE date BETWEEN ? AND ? AND connection_id IN ({marks})", scoped)
    execute(
        """
        INSERT INTO crm_daily
            (date, channel, source, lead_type, leads, contacted, qualified,
             booked, completed, won, revenue, connection_id)
        SELECT created_date, channel, utm_source, lead_type,
               COUNT(*),
               COALESCE(SUM(is_contacted), 0),
               COALESCE(SUM(is_qualified), 0),
               COALESCE(SUM(is_visit_booked), 0),
               COALESCE(SUM(is_visit_done), 0),
               COALESCE(SUM(is_sale), 0),
               COALESCE(SUM(revenue), 0),
               connection_id
        FROM crm_leads
        WHERE created_date BETWEEN ? AND ? AND connection_id IN (""" + marks + """)
        GROUP BY created_date, channel, utm_source, lead_type, connection_id
        """,
        scoped,
    )

    execute(f"DELETE FROM crm_city_daily WHERE date BETWEEN ? AND ? AND connection_id IN ({marks})", scoped)
    execute(
        """
        INSERT INTO crm_city_daily (date, city, lead_type, leads, won, connection_id)
        SELECT created_date, city, lead_type, COUNT(*),
               COALESCE(SUM(is_sale), 0), connection_id
        FROM crm_leads
        WHERE created_date BETWEEN ? AND ? AND connection_id IN (""" + marks + """)
        GROUP BY created_date, city, lead_type, connection_id
        """,
        scoped,
    )
    return int(scalar("SELECT COUNT(*) FROM crm_daily WHERE date BETWEEN ? AND ?", (s, e)))


def rollup_campaign_daily(start: date, end: date, connection_id: int) -> int:
    """Derive campaign-lead facts from a direct Superleap opportunity sync."""
    s, e = start.isoformat(), end.isoformat()
    execute("DELETE FROM crm_campaign_daily WHERE connection_id = ? AND date BETWEEN ? AND ?",
            (connection_id, s, e))
    execute(
        """
        INSERT INTO crm_campaign_daily
            (date, channel, campaign_id, campaign_name, lead_type, leads, won, connection_id)
        SELECT created_date, channel, '', TRIM(utm_campaign), lead_type, COUNT(*),
               COALESCE(SUM(is_sale), 0), connection_id
        FROM crm_leads
        WHERE connection_id = ? AND created_date BETWEEN ? AND ? AND TRIM(utm_campaign) != ''
        GROUP BY created_date, channel, TRIM(utm_campaign), lead_type, connection_id
        """,
        (connection_id, s, e),
    )
    return int(scalar("SELECT COUNT(*) FROM crm_campaign_daily WHERE connection_id = ? AND date BETWEEN ? AND ?",
                      (connection_id, s, e)))
