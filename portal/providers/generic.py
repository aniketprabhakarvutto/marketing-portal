"""Providers for platforms the portal has no purpose-built connector for.

Between them these two cover essentially anything: if a platform has a JSON API
you can point the REST provider at it, and if it only exports files the CSV
provider will read them. Both are configured entirely from the browser — a
field mapping rather than new code.
"""
from __future__ import annotations

import csv
import glob
import io
import json
from datetime import date
from pathlib import Path
from typing import Any

from ..config import APP, ROOT
from ..httpclient import get, get_text
from .base import Connection, Field, Provider
from .shape import TABLE_COLUMNS, build_row, dig, normalise_channel, stage_matches

TABLE_CHOICES = (
    "paid_metrics, web_metrics, search_metrics, crm_leads, crm_daily, "
    "crm_city_daily, crm_campaign_daily"
)

STAGE_FLAG_FIELDS = {
    "is_contacted": "stages_contacted",
    "is_qualified": "stages_qualified",
    "is_visit_booked": "stages_booked",
    "is_visit_done": "stages_completed",
    "is_sale": "stages_won",
}

IMPORT_ROOT = (ROOT / "extracts").resolve()
IMPORT_ROOTS = tuple(dict.fromkeys((IMPORT_ROOT, Path(APP.db_path).expanduser().resolve().parent)))


def _local_csv_paths(source: str) -> list[Path]:
    """Resolve configured CSV files while confining reads to extracts/."""
    raw = Path(source).expanduser()
    pattern = raw if raw.is_absolute() else IMPORT_ROOT / raw
    matches = glob.glob(str(pattern)) if any(c in str(pattern) for c in "*?[") else [str(pattern)]
    paths: list[Path] = []
    for match in sorted(matches):
        resolved = Path(match).resolve()
        if not any(resolved.is_relative_to(root) for root in IMPORT_ROOTS):
            allowed = " or ".join(str(root) for root in IMPORT_ROOTS)
            raise RuntimeError(f"Local CSV files must be inside {allowed}.")
        if resolved.is_file():
            paths.append(resolved)
    if not paths:
        raise RuntimeError(f"No CSV files matched inside {IMPORT_ROOT}.")
    return paths


def _mapping(conn: Connection) -> dict[str, str]:
    raw = conn.get("field_map") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Field mapping is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise RuntimeError("Field mapping must be a JSON object of column → source field.")
    return parsed


def _table(conn: Connection) -> str:
    table = str(conn.get("target_table") or "paid_metrics")
    if table not in TABLE_COLUMNS:
        raise RuntimeError(f"Unknown target table '{table}'. Choose one of: {TABLE_CHOICES}.")
    return table


def _finish(conn: Connection, table: str, rows: list[dict], records: list[Any],
            mapping: dict[str, str]) -> list[dict]:
    """Apply the derived columns the warehouse expects but sources rarely have."""
    stage_path = mapping.get("stage")
    for row, record in zip(rows, records):
        if table in ("paid_metrics",) and not row.get("source"):
            row["source"] = str(conn.get("source_name") or conn.name)
        if table in ("crm_leads", "crm_daily") and not row.get("channel"):
            row["channel"] = normalise_channel(
                row.get("utm_source") or row.get("source") or "",
                row.get("utm_medium") or "",
            )
        if table == "crm_leads" and stage_path:
            stage = row.get("stage") or ""
            for flag, setting in STAGE_FLAG_FIELDS.items():
                spec = conn.get(setting)
                if spec:
                    row[flag] = stage_matches(stage, spec)
    key = {"crm_leads": "created_date"}.get(table, "date")
    return [r for r in rows if r.get(key)]


class RestProvider(Provider):
    id = "rest"
    label = "Any REST API"
    category = "other"
    table = "paid_metrics"  # overridden per connection
    summary = "Point at any JSON endpoint and map its fields onto a table."
    docs_url = ""
    fields = (
        Field("url", "Endpoint URL", kind="url",
              placeholder="https://api.example.com/v1/reports"),
        Field("target_table", "Load into table", placeholder="paid_metrics",
              help=f"One of: {TABLE_CHOICES}."),
        Field("field_map", "Field mapping", kind="text",
              placeholder='{"date": "day", "spend": "cost", "clicks": "clicks"}',
              help="JSON object mapping this portal's columns to fields in the response. "
                   "Dotted paths work: \"metrics.0.spend\"."),
        Field("records_path", "Path to the records", required=False, placeholder="data",
              help="Where the array of rows sits in the response. Blank if the body is the array."),
        Field("auth_style", "Authentication", required=False, placeholder="bearer",
              help="bearer, header or query. Blank for a public endpoint."),
        Field("auth_name", "Header or parameter name", required=False,
              placeholder="X-Api-Key or api_key"),
        Field("token", "API key or token", secret=True, required=False),
        Field("date_from_param", "Start-date parameter", required=False, placeholder="start_date"),
        Field("date_to_param", "End-date parameter", required=False, placeholder="end_date"),
        Field("extra_params", "Extra query parameters", required=False,
              placeholder='{"granularity": "daily"}', help="JSON object, sent on every request."),
        Field("page_param", "Page parameter", required=False, placeholder="page",
              help="Set to page through results; the portal stops on an empty page."),
        Field("page_size_param", "Page-size parameter", required=False, placeholder="per_page"),
        Field("page_size", "Page size", required=False, placeholder="500", kind="number"),
        Field("source_name", "Source label", required=False,
              help="What to call this platform in reports. Defaults to the connection name."),
    )

    def _request_bits(self, conn: Connection) -> tuple[dict, dict]:
        headers: dict[str, str] = {}
        params: dict[str, Any] = {}
        style = str(conn.get("auth_style") or "").strip().lower()
        token = str(conn.get("token") or "")
        name = str(conn.get("auth_name") or "").strip()
        if token and style == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif token and style == "header":
            headers[name or "Authorization"] = token
        elif token and style == "query":
            params[name or "api_key"] = token
        extra = conn.get("extra_params")
        if extra:
            try:
                params.update(json.loads(extra) if isinstance(extra, str) else extra)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Extra parameters are not valid JSON: {exc}") from None
        return headers, params

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        table, mapping = _table(conn), _mapping(conn)
        headers, params = self._request_bits(conn)
        if conn.get("date_from_param"):
            params[str(conn.get("date_from_param"))] = start.isoformat()
        if conn.get("date_to_param"):
            params[str(conn.get("date_to_param"))] = end.isoformat()

        page_param = str(conn.get("page_param") or "").strip()
        size_param = str(conn.get("page_size_param") or "").strip()
        size = int(conn.get("page_size") or 500)
        if size_param:
            params[size_param] = size

        records: list[Any] = []
        for page in range(1, 501 if page_param else 2):
            if page_param:
                params[page_param] = page
            body = get(str(conn.get("url")), headers=headers, params=params, public_only=True,
                       allowed_hosts=APP.connector_hosts)
            path = str(conn.get("records_path") or "").strip()
            batch = dig(body, path) if path else body
            if isinstance(batch, dict):
                batch = [batch]
            if not batch:
                break
            records.extend(batch)
            if not page_param or len(batch) < size:
                break

        rows = [build_row(r, table, mapping) for r in records]
        return _finish(conn, table, rows, records, mapping)


class CsvProvider(Provider):
    id = "csv"
    label = "CSV file, folder or URL"
    category = "other"
    table = "paid_metrics"
    summary = "Read exports from disk or a URL — for platforms with no usable API."
    fields = (
        Field("path", "File, folder or URL",
              placeholder="daily-export.csv",
              help="A file or glob inside the portal's extracts folder, or an https:// address "
                   "serving CSV — a published Google Sheet works, which is how you "
                   "get data out of a platform whose API you cannot reach."),
        Field("target_table", "Load into table", placeholder="crm_leads",
              help=f"One of: {TABLE_CHOICES}."),
        Field("field_map", "Field mapping", kind="text",
              placeholder='{"created_date": "Created At", "stage": "Stage"}',
              help="JSON object mapping this portal's columns to CSV headers."),
        Field("stages_contacted", "Stages counting as contacted", required=False,
              placeholder="contacted,qualified,won"),
        Field("stages_qualified", "Stages counting as qualified", required=False),
        Field("stages_booked", "Stages counting as booked", required=False),
        Field("stages_completed", "Stages counting as completed", required=False),
        Field("stages_won", "Stages counting as won", required=False,
              placeholder="won,closed won,sold"),
        Field("source_name", "Source label", required=False,
              help="What to call this platform in reports — set it to google_ads or "
                   "meta_ads so the rows blend with your CRM data."),
        Field("token", "Bearer token", secret=True, required=False,
              help="Only if the URL needs one. A published Sheet does not."),
    )

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        table, mapping = _table(conn), _mapping(conn)
        source = str(conn.get("path") or "").strip()
        records: list[dict] = []

        if source.lower().startswith(("http://", "https://")):
            headers = {}
            token = str(conn.get("token") or "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            text = get_text(source, headers=headers, public_only=True,
                            allowed_hosts=APP.connector_hosts)
            if text.lstrip().startswith("<"):
                raise RuntimeError(
                    "That URL returned a web page rather than CSV. For a Google Sheet, "
                    "use File → Share → Publish to web → Comma-separated values, and "
                    "paste the published link."
                )
            records = list(csv.DictReader(io.StringIO(text)))
        else:
            paths = _local_csv_paths(source)
            for path in paths:
                with open(path, newline="", encoding="utf-8-sig") as fh:
                    records.extend(list(csv.DictReader(fh)))

        rows = [build_row(r, table, mapping) for r in records]
        rows = _finish(conn, table, rows, records, mapping)
        # A file usually holds more than the requested window; keep the window.
        key = "created_date" if table == "crm_leads" else "date"
        lo, hi = start.isoformat(), end.isoformat()
        return [r for r in rows if lo <= r[key] <= hi]
