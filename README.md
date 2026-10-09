# Vortex Veri

A self-hosted marketing data portal. It pulls
marketing data straight from the source APIs into a database on your own
machine and serves a dashboard at `http://localhost:8787`.

No subscription, no third party holding your data, no row limits, and no
account caps — connect as many platforms and as many accounts as you like.

The core portal runs on Python with no third-party Python packages. The optional
embedded AI uses a separate local model runtime and downloaded model. The
runtime handles only report summaries supplied by the portal, not connector
credentials.

---

## Quick start

From a terminal:

```bash
cd marketing-portal
./run.sh
```

On macOS you can also double-click **`Start Portal.command`**, which starts the
portal and opens your browser for you.

> **First time, macOS will block it:** *"Apple could not verify Start
> Portal.command is free of malware."* That is Gatekeeper reacting to the
> quarantine tag macOS puts on any file written by an app — not a problem with
> the file. Run `./run.sh` from a terminal once and it clears the tag for you,
> after which double-clicking works. To clear it yourself instead:
>
> ```bash
> xattr -dr com.apple.quarantine ~/Documents/marketing-portal
> ```
>
> Or open *System Settings → Privacy & Security*, scroll to the bottom and
> press **Open Anyway**. (On recent macOS versions the old right-click → Open
> trick no longer works for this dialog.)

Open **http://localhost:8787**. It starts on 90 days of generated sample data,
so you can see the whole thing working before connecting anything.

On first launch, create the administrator account from this computer. Use a
password with at least 14 characters. The one-time setup closes after the
administrator is created. Administrators can add individual accounts in
**Workspace → Portal accounts**. Viewers can see reports; analysts can also
use the workspace tools; administrators manage connectors, users, and settings.
Sessions expire after eight hours. Share temporary passwords through a separate
secure channel.

To stop it: `Ctrl+C`, or close the terminal window.

### Keep the local portal running on macOS

The terminal launcher stops when its window closes. For a portal that starts
when you sign in and restarts after a crash, install its per-user LaunchAgent.
The installer copies the runtime to private Application Support because macOS
blocks background agents from reading a project under Documents. Re-run the
installer after editing the source code to deploy those changes:

```bash
python3 -m scripts.local_service install
```

Before the first start, make a verified SQLite backup of the existing portal
database at `~/Library/Application Support/VortexVeri/data/portal.db`, then set
`PORTAL_DB_PATH` in the source `.env` to that path. Do not copy a live `.db`
file without its WAL state. Stop any manually started `./run.sh` first. Then
start the managed service:

```bash
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/com.vortexveri.portal.plist"
curl -fsS http://127.0.0.1:8787/healthz
```

The service binds to `127.0.0.1` and uses the Application Support database
even if `.env` changes. It starts the web server and its scheduler together.
Logs are in
`~/Library/Logs/VortexVeri/portal.log` and `portal.err.log`. To inspect or
restart it:

```bash
launchctl print gui/$(id -u)/com.vortexveri.portal
launchctl kickstart -k gui/$(id -u)/com.vortexveri.portal
```

To stop it, run
`launchctl bootout gui/$(id -u)/com.vortexveri.portal`. Do not run
`./run.sh` at the same time as the managed service. A user LaunchAgent works
only while that user is signed in and the Mac is awake. It does not make this
Mac permanent production hosting.

For a consistent SQLite backup while the portal remains online, call
`portal.db.backup_database(destination)` with a private destination path, then
verify the backup with `PRAGMA integrity_check`. To restore, stop the service,
verify the backup first, move the configured `PORTAL_DB_PATH` file and any
`-wal` and `-shm` sidecars to a separate recovery folder, copy the verified
backup to `PORTAL_DB_PATH`, set its permissions to `0600`, and start the
service. Never copy a live SQLite database file without its WAL state. A
backup of the database does not contain credentials kept in the macOS Keychain;
restoring on another Mac also requires reconnecting those accounts.

---

## Connecting a platform

Everything happens on the **Connections** screen: press *Add a connection*,
pick a platform, fill in the form, and press *Connect*. Connector credentials
are stored in this macOS user's Keychain; the database keeps only opaque
references. A database backup on a different Mac requires reconnecting the
accounts. The source `.env` may still contain an MCP access token, so keep it
private and never commit it.

### Built-in platforms

| Platform | Auth | What it pulls |
|---|---|---|
| Google Ads | OAuth | Campaign spend, clicks, conversions by day |
| Meta Ads | OAuth or System User token | Facebook and Instagram campaign performance |
| Google Analytics 4 | OAuth | Sessions and conversions by channel, source, campaign |
| Google Search Console | OAuth | Organic clicks, impressions, position by query and page |
| Superleap CRM | Read-only API key | Opportunity leads, funnel stages, location, and campaign attribution |
| LinkedIn Ads | OAuth | Campaign spend, clicks, website conversions |
| TikTok Ads | OAuth | Campaign spend, clicks, conversions |
| **Any REST API** | API key | Anything with a JSON endpoint |
| **CSV file, folder or URL** | — | Anything that can export a file, including CRM city rollups |

The last two are the important ones. Between them they cover platforms this
portal has never heard of: point the REST provider at an endpoint, describe
where the rows are and which field means what, and it loads them like any
built-in connector. Both are configured entirely from the browser.

### OAuth in one click

For OAuth platforms the portal runs the whole round trip itself — you never
copy a refresh token by hand. When you create the connection it shows you the
callback URL to register in the platform's developer console:

```
http://localhost:8787/oauth/callback
```

Register that, save the connection, press **Connect**, approve, and you're
done. Tokens refresh automatically from then on.

> **Google platforms, one gotcha.** While your OAuth consent screen sits in
> **Testing**, Google expires the refresh token after **7 days** and the
> connector silently stops syncing. In Google Cloud Console go to
> *APIs & Services → OAuth consent screen → Publish app*. It stays private to
> you — publishing only removes the 7-day clock, and the "unverified app"
> warning is expected and harmless for your own account.

### Connecting something not on the list

Use **Any REST API**. A worked example — a platform returning
`{"result": {"rows": [...]}}` with bearer auth:

| Field | Value |
|---|---|
| Endpoint URL | `https://api.example.com/v1/reports` |
| Load into table | `paid_metrics` |
| Path to the records | `result.rows` |
| Authentication | `bearer` |
| API key or token | your key |
| Start / end date parameter | `start_date` / `end_date` |
| Page parameter | `page` |
| Field mapping | see below |

```json
{
  "date": "day",
  "campaign_id": "campaign.id",
  "campaign_name": "campaign.name",
  "spend": "cost",
  "clicks": "clicks",
  "impressions": "impr",
  "conversions": "conv"
}
```

Dotted paths reach into nested objects and arrays. Numbers arriving as strings,
dates in almost any format, and epoch timestamps are all handled.

For a platform with no usable API at all, use the **CSV file, folder or URL**
provider. It takes a file path, a glob (so a folder of daily exports works), or
an `https://` address that serves CSV — including a published Google Sheet.

### When the API is closed to you

Some platforms gate their API behind an approval you may not have. Google Ads is
the common one: the developer token needs Basic access, which not every account
gets. There is a way round it that needs no API access at all.

**Google Ads Scripts run inside the account**, so they need no developer token
and no approval. `integrations/google-ads-to-sheet.js` in this project is a
ready-made one: schedule it daily, it writes campaign stats to a Google Sheet,
you publish that sheet as CSV, and the portal reads it like any other source.
Setup instructions and the field mapping are in the file's header.

The same shape works anywhere a platform can email or export a scheduled
report: land the file somewhere the portal can read, and point a CSV connection
at it.

### Superleap CRM

Choose **Superleap CRM** from Connections, create an API key in
Superleap **Workspace settings → API Access**, paste it into the portal, and
press **Test connection** before saving. The portal verifies the key and checks
that the `opportunity` object is available. **Sync now** then reads opportunities
directly from Superleap, pages through the result set, and uses `updated_at` on
later runs so changed stages are refreshed without repeatedly importing the
entire history. The default Vutto mapping covers campaign, city, demand/supply,
and funnel-stage fields. Actual CRM revenue stays unset unless a recognized
booked-revenue field is deliberately mapped.

---

## What's in the box

| Tab | What it shows |
|---|---|
| **Overview** | Blended KPIs with period-over-period change, daily spend and lead trends, and a channel table joining ad spend to CRM outcomes |
| **Paid campaigns** | Every ad platform in one table, same columns, same metric definitions |
| **Website** | Sessions, users, engagement and conversions by channel, source or campaign |
| **Search Console** | Organic clicks, impressions, CTR and average position |
| **CRM funnel** | Your funnel, one panel per lead type, plus source and location splits |
| **Connections** | Add, authorise, sync, pause or remove accounts; full sync history |
| **Data hub** | Saved report views, CSV/Excel exports, calculated campaign metrics, performance insights, automation status, and a searchable 360-connector catalogue |

The header can save the current date range, page, filters, and visible campaign
columns as a reusable view. **Export** downloads the visible tables as CSV,
which opens directly in Excel or can be imported into Google Sheets. The Data
hub also lets you define ratio metrics from campaign fields; they appear as
optional columns in Paid campaigns and are saved for the signed-in account.

The Workspace explorer follows the same reporting dates as the main dashboard
and refreshes when those dates change. Monetary metrics automatically include
account currency and keep different currencies in separate rows. Amounts and
cost rates are unavailable when currency is missing; the portal does not apply
an implicit exchange rate. Previously saved Explorer column choices are
preserved when the currency column is introduced.

The complete source catalogue is documented in [`CONNECTORS.md`](CONNECTORS.md).
Google Ads, Meta Ads, GA4, Search Console, LinkedIn Ads, TikTok Ads, REST APIs,
and CSV are native providers. Other catalogue entries use the generic REST or
CSV route until a dedicated provider adapter is added. Connector cards include
brand marks and a **Connect** action: native cards open their dedicated setup,
while other cards offer a direct connection to the platform's official REST API
or a CSV / published Google Sheets import. No external data intermediary is
required. Each setup field includes contextual help explaining where its value
can be found.

### AI assistants through MCP

The portal includes a read-only Model Context Protocol server. Open **Data hub →
Set up MCP** to copy the configuration for your AI client.

Claude Desktop and other local MCP hosts can start `mcp_server.py` directly over
stdio. No network access or token is required for this local connection.

Hosted clients such as the OpenAI API use the Streamable HTTP endpoint at
`/mcp`. It is disabled unless `PORTAL_MCP_TOKEN` is set in `.env`, and every
request must send that value as a Bearer token. Put the portal behind a private
HTTPS address before using the endpoint from a hosted client. All exposed tools
are read-only: they can inspect health, overview, channels, campaigns, website,
search, CRM funnel, and Ahrefs data, but cannot sync, edit, or delete anything.

Campaign searches match the campaign name displayed in Paid campaigns. Results
include `count` (returned rows), `total_matches` (matches in the scanned set),
and `search_complete`. The backend scans up to 1,000 campaign groups per request;
when `search_complete` is false, the result cannot prove that every campaign was
searched. Missing values sort after measured values in both directions.

The thing hosted tools charge for is the **blended channel table** on Overview:
real cost per lead and cost per sale, because spend and outcomes live in the
same database and share a normalised channel key.

---

## Making it your funnel

The CRM funnel is six positions — `leads, contacted, qualified, booked,
completed, won` — and you choose the order and the words in `.env`:

```
FUNNEL_ORDER=leads,qualified,contacted,booked,completed,won
FUNNEL_LABELS=Leads,Qualified,Contacted,Inspection planned,Inspection done,Procured
```

Stages that are empty for a given lead type disappear on their own, so one
order can serve several different funnels at once.

---

## Loading data by hand

Any extract you can produce can go straight in:

```bash
python3 -m portal.cli import crm_daily      ~/exports/crm_daily.csv
python3 -m portal.cli import paid_metrics   ~/exports/ads.csv
```

The CSV header just has to match the table's column names; extra columns are
ignored. Rows land on the same natural key a synced row would use, so
re-importing updates rather than duplicates.

---

## Command line

```bash
python3 -m portal.cli providers                 # platforms you can connect
python3 -m portal.cli providers --show rest     # one platform's fields
python3 -m portal.cli connections               # saved accounts
python3 -m portal.cli connect csv "Exports" --set path=~/exports/*.csv --set target_table=crm_leads
python3 -m portal.cli sync                      # pull everything now
python3 -m portal.cli sync --connection 3       # just one account
python3 -m portal.cli status                    # row counts and health
python3 -m portal.cli disconnect 3 --drop-data  # remove an account and its rows
python3 -m portal.cli reset-demo                # clear the sample data
```

---

## How it refreshes itself

While the portal is running:

- **Daily at 06:15** — full re-pull of the last 30 days
- **Every 4 hours** — light top-up of the last 3 days

Both re-pull a *window* rather than just yesterday, and upsert on a natural key.
That is deliberate: ad platforms restate conversions for days after the fact and
Search Console lags two to three days, so a window that overwrites itself is the
only way the numbers stay right without a full reload. Re-running any sync is
always safe — it cannot double-count.

For syncs that happen even when the portal isn't open:

```bash
crontab -e
15 6 * * * cd ~/marketing-portal && /usr/bin/python3 -m portal.cli sync
```

---

## The data model

Every platform of the same kind lands in the same table with the same columns,
which is what makes blended reporting a `GROUP BY` rather than a join across
vendors.

| Table | Grain | Fed by |
|---|---|---|
| `paid_metrics` | day × account × campaign | every ad platform |
| `web_metrics` | day × channel × source/medium/campaign | analytics |
| `search_metrics` | day × query × page × device | search consoles |
| `crm_leads` | one row per lead, with attribution and funnel flags | CRM |
| `crm_daily`, `crm_city_daily` | daily rollups — the CRM reporting layer | derived, or imported |
| `connections` | your saved accounts and their credentials | you |
| `sync_runs` | one row per pull attempt | all |

It's a single SQLite file you can back up, copy or open in any SQL client. On
macOS it lives at `~/Library/Application Support/marketing-portal/portal.db` —
not in this folder, because iCloud-synced folders cannot host a SQLite database.
`python3 -m portal.cli status` always prints the path in use.

The web API now requires a signed-in portal session. Browser write requests also
require a same-origin request and a CSRF token. The read-only MCP endpoint has
separate bearer-token authentication when enabled; local MCP clients launched
through `mcp_server.py` run as the operating-system user and are governed by
that user's computer account.

### Embedded AI assistant

The **Ask AI** button is available across portal tabs. When a local Ollama
runtime has the `qwen3:1.7b` model installed, the portal sends a bounded,
read-only summary of the selected reports to that model at
`127.0.0.1:11434`. Answers show the date range and reports used. No paid
model key is required and no report data is sent to a hosted AI provider.
The model cannot change data or settings. Model answers can still be wrong,
so verify important figures against the cited reports. If Ollama is stopped
or the model is missing, the existing basic report answers remain available.
Set `PORTAL_LOCAL_AI_MODEL` to another installed local Ollama model if needed.
On this Mac, the runtime and model are stored under `.local-ai` in the portal
folder. `./run.sh` starts the local runtime automatically when it is present.
The model needs roughly 1.4 GB of disk space and additional memory while in use.

## Login and security limits

Passwords are stored as salted PBKDF2 hashes. Sessions use random tokens, are
stored as hashes in the database, and expire after eight hours. The portal
limits repeated failed sign-in attempts, applies server-side role checks, and
requires CSRF protection on browser writes. The first administrator can only
be created by opening the portal directly on the computer running it.
Forwarded client-IP headers are ignored unless `PORTAL_TRUSTED_PROXY_IPS` is
set to a proxy that removes client-supplied forwarding headers and sends one
unambiguous client address. Conflicting or multi-value headers fall back to the
proxy's socket address. Leave the setting blank for direct localhost use.

The local SQLite file contains connector access tokens and is restricted to
the current operating-system user with file permissions. Connector secrets are
not encrypted inside SQLite. Anyone who can read that database or control the
Mac can still access those credentials. This login system is a local access
control layer, not a substitute for production identity management. It does not
include multi-factor authentication, Google or Microsoft single sign-on,
password recovery, or a managed user directory. Do not expose the portal on the
public internet without HTTPS, backups, secret-at-rest protection, and a
review of the separately authenticated MCP access path.

---

## Adding a platform properly

If you'd rather have a first-class connector than a REST mapping, one file in
`portal/providers/` is the whole job. A provider declares its fields and how to
fetch; the setup form, OAuth flow, scheduling, storage and error reporting are
all generated from that declaration. Copy `portal/providers/linkedin.py` as a
starting point and add it to `REGISTRY` in `portal/providers/__init__.py`.

---

## Where your credentials live

Connector credentials live in the local SQLite file, readable only by your
operating-system account on this machine. The server binds to `127.0.0.1` by
default, so the browser portal is reachable from this computer only. Use an
HTTPS reverse proxy and managed identity before making it available to other
devices or the internet.

Removing a connection deletes its credentials, and optionally the data it
brought in.

---

## Troubleshooting

**A connection says "Needs: …"** — that field is still empty. Press *Edit*.

**Google connectors worked, then stopped after a week** — the OAuth consent
screen is still in Testing. Publish the app, then press *Connect* again.

**`redirect_uri_mismatch`** — the callback URL registered with the platform
doesn't match the portal's. The exact string to register is shown in the
connection form, and changes if you change `PORTAL_PORT`.

**Google Ads returns `DEVELOPER_TOKEN_NOT_APPROVED`** — a new developer token
only reads test accounts. Apply for Basic access in the API Center (manager
accounts only — regular accounts have no API Center). If you can't get Basic
access, use `integrations/google-ads-to-sheet.js` instead; it bypasses the API
entirely.

**Google Ads returns 404 for every version** — Google retires API versions on a
schedule. The connector auto-detects a working one; if it can't, set an API
version on the connection.

**Meta returns `(#190) access token expired`** — you used a short-lived token.
Generate a System User token instead, or press *Connect* to use OAuth.

**Search Console returns nothing for recent days** — normal, its data lags two
to three days. The connector already stops at `today - 2`.

**"Could not open the database … disk I/O error"** — the database path is on a
network share or a cloud-synced folder. Set `PORTAL_DB_PATH` to a local path.

**Port 8787 is busy** — change `PORTAL_PORT` in `.env`. Remember to update the
callback URL registered with any OAuth platform.

**`./run.sh: Permission denied`** — run `chmod +x run.sh` once.

Every failure is recorded in full on the **Connections** screen under Sync
history, which is the first place to look when a number stops updating.
