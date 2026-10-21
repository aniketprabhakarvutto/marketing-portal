"""Decrypt a Vortex Veri backup into a new, separate SQLite database.

Requires the cryptography package. Never overwrite a running portal database.
"""
import argparse
import base64
import os
import sqlite3
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VortexVeriBackup-v1"


def restore(source: Path, key_file: Path, destination: Path) -> int:
    key = base64.urlsafe_b64decode(key_file.read_bytes().strip())
    payload = source.read_bytes()
    if len(key) != 32 or not payload.startswith(MAGIC) or len(payload) < len(MAGIC) + 28:
        raise ValueError("Invalid backup or recovery key")
    nonce = payload[len(MAGIC):len(MAGIC) + 12]
    data = AESGCM(key).decrypt(nonce, payload[len(MAGIC) + 12:], MAGIC)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        db = sqlite3.connect(f"{destination.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored database failed integrity check")
            return db.execute("SELECT COUNT(*) FROM crm_leads").fetchone()[0]
        finally:
            db.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("recovery_key", type=Path)
    parser.add_argument("new_database", type=Path)
    args = parser.parse_args()
    print(f"Restored database verified. CRM rows: {restore(args.backup, args.recovery_key, args.new_database)}")
