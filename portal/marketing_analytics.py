"""Marketing Analytics workbook discovery, viewing, and campaign-cost export.

The module uses only saved Vortex Veri connections. It never accepts arbitrary
spreadsheet URLs from an HTTP request and it never writes unless the target is
an enabled Google Sheets connection explicitly tagged demand or supply.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import math
import re
from time import monotonic
from urllib.parse import quote

from . import connections as conns
from .db import query
from .httpclient import GOOGLE_API_TIMEOUT, HttpError, get as _http_get, request as _http_request
from .oauth import access_token
from .providers import REGISTRY

_MONTHS = {
    name.lower(): number for number, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"), 1
    )
}
_CELL_RANGE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$")
TEST_WORKBOOKS = {
    "demand": "1ijVhe0wTsUIYKN6mmd-WISi7VVBZZrnARPXijhFiAz0",
    "supply": "10IC3uyDYQvd0r1APYMeiNdWkHbw9GHyYNLgnaccfS-I",
}
MARKETING_ACCOUNT_IDS = {
    "google_ads": {"7329923258"},
    "meta_ads": {"1023092196493019"},
}
# Explicit business decision: these campaigns are excluded from Marketing
# Analytics until a workbook mapping exists.  They are reported as ignored,
# not silently reclassified.
IGNORED_CAMPAIGNS = frozenset({
    "Google_Hyyv_Search_Campaign_1",
    "Google_Hyyv_Search_Campaign_2",
    "Hyyv_demand_demandgen_",
})
DISCOVERY_CACHE_SECONDS = 300
_discovery_cache: dict[tuple[int, str, str, str], tuple[float, list[dict]]] = {}


def get(url: str, **kwargs):
    """Keep interactive Google calls bounded without changing other providers."""
    kwargs.setdefault("timeout", GOOGLE_API_TIMEOUT)
    return _http_get(url, **kwargs)


def request(method: str, url: str, **kwargs):
    """Keep interactive Google write calls bounded without changing other providers."""
    kwargs.setdefault("timeout", GOOGLE_API_TIMEOUT)
    return _http_request(method, url, **kwargs)


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


def _viewable(conn) -> bool:
    return bool(conn.enabled or conn.get("analytics_view_only") is True)


def _month_key(title: str) -> str:
    text = title.casefold()
    year_match = re.search(r"\b(20\d{2})\b", text)
    named = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s*['’\- ]\s*(20\d{2}|\d{2})\b", text)
    if named:
        month = next(number for name, number in _MONTHS.items() if name.startswith(named.group(1)[:3]))
        year = int(named.group(2))
        return f"{year + 2000 if year < 100 else year:04d}-{month:02d}"
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
        timeout=GOOGLE_API_TIMEOUT,
    )


def _drive_files(conn) -> list[dict]:
    folder_id = str(conn.get("drive_folder_id") or "").strip()
    if folder_id and not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("Invalid Drive folder ID")
    query = "trashed=false and mimeType='application/vnd.google-apps.spreadsheet' and name contains 'Marketing Analytics'"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    files, page_token = [], ""
    while True:
        params = {"q": query, "fields": "nextPageToken,files(id,name,modifiedTime)", "pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        body = get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {_token(conn)}"}, params=params,
            timeout=GOOGLE_API_TIMEOUT,
        )
        files.extend(body.get("files") or [])
        page_token = body.get("nextPageToken") or ""
        if not page_token:
            break
    return [item for item in files if _discovered_role(item.get("name") or "") == _role(conn)]


def _discovered_role(title: str) -> str:
    match = re.match(r"^\s*Marketing Analytics\s*\((Demand|Supply)\)", title, re.I)
    return match.group(1).lower() if match else ""


def _tabs(metadata: dict) -> list[dict]:
    return [
        {"id": int((sheet.get("properties") or {}).get("sheetId") or 0),
         "title": str((sheet.get("properties") or {}).get("title") or "")}
        for sheet in metadata.get("sheets") or []
        if (sheet.get("properties") or {}).get("title")
    ]


def _saved_workbook(conn) -> dict:
    """Build a listing row using only the saved connection record."""
    name = conn.name
    return {
        "connection_id": conn.id,
        "name": name,
        "role": _role(conn),
        "month": _month_key(name) or str(conn.get("analytics_month") or "").strip(),
        "spreadsheet_id": _spreadsheet_id(conn),
        "tabs": [],
        "status": "ready",
        "message": "",
    }


def _cache_key(conn) -> tuple[int, str, str, str]:
    return (conn.id, _spreadsheet_id(conn), _role(conn), str(conn.get("drive_folder_id") or ""))


def _discover_workbooks(conn, saved: dict) -> list[dict]:
    """Fetch workbook metadata and Drive siblings after DB work has completed."""
    key = _cache_key(conn)
    cached = _discovery_cache.get(key)
    if cached and cached[0] > monotonic():
        return cached[1]

    item = {**saved, "tabs": []}
    out = [item]
    try:
        metadata = _metadata(conn)
        title = str((metadata.get("properties") or {}).get("title") or conn.name)
        item["name"] = title
        item["month"] = _month_key(title) or str(conn.get("analytics_month") or "").strip()
        item["tabs"] = _tabs(metadata)
    except Exception as exc:  # one inaccessible workbook must not hide the other
        item["status"] = "error"
        item["message"] = str(exc)
        _discovery_cache[key] = (monotonic() + DISCOVERY_CACHE_SECONDS, out)
        return out

    try:
        for discovered in _drive_files(conn):
            spreadsheet_id = str(discovered.get("id") or "")
            if not spreadsheet_id or spreadsheet_id == item["spreadsheet_id"]:
                continue
            discovered_metadata = _metadata(conn, spreadsheet_id)
            out.append({
                "connection_id": conn.id, "name": str(discovered.get("name") or ""),
                "role": _role(conn), "month": _month_key(str(discovered.get("name") or "")),
                "spreadsheet_id": spreadsheet_id, "tabs": _tabs(discovered_metadata),
                "status": "discovered", "message": "Discovered from the configured Drive folder.",
            })
    except Exception as exc:
        # The saved workbook is still usable when sibling discovery fails.
        item["message"] = f"Drive discovery unavailable: {exc}"
    _discovery_cache[key] = (monotonic() + DISCOVERY_CACHE_SECONDS, out)
    return out


def workbooks(discover: bool = False) -> list[dict]:
    """List saved workbooks immediately; fetch Google metadata only on refresh."""
    # all_connections finishes its database read before any Google request.
    connections = tuple(conns.all_connections(provider="google_sheets"))
    saved = [_saved_workbook(conn) for conn in connections
             if _role(conn) in {"demand", "supply"} and _viewable(conn)]
    if not discover:
        return sorted(saved, key=lambda item: (item["month"], item["role"], item["name"]), reverse=True)

    by_id = {conn.id: conn for conn in connections}
    out = []
    for item in saved:
        out.extend(_discover_workbooks(by_id[item["connection_id"]], item))
    return sorted(out, key=lambda item: (item["month"], item["role"], item["name"]), reverse=True)


def read_tab(connection_id: int, tab: str, cell_range: str = "A1:ZZ500",
             spreadsheet_id: str = "") -> dict:
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or not _viewable(conn) or _role(conn) not in {"demand", "supply"}:
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
    # Preserve the sheet author's visual hierarchy without inferring meaning
    # from campaign names or values. Only return a bounded set of safe styles.
    # Do not request formatting for the entire grid when the viewer asks for
    # all rows. Sheets omits trailing empty rows from the values response.
    start_match = re.match(r"([A-Z]+)([0-9]+):([A-Z]+)([0-9]+)", cell_range)
    start_column, start_row, end_column, _ = start_match.groups()
    last_value_row = int(start_row) + max(0, len(body.get("values") or []) - 1)
    format_range = f"{start_column}{start_row}:{end_column}{last_value_row}"
    formatting = get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        headers={"Authorization": f"Bearer {_token(conn)}"},
        params={"ranges": f"'{tab.replace(chr(39), chr(39) * 2)}'!{format_range}",
                "fields": "sheets(properties(gridProperties(rowCount)),data(startRow,startColumn,rowData(values(effectiveFormat(backgroundColor,textFormat(bold,foregroundColor),horizontalAlignment)))))"},
    )
    styles = []
    for sheet in formatting.get("sheets") or []:
        for grid in sheet.get("data") or []:
            for ri, row in enumerate(grid.get("rowData") or []):
                for ci, cell in enumerate(row.get("values") or []):
                    fmt = cell.get("effectiveFormat") or {}
                    if fmt:
                        styles.append([int(grid.get("startRow") or 0) + ri,
                                       int(grid.get("startColumn") or 0) + ci, fmt])
    # Drive's modifiedTime is the workbook modification time, not the time
    # upstream advertising or CRM data was last synchronized.
    try:
        file_metadata = get(
            f"https://www.googleapis.com/drive/v3/files/{quote(spreadsheet_id, safe='')}",
            headers={"Authorization": f"Bearer {_token(conn)}"},
            params={"fields": "modifiedTime"},
        )
        modified_time = file_metadata.get("modifiedTime")
        modified_status = "available" if modified_time else "unavailable"
    except HttpError as exc:
        modified_time = None
        modified_status = "unavailable"
        try:
            error = json.loads(exc.body).get("error", {})
            reasons = {item.get("reason") for item in error.get("errors", []) if isinstance(item, dict)}
            if exc.status == 403 and "insufficientPermissions" in reasons:
                modified_status = "permission_required"
            elif exc.status == 403 and "accessNotConfigured" in reasons:
                modified_status = "api_not_enabled"
        except (ValueError, AttributeError, TypeError):
            pass
    except Exception:
        modified_time = None
        modified_status = "unavailable"
    sheet_row_count = next((s.get("properties", {}).get("gridProperties", {}).get("rowCount") for s in formatting.get("sheets") or [] if s.get("properties", {}).get("gridProperties", {}).get("rowCount")), None)
    return {"connection_id": conn.id, "role": _role(conn), "tab": tab, "sheet_row_count": sheet_row_count,
            "range": body.get("range") or cell_range, "rows": body.get("values") or [], "styles": styles,
            "workbook_modified_time": modified_time, "workbook_modified_status": modified_status}


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
        explicit_tokens = {role for role in ("demand", "supply")
                           if re.search(rf"(?<![a-z0-9]){role}(?![a-z0-9])", explicit)}
        if explicit in {"buy", "demand"} or explicit_tokens == {"demand"}:
            side = "demand"
        elif explicit in {"sell", "supply"} or explicit_tokens == {"supply"}:
            side = "supply"
        elif explicit:
            side = ""
        else:
            target_text = target.casefold()
            source_text = source.casefold()
            # DemandGen is an advertising format, not a funnel designation.
            # Require separate tokens so a Supply_DemandGen campaign remains supply.
            def funnel_tokens(text):
                return {role for role in ("demand", "supply")
                        if re.search(rf"(?<![a-z0-9]){role}(?![a-z0-9])", text)}
            tokens = funnel_tokens(target_text) or funnel_tokens(source_text)
            side = next(iter(tokens)) if len(tokens) == 1 else ""
        if side:
            result[side].append((source, target))
        else:
            result["unclassified"].append(source)
    return result


def prepare_cost_rows(source_rows, mappings, day: str, allowed_accounts: dict[str, set[str]],
                      other_side_campaigns=(), ignored_campaigns=()) -> dict:
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
    other_side = {str(name).strip().casefold() for name in other_side_campaigns}
    ignored = {str(name).strip().casefold() for name in ignored_campaigns}
    if set(lookup) & other_side:
        raise ValueError("Campaign mapping belongs to both demand and supply")
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
        if campaign.casefold() in ignored:
            continue
        mapped = lookup.get(campaign.casefold())
        if mapped is None:
            if campaign.casefold() in other_side:
                continue
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


def mapping_status(month: str) -> dict:
    """Compare saved paid campaign names with the configured mapping sheet.

    This is a read-only snapshot of locally ingested data, not proof that the
    advertising accounts have been fully synchronized.
    """
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month or ""):
        raise ValueError("Choose a valid reporting month")
    start = date.fromisoformat(month + "-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    rows = query("SELECT source,campaign_name FROM paid_metrics WHERE date>=? AND date<? "
                 "AND ((source='google_ads' AND account_id=?) OR "
                 "(source='meta_ads' AND account_id=?)) GROUP BY source,campaign_name "
                 "ORDER BY source,campaign_name",
                 (start.isoformat(), end.isoformat(),
                  next(iter(MARKETING_ACCOUNT_IDS["google_ads"])),
                  next(iter(MARKETING_ACCOUNT_IDS["meta_ads"]))))
    campaigns = [(str(r["source"]), str(r["campaign_name"] or "").strip()) for r in rows]
    connections = [c for c in conns.all_connections(provider="google_sheets") if _role(c) == "mapping" and _viewable(c)]
    result = {"month": month, "status": "unavailable", "sources": sorted({source for source, _ in campaigns}),
              "campaign_count": len(campaigns), "unmapped": [], "ambiguous": []}
    if not campaigns:
        result["message"] = "No saved advertising campaign data was found for this month, so mappings cannot be checked."
        return result
    if len(connections) != 1:
        result["message"] = "Connect exactly one mapping workbook for read-only checks."
        return result
    conn = connections[0]
    try:
        tab = str(conn.get("sheet_name") or "").strip()
        metadata = _metadata(conn)
        titles = {str((s.get("properties") or {}).get("title") or "") for s in metadata.get("sheets") or []}
        if not tab or tab not in titles:
            raise ValueError("Configured mapping tab was not found")
        escaped = quote(f"'{tab.replace(chr(39), chr(39) * 2)}'!A1:ZZ2000", safe="")
        body = get(f"https://sheets.googleapis.com/v4/spreadsheets/{_spreadsheet_id(conn)}/values/{escaped}",
                   headers={"Authorization": f"Bearer {_token(conn)}"},
                   params={"majorDimension": "ROWS", "valueRenderOption": "FORMATTED_VALUE"})
        values = body.get("values") or []
        if len(values) >= 2000:
            raise ValueError("Mapping sheet exceeds the supported range")
        pairs = mapping_pairs(values, conn.get("mapping_source_column") or "A",
                              conn.get("mapping_target_column") or "B", conn.get("mapping_side_column") or "")
        lookup = defaultdict(set)
        for role in ("demand", "supply"):
            for source, target in pairs[role]:
                lookup[source.casefold()].add((role, target))
        unclassified = {name.casefold() for name in pairs["unclassified"]}
        for source, name in campaigns:
            entries = lookup.get(name.casefold(), set())
            item = {"source": source, "campaign": name}
            if not name or name.casefold() in unclassified or len(entries) > 1:
                result["ambiguous"].append(item)
            elif not entries:
                result["unmapped"].append(item)
        result["status"] = "checked"
        result["message"] = "Compared saved campaign data with the mapping sheet; source completeness is not verified."
    except Exception:
        result["message"] = "Mapping workbook could not be read. Check its connection and access."
    return result


def write_cost_tab(connection_id: int, tab: str, rows: list[list], start_cell: str = "A1") -> dict:
    """Atomically replace values in the dedicated staging tab, including old rows."""
    conn = conns.get(connection_id)
    if not conn or conn.provider != "google_sheets" or not conn.enabled or _role(conn) not in {"demand", "supply"}:
        raise KeyError("Marketing Analytics workbook not found")
    if not tab or len(tab) > 100 or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", start_cell.upper()):
        raise ValueError("Invalid sheet target")
    if tab != "__vortex_cost_test" or start_cell.upper() != "A1":
        raise ValueError("Only the dedicated staging tab can be replaced")
    spreadsheet_id = _spreadsheet_id(conn)
    if spreadsheet_id != TEST_WORKBOOKS[_role(conn)]:
        raise ValueError("Writes are limited to the approved private test workbook")
    token = _token(conn)
    metadata = get(f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
                   headers={"Authorization": f"Bearer {token}"},
                   params={"fields": "sheets.properties"})
    matches = [s.get("properties", {}) for s in metadata.get("sheets", [])
               if s.get("properties", {}).get("title") == tab]
    if len(matches) != 1:
        raise ValueError("Dedicated staging tab not found")
    props = matches[0]
    grid = props.get("gridProperties") or {}
    row_count, col_count = int(grid.get("rowCount") or 0), int(grid.get("columnCount") or 0)
    if len(rows) > row_count or any(len(row) > col_count for row in rows):
        raise ValueError("Export exceeds staging tab dimensions")
    if not row_count or not col_count or "sheetId" not in props:
        raise ValueError("Staging tab dimensions unavailable")
    def cell(value):
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Invalid numeric value")
            return {"userEnteredValue": {"numberValue": number}}
        return {"userEnteredValue": {"stringValue": str(value)}}
    row_data = [{"values": [cell(value) for value in row]} for row in rows]
    used_columns = max(len(row) for row in rows) if rows else 0
    requests = [{"updateCells": {
        "range": {"sheetId": props["sheetId"], "startRowIndex": 0,
                  "endRowIndex": len(rows), "startColumnIndex": 0,
                  "endColumnIndex": used_columns},
        "rows": row_data, "fields": "userEnteredValue"}}]
    if used_columns < col_count:
        requests.append({"repeatCell": {
            "range": {"sheetId": props["sheetId"], "startRowIndex": 0,
                      "endRowIndex": row_count, "startColumnIndex": used_columns,
                      "endColumnIndex": col_count},
            "cell": {}, "fields": "userEnteredValue"}})
    if len(rows) < row_count:
        requests.append({"repeatCell": {
            "range": {"sheetId": props["sheetId"], "startRowIndex": len(rows),
                      "endRowIndex": row_count, "startColumnIndex": 0,
                      "endColumnIndex": used_columns},
            "cell": {}, "fields": "userEnteredValue"}})
    body = request(
        "POST", f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate",
        headers={"Authorization": f"Bearer {token}"},
        json_body={"requests": requests},
    )
    return {"connection_id": conn.id, "role": _role(conn), "tab": tab,
            "updated_range": f"{tab}!A1", "updated_rows": len(rows)}
