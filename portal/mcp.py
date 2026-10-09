"""Read-only Model Context Protocol server for Vortex Veri.

Supports local stdio clients (Claude Desktop, editors and Codex) and the
portal's Streamable HTTP endpoint.  The implementation intentionally uses the
Python standard library so the portal keeps its zero-install setup.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import api
from . import platform
from .db import close_connection, init_db

SERVER_INFO = {"name": "marketing-data-portal", "version": "1.0.0"}
PROTOCOL_VERSION = "2025-11-25"


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


DATE_FIELDS = {
    "start": {"type": "string", "format": "date", "description": "First date, YYYY-MM-DD. Defaults to the portal reporting window."},
    "end": {"type": "string", "format": "date", "description": "Last date, YYYY-MM-DD. Defaults to the latest available date."},
}


def _overview(args: dict) -> Any:
    return api.overview(args.get("start"), args.get("end"))


def _channels(args: dict) -> Any:
    return api.channels(args.get("start"), args.get("end"))


def _campaigns(args: dict) -> Any:
    limit = max(1, min(int(args.get("limit", 100)), 500))
    source = str(args.get("source") or "").strip() or None
    if source is not None and source not in api.PAID_SOURCES:
        raise ValueError(f"Unsupported paid source: {source}")
    # Search/filter the complete report before applying the response-size cap.
    rows = api.campaigns(args.get("start"), args.get("end"), source, None)
    search_complete = True
    query = str(args.get("query") or "").strip().lower()
    if query:
        rows = [row for row in rows if query in str(row.get("campaign") or "").lower()]
    sort_by = str(args.get("sort_by") or "spend")
    if rows and sort_by not in rows[0]:
        raise ValueError(f"Unsupported campaign sort field: {sort_by}")
    if rows:
        measured = [row for row in rows if row.get(sort_by) is not None]
        missing = [row for row in rows if row.get(sort_by) is None]
        measured.sort(key=lambda row: row[sort_by], reverse=bool(args.get("descending", True)))
        rows = measured + missing
    return {"count": min(len(rows), limit), "total_matches": len(rows),
            "search_complete": search_complete, "campaigns": rows[:limit]}


def _website(args: dict) -> Any:
    return api.web(args.get("start"), args.get("end"), args.get("dimension", "channel"))


def _search(args: dict) -> Any:
    limit = max(1, min(int(args.get("limit", 50)), 500))
    return api.search(args.get("start"), args.get("end"), args.get("dimension", "query"), limit)


def _crm(args: dict) -> Any:
    return api.crm(args.get("start"), args.get("end"))


def _health(_args: dict) -> Any:
    meta = api.meta()
    return {
        "data_through": meta.get("data_to"),
        "currency": meta.get("currency"),
        "row_counts": meta.get("row_counts"),
        "sources": meta.get("sources"),
    }


def _ahrefs(args: dict) -> Any:
    return api.ahrefs(args.get("end"))


def _quality(args: dict) -> Any:
    return api.data_quality(args.get("start"), args.get("end"))


def _planning(args: dict) -> Any:
    return platform.planning(args.get("start"), args.get("end"))


def _explorer(args: dict) -> Any:
    return platform.explore(args)


ToolHandler = Callable[[dict], Any]
TOOLS: list[tuple[dict, ToolHandler]] = [
    ({"name": "marketing_data_health", "description": "Check which marketing sources are current, delayed, missing, or awaiting data.", "inputSchema": _schema()}, _health),
    ({"name": "marketing_overview", "description": "Get blended spend, traffic, lead, sales, CPL and CAC performance for a reporting period, including the previous-period comparison.", "inputSchema": _schema(DATE_FIELDS)}, _overview),
    ({"name": "marketing_channels", "description": "Compare normalized channel performance across advertising, website and CRM outcomes.", "inputSchema": _schema(DATE_FIELDS)}, _channels),
    ({"name": "marketing_campaigns", "description": "List paid campaigns with spend, clicks, conversions, CRM leads, sales, CPL and CAC. Supports filtering, sorting and a result limit.", "inputSchema": _schema({**DATE_FIELDS, "source": {"type": "string", "description": "Optional source such as google_ads or meta_ads."}, "query": {"type": "string", "description": "Optional campaign-name search."}, "sort_by": {"type": "string", "description": "Metric field used for sorting. Defaults to spend."}, "descending": {"type": "boolean", "default": True}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}})}, _campaigns),
    ({"name": "marketing_website", "description": "Get GA4 website performance grouped by channel, source or campaign.", "inputSchema": _schema({**DATE_FIELDS, "dimension": {"type": "string", "enum": ["channel", "source", "campaign"], "default": "channel"}})}, _website),
    ({"name": "marketing_search", "description": "Get organic Search Console performance grouped by query or landing page.", "inputSchema": _schema({**DATE_FIELDS, "dimension": {"type": "string", "enum": ["query", "page"], "default": "query"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}})}, _search),
    ({"name": "marketing_crm_funnel", "description": "Get CRM funnel totals, source mix, city mix and demand-versus-supply outcomes for a period.", "inputSchema": _schema(DATE_FIELDS)}, _crm),
    ({"name": "marketing_ahrefs", "description": "Get the latest available Ahrefs SEO authority and organic-search snapshot.", "inputSchema": _schema({"end": DATE_FIELDS["end"]})}, _ahrefs),
    ({"name": "marketing_data_quality", "description": "Check refresh health, coverage, revenue availability, duplicate leads, and unclassified channels for a reporting period.", "inputSchema": _schema(DATE_FIELDS)}, _quality),
    ({"name": "marketing_planning", "description": "Get transparent run-rate spend, lead, and sales forecasts with attribution limitations.", "inputSchema": _schema(DATE_FIELDS)}, _planning),
    ({"name": "marketing_explorer", "description": "Explore paid campaign data with governed dimensions, metrics, filters, sorting, and a result limit.", "inputSchema": _schema({**DATE_FIELDS, "dimensions": {"type": "array", "items": {"type": "string"}}, "metrics": {"type": "array", "items": {"type": "string"}}, "filters": {"type": "array", "items": {"type": "object"}}, "sort": {"type": "string"}, "direction": {"type": "string", "enum": ["asc", "desc"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}})}, _explorer),
]

for definition, _handler in TOOLS:
    definition["annotations"] = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOL_MAP = {definition["name"]: handler for definition, handler in TOOLS}


def _result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(message: dict) -> dict | None:
    """Handle one MCP JSON-RPC message for stdio or Streamable HTTP."""
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL_VERSION} else PROTOCOL_VERSION
        return _result(request_id, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO, "instructions": "Use these read-only tools to answer questions about the connected marketing and CRM data."})
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [definition for definition, _ in TOOLS]})
    if method == "tools/call":
        name = str(params.get("name") or "")
        handler = TOOL_MAP.get(name)
        if not handler:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Tool arguments must be an object")
        try:
            data = handler(arguments)
            text = json.dumps(data, default=str, separators=(",", ":"))
            return _result(request_id, {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": False})
        except (TypeError, ValueError) as exc:
            return _result(request_id, {"content": [{"type": "text", "text": f"Invalid request: {exc}"}], "isError": True})
        except Exception as exc:  # keep protocol errors concise; server logs stay local
            return _result(request_id, {"content": [{"type": "text", "text": f"Portal query failed: {type(exc).__name__}: {exc}"}], "isError": True})
        finally:
            close_connection()
    return _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    """Run the local stdio transport used by Claude Desktop and similar hosts."""
    init_db()
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            response = handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, default=str, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps(_error(None, -32700, f"Parse error: {exc}"), separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
