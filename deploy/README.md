# Linux server deployment (Supabase PostgreSQL)

This deployment path is separate from the Mac launcher. It leaves `run.sh`,
the local database, macOS Keychain, and the local AI runtime untouched.

## Local Docker verification

The local container uses its own database volume and port `8788`, so it does
not replace the normal macOS database or conflict with the portal on port
`8787`.

```bash
cp deploy/local.env.example deploy/local.env
# Set generated, distinct PORTAL_SECRET_ENCRYPTION_KEY and
# PORTAL_MFA_ENCRYPTION_KEY values in deploy/local.env.
docker compose -f docker-compose.local.yml up -d --build
curl -fsS http://127.0.0.1:8788/healthz
```

Open `http://127.0.0.1:8788`. The first launch creates a new administrator for
the isolated container. The local profile explicitly permits first-admin setup
through Docker Desktop's loopback port. The production profile keeps that
exception disabled. Stop it without deleting its data with:

```bash
docker compose -f docker-compose.local.yml down
```

The scheduler can create verified database backups in `./backups` on the Mac.
That folder is outside the container and ignored by Git. Copy its contents to
separate storage regularly. Delete the isolated local database only when it is
no longer needed:

```bash
docker compose -f docker-compose.local.yml down -v
```

## What this deploys

- the existing Python portal and its scheduler in one Docker container;
- persistence in the existing Supabase PostgreSQL database, selected by
  `PORTAL_DATABASE_URL`; the container holds no application data of its own;
- encrypted connector credentials stored in that database through Fernet;
- localhost-only server binding for a reverse proxy or Cloudflare Tunnel.

The `/data` volume is only scratch space (and the legacy SQLite path, unused
once `PORTAL_DATABASE_URL` is set). Users, sessions and connector credentials
live in Supabase. Because credentials are Fernet-encrypted, the server must use
the **same** `PORTAL_SECRET_ENCRYPTION_KEY` that encrypted them; with a new key,
reconnect each provider. Back that key up in a password manager, never in Git.

## VM preparation (Ubuntu 24.04)

Install Docker Engine and the Compose plugin using the official Docker
instructions. Do not expose port 8787 to the internet.

```bash
git clone https://github.com/aniketprabhakarvutto/marketing-portal.git
cd marketing-portal
sudo install -d -m 700 -o 10001 -g 10001 /srv/vortex-veri/data /srv/vortex-veri/backups
cp deploy/production.env.example deploy/production.env
chmod 600 deploy/production.env
```

Edit `deploy/production.env`:

- `PORTAL_DATABASE_URL`: the Supabase connection string with `?sslmode=require`.
  Use the pooler (session mode) URL if the VM has no IPv6.
- `PORTAL_SECRET_ENCRYPTION_KEY`: the existing key (or a newly generated one).
- `PORTAL_MFA_ENCRYPTION_KEY`: a separate Fernet key used only to encrypt MFA
  seeds. Back it up in the same controlled secret store as the connector key.
- `PORTAL_OAUTH_REDIRECT_URI`: your real HTTPS hostname.
- `PORTAL_MCP_TOKEN`: a long, different token if remote MCP is needed.

Start the portal and wait for readiness before routing traffic to it:

```bash
docker compose -f docker-compose.production.yml up -d --build
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8787/readyz)" = 200 ]; do sleep 2; done
echo ready
```

On first start the portal applies its versioned migrations to Supabase. Take a
Supabase backup first if the database already holds data you care about.

## One-time SQLite to PostgreSQL migration

Use this only when moving an existing local SQLite portal database into a new,
empty PostgreSQL database. Do not point it at the live PostgreSQL database
used by the portal. It deliberately refuses a populated target unless an
earlier run against the same source was interrupted.

1. Stop the portal so no writes occur during the transfer.
2. Back up both the SQLite file and the target PostgreSQL database.
3. Set `PORTAL_DATABASE_URL` in the environment to the new PostgreSQL target.
4. Run the source inspection first:

```bash
python3 scripts/backfill_postgres.py --dry-run
```

5. Start the portal once so it applies the PostgreSQL schema migrations, then
   stop it again.
6. Copy and verify every application table:

```bash
python3 scripts/backfill_postgres.py
```

7. The command prints source and target counts and fails if any count differs.
   Only use `--resume` after an interrupted run against that exact source and
   otherwise untouched target.
8. Start the portal with the PostgreSQL URL, verify `/readyz`, sign in, and
   check connections, users, CRM, and reports before retiring the SQLite file.

The SQLite source is opened read-only and is never modified. Keep it and its
backup until the PostgreSQL cutover has been accepted. Connector credentials
need a separate check: a Mac Keychain-backed SQLite setup and a Fernet-encrypted
PostgreSQL setup do not use the same secret-store format.

## Public access

Use a Cloudflare Tunnel or an HTTPS reverse proxy to route the chosen hostname
to `http://127.0.0.1:8787`. Set `PORTAL_TRUSTED_PROXY_IPS` only to the actual
local proxy address after confirming that the proxy overwrites forwarding
headers. HTTPS is required because the portal's session cookies are secure.

Run exactly one application instance. Each instance starts its own scheduler,
keeps login throttles in process memory, and performs interrupted-job recovery.
Do not scale replicas. Also stop the Mac portal (or set `SCHEDULER_ENABLED=0`
there) so two schedulers do not sync against the same Supabase database.

Before network exposure, every enabled password account must finish MFA
enrollment. Startup fails closed when the MFA key is absent or any enabled
account is unenrolled. Keep port 8787 bound to loopback and terminate HTTPS at
the trusted proxy or tunnel.

Use an HTTPS reverse proxy with bounded connections and request body, header and
upstream timeouts. Monitor `/readyz` from the trusted local network: it runs a
database query and returns 503 if Supabase is unreachable. `/healthz` only
checks that the HTTP process responds. Neither proves connectors are fresh.
Alert on readiness failures, failed syncs and disk capacity.

## Backups

The database is Supabase, so backups are Supabase's: enable daily backups (or
Point-in-Time Recovery) in the Supabase project, and periodically test a
restore into a separate project. Separately back up the Fernet key. The SQLite
backup job and `.vvb` snapshots (`scripts/restore_encrypted_backup.py`) apply
only to the old SQLite file, `~/Library/Application Support/VortexVeri/data/portal.db`;
keep a copy of it until you are satisfied the Supabase data is complete.

## Updates

```bash
git pull --ff-only
docker compose -f docker-compose.production.yml up -d --build
```

This replaces the container; data stays in Supabase. Wait for `/readyz` to
return 200 again before testing.

## Marketing Analytics automation

The runner refreshes saved Google Ads, Meta Ads, and Superleap connections and
previews mapped campaign costs. It stops if a required source is missing or a
refresh does not report success. It does not yet reproduce the Superleap
Marketing DB reports, update the actual workbook data, or create monthly copies.
Do not treat a successful preview as a completed daily workbook update.

For testing, tag Google Sheets connections with the appropriate Marketing
Analytics role: `demand`, `supply`, or `mapping`. Configure the mapping workbook's
source, target, and funnel columns. Writes are restricted to the
`__vortex_cost_test` staging tab and must only be used with private test copies.

Run a non-writing preview first:

```bash
docker compose -f docker-compose.production.yml --profile automation run --rm -e MARKETING_ANALYTICS_WRITE=0 marketing-analytics-runner
```

The unit and timer are provided as infrastructure for a future verified runner.
Do not enable them as a production workbook update until the report matching,
sheet writes, rollover, and an end-to-end test have been completed. To install
the timer on a suitable host, put the unit and timer under
`/etc/systemd/system/`. Change the service `WorkingDirectory` if the repository
is not located at `/opt/vortex-veri`, then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vortex-veri-marketing-analytics.timer
systemctl list-timers vortex-veri-marketing-analytics.timer
```

The timer is persistent and uses `Asia/Kolkata`, so a missed run starts when the
server returns. It is independent of the laptop, but it still requires an
always-running Linux host with the portal database and encrypted connector
credentials.
