"""Keep the test suite isolated from any production database in ``.env``."""
from __future__ import annotations

import os


# Test modules create disposable SQLite databases.  These variables must be
# present before portal.config is imported so its .env loader cannot redirect
# those fixtures into a configured PostgreSQL database.
os.environ["PORTAL_DATABASE_URL"] = ""
os.environ["DATABASE_URL"] = ""
