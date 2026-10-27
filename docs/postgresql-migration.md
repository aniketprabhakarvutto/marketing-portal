# PostgreSQL persistence

Local development continues to use SQLite. Production uses PostgreSQL when
`PORTAL_DATABASE_URL` is set. `DATABASE_URL` is also accepted for managed-host
deployments. The portal never logs either variable.

```env
PORTAL_DATABASE_URL=postgresql://portal:password@host:5432/marketing_portal?sslmode=require
```

Install `requirements.txt` before starting a PostgreSQL deployment. On startup,
the portal applies versioned migrations from `portal/migrations/` and records
each version in `schema_migrations`. The initial migration creates every model
declared in `portal/models.py`, including metrics, CRM, identity, connection,
scheduling, and reporting tables.

Do not point a running production portal at an empty URL without first taking a
verified PostgreSQL backup. The initial migration is additive. Its rollback
refuses to drop populated tables unless a maintainer explicitly runs the down
migration with `force=True`; this prevents an accidental data-destroying
rollback.
