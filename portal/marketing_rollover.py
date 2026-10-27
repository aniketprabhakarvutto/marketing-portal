"""Read-only planning helpers for a future monthly workbook rollover.

No workbook is copied, edited or reset here. The caller must supply an audited
snapshot of formulas and manually entered date values from the test workbooks.
"""
from __future__ import annotations

from datetime import date
import re


_MONTH = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])$")
_SHEET_LINK = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", re.I)
_IMPORT_URL = re.compile(
    r'(\bIMPORTRANGE\s*\(\s*")(https://docs\.google\.com/spreadsheets/d/'
    r'([A-Za-z0-9_-]+)(?:/[^"\s]*)?)(")', re.I
)
_WORKBOOK_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _outside_string(formula: str, position: int) -> bool:
    inside = False
    index = 0
    while index < position:
        if formula[index] == '"':
            if inside and index + 1 < position and formula[index + 1] == '"':
                index += 2
                continue
            inside = not inside
        index += 1
    return not inside


def month_bounds(month: str) -> tuple[date, date]:
    """Return the first day of the requested month and the following month."""
    match = _MONTH.fullmatch(month)
    if not match:
        raise ValueError("Month must be YYYY-MM")
    year, number = map(int, match.groups())
    if not 1 <= year <= 9998:
        raise ValueError("Year is outside supported range")
    return date(year, number, 1), date(year + (number == 12), number % 12 + 1, 1)


def review_snapshot(month: str, cells: list[dict], old_workbook_ids: list[str]) -> dict:
    """Identify rollover risks in a supplied snapshot; never suggest replacements.

    Each cell has a sheet name, address, and either a formula or an ISO date.
    This is a partial audit of supplied cells, not proof that a workbook is safe.
    """
    start, end = month_bounds(month)
    old_ids = set(old_workbook_ids)
    findings = []
    for cell in cells:
        sheet, address = str(cell.get("sheet") or ""), str(cell.get("address") or "")
        if not sheet or not re.fullmatch(r"[A-Z]+[1-9][0-9]*", address):
            raise ValueError("Every cell needs a sheet name and A1 address")
        formula = cell.get("formula")
        if formula is not None:
            if not isinstance(formula, str) or not formula.startswith("="):
                raise ValueError("Formula must start with =")
            links = _SHEET_LINK.findall(formula)
            if any(link in old_ids for link in links):
                findings.append({"sheet": sheet, "cell": address, "issue": "old_workbook_link"})
            if re.search(r"\bIMPORTRANGE\s*\(", formula, re.I):
                findings.append({"sheet": sheet, "cell": address, "issue": "import_range_review"})
            if re.search(r"\bDATE\s*\(|\b(?:20\d{2}|19\d{2})\b", formula, re.I):
                findings.append({"sheet": sheet, "cell": address, "issue": "formula_date_review"})
        if cell.get("date") is not None:
            try:
                value = date.fromisoformat(cell["date"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Date must be an ISO date") from exc
            if not start <= value < end:
                findings.append({"sheet": sheet, "cell": address, "issue": "date_outside_target_month"})
    return {"month": month, "start": start.isoformat(), "end_exclusive": end.isoformat(),
            "reviewed_cells": len(cells), "findings": findings,
            "complete": False}


def plan_import_link_updates(cells: list[dict], replacements: dict[str, str]) -> list[dict]:
    """Plan literal IMPORTRANGE URL changes from an audited formula snapshot.

    The result is a review artifact, not a workbook write. Other formula text,
    references and values are untouched. Dynamic or bare-ID imports must be
    handled separately. A caller must recheck the current formula before any
    future application of a plan and verify access to the new source workbook.
    """
    for old, new in replacements.items():
        if not isinstance(old, str) or not isinstance(new, str) or not _WORKBOOK_ID.fullmatch(old) or not _WORKBOOK_ID.fullmatch(new) or old == new:
            raise ValueError("Replacement workbook IDs must be distinct valid IDs")
    if len(set(replacements.values())) != len(replacements):
        raise ValueError("Replacement workbook IDs must be unique")

    planned = []
    seen = set()
    for cell in cells:
        sheet, address = cell.get("sheet"), cell.get("address")
        formula = cell.get("formula")
        if not isinstance(sheet, str) or not sheet or not isinstance(address, str) or not re.fullmatch(r"[A-Z]+[1-9][0-9]*", address):
            raise ValueError("Every cell needs a sheet name and A1 address")
        if not isinstance(formula, str) or not formula.startswith("="):
            raise ValueError("Every cell needs a formula starting with =")
        if (sheet, address) in seen:
            raise ValueError("Duplicate formula cell")
        seen.add((sheet, address))
        matched = []
        def replace(match):
            if not _outside_string(formula, match.start()):
                return match.group(0)
            old = match.group(3)
            if old not in replacements:
                return match.group(0)
            matched.append(old)
            url = match.group(2)
            prefix = url[:url.lower().index('/spreadsheets/d/') + len('/spreadsheets/d/')]
            suffix = url[len(prefix) + len(old):]
            return match.group(1) + prefix + replacements[old] + suffix + match.group(4)
        updated = _IMPORT_URL.sub(replace, formula)
        if matched:
            planned.append({"sheet": sheet, "cell": address, "before": formula,
                            "after": updated, "replaced_workbooks": sorted(set(matched))})
    return planned
