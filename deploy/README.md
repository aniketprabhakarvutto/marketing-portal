# Linux server deployment

This deployment path is separate from the Mac launcher. It leaves `run.sh`,
the local database, macOS Keychain, and the local AI runtime untouched.

## Local Docker verification

The local container uses its own database volume and port `8788`, so it does
not replace the normal macOS database or conflict with the portal on port
`8787`.

```bash
cp deploy/local.env.example deploy/local.env
# Set a generated PORTAL_SECRET_ENCRYPTION_KEY in deploy/local.env.
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
- a persistent SQLite database at `/srv/vortex-veri/data/portal.db` on the server;
- encrypted connector credentials stored in that database through Fernet;
- localhost-only server binding for a reverse proxy or Cloudflare Tunnel.

It does not copy the laptop database, sessions, users, credentials, or local
Ollama model. Create the server administrator and reconnect each provider after
the server is reachable. Migrating production analytics to PostgreSQL is a
separate project; this deployment keeps SQLite on a server-local persistent disk
to avoid changing the running portal's reporting behaviour.

## Oracle VM preparation

On an Ubuntu 24.04 VM, install Docker Engine and the Compose plugin using the
official Docker instructions. Do not expose port 8787 to the internet.

Clone the private repository and prepare the persistent data directory:

```bash
git clone https://github.com/aniketprabhakarvutto/marketing-portal.git
cd marketing-portal
sudo install -d -m 700 -o 10001 -g 10001 /srv/vortex-veri/data /srv/vortex-veri/backups
cp deploy/production.env.example deploy/production.env
chmod 600 deploy/production.env
```

Set a real HTTPS hostname in `PORTAL_OAUTH_REDIRECT_URI`, add a newly generated
Fernet key, and set a different long MCP token if remote MCP is required. Keep
those values out of GitHub.

Start the portal:

```bash
docker compose -f docker-compose.production.yml up -d --build
curl -fsS http://127.0.0.1:8787/healthz
```

## Public access

Use a Cloudflare Tunnel or an HTTPS reverse proxy to route the chosen hostname
to `http://127.0.0.1:8787`. Set `PORTAL_TRUSTED_PROXY_IPS` only to the actual
local proxy address after confirming that the proxy overwrites forwarding
headers. HTTPS is required because the portal's session cookies are secure.

Run exactly one application instance. Each instance starts its own scheduler,
keeps login throttles in process memory, and performs interrupted-job recovery.
Do not scale replicas or share this SQLite file over network storage. A
multi-instance deployment requires shared coordination and a tested database
migration before it is safe.

Use an HTTPS reverse proxy with bounded connections and request body, header and
upstream timeouts. Restrict the portal port to loopback as in the Compose file.
Monitor `/readyz` from the trusted local network: it checks a database query and
returns 503 if it fails. `/healthz` only checks that the HTTP process responds.
Neither endpoint proves that connectors are fresh or backups are restorable.
Configure alerts for readiness failures, disk capacity, failed sync and backup
jobs, and backup age. The health check in the Docker image uses `/readyz`.

### Encrypted backup recovery

Encrypted `.vvb` snapshots use AES-256-GCM. Store the recovery key in a
password manager outside the laptop and never commit it to Git. The key is
required to restore the snapshot. The backup contains portal database records,
but does not contain connector credentials stored in macOS Keychain.

To verify a downloaded snapshot without replacing the live database, use a
Python environment with `cryptography` installed:

```sh
python3 scripts/restore_encrypted_backup.py /path/to/backup.vvb /path/to/recovery-key.txt /path/to/new-restore.db
```

The destination must not exist. The script authenticates the ciphertext and
checks SQLite integrity. Inspect the restored copy before following the normal
offline restoration procedure. A one-time upload to Drive does not schedule
future uploads; configure and test an authenticated background uploader.

The backup job writes to the mounted backups directory, but a mount on the same
server is not an off-server backup. Configure a backup job, copy encrypted
backups to independent storage, protect and separately back up the Fernet key,
and regularly restore a backup into an isolated environment. A local test
cannot establish that the production job, remote copy or recovery works.

## Updates

On the server:

```bash
git pull --ff-only
docker compose -f docker-compose.production.yml up -d --build
```

This replaces the application container while retaining the server database in
`/srv/vortex-veri/data`. Scheduled database backups are stored separately in
`/srv/vortex-veri/backups`. Copy both folders to storage outside the server
before upgrades.
