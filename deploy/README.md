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
