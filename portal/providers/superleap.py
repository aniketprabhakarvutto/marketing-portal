"""Direct, read-only Superleap CRM connector."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..config import APP
from ..db import query
from ..httpclient import HttpError, get, post
from .base import Connection, Field, Provider
from .shape import build_row, normalise_channel

API_ROOT = "https://app.superleap.com/api/v1"
ALLOWED_HOSTS = ("app.superleap.com",)
DEFAULT_FIELD_MAP = {
    "external_id": "id",
    "created_date": "created_at",
    "utm_source": "vutto_source",
    "utm_medium": "lead_type",
    "utm_campaign": "utm_campaign",
    "lead_type": "lead_category",
    "city": "customer_city",
    "store": "sales_store",
    "stage": "stage1",
}

SUPPLY_COMPLETED_STAGES = {
    "video inspection done", "photo inspection done", "inspection done",
    "deal done", "procured",
}
SUPPLY_BOOKED_STAGES = SUPPLY_COMPLETED_STAGES | {
    "store inspection planned", "at home inspection planned",
    "video inspection planned", "photo inspection planned",
    "unmanned procurement planned", "un manned procurement planned",
    "store visit planned",
}
SOURCE_ONLY_PAID_CHANNELS = {"google": "google_ads", "facebook": "meta_ads"}
VUTTO_SOURCE_CHANNELS = {
    "gmb": "gmb", "whatsapp": "whatsapp", "influencer": "influencer",
    "cross_funnel": "cross_funnel", "flipkart": "flipkart", "olx": "olx",
    "store": "store", "others": "others", "superleap": "others",
}


class SuperleapCRMProvider(Provider):
    id = "superleap_crm"
    label = "Superleap CRM"
    category = "crm"
    table = "crm_leads"
    docs_url = "https://docs.superleap.com/api-reference/appflow/get_records"
    summary = "Import opportunities and campaign-attributed leads directly from Superleap."
    fields = (
        Field("api_key", "Superleap API key", secret=True,
              help="Paste the full key from Superleap Workspace → Settings → API Access. A leading 'Bearer ' is accepted. This connector only verifies access and reads CRM records."),
        Field("object_slug", "CRM object", default="opportunity",
              help="The opportunity object is the Vutto demand and supply funnel. Other objects do not map to this CRM report."),
        Field("field_map", "Field mapping (advanced)", required=False, kind="text",
              default=json.dumps(DEFAULT_FIELD_MAP, indent=2),
              help="Optional JSON mapping from portal field names to Superleap field slugs. Leave unchanged for the Vutto Opportunity schema."),
        Field("revenue_field", "CRM revenue field", required=False,
              help="Optional Superleap field slug containing actual booked revenue. Leave blank when CRM does not store recognized revenue."),
        Field("initial_sync_from", "Initial history start date", required=False, kind="date",
              help="Optional start date for the first import. Leave blank to import all available opportunity history."),
    )

    @staticmethod
    def _request(method: str, path: str, conn: Connection, *, body: dict | None = None) -> dict:
        token = str(conn.get("api_key") or "").strip()
        # Superleap's docs require an Authorization Bearer header. Accept both
        # the raw key copied from API Access and a complete header value so a
        # pasted "Bearer <key>" does not become "Bearer Bearer <key>".
        if token.lower().startswith("authorization:"):
            token = token.split(":", 1)[1].strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise RuntimeError("Paste the full Superleap API key from Workspace → Settings → API Access.")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{API_ROOT}{path}"
        kwargs = {"headers": headers, "public_only": True, "allowed_hosts": ALLOWED_HOSTS}
        try:
            response = (get(url, **kwargs) if method == "GET"
                        else post(url, json_body=body or {}, **kwargs))
        except HttpError as exc:
            if exc.status == 401:
                raise RuntimeError(
                    "Superleap rejected this API key (401). Create or copy an active key from "
                    "Workspace → Settings → API Access, then test again. The portal adds the Bearer prefix automatically."
                ) from None
            raise
        if not isinstance(response, dict):
            raise RuntimeError("Superleap returned an unexpected response. Check the API key and workspace access.")
        if response.get("success") is False or response.get("error"):
            detail = response.get("error") or response.get("message") or "request rejected"
            raise RuntimeError(f"Superleap API rejected the request: {str(detail)[:350]}")
        return response

    @staticmethod
    def _data(response: dict) -> Any:
        return response.get("data", response)

    @staticmethod
    def _records_page(response: dict, *, allow_terminal_null: bool = False) -> tuple[list, str | None]:
        """Read the records page from Superleap's supported response envelopes.

        Appflow responses may wrap the page in ``data``/``result`` and return
        rows as ``records`` or ``items``. Keep this tolerant of both cursor
        spellings while rejecting unknown shapes with a safe diagnostic.
        Superleap sometimes uses records=null to terminate a cursor chain,
        even though its documentation specifies an array.
        """
        payload: Any = response
        for key in ("data", "result", "payload"):
            if isinstance(payload, dict) and key in payload:
                payload = payload[key]
                break

        cursor = None
        if isinstance(payload, list):
            return payload, None
        if isinstance(payload, dict):
            if "records" in payload and payload.get("records") is None:
                if (allow_terminal_null and response.get("success") is True
                        and "next_token" in payload and payload["next_token"] in (None, "")):
                    return [], None
                cursor_state = "with a pagination token" if payload.get("next_token") else "without a pagination token"
                raise RuntimeError(
                    f"Superleap accepted the records request but returned records=null {cursor_state}. "
                    "Its API documents records as an array. Confirm this API key can read records from the "
                    "opportunity object, then ask Superleap support to check the Appflow response. No CRM rows were imported."
                )
            records = next((payload[key] for key in ("records", "items", "results")
                            if isinstance(payload.get(key), list)), None)
            cursor = next((payload[key] for key in ("next_token", "nextToken", "next_cursor", "nextCursor")
                           if payload.get(key)), None)
            page = payload.get("pagination")
            if not cursor and isinstance(page, dict):
                cursor = next((page[key] for key in ("next_token", "nextToken", "next_cursor", "nextCursor")
                               if page.get(key)), None)
            if records is not None:
                return records, str(cursor) if cursor else None

        shape = type(payload).__name__
        keys = ", ".join(sorted(str(key) for key in payload.keys())) if isinstance(payload, dict) else "none"
        value_types = "; ".join(
            f"{key}:{type(value).__name__}" for key, value in sorted(payload.items())
        ) if isinstance(payload, dict) else "none"
        raise RuntimeError(
            f"Superleap returned an unsupported records page (payload type: {shape}; keys: {keys}; "
            f"value types: {value_types}). "
            "Check the Appflow API response format and CRM object permissions."
        )

    @staticmethod
    def _field_map(conn: Connection) -> dict[str, str]:
        raw = conn.get("field_map")
        if not raw:
            return dict(DEFAULT_FIELD_MAP)
        try:
            mapping = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError("Field mapping must be a valid JSON object.") from None
        if not isinstance(mapping, dict):
            raise RuntimeError("Field mapping must be a JSON object of portal field to Superleap field.")
        allowed = {"external_id", "created_date", "utm_source", "utm_medium", "utm_campaign",
                   "channel", "lead_type", "city", "store", "stage", "is_contacted",
                   "is_qualified", "is_visit_booked", "is_visit_done", "is_sale", "revenue"}
        unknown = sorted(set(mapping) - allowed)
        if unknown:
            raise RuntimeError("Unknown portal fields in field mapping: " + ", ".join(unknown))
        return {str(key): str(value).strip() for key, value in mapping.items() if str(value).strip()}

    @staticmethod
    def _object_slug(conn: Connection) -> str:
        slug = str(conn.get("object_slug") or "opportunity").strip()
        if not slug or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in slug):
            raise RuntimeError("CRM object must be a valid Superleap object slug.")
        return slug

    @staticmethod
    def _to_ist_date(value: Any) -> str:
        if value in (None, ""):
            return ""
        zone = ZoneInfo(APP.timezone)
        if isinstance(value, (int, float)):
            seconds = float(value) / 1000 if float(value) > 1e11 else float(value)
            try:
                return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(zone).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return ""
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(text[:10]).isoformat()
            except ValueError:
                return ""
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(zone).date().isoformat()

    @staticmethod
    def _truthy(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value != 0)
        return int(str(value or "").strip().lower() in {
            "1", "true", "yes", "y", "qualified", "valid", "eligible", "won", "closed won",
        })

    @classmethod
    def _normalise(cls, conn: Connection, record: dict, mapping: dict[str, str]) -> dict:
        row = build_row(record, "crm_leads", mapping)
        row["created_date"] = cls._to_ist_date(record.get(mapping.get("created_date", "created_at")))

        side = str(row.get("lead_type") or "").strip().lower()
        row["lead_type"] = {"demand": "buy", "supply": "sell"}.get(side, side)

        stage = re.sub(r"[\s_-]+", " ", str(record.get(mapping.get("stage", "stage1")) or "").strip().lower())
        if "is_contacted" not in mapping:
            row["is_contacted"] = int(bool(stage) and not any(
                marker in stage for marker in ("not assigned", "not attempted", "not contacted", "not connected")))
        # Superleap's documented Opportunity pipeline has no supported
        # qualification field. Stage1 is the authoritative funnel state.
        # Do not suppress a contacted supply lead using a retired custom field.
        if "is_visit_booked" not in mapping:
            row["is_visit_booked"] = int(
                stage in {"store visit planned", "store visit done", "sales done"}
                if side == "buy" else stage in SUPPLY_BOOKED_STAGES
                if side == "sell" else any(marker in stage for marker in (
                    "store visit planned", "inspection planned", "video inspection planned",
                    "photo inspection planned", "at home inspection planned", "visit booked")))
        if "is_visit_done" not in mapping:
            row["is_visit_done"] = int(
                stage in {"store visit done", "sales done"}
                if side == "buy" else stage in SUPPLY_COMPLETED_STAGES
                if side == "sell" else any(marker in stage for marker in (
                    "store visit done", "inspection done", "visit completed")))
        if "is_sale" not in mapping:
            row["is_sale"] = int("sales done" in stage or "procured" in stage)
        if not row.get("channel"):
            source = str(row.get("utm_source") or "").strip().lower()
            medium = str(row.get("utm_medium") or "").strip()
            # When Vutto's source is unclassified, the original UTM tags can
            # identify paid traffic. Require both source and medium rather than
            # inferring an ad platform from a campaign name or a bare source.
            raw_source = str(record.get("utm_source") or "").strip()
            raw_medium = str(record.get("utm_medium") or "").strip()
            tagged_channel = normalise_channel(raw_source, raw_medium)
            if (mapping.get("utm_source") == "vutto_source"
                    and source in {"", "other", "others"}
                    and medium.lower() == "paid"
                    and raw_medium.lower() in {"paid", "cpc", "ppc", "paid_social", "paidsocial"}
                    and tagged_channel in {"google_ads", "meta_ads"}):
                row["channel"] = tagged_channel
            # Vutto's named source is more specific than its Paid/Organic lead
            # type. Use the existing Vutto source categories before generic
            # UTM rules, which would treat every Organic lead as search.
            row["channel"] = row.get("channel") or (SOURCE_ONLY_PAID_CHANNELS[source]
                              if not medium and source in SOURCE_ONLY_PAID_CHANNELS
                              else VUTTO_SOURCE_CHANNELS.get(source)
                              or normalise_channel(source, medium))
        return row

    def _verify(self, conn: Connection) -> None:
        response = self._request("GET", "/appflow/verify/", conn)
        if response.get("status") != "ok" and response.get("success") is not True:
            raise RuntimeError("Superleap did not confirm this API key. Recheck it in Workspace Settings → API Access.")

    def test_connection(self, conn: Connection) -> str:
        """Verify credentials and object access without downloading CRM rows."""
        self._verify(conn)
        slug = self._object_slug(conn)
        response = self._request("POST", "/appflow/objects/list/", conn)
        objects = self._data(response)
        if not isinstance(objects, list):
            raise RuntimeError("Superleap did not return its object list.")
        available = {str(item.get("entity_identifier") or item.get("slug") or "")
                     for item in objects if isinstance(item, dict)}
        if slug not in available:
            raise RuntimeError(f"Superleap key works, but object '{slug}' is not available to this user.")
        return f"Superleap API key verified. The {slug} object is available; no records were changed."

    @staticmethod
    def _since(conn: Connection) -> tuple[str, int] | None:
        """Use the last successful API sync as the incremental updated_at cursor."""
        if conn.id <= 0:
            return None
        runs = query(
            "SELECT finished_at FROM sync_runs WHERE connection_id=? AND connector='superleap_crm' "
            "AND status IN ('ok','empty') AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1",
            (conn.id,),
        )
        if not runs:
            return None
        try:
            timestamp = datetime.fromisoformat(str(runs[0]["finished_at"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        # A short overlap catches writes that landed while the previous cursor was committing.
        return "updated_at", max(0, int((timestamp - timedelta(minutes=10)).timestamp() * 1000))

    @staticmethod
    def _initial_filter(conn: Connection) -> tuple[str, int] | None:
        raw = str(conn.get("initial_sync_from") or "").strip()
        if not raw:
            return None
        try:
            local_day = date.fromisoformat(raw)
        except ValueError:
            raise RuntimeError("Initial history start date must use YYYY-MM-DD.") from None
        local_midnight = datetime.combine(local_day, time.min, tzinfo=ZoneInfo(APP.timezone))
        return "created_at", int(local_midnight.timestamp() * 1000)

    def fetch(self, conn: Connection, start: date, end: date) -> list[dict]:
        self._verify(conn)
        slug = self._object_slug(conn)
        mapping = self._field_map(conn)
        if conn.get("revenue_field"):
            mapping["revenue"] = str(conn.get("revenue_field")).strip()

        schema_response = self._request("GET", f"/appflow/objects/{quote(slug, safe='')}", conn)
        schema = self._data(schema_response)
        fields = schema.get("fields") if isinstance(schema, dict) else None
        if not isinstance(fields, list):
            raise RuntimeError(f"Superleap did not return a schema for object '{slug}'.")
        available = {str(item.get("field_name") or item.get("slug") or "")
                     for item in fields if isinstance(item, dict)}
        selected = list(dict.fromkeys([*mapping.values(), "updated_at"]))
        if mapping.get("utm_source") == "vutto_source":
            selected.extend(field for field in ("utm_source", "utm_medium")
                            if field in available and field not in selected)
        missing = [name for name in selected if name and name not in available]
        if missing:
            raise RuntimeError("The selected Superleap object is missing mapped fields: " + ", ".join(missing))
        if not mapping.get("external_id") or not mapping.get("created_date") or not mapping.get("stage"):
            raise RuntimeError("Field mapping must include external_id, created_date, and stage for CRM reporting.")

        cursor = self._since(conn) or self._initial_filter(conn)
        conditions = []
        if cursor:
            conditions.append({"field": cursor[0], "operator": "gte", "value": cursor[1]})
        body_query: dict[str, Any] = {"fields": selected}
        if conditions:
            body_query["filter"] = {"and": conditions}

        rows: list[dict] = []
        next_token: str | None = None
        for _ in range(1000):
            response = self._request("POST", f"/appflow/objects/{quote(slug, safe='')}/records", conn,
                                     body={"query": body_query, "next_token": next_token})
            records, next_token = self._records_page(
                response, allow_terminal_null=bool(next_token) and bool(rows)
            )
            for record in records:
                if not isinstance(record, dict):
                    continue
                row = self._normalise(conn, record, mapping)
                if row.get("external_id") and row.get("created_date"):
                    rows.append(row)
            if not next_token:
                return rows
        raise RuntimeError("Superleap pagination exceeded 1,000 pages. Set an initial history start date and retry.")
