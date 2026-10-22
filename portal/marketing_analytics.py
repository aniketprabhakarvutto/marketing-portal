"""Marketing Analytics workbook discovery, viewing, and campaign-cost export.

The module uses only saved Vortex Veri connections. It never accepts arbitrary
spreadsheet URLs from an HTTP request and it never writes unless the target is
an enabled Google Sheets connection explicitly tagged demand or supply.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import quote

from . import connections as conns
from .db import query
from .httpclient import get, request
from .oauth import access_token
from .providers import REGISTRY

_MONTHS = {
    name.lower(): number for number, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"), 1
    )
}
_CELL_RANGE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$")


def _sheet_provider():
    return REGISTRY["google_sheets"]


def _token(conn) -> str:
    return access_token(conn, _sheet_provider().oauth)


def _spreadsheet_id(conn) -> str:
    return _sheet_provider()._spreadsheet_id(str(conn.get("spreadsheet_url") or ""))


def _role(conn) -> str:
    role = str(conn.get("analytics_role") or "").strip().lower()
    if role in {"demand", "supply", "mapping"}:
        return role
    name = conn.name.casefold()
    if name.startswith("demand "):
        return "demand"
    if name.startswith("supply "):
        return "supply"
    return ""


def _month_key(title: str) -> str:
    text = title.casefold()
    year_match = re.search(r"\b(20\d{2})\b", text)
    if not year_match:
        return ""
    month = next((number for name, number in _MONTHS.items() if name in text), None)
    if month is None:
        numeric = re.search(r"\b(0?[1-9]|1[0-2])[\s._/-]+(20\d{2})\b", text)
        if numeric:
            month, year = int(numeric.group(1)), int(numeric.group(2))
            return f"{year:04d}-{month:02d}"
        return ""
    return f"{int(year_match.group(1)):04d}-{month:02d}"


def _metadata(conn, spreadsheet_id: str = "") -> dict:
    spreadsheet_id = spreadsheet_id or _spreadsheet_id(conn)
    return get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {_token(conn)}"},
        params={"fields": "spreadsheetId,properties.title,sheets.properties"},
    )


def _drive_files(conn) -> list[dict]:
    folder_id = str(conn.get("drive_folder_id") or "").strip()
    if not folder_id:
        return []
    body = get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {_token(conn)}"},
        params={
            "q": f"'{folder_id}' in parents and trashed=false and mimeType='application/vnd.google-apps.spreadsheet'",
            "fields": "files(id,name,modifiedTime)", "pageSize": 1000,
            "orderBy": "name desc",
        },
    )
    return body.get("files") or []


def workbooks() -> list[dict]:
    """Return only explicitly saved Marketing Analytics workbooks."""
    out = []
    for conn in conns.all_connections(provider="google_sheets", enabled_only=True):
        role = _role(conn)
        if role not in {"demand", "supply"}:
            continue
        item = {
            "connection_id": conn.id,
            "name": conn.name,
            "role": role,
            "month": "",
            "spreadsheet_id": _spreadsheet_id(conn),
            "tabs": [],
            "status": "ready",
            "message": "",
        }
        try:
            metadata = _metadata(conn)
            title = str((metadata.get("properties") or {}).get("title") or conn.name)
            item["name"] = title
            item["month"] = _month_key(title)
            item["tabs"] = [
                {"id": int((sheet.get("properties") or {}).get("sheetId") or 0),
                 "title": str((sheet.get("properties") or {}).get("title") or "")}
                for sheet in metadata.get("sheets") or []
                if (sheet.get("properties") or {}).get("title")
            ]
        except Exception as exc:  # one inaccessible workbook must not hide the other
            item["status"] = "error"
            item["message"] = str(exc)
        out.append(item)
        if item["status"] == "error":
            continue
        try:
            for discovered in _drive_files(conn):
                spreadsheet_id = str(discovered.get("id") or "")
                if not spreadsheet_id or any(row["spreadsheet_id"] == spreadsheet_id for row in out):
                    continue
                discovered_metadata = _metadata(conn, spreadsheet_id)
                out.append({
                    "connection_id": conn.id, "name": str(discovered.get("name") or ""),
                    "role": role, "month": _month_key(str(discovered.get("name") or "")),
                    "spreadsheet_id": spreadsheet_id,
                    "tabs": [
                        {"id": int((sheet.get("properties") or {}).get("sheetId") or 0),
                         "title": str((sheet.get("properties") or {}).get("title") or "")}
                        for sheet in discovered_metadata.get("sheets") or []
                        if (sheet.get("properties") or {}).get("title")
                    ],
                    "status": "discovered", "message": "Discovered from the configured Drive folder.",
                })
        except Exception as exc:
            item["status"] = "error"
            item["message"] = str(exc)
    return sorted(out, key=lambda item: (item["month"], item["role"], item["name"]), reverse=True)


def read_tab(connection_id: int, tab: str, cell_range: str = "A1:ZZ500",
             spreadsheet_id: str = "") -> dict:
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or not conn.enabled or _role(conn) not in {"demand", "supply"}:
        raise KeyError("Marketing Analytics workbook not found")
    if not tab or len(tab) > 100:
        raise ValueError("Choose a valid sheet tab")
    cell_range = cell_range.strip().upper()
    if not _CELL_RANGE.fullmatch(cell_range):
        raise ValueError("Use a bounded cell range such as A1:Z200")
    saved_id = _spreadsheet_id(conn)
    spreadsheet_id = str(spreadsheet_id or saved_id).strip()
    if spreadsheet_id != saved_id:
        discovered_ids = {str(item.get("id") or "") for item in _drive_files(conn)}
        if spreadsheet_id not in discovered_ids:
            raise KeyError("Workbook is not in the configured Marketing Analytics folder")
    escaped = quote(f"'{tab.replace(chr(39), chr(39) * 2)}'!{cell_range}", safe="")
    body = get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{escaped}",
        headers={"Authorization": f"Bearer {_token(conn)}"},
        params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"},
    )
    return {"connection_id": conn.id, "role": _role(conn), "tab": tab,
            "range": body.get("range") or cell_range, "rows": body.get("values") or []}


def selection_stats(values) -> dict:
    numbers = []
    count = 0
    for row in values or []:
        for value in row or []:
            if value not in (None, ""):
                count += 1
            if isinstance(value, bool) or value in (None, ""):
                continue
            try:
                number = Decimal(str(value).replace(",", "").replace("₹", "").strip())
            except (InvalidOperation, ValueError):
                continue
            if number.is_finite():
                numbers.append(number)
    return {
        "sum": float(sum(numbers, Decimal(0))) if numbers else None,
        "avg": float(sum(numbers, Decimal(0)) / len(numbers)) if numbers else None,
        "min": float(min(numbers)) if numbers else None,
        "max": float(max(numbers)) if numbers else None,
        "count": count,
        "count_numbers": len(numbers),
    }


def _column_index(letter: str) -> int:
    letter = str(letter or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", letter):
        raise ValueError("Mapping columns must be letters such as A, B, or AA")
    result = 0
    for char in letter:
        result = result * 26 + ord(char) - 64
    return result - 1


def mapping_pairs(values, source_column="A", target_column="B", side_column="C") -> dict:
    source_index, target_index = _column_index(source_column), _column_index(target_column)
    side_index = _column_index(side_column) if side_column else None
    result = {"demand": [], "supply": [], "unclassified": []}
    for row in values or []:
        source = str(row[source_index] if source_index < len(row) else "").strip()
        target = str(row[target_index] if target_index < len(row) else "").strip()
        if not source or not target or source.casefold() in {
            "campaign", "campaign name", "source", "platform name",
        }:
            continue
        explicit = str(row[side_index] if side_index is not None and side_index < len(row) else "").strip().lower()
        if explicit in {"buy", "demand"}:
            side = "demand"
        elif explicit in {"sell", "supply"}:
            side = "supply"
        else:
            text = f"{source} {target}".casefold()
            side = "demand" if "demand" in text else "supply" if "supply" in text else ""
        if side:
            result[side].append((source, target))
        else:
            result["unclassified"].append(source)
    return result


def prepare_cost_rows(source_rows, mappings, day: str, allowed_accounts: dict[str, set[str]]) -> dict:
    """Map exact campaign names and reject incomplete or ambiguous exports."""
    date.fromisoformat(day)
    lookup = {}
    for source, target in mappings:
        source, target = str(source or "").strip(), str(target or "").strip()
        if not source or not target:
            continue
        key = source.casefold()
        if key in lookup and lookup[key] != target:
            raise ValueError("Conflicting campaign mapping: " + source)
        lookup[key] = target
    grouped = defaultdict(Decimal)
    unmapped, sources_seen = set(), set()
    for row in source_rows:
        platform = str(row.get("source") or "")
        if platform not in allowed_accounts or str(row.get("account_id") or "") not in allowed_accounts[platform]:
            continue
        if str(row.get("date") or "") != day:
            continue
        if str(row.get("currency") or "").upper() != "INR":
            raise ValueError("Only verified INR data may be exported")
        campaign = str(row.get("campaign_name") or "").strip()
        if not campaign:
            raise ValueError("Campaign name is missing")
        try:
            amount = Decimal(str(row["spend"]))
        except (KeyError, InvalidOperation, ValueError):
            raise ValueError("Invalid spend amount") from None
        if not amount.is_finite():
            raise ValueError("Invalid spend amount")
        sources_seen.add(platform)
        mapped = lookup.get(campaign.casefold())
        if mapped is None:
            unmapped.add(campaign)
            continue
        grouped[(platform, campaign, mapped)] += amount
    missing = set(allowed_accounts) - sources_seen
    if missing:
        raise ValueError("No data for required sources: " + ", ".join(sorted(missing)))
    return {
        "rows": [(day, platform, campaign, mapped, amount)
                 for (platform, campaign, mapped), amount in sorted(grouped.items())],
        "unmapped": sorted(unmapped),
    }


def paid_rows(day: str) -> list[dict]:
    date.fromisoformat(day)
    return [dict(row) for row in query(
        "SELECT date,source,account_id,campaign_name,currency,SUM(spend) AS spend "
        "FROM paid_metrics WHERE date=? AND source IN ('google_ads','meta_ads') "
        "GROUP BY date,source,account_id,campaign_name,currency ORDER BY source,campaign_name",
        (day,),
    )]


def write_cost_tab(connection_id: int, tab: str, rows: list[list], start_cell: str = "A1") -> dict:
    """Write a rectangular cost export to one explicitly saved test/target tab."""
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or not conn.enabled or _role(conn) not in {"demand", "supply"}:
        raise KeyError("Marketing Analytics workbook not found")
    if not tab or len(tab) > 100 or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", start_cell.upper()):
        raise ValueError("Invalid sheet target")
    spreadsheet_id = _spreadsheet_id(conn)
    escaped = quote(f"'{tab.replace(chr(39), chr(39) * 2)}'!{start_cell.upper()}", safe="")
    body = request(
        "PUT",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{escaped}",
        headers={"Authorization": f"Bearer {_token(conn)}"},
        params={"valueInputOption": "USER_ENTERED"},
        json_body={"majorDimension": "ROWS", "values": rows},
    )
    return {"connection_id": conn.id, "role": _role(conn), "tab": tab,
            "updated_range": body.get("updatedRange") or "", "updated_rows": int(body.get("updatedRows") or 0)}
