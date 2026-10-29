"""Migrate connector credentials from macOS Keychain references to Fernet.

The Keychain items are deliberately retained as a rollback source. The script
prints counts and paths only, never credential values or encryption keys.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from cryptography.fernet import Fernet

from portal import db, secret_store
from portal.config import ROOT


def _set_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    output: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line else ""
        if name in pending:
            output.append(f"{name}={pending.pop(name)}")
        else:
            output.append(line)
    output.extend(f"{name}={value}" for name, value in pending.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate(env_paths: list[Path], backup_dir: Path) -> dict[str, object]:
    if sys.platform != "darwin":
        raise RuntimeError("Keychain migration must run on the Mac that owns the credentials")
    rows = db.query("SELECT id, secrets FROM connections ORDER BY id")
    staged: list[tuple[int, str, str]] = []
    key = Fernet.generate_key()
    cipher = Fernet(key)
    for row in rows:
        raw = row["secrets"] or "{}"
        reference = secret_store.reference(raw)
        if reference is None:
            continue
        values = secret_store.get(reference)
        token = cipher.encrypt(json.dumps(values, separators=(",", ":")).encode()).decode()
        staged.append((int(row["id"]), raw, json.dumps({secret_store.FERNET_KEY: token}, separators=(",", ":"))))
    if not staged:
        return {"migrated": 0, "environment_files": [str(path) for path in env_paths]}

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"keychain-references-{stamp}.json"
    backup.write_text(json.dumps({str(i): old for i, old, _ in staged}, indent=2), encoding="utf-8")
    os.chmod(backup, 0o600)

    encoded_key = key.decode("ascii")
    for path in env_paths:
        _set_env(path, {"PORTAL_SECRET_STORE": "fernet", "PORTAL_SECRET_ENCRYPTION_KEY": encoded_key})

    conn = db.connect()
    try:
        conn.execute("BEGIN")
        for connection_id, old, encrypted in staged:
            cursor = conn.execute(
                "UPDATE connections SET secrets=?, updated_at=datetime('now') WHERE id=? AND secrets=?",
                (encrypted, connection_id, old),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Connection {connection_id} changed during migration")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"migrated": len(staged), "backup": str(backup), "environment_files": [str(path) for path in env_paths]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="perform the migration")
    args = parser.parse_args()
    if not args.write:
        raise SystemExit("Refusing to change credentials without --write")
    runtime_env = Path.home() / "Library" / "Application Support" / "VortexVeri" / "runtime" / ".env"
    result = migrate([ROOT / ".env", runtime_env], ROOT / "backups")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
