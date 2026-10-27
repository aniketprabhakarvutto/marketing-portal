"""Strict importer for Superleap Marketing DB aggregate exports.

The Marketing DB reports contain business calculations that the Opportunity
API does not expose.  Treat the exported 19-column report as the source of
truth and write only the report fields into Backend C:U. Live destinations
are an explicit month-and-workbook allowlist, never arbitrary Sheet URLs.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from urllib.parse import quote

from . import connections as conns
from .marketing_analytics import TEST_WORKBOOKS, _role, _spreadsheet_id, _token
from .httpclient import get, request


DEMAND_HEADERS = (
    "Created at", "UTM Campaign", "Vutto Source", "Leads", "Search%",
    "Intent%", "Intent Leads", "Connect%", "Connected", "VB%", "VB",
    "VD%", "VD", "Sales%", "Sales", "Search Leads", "Attempted",
    "Drop%", "Dropped",
)
SUPPLY_HEADERS = (
    "Created at", "UTM Campaign", "Vutto Source", "Leads", "Search%",
    "Qualified%", "Qualified Leads", "Connect%", "Connected",
    "Scheduled Initial%", "IS", "Procured", "Attempted", "FS(at Home)",
    "FS(at Store)", "Insp Done (At Home)", "Insp Done (At Store)",
    "Search Leads", "Audit Done",
)
HEADERS = {"demand": DEMAND_HEADERS, "supply": SUPPLY_HEADERS}
BACKEND_SHEET_IDS = {"demand": 242976363, "supply": 2053470233}
MAX_ROWS = 50_498
LIVE_WORKBOOKS = {
    "2026-10": {
        "demand": "1jGhzNO45iRquAroGv_ieG7-u9nLAy4UiGwEKbbNqNGM",
        "supply": "1tLRAp6luNyIZOG2Yl2h4U6MfJMjKl3ZL7qNKH7JJNKo",
    },
    "2026-11": {
        "demand": "1MVJ5IW9b8TNeR0sWoK0G4Lty2TRBxAQEQ8-zONAc524",
        "supply": "1iRCQqe8-aDKGmNuCcGVeFhI73eNIYdnKeG_uLNRIXdY",
    },
}
LIVE_CAMPAIGN_COST = {
    "2026-10": ("1jGhzNO45iRquAroGv_ieG7-u9nLAy4UiGwEKbbNqNGM", 1164262942, "Campaign Cost"),
}


def _date_value(value: str) -> date:
    text = value.strip()
    if not text:
        raise ValueError("Marketing DB export contains a blank Created at value")
    for candidate in (text[:10], text):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    for pattern in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.split("T", 1)[0], pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid Marketing DB Created at value: {text[:80]}")


def _number(value: str):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return ""
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Invalid Marketing DB metric value: {value!r}") from None
    return int(number) if number == number.to_integral_value() else float(number)


def read_export(path: str | Path, role: str, month: str, *, allow_other_months: bool = False) -> list[list]:
    if role not in HEADERS:
        raise ValueError("Marketing DB role must be demand or supply")
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError("Marketing DB month must use YYYY-MM")
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".csv":
        raise ValueError(f"Marketing DB {role} export must be an existing CSV file")
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration:
            raise ValueError(f"Marketing DB {role} export is empty") from None
        if header != HEADERS[role]:
            raise ValueError(f"Marketing DB {role} headers do not match the approved report")
        rows = []
        for line_number, raw in enumerate(reader, start=2):
            if not any(str(value).strip() for value in raw):
                continue
            if len(raw) != 19:
                raise ValueError(f"Marketing DB {role} row {line_number} has {len(raw)} columns, expected 19")
            created = _date_value(raw[0])
            if created.strftime("%Y-%m") != month and allow_other_months:
                continue
            if created.strftime("%Y-%m") != month:
                raise ValueError(f"Marketing DB {role} row {line_number} is outside {month}")
            rows.append([raw[0].strip(), raw[1].strip(), raw[2].strip()]
                        + [_number(value) for value in raw[3:]])
    if not rows:
        raise ValueError(f"Marketing DB {role} export has no rows for {month}")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Marketing DB {role} export exceeds the Backend tab capacity")
    return rows


def summarise(rows: list[list]) -> dict:
    return {
        "rows": len(rows),
        "leads": sum(Decimal(str(row[3] or 0)) for row in rows),
        "first_date": min(_date_value(str(row[0])) for row in rows).isoformat(),
        "last_date": max(_date_value(str(row[0])) for row in rows).isoformat(),
    }


def _cell(value):
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return {"userEnteredValue": {"numberValue": float(value)}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def write_backend(connection_id: int, role: str, rows: list[list], *, live: bool = False) -> dict:
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or _role(conn) != role:
        raise KeyError(f"{role.title()} workbook connection not found")
    spreadsheet_id = _spreadsheet_id(conn)
    live_ids = {values[role] for values in LIVE_WORKBOOKS.values()}
    if live:
        if spreadsheet_id not in live_ids:
            raise ValueError("Backend writes are limited to the approved live workbook")
    elif not conn.enabled or spreadsheet_id != TEST_WORKBOOKS[role]:
        raise ValueError("Backend writes are limited to the approved private test workbook")
    if not rows or len(rows) > MAX_ROWS or any(len(row) != 19 for row in rows):
        raise ValueError("Backend export must contain 1 to 50,498 rows of 19 columns")
    token = _token(conn)
    metadata = get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "sheets.properties"},
    )
    matches = [item.get("properties", {}) for item in metadata.get("sheets", [])
               if item.get("properties", {}).get("title") == "Backend"]
    if len(matches) != 1 or matches[0].get("sheetId") != BACKEND_SHEET_IDS[role]:
        raise ValueError("Approved workbook Backend tab identity does not match")
    props = matches[0]
    grid = props.get("gridProperties") or {}
    if int(grid.get("rowCount") or 0) < 50_500 or int(grid.get("columnCount") or 0) < 21:
        raise ValueError("Approved workbook Backend tab is smaller than expected")
    data = [{"values": [_cell(value) for value in row]} for row in rows]
    request(
        "POST", f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate",
        headers={"Authorization": f"Bearer {token}"},
        json_body={"requests": [{"updateCells": {
            "range": {"sheetId": props["sheetId"], "startRowIndex": 2,
                      "endRowIndex": 50_500, "startColumnIndex": 2, "endColumnIndex": 21},
            "rows": data, "fields": "userEnteredValue",
        }}]},
    )
    return {"connection_id": conn.id, "role": role, "tab": "Backend",
            "range": f"C3:U{len(rows) + 2}", "rows": len(rows)}


def _sheet_day(value) -> str | None:
    if isinstance(value, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    text = str(value or "").strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def append_live_campaign_cost(connection_id: int, day: str, rows) -> dict:
    """Append one complete reporting day, or no-op when it is already exact."""
    month = date.fromisoformat(day).strftime("%Y-%m")
    approved = LIVE_CAMPAIGN_COST.get(month)
    if not approved:
        raise ValueError("No approved live Campaign Cost tab is configured for this month")
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or _role(conn) != "demand":
        raise KeyError("Demand workbook connection not found")
    workbook_id, sheet_id, tab = approved
    if _spreadsheet_id(conn) != workbook_id:
        raise ValueError("Campaign Cost writes are limited to the approved live Demand workbook")
    intended = [(str(row[3]), Decimal(str(row[4]))) for row in rows]
    if not intended or len({name for name, _ in intended}) != len(intended):
        raise ValueError("Campaign Cost mappings must produce unique campaign names")
    token = _token(conn)
    headers = {"Authorization": f"Bearer {token}"}
    metadata = get(f"https://sheets.googleapis.com/v4/spreadsheets/{workbook_id}",
                   headers=headers, params={"fields": "sheets.properties"})
    matches = [item.get("properties", {}) for item in metadata.get("sheets", [])
               if item.get("properties", {}).get("title") == tab]
    if len(matches) != 1 or matches[0].get("sheetId") != sheet_id:
        raise ValueError("Approved Campaign Cost tab identity does not match")
    encoded = quote(f"'{tab}'!A1:C50000", safe="")
    current = get(f"https://sheets.googleapis.com/v4/spreadsheets/{workbook_id}/values/{encoded}",
                  headers=headers, params={"valueRenderOption": "UNFORMATTED_VALUE"}).get("values") or []
    existing = [(str(row[1]), Decimal(str(row[2]))) for row in current
                if len(row) >= 3 and _sheet_day(row[0]) == day]
    if existing:
        existing_by_name = {name: amount for name, amount in existing}
        intended_by_name = {name: amount for name, amount in intended}
        if len(existing_by_name) != len(existing) or any(
                intended_by_name.get(name) != amount for name, amount in existing_by_name.items()):
            raise ValueError("Campaign Cost already contains different data for this day")
        missing = [(name, amount) for name, amount in intended if name not in existing_by_name]
        if not missing:
            return {"connection_id": conn.id, "tab": tab, "date": day, "rows": len(intended), "status": "already_present"}
        intended = missing
    target = quote(f"'{tab}'!A:C", safe="")
    request("POST", f"https://sheets.googleapis.com/v4/spreadsheets/{workbook_id}/values/{target}:append",
            headers=headers, params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json_body={"majorDimension": "ROWS", "values": [[day, name, float(amount)] for name, amount in intended]})
    return {"connection_id": conn.id, "tab": tab, "date": day, "rows": len(intended), "status": "appended"}
