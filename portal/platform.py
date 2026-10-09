"""Organisation features: governance, explorer, dashboards, jobs and audit."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import APP, ROOT
from . import auth, db
from .db import backup_database, execute, query, scalar


METRICS = {
    "spend": ("SUM(spend)", "Spend", "currency"),
    "impressions": ("SUM(impressions)", "Impressions", "number"),
    "clicks": ("SUM(clicks)", "Clicks", "number"),
    "conversions": ("SUM(conversions)", "Platform conversions", "number"),
    "conversion_value": ("SUM(conversion_value)", "Conversion value", "currency"),
    "ctr": ("CASE WHEN SUM(impressions)>0 THEN 100.0*SUM(clicks)/SUM(impressions) END", "CTR", "percent"),
    "cpc": ("CASE WHEN SUM(clicks)>0 THEN SUM(spend)/SUM(clicks) END", "CPC", "currency"),
    "cpa": ("CASE WHEN SUM(conversions)>0 THEN SUM(spend)/SUM(conversions) END", "CPA", "currency"),
}
DIMENSIONS = {
    "date": "date", "platform": "source", "account": "account_name",
    "campaign": "campaign_name", "campaign_type": "channel",
}


def _json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def audit(action: str, object_type: str = "", object_id: object = "", detail: dict | None = None,
          actor: str | None = None) -> None:
    db.audit(action, object_type, object_id, detail, actor)


def audit_entries(limit: int = 100) -> list[dict[str, Any]]:
    return [{**dict(r), "detail": _json(r["detail"], {})} for r in query(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))]


def seed_governance() -> None:
    definitions = [
        ("spend", "Ad spend", "Amount charged by advertising platforms.", "SUM(spend)", "currency"),
        ("leads", "CRM leads", "Leads created in the CRM during the selected period.", "SUM(leads)", "number"),
        ("sales", "Completed outcomes", "CRM leads marked won, sold, or procured.", "SUM(won)", "number"),
        ("cpl", "Cost per lead", "Advertising spend divided by CRM leads.", "spend / leads", "currency"),
        ("cac", "Customer acquisition cost", "Advertising spend divided by completed outcomes.", "spend / sales", "currency"),
        ("roas", "Return on ad spend", "CRM revenue divided by advertising spend; unavailable until revenue is mapped.", "revenue / spend", "ratio"),
    ]
    for row in definitions:
        execute("INSERT OR IGNORE INTO metric_definitions(key,label,description,formula,format) VALUES(?,?,?,?,?)", row)


def governance() -> dict[str, Any]:
    seed_governance()
    return {
        "metrics": [dict(r) for r in query("SELECT * FROM metric_definitions WHERE enabled=1 ORDER BY label")],
        "users": auth.users(),
        "rules": [dict(r) for r in query("SELECT * FROM transformation_rules ORDER BY priority,id")],
    }


def save_user(body: dict[str, Any]) -> dict[str, Any]:
    return auth.save_user(body, actor=db.audit_actor() or "system")


def explorer_schema() -> dict[str, Any]:
    return {
        "dataset": "Paid campaign performance",
        "dimensions": [{"id": k, "label": k.replace("_", " ").title()} for k in DIMENSIONS],
        "metrics": [{"id": k, "label": spec[1], "format": spec[2]} for k, spec in METRICS.items()],
        "operators": ["contains", "equals", "starts_with", "ends_with"],
    }


def explore(body: dict[str, Any]) -> dict[str, Any]:
    from .api import date_range

    dimensions = [x for x in body.get("dimensions", ["campaign"]) if x in DIMENSIONS][:4] or ["campaign"]
    metrics = [x for x in body.get("metrics", ["spend", "clicks"]) if x in METRICS][:8] or ["spend"]
    start, end = date_range(body.get("start"), body.get("end"))
    financial_metrics = [metric for metric in metrics if METRICS[metric][2] == "currency"]
    rules = [dict(r) for r in query(
        "SELECT field,match_type,pattern,output FROM transformation_rules "
        "WHERE enabled=1 ORDER BY priority,id")]

    def apply_rules(field: str, value: object, encoded_rules: str) -> str:
        original = str(value or "")
        try:
            active_rules = json.loads(encoded_rules)
        except (TypeError, json.JSONDecodeError):
            return original
        for rule in active_rules:
            if rule.get("field") != field:
                continue
            pattern = str(rule.get("pattern") or "")
            candidate = original.lower()
            try:
                matched = (
                    (rule.get("match_type") == "equals" and candidate == pattern.lower())
                    or (rule.get("match_type") == "contains" and pattern.lower() in candidate)
                    or (rule.get("match_type") == "regex" and bool(re.search(pattern, original, re.I)))
                )
            except re.error:
                matched = False
            if matched:
                return str(rule.get("output") or original)
        return original

    transform_fields = {"platform": "source", "campaign": "campaign_name",
                        "campaign_type": "channel"}
    encoded_rules = json.dumps(rules)
    db.connect().create_function("portal_transform", 3, apply_rules)
    select: list[str] = []
    group: list[str] = []
    select_params: list[Any] = []
    for dimension in dimensions:
        field = DIMENSIONS[dimension]
        transform_field = transform_fields.get(dimension)
        if transform_field:
            select.append(f"portal_transform(?,{field},?) AS {dimension}")
            select_params.extend((transform_field, encoded_rules))
            group.append(dimension)
        else:
            select.append(f"{field} AS {dimension}")
            group.append(field)
    result_dimensions = list(dimensions)
    if financial_metrics:
        # Account currencies cannot be added without an approved FX policy.
        # Include currency even when all four user-selected dimensions are used.
        select.append("UPPER(TRIM(COALESCE(currency,''))) AS currency")
        group.append("UPPER(TRIM(COALESCE(currency,'')))")
        result_dimensions.append("currency")
    for metric in metrics:
        expression = METRICS[metric][0]
        if metric in financial_metrics:
            expression = f"CASE WHEN TRIM(COALESCE(currency,'')) != '' THEN {expression} END"
        select.append(f"{expression} AS {metric}")
    where = ["date BETWEEN ? AND ?"]
    params: list[Any] = [start, end]
    for item in body.get("filters", [])[:10]:
        field = DIMENSIONS.get(str(item.get("field") or ""))
        value = str(item.get("value") or "")
        operator = item.get("operator")
        if not field or not value:
            continue
        if operator == "equals":
            where.append(f"{field} = ?"); params.append(value)
        elif operator == "starts_with":
            where.append(f"{field} LIKE ?"); params.append(value + "%")
        elif operator == "ends_with":
            where.append(f"{field} LIKE ?"); params.append("%" + value)
        else:
            where.append(f"{field} LIKE ?"); params.append("%" + value + "%")
    sort = str(body.get("sort") or metrics[0])
    sort = sort if sort in metrics + dimensions else metrics[0]
    direction = "ASC" if str(body.get("direction")).lower() == "asc" else "DESC"
    limit = max(1, min(int(body.get("limit") or 100), 500))
    sql = f"SELECT {','.join(select)} FROM paid_metrics WHERE {' AND '.join(where)} GROUP BY {','.join(group)} ORDER BY {sort} {direction} LIMIT ?"
    params = select_params + params + [limit]
    rows = [dict(r) for r in query(sql, params)]
    warnings = []
    if financial_metrics:
        if any(not row["currency"] for row in rows):
            warnings.append("Some accounts have no currency. Their counts are shown, but monetary metrics are unavailable until currency is configured.")
        if len({row["currency"] for row in rows if row["currency"]}) > 1:
            warnings.append("Monetary metrics are grouped by account currency. No currency conversion is applied.")
    return {"columns": result_dimensions + metrics, "rows": rows, "count": len(rows),
            "warnings": warnings, "range": {"start": start, "end": end},
            "query": {"dimensions": result_dimensions, "metrics": metrics}}


def list_objects(table: str) -> list[dict[str, Any]]:
    allowed = {"dashboards", "data_destinations", "scheduled_jobs", "saved_explorer_views", "report_annotations"}
    if table not in allowed:
        raise ValueError("Unsupported workspace object")
    rows = []
    for row in query(f"SELECT * FROM {table} ORDER BY id DESC"):
        item = dict(row)
        for key in ("layout", "config", "definition"):
            if key in item:
                item[key] = _json(item[key], [] if key == "layout" else {})
        if table == "data_destinations" and item.get("kind") == "email":
            config = item.get("config") or {}
            from . import connections
            from .providers import REGISTRY
            sender_ready = any(
                REGISTRY["gmail"].ready(conn)
                for conn in connections.all_connections(provider="gmail", enabled_only=True)
            )
            recipient_ready = bool(re.fullmatch(
                r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", str(config.get("recipient") or "")))
            item["status"] = "ready" if sender_ready and recipient_ready else (
                "needs Gmail access" if not sender_ready else "needs recipient email")
        elif table == "data_destinations" and item.get("kind") not in {"download", "folder"}:
            # Keep legacy records visible without calling unsupported delivery ready.
            item["status"] = "not_available"
        rows.append(item)
    return rows


def artifact_for_run(run_id: int) -> Path:
    """Return a completed local artifact only when it is portal-owned."""
    rows = query("SELECT artifact FROM job_runs WHERE id=? AND status='ok'", (run_id,))
    if not rows or not rows[0]["artifact"]:
        raise ValueError("Download is not available for this job run")
    artifact = Path(rows[0]["artifact"]).resolve()
    allowed = [(ROOT / "reports").resolve(), (ROOT / "backups").resolve()]
    if not artifact.is_file() or not any(artifact == folder or folder in artifact.parents for folder in allowed):
        raise ValueError("Job artifact is unavailable")
    return artifact


def save_object(table: str, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("Name is required")
    if table in {"dashboards", "saved_explorer_views", "data_destinations", "scheduled_jobs"}:
        existing = query(f"SELECT name FROM {table} WHERE lower(name)=lower(?) LIMIT 1", (name,))
        if existing:
            name = str(existing[0]["name"])
    if table == "dashboards":
        payload = (name, str(body.get("description") or ""), json.dumps(body.get("layout") or []), int(bool(body.get("shared"))))
        execute("INSERT INTO dashboards(name,description,layout,shared) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description,layout=excluded.layout,shared=excluded.shared,updated_at=datetime('now')", payload)
    elif table == "saved_explorer_views":
        execute("INSERT INTO saved_explorer_views(name,definition) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET definition=excluded.definition,updated_at=datetime('now')", (name, json.dumps(body.get("definition") or {})))
    elif table == "data_destinations":
        kind = str(body.get("kind") or "download")
        if kind in {"webhook", "google_sheets", "warehouse"}:
            raise ValueError(f"{kind.replace('_', ' ').title()} delivery is not implemented yet.")
        if kind not in {"download", "folder", "email"}:
            raise ValueError("Unsupported destination type")
        config = body.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError("Destination settings must be an object")
        if kind == "email":
            recipient = str(config.get("recipient") or "").strip()
            if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", recipient):
                raise ValueError("Enter a valid recipient email address for this destination.")
        # Only local delivery is usable without an external credential or endpoint.
        status = "ready" if kind in {"download", "folder"} else "needs configuration"
        execute("INSERT INTO data_destinations(name,kind,config,enabled,status) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,config=excluded.config,enabled=excluded.enabled,status=excluded.status,updated_at=datetime('now')", (name, kind, json.dumps(config), int(body.get("enabled", True)), status))
    elif table == "scheduled_jobs":
        job_type = str(body.get("job_type") or "report")
        execute("INSERT INTO scheduled_jobs(name,job_type,schedule,run_at,definition,enabled) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET job_type=excluded.job_type,schedule=excluded.schedule,run_at=excluded.run_at,definition=excluded.definition,enabled=excluded.enabled,updated_at=datetime('now')", (name, job_type, str(body.get("schedule") or "daily"), str(body.get("run_at") or "09:00"), json.dumps(body.get("definition") or {}), int(body.get("enabled", True))))
    else:
        raise ValueError("This object cannot be saved here")
    object_id = scalar(f"SELECT id FROM {table} WHERE name=?", (name,))
    audit("save", table, object_id, {"name": name})
    return next(item for item in list_objects(table) if item["id"] == object_id)


def delete_dashboard(dashboard_id: int) -> dict[str, int]:
    rows = query("SELECT name FROM dashboards WHERE id=?", (dashboard_id,))
    if not rows:
        raise KeyError(f"dashboard {dashboard_id} was not found")
    name = str(rows[0]["name"])
    execute("DELETE FROM dashboards WHERE id=?", (dashboard_id,))
    audit("delete", "dashboard", dashboard_id, {"name": name})
    return {"deleted": dashboard_id}


def delete_object(table: str, object_id: int) -> dict[str, int]:
    """Remove a saved configuration without touching source facts."""
    allowed = {"saved_explorer_views", "transformation_rules", "data_destinations", "scheduled_jobs"}
    if table not in allowed:
        raise ValueError("Unsupported workspace object")
    rows = query(f"SELECT * FROM {table} WHERE id=?", (object_id,))
    if not rows:
        raise KeyError(f"{table} item {object_id} was not found")
    name = str(rows[0]["name"])
    if table == "data_destinations":
        for job in query("SELECT id,definition FROM scheduled_jobs WHERE enabled=1"):
            if str(_json(job["definition"], {}).get("destination_id") or "") == str(object_id):
                raise ValueError("This destination is used by a scheduled job. Remove that job first.")
    detail = {"name": name}
    if table == "scheduled_jobs":
        detail["run_count"] = scalar("SELECT COUNT(*) FROM job_runs WHERE job_id=?", (object_id,))
    execute(f"DELETE FROM {table} WHERE id=?", (object_id,))
    audit("delete", table, object_id, detail)
    return {"deleted": object_id}


def save_rule(body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    field = str(body.get("field") or "channel").strip()
    pattern = str(body.get("pattern") or "").strip()
    output = str(body.get("output") or "").strip()
    match_type = str(body.get("match_type") or "contains")
    if not all((name, field, pattern, output)):
        raise ValueError("Name, field, pattern, and output are required")
    if match_type == "regex":
        re.compile(pattern)
    existing = query("SELECT id FROM transformation_rules WHERE lower(name)=lower(?) ORDER BY id LIMIT 1", (name,))
    if existing:
        rule_id = int(existing[0]["id"])
        execute("UPDATE transformation_rules SET name=?,field=?,match_type=?,pattern=?,output=?,priority=?,enabled=?,updated_at=datetime('now') WHERE id=?",
                (name, field, match_type, pattern, output, int(body.get("priority") or 100), int(body.get("enabled", True)), rule_id))
    else:
        execute("INSERT INTO transformation_rules(name,field,match_type,pattern,output,priority,enabled) VALUES(?,?,?,?,?,?,?)",
                (name, field, match_type, pattern, output, int(body.get("priority") or 100), int(body.get("enabled", True))))
        rule_id = scalar("SELECT last_insert_rowid()")
    audit("update" if existing else "create", "transformation_rule", rule_id, {"name": name})
    return dict(query("SELECT * FROM transformation_rules WHERE id=?", (rule_id,))[0])


def transform(field: str, value: str | None) -> str:
    """Apply the first enabled, governed rule to a reporting value."""
    original = str(value or "")
    for rule in query("SELECT match_type,pattern,output FROM transformation_rules WHERE enabled=1 AND field=? ORDER BY priority,id", (field,)):
        candidate, pattern = original.lower(), str(rule["pattern"])
        try:
            matched = ((rule["match_type"] == "equals" and candidate == pattern.lower()) or
                       (rule["match_type"] == "contains" and pattern.lower() in candidate) or
                       (rule["match_type"] == "regex" and bool(re.search(pattern, original, re.I))) )
        except re.error:
            matched = False
        if matched:
            return str(rule["output"])
    return original


def planning(start: str | None = None, end: str | None = None) -> dict[str, Any]:
    from . import api
    overview = api.overview(start, end)
    current = overview["current"]
    coverage = overview["coverage"]
    spend = current.get("spend") or 0
    leads = current.get("leads") or 0
    sales = current.get("sales") or 0
    days = max(1, coverage["paid"]["days"] or 1)
    return {
        "daily_spend": round(spend / days, 2),
        "forecast_30_day_spend": round(spend / days * 30, 2),
        "forecast_30_day_leads": round(leads / max(1, coverage["crm"]["days"]) * 30),
        "forecast_30_day_sales": round(sales / max(1, coverage["crm"]["days"]) * 30),
        "cpl": current.get("cpl"), "cac": current.get("cac"),
        "model": "run-rate", "warning": "Forecast uses the observed daily average and is not a causal prediction.",
        "attribution": {"model": "CRM campaign mapping", "status": "campaign_level_only",
                        "note": "Campaign-level CRM mapping only. Multi-touch attribution needs event-level journey data."},
    }


def _job_due(job: dict[str, Any], today: str, now_time: str) -> bool:
    if job.get("last_run_at"):
        try:
            from zoneinfo import ZoneInfo
            local_zone = ZoneInfo(APP.timezone)
            last_run = datetime.fromisoformat(str(job["last_run_at"]))
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            if last_run.astimezone(local_zone).date().isoformat() == today:
                return False
        except (ValueError, KeyError):
            pass
    if now_time < str(job.get("run_at") or "00:00"):
        return False
    schedule = job.get("schedule") or "daily"
    if schedule == "weekdays" and date.fromisoformat(today).weekday() >= 5:
        return False
    if schedule == "weekly" and date.fromisoformat(today).weekday() != 0:
        return False
    if schedule == "monthly" and date.fromisoformat(today).day != 1:
        return False
    return True


def _report_folder(folder: object) -> Path:
    """Resolve report folders inside the portal-owned reports directory only."""
    root = (ROOT / "reports").resolve()
    requested = str(folder or "").strip()
    target = (root / requested).resolve() if requested else root
    if target != root and root not in target.parents:
        raise ValueError("Report folder must stay inside the portal reports directory")
    return target


def run_job(job_id: int, *, scheduled_for: tuple[str, str] | None = None) -> dict[str, Any]:
    # Claim the job atomically before any delivery or report generation. The
    # HTTP worker and scheduler use separate database connections.
    with db._write_lock:
        conn = db.connect()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError("Scheduled job was not found")
            job = dict(row)
            if scheduled_for and (not job["enabled"] or not _job_due(job, *scheduled_for)):
                return {"id":job_id,"status":"skipped","message":"Job is no longer due","artifact":""}
            running = conn.execute("SELECT id FROM job_runs WHERE job_id=? AND status='running' LIMIT 1", (job_id,)).fetchone()
            if running:
                if scheduled_for:
                    return {"id":job_id,"status":"skipped","message":"Job is already running","artifact":""}
                raise ValueError("This job is already running. Wait for its current run to finish.")
            cursor = conn.execute("INSERT INTO job_runs(job_id,status,message) VALUES(?,?,?)", (job_id, "running", "Starting"))
            run_id = int(cursor.lastrowid)
            if scheduled_for:
                # An interrupted email may already have been accepted by its
                # provider. Record the attempt before sending, not afterward.
                conn.execute("UPDATE scheduled_jobs SET last_run_at=datetime('now'),updated_at=datetime('now') WHERE id=?", (job_id,))
    try:
        definition = _json(job.get("definition"), {})
        artifact = ""
        if job["job_type"] == "sync":
            from .sync import run_all
            results = run_all()
            if not results:
                raise ValueError("No enabled data connections are available to sync.")
            incomplete = [item for item in results if item.get("status") != "ok"]
            if incomplete:
                failed_names = ", ".join(str(item.get("name") or item.get("provider") or "Unknown source")
                                         for item in incomplete[:5])
                suffix = f" and {len(incomplete) - 5} more" if len(incomplete) > 5 else ""
                raise ValueError(f"{len(incomplete)} of {len(results)} source syncs did not complete: "
                                 f"{failed_names}{suffix}. Check Connections and sync history.")
            rows_written = sum(int(item.get("rows") or 0) for item in results)
            message = f"Synced {len(results)} source(s), {rows_written:,} rows"
        elif job["job_type"] in {"report", "snapshot"}:
            from .automation import build_csv
            from .api import date_range
            report_start, report_end = date_range(definition.get("start"), definition.get("end"))
            filename, content = build_csv(report_start, report_end)
            destination_id = definition.get("destination_id")
            destination = None
            if destination_id:
                found = query("SELECT * FROM data_destinations WHERE id=? AND enabled=1", (destination_id,))
                if not found:
                    raise ValueError("Selected destination is unavailable")
                destination = dict(found[0])
                if destination["kind"] not in {"download", "folder", "email"}:
                    raise ValueError(f"{destination['name']} is not supported for report delivery yet")
            destination_config = _json(destination.get("config"), {}) if destination else {}
            if destination and destination["kind"] == "email":
                from . import connections
                from .providers import REGISTRY
                senders = connections.all_connections(provider="gmail", enabled_only=True)
                sender = next((conn for conn in senders if REGISTRY["gmail"].ready(conn)), None)
                if not sender:
                    raise ValueError("Connect and authorise a send-only Gmail account in Profile & Settings first.")
                recipient = str(destination_config.get("recipient") or "").strip()
                if not recipient:
                    raise ValueError(f"{destination['name']} needs a report recipient email address.")
                REGISTRY["gmail"].send_report(
                    sender, recipient, filename, content,
                    report_start, report_end)
                message = f"Emailed {filename} to {recipient}"
            else:
                output = _report_folder(definition.get("folder") or destination_config.get("folder"))
                output.mkdir(parents=True, exist_ok=True)
                report_name = Path(filename)
                target = output / f"{report_name.stem}-run-{run_id}{report_name.suffix}"
                with target.open("xb") as report:
                    report.write(content)
                artifact, message = str(target), f"Created {target.name}" + (f" in {destination['name']}" if destination else "")
        elif job["job_type"] == "quality":
            from .api import data_quality
            result = data_quality(definition.get("start"), definition.get("end"))
            message = f"Quality check: {result['summary']['pass']} passed, {result['summary']['warn']} warnings, {result['summary']['fail']} failed"
        elif job["job_type"] == "backup":
            backup_dir = ROOT / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            from .api import portal_today
            target = backup_dir / f"portal-{portal_today().isoformat()}-run-{run_id}.db"
            backup_database(str(target))
            artifact, message = str(target), f"Created database backup {target.name}"
        elif job["job_type"] in {"alert", "webhook"}:
            raise ValueError("This scheduled delivery type is not implemented yet. Use a report, quality check, source refresh, or database backup job.")
        else:
            raise ValueError("Unsupported scheduled job type")
        execute("UPDATE job_runs SET status='ok',finished_at=datetime('now'),message=?,artifact=? WHERE id=?", (message, artifact, run_id))
        execute("UPDATE scheduled_jobs SET last_run_at=datetime('now'),updated_at=datetime('now') WHERE id=?", (job_id,))
        audit("run", "scheduled_job", job_id, {"status": "ok", "message": message})
        return {"id": job_id, "status": "ok", "message": message, "artifact": artifact}
    except Exception as exc:
        message = f"Job failed: {exc}" if isinstance(exc, ValueError) else f"Job failed: {type(exc).__name__}"
        execute("UPDATE job_runs SET status='error',finished_at=datetime('now'),message=? WHERE id=?", (message, run_id))
        audit("run", "scheduled_job", job_id, {"status": "error", "message": message})
        raise


def recover_interrupted_jobs() -> int:
    """Close abandoned runs on service startup without retrying delivery blindly."""
    with db._write_lock:
        conn = db.connect()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE scheduled_jobs SET last_run_at=CASE
                WHEN last_run_at IS NULL OR last_run_at < (SELECT MAX(started_at) FROM job_runs WHERE job_id=scheduled_jobs.id AND status='running')
                THEN (SELECT MAX(started_at) FROM job_runs WHERE job_id=scheduled_jobs.id AND status='running')
                ELSE last_run_at END, updated_at=datetime('now')
                WHERE id IN (SELECT job_id FROM job_runs WHERE status='running')""")
            cursor = conn.execute("UPDATE job_runs SET status='error',finished_at=datetime('now'),message=? WHERE status='running'",
                ("Service interrupted this run. Delivery may have completed; check the destination before retrying manually.",))
            count = cursor.rowcount
    return count


def run_due_jobs(today: str, now_time: str) -> list[dict[str, Any]]:
    results = []
    for row in query("SELECT * FROM scheduled_jobs WHERE enabled=1"):
        job = dict(row)
        if _job_due(job, today, now_time):
            try:
                result = run_job(int(job["id"]), scheduled_for=(today, now_time))
                if result["status"] != "skipped":
                    results.append(result)
            except Exception:
                # Record the attempt so the one-minute scheduler does not retry a
                # failed delivery all day. In particular, an email send can fail
                # after acceptance by the provider, so automatic repeated sends
                # would risk duplicates. The operator can still retry manually.
                execute("UPDATE scheduled_jobs SET last_run_at=datetime('now'),"
                        "updated_at=datetime('now') WHERE id=?", (job["id"],))
                failed = query(
                    "SELECT message FROM job_runs WHERE job_id=? "
                    "ORDER BY id DESC LIMIT 1", (job["id"],))
                results.append({
                    "id": int(job["id"]), "status": "error",
                    "message": str(failed[0]["message"]) if failed else "Job failed before a run was recorded",
                    "artifact": "",
                })
    return results
