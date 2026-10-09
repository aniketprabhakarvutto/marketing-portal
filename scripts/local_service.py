"""Deploy and install the user-level macOS service for the localhost portal.

LaunchAgents cannot read a project under macOS-protected Documents without a
separate Full Disk Access grant. Run a private copy from Application Support.
Bootstrap only after the database has been migrated and manual server stopped.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.vortexveri.portal"
ROOT = Path(__file__).resolve().parent.parent


def make_plist(root: Path, log_dir: Path, db_path: Path) -> dict:
    root = root.resolve()
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/bash", str(root / "run.sh")],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "PORTAL_HOST": "127.0.0.1",
            "PYTHON": "/usr/bin/python3",
            "PORTAL_DB_PATH": str(db_path),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "ExitTimeOut": 20,
        "Umask": 0o077,
        "StandardOutPath": str(log_dir / "portal.log"),
        "StandardErrorPath": str(log_dir / "portal.err.log"),
    }


def install(root: Path = ROOT, home: Path | None = None) -> Path:
    root = root.resolve()
    if not (root / "run.sh").is_file():
        raise FileNotFoundError(f"Portal launcher is missing: {root / 'run.sh'}")
    home = (home or Path.home()).resolve()
    base = home / "Library" / "Application Support" / "VortexVeri"
    runtime = base / "runtime"
    data_dir = base / "data"
    for directory in (base, runtime, data_dir):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    # rsync updates code and the local model incrementally. The source DB and
    # backups stay outside the deployment and are migrated separately.
    subprocess.run([
        "/usr/bin/rsync", "-a", "--delete",
        "--exclude=backups/", "--exclude=data/", "--exclude=reports/",
        "--exclude=tests/", "--exclude=__pycache__/", "--exclude=.git/",
        "--exclude=.local-ai/ollama.log",
        str(root) + "/", str(runtime) + "/",
    ], check=True)
    runtime.chmod(0o700)
    if (runtime / ".env").exists():
        (runtime / ".env").chmod(0o600)
    log_dir = home / "Library" / "Logs" / "VortexVeri"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir.chmod(0o700)
    agent_dir = home / "Library" / "LaunchAgents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    target = agent_dir / f"{LABEL}.plist"
    staged = agent_dir / f".{LABEL}.{os.getpid()}.tmp"
    try:
        staged.write_bytes(plistlib.dumps(make_plist(runtime, log_dir, data_dir / "portal.db")))
        staged.chmod(0o644)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install"])
    args = parser.parse_args(argv)
    if args.command == "install":
        path = install()
        print(f"Installed {path}")
        print("Database: ~/Library/Application Support/VortexVeri/data/portal.db")
        print("Verify the database exists before bootstrapping the service.")
        print(f"Start: launchctl bootstrap gui/{os.getuid()} '{path}'")
        print(f"Stop: launchctl bootout gui/{os.getuid()}/{LABEL}")
        print(f"Restart: launchctl kickstart -k gui/{os.getuid()}/{LABEL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
