"""Command line for the Marketing Data Portal.

    serve                       start the portal
    providers                   list every platform that can be connected
    connections                 list saved accounts and their state
    connect <provider> <name>   add or update an account: --set key=value
    disconnect <id>             remove an account (--drop-data to delete its rows)
    sync [--connection ID]      pull data now
    import <table> <csv>        load a CSV extract straight into a fact table
    status                      row counts and connection health
    reset-demo | reset-all      clear sample data, or start over
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from . import api
from . import connections as conns
from .config import APP
from .db import CONFLICT_KEYS, connect, init_db, query, reset_all, scalar, upsert
from .providers import REGISTRY, table_for

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-16s %(message)s",
    datefmt="%H:%M:%S",
)


def _kv(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got: {pair}")
        key, _, value = pair.partition("=")
        out[key.strip()] = value.strip()
    return out


# --------------------------------------------------------------------- commands
def cmd_providers(_args) -> int:
    print(f"{'ID':<16} {'CATEGORY':<11} {'AUTH':<8} PLATFORM")
    for p in REGISTRY.values():
        auth = "oauth2" if p.oauth else "keys"
        print(f"{p.id:<16} {p.category:<11} {auth:<8} {p.label}")
    print("\nFields for one platform:  portal providers --show <id>")
    return 0


def cmd_provider_show(provider_id: str) -> int:
    provider = REGISTRY.get(provider_id)
    if not provider:
        print(f"no such provider: {provider_id}", file=sys.stderr)
        return 2
    print(f"{provider.label}  ({provider.id})")
    print(f"  {provider.summary}")
    if provider.docs_url:
        print(f"  docs: {provider.docs_url}")
    print(f"  writes to: {provider.table}")
    print("  fields:")
    for f in provider.fields:
        flags = []
        if not f.required:
            flags.append("optional")
        if f.secret:
            flags.append("secret")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"    {f.key:<22}{f.label}{suffix}")
        if f.help:
            print(f"    {'':<22}  {f.help}")
    return 0


def cmd_connections(_args) -> int:
    init_db()
    rows = conns.all_connections()
    if not rows:
        print("No accounts connected yet. Add one with:  portal connect <provider> <name>")
        return 0
    print(f"{'ID':<4} {'PROVIDER':<16} {'NAME':<24} STATE")
    for c in rows:
        provider = REGISTRY.get(c.provider)
        if not provider:
            state = "unknown provider"
        elif not c.enabled:
            state = "disabled"
        elif provider.ready(c):
            state = "ready"
        else:
            state = "needs: " + ", ".join(provider.missing(c))
        print(f"{c.id:<4} {c.provider:<16} {c.name:<24} {state}")
    return 0


def cmd_connect(args) -> int:
    init_db()
    provider = REGISTRY.get(args.provider)
    if not provider:
        print(f"no such provider: {args.provider}\nRun 'portal providers' to see the list.",
              file=sys.stderr)
        return 2

    values = _kv(args.set)
    secret_keys = {f.key for f in provider.fields if f.secret}
    config = {k: v for k, v in values.items() if k not in secret_keys}
    secrets = {k: v for k, v in values.items() if k in secret_keys}

    existing = next((c for c in conns.all_connections(args.provider)
                     if c.name == args.name), None)
    if existing:
        conn = conns.update(existing.id, config=config, secrets=secrets)
        print(f"updated connection {conn.id} ({args.provider} / {args.name})")
    else:
        conn = conns.create(args.provider, args.name, config, secrets)
        print(f"created connection {conn.id} ({args.provider} / {args.name})")

    if not provider.ready(conn):
        print("  still needs: " + ", ".join(provider.missing(conn)))
        if provider.oauth and not conn.get("refresh_token"):
            print(f"  authorise it in the browser: http://localhost:{APP.port}/#connections")
    return 0


def cmd_disconnect(args) -> int:
    init_db()
    conn = conns.get(args.id)
    if not conn:
        print(f"no connection with id {args.id}", file=sys.stderr)
        return 2
    conns.delete(args.id, drop_data=args.drop_data)
    extra = " and its data" if args.drop_data else " (its data was kept)"
    print(f"removed {conn.provider} / {conn.name}{extra}")
    return 0


def cmd_sync(args) -> int:
    from .sync import run_connection

    init_db()
    end = date.fromisoformat(args.end) if args.end else api.portal_today()
    days = max(1, APP.sync_lookback_days if args.days is None else args.days)
    start = (date.fromisoformat(args.start) if args.start
             else end - timedelta(days=days - 1))

    targets = ([conns.get(args.connection)] if args.connection
               else conns.all_connections(enabled_only=True))
    targets = [c for c in targets if c]
    if not targets:
        print("Nothing to sync — no enabled connections.", file=sys.stderr)
        return 1

    failed = 0
    for conn in targets:
        res = run_connection(conn, start, end)
        icon = {"ok": "OK  ", "skipped": "--  ", "error": "FAIL"}.get(res["status"], "?   ")
        print(f"{icon} {conn.provider}/{conn.name:<20} {res['message']}")
        failed += res["status"] == "error"
    return 1 if failed else 0


def cmd_import(args) -> int:
    import csv as _csv

    init_db()
    if args.table not in CONFLICT_KEYS:
        print(f"unknown table: {args.table} (choose from {', '.join(CONFLICT_KEYS)})",
              file=sys.stderr)
        return 2
    path = Path(args.path).expanduser()
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    valid = {r[1] for r in connect().execute(f"PRAGMA table_info({args.table})")}
    valid -= {"id", "ingested_at"}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = _csv.DictReader(fh)
        header = set(reader.fieldnames or [])
        rows = [{k: v for k, v in r.items() if k in valid} for r in reader]
    if not rows:
        print("file has no rows", file=sys.stderr)
        return 1

    missing = set(CONFLICT_KEYS[args.table]) - header
    if missing:
        print(f"CSV is missing key column(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return 2

    written = upsert(args.table, rows)
    print(f"imported {written:,} rows into {args.table}")
    if header - valid:
        print(f"(ignored unrecognised columns: {', '.join(sorted(header - valid))})")
    if args.table == "crm_leads":
        from .rollup import rollup_daily

        dates = sorted(r["created_date"] for r in rows if r.get("created_date"))
        if dates:
            rollup_daily(date.fromisoformat(dates[0]), date.fromisoformat(dates[-1]))
            print("rebuilt the daily CRM rollup for the imported range")
    return 0


def cmd_status(_args) -> int:
    init_db()
    print(f"Database : {APP.db_path}")
    print(f"Portal   : http://localhost:{APP.port}")
    print(f"Demo mode: {APP.demo_mode}\n")

    for table in ("paid_metrics", "web_metrics", "search_metrics",
                  "crm_leads", "crm_daily", "crm_city_daily"):
        print(f"  {table:<16} {scalar(f'SELECT COUNT(*) FROM {table}'):>9,} rows")
    print()

    rows = conns.all_connections()
    if not rows:
        print("  No accounts connected yet.")
        print(f"  Open http://localhost:{APP.port}/#connections to add one.")
        return 0

    for c in rows:
        provider = REGISTRY.get(c.provider)
        label = provider.label if provider else c.provider
        state = "ready" if provider and provider.ready(c) else "not ready"
        print(f"  [{c.id}] {label} / {c.name:<20} {state}")
        if provider and not provider.ready(c):
            print(f"       needs: {', '.join(provider.missing(c))}")
        last = query(
            "SELECT status, started_at, message FROM sync_runs WHERE connection_id = ? "
            "ORDER BY id DESC LIMIT 1", (c.id,)
        )
        if last:
            print(f"       last: {last[0]['status']} at {last[0]['started_at']} "
                  f"— {last[0]['message'][:80]}")
    return 0


def cmd_reset_demo(_args) -> int:
    from .seed import wipe_demo

    init_db()
    print(f"removed {wipe_demo():,} demo rows")
    print("set DEMO_MODE=false in .env so they are not seeded again")
    return 0


def cmd_reset_all(_args) -> int:
    reset_all()
    print("database reset — connections and data both cleared")
    return 0


def cmd_serve(_args) -> int:
    from .server import serve

    serve()
    return 0


# ------------------------------------------------------------------------ parser
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="portal", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("providers", help="platforms that can be connected")
    pv.add_argument("--show", help="show one platform's fields")
    pv.set_defaults(fn=lambda a: cmd_provider_show(a.show) if a.show else cmd_providers(a))

    sub.add_parser("connections", help="saved accounts").set_defaults(fn=cmd_connections)

    cn = sub.add_parser("connect", help="add or update an account")
    cn.add_argument("provider", choices=list(REGISTRY))
    cn.add_argument("name")
    cn.add_argument("--set", action="append", metavar="KEY=VALUE", default=[])
    cn.set_defaults(fn=cmd_connect)

    dc = sub.add_parser("disconnect", help="remove an account")
    dc.add_argument("id", type=int)
    dc.add_argument("--drop-data", action="store_true", help="also delete its rows")
    dc.set_defaults(fn=cmd_disconnect)

    sp = sub.add_parser("sync", help="pull data now")
    sp.add_argument("--connection", type=int, help="only this connection id")
    sp.add_argument("--start", help="YYYY-MM-DD")
    sp.add_argument("--end", help="YYYY-MM-DD")
    sp.add_argument("--days", type=int, help="days back from today")
    sp.set_defaults(fn=cmd_sync)

    ip = sub.add_parser("import", help="load a CSV into a fact table")
    ip.add_argument("table", choices=list(CONFLICT_KEYS))
    ip.add_argument("path")
    ip.set_defaults(fn=cmd_import)

    sub.add_parser("status", help="row counts and connection health").set_defaults(fn=cmd_status)
    sub.add_parser("serve", help="start the web portal").set_defaults(fn=cmd_serve)
    sub.add_parser("reset-demo", help="delete seeded demo rows").set_defaults(fn=cmd_reset_demo)
    sub.add_parser("reset-all", help="drop and recreate everything").set_defaults(fn=cmd_reset_all)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
