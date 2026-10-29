# Vortex Veri Portal Knowledge Base

Last updated: 2026-10-28, Asia/Kolkata  
Maintainer: Codex and the portal owner  
Status: Living document. Update after every material portal, database, deployment, connector, or Marketing Analytics automation change.

## Why this file exists

This is the durable context file for Vortex Veri. Read it before changing the portal. It records what the portal does, how it is deployed, decisions already made, completed work, known issues, verification results, and the Marketing Analytics workbook automation.

This file does not contain passwords, API keys, database URLs, session tokens, or connector secrets. Those belong in the local `.env`, the macOS Keychain, or the configured Linux secret store.

## Sources used

The initial version was reconciled from these Codex chats and the current repository:

| Source | Thread ID | Main subject |
|---|---|---|
| Vortex Veri | `01a0af1e-3cd9-7940-ba4a-834da5549e34` | Original portal implementation, connectors, dashboard, authentication, local service, and operating model |
| Vortex Veri chat 2 | `01a15de0-b684-7452-9952-c4abc46764fd` | Security, CRM reporting, UI and chart fixes, performance, and deployment work |
| Marketing Analytics Test Automation | `01a16dc9-18d1-7b71-b776-3615cf73980a` | Demand and Supply workbook automation, PostgreSQL migration, runtime recovery, performance testing, and sign-in incident |
| Repository | `/Users/macbookpro/Documents/marketing-portal` | Current code, tests, migrations, deployment files, and Git history |

Chat history is evidence, not the final source of truth. Current code, database state, runtime checks, and live workbook checks override old chat claims.

## Portal purpose

Vortex Veri is a self-hosted marketing data portal for Vutto. It consolidates paid media, website analytics, organic search, CRM funnel data, campaign attribution, exports, and scheduled automation in one authenticated interface.

Primary goals:

- Pull data from first-party source APIs or controlled CSV and Google Sheets exports.
- Normalize reporting across Google Ads, Meta Ads, GA4, Search Console, Superleap CRM, and other sources.
- Join advertising spend with CRM outcomes by campaign, channel, lead type, city, and date.
- Provide Demand and Supply reporting without changing metric definitions between sources.
- Automate the Marketing Analytics workbooks while protecting production sheets from accidental writes.
- Keep connector credentials out of the database and browser.
- Support SQLite for local development and PostgreSQL for persistent production-style storage.

## Non-negotiable product and data decisions

- Use Vortex Veri native connectors and controlled exports. Windsor is not part of the solution.
- Direct Superleap is the CRM authority where its exact report definition has been verified. Do not re-enable overlapping CRM Sheets that duplicate the same leads.
- Never fabricate booked revenue, qualification, campaign attribution, or funnel outcomes to clear a warning.
- Estimated resale price is not booked revenue. Procurement price is a cost, not revenue.
- Campaign CAC and revenue are available only when exact CRM attribution exists.
- Keep one active scheduler for a shared database. The Mac runtime and any Linux deployment must not both schedule the same jobs.
- Keep the portal assistant read-only. It must not claim that it changed campaigns, workbooks, users, or connectors.
- GitHub Pages and Google Drive cannot host the Python application. They can hold static files or exports only.
- Git repositories are not acceptable CRM database backup storage because deleted secrets and records can remain in history.
- Production workbook writes require explicit allowlisting, source completeness, mapping validation, and a live post-write check.

Product design constraints recorded by the owner:

- No purple gradients, fake reviews, fake metrics, fake counters, or fabricated hero claims.
- No emoji icons, excessive scroll animation, cursor animation, or generic AI-generated marketing imagery.
- Avoid pill-shaped controls unless the interaction genuinely requires one.
- A public launch requires a company-controlled domain, HTTPS, favicon, privacy policy, terms, and no AI-builder branding.

## Current locations

| Item | Location |
|---|---|
| Source repository | `/Users/macbookpro/Documents/marketing-portal` |
| GitHub repository | `https://github.com/aniketprabhakarvutto/marketing-portal` |
| Installed local runtime | `/Users/macbookpro/Library/Application Support/VortexVeri/runtime` |
| Previous local SQLite database | `/Users/macbookpro/Library/Application Support/VortexVeri/data/portal.db` |
| LaunchAgent | `/Users/macbookpro/Library/LaunchAgents/com.vortexveri.portal.plist` |
| Runtime logs | `/Users/macbookpro/Library/Logs/VortexVeri/portal.log` and `portal.err.log` |
| Local portal | `http://127.0.0.1:8787` |
| Paid campaigns page | `http://127.0.0.1:8787/#paid` |
| Marketing Analytics page | `http://127.0.0.1:8787/#marketing-analytics` |

The source repository and installed runtime are separate copies. A source edit is not deployed until the runtime is updated or the local service installer is rerun.

## Current architecture

```text
Google Ads / Meta / GA4 / GSC / Superleap / CSV / REST
                         |
                         v
                provider adapters and sync
                         |
                         v
        PostgreSQL when PORTAL_DATABASE_URL is set
             SQLite when no database URL is set
                         |
                         v
               Python reporting and API layer
                         |
                         v
       authenticated browser dashboard on port 8787
                         |
                         v
       exports, saved views, scheduled jobs, MCP reads
```

Important implementation areas:

| Area | Files |
|---|---|
| Runtime configuration | `portal/config.py` |
| Database adapter, schema, queries | `portal/db.py` |
| Portable table catalogue | `portal/models.py` |
| Versioned migrations | `portal/migrations/` |
| Authentication and sessions | `portal/auth.py`, `portal/server.py`, `portal/web/login.js` |
| API and reporting | `portal/api.py`, `portal/rollup.py` |
| Connectors and sync | `portal/providers/`, `portal/connections.py`, `portal/sync.py` |
| Scheduling | `portal/scheduler.py`, `portal/automation.py` |
| Marketing Analytics | `portal/marketing_analytics.py`, `portal/marketing_rollover.py`, `portal/marketing_db_exports.py` |
| Dashboard UI | `portal/web/` |
| Tests | `tests/` |
| Deployment | `deploy/`, `Dockerfile`, `docker-compose.production.yml`, `scripts/local_service.py` |

## User-facing portal sections

- Overview: blended KPIs, daily advertising spend, CRM lead and sales trends, and channel-level performance.
- Paid campaigns: Google and Meta campaign performance with platform, account, Demand and Supply, status, search, and date filters.
- Website: GA4 sessions, users, engagement, conversions, source, medium, campaign, and channel reporting.
- Search: Search Console and supported organic search reporting.
- CRM funnel: Superleap or imported CRM leads, funnel stages, lead types, campaign attribution, and city reporting.
- Connections: connector creation, OAuth, test, sync, pause, and sync history.
- Workspace and Data hub: saved views, calculated metrics, exports, scheduled jobs, annotations, transformations, and MCP setup.
- Marketing Analytics: workbook configuration, validation, reconciliation, staging, and controlled write automation.

## Supported data sources

Native providers include Google Ads, Meta Ads, GA4, Google Search Console, Superleap CRM, LinkedIn Ads, TikTok Ads, Ahrefs, Gmail reporting, generic REST APIs, and CSV files, folders, URLs, or published Google Sheets.

Connector credentials were migrated on 2026-10-28 from 16 macOS Keychain references to Docker-compatible Fernet ciphertext in PostgreSQL. The Fernet key remains a plaintext environment secret in the ignored source and installed-runtime `.env` files because encryption keys, API keys, and database URLs must be recoverable at runtime and therefore cannot be hashed. The original Keychain items were retained for rollback. Passwords remain salted PBKDF2-SHA256 hashes with 600,000 iterations; session and invitation tokens remain SHA-256 hashes. No credential values belong in this file or Git.

## Persistence and migrations

The persistence migration is implemented.

- `PORTAL_DATABASE_URL` is the preferred production variable.
- `DATABASE_URL` is accepted for managed hosts.
- Leaving both blank keeps SQLite development mode.
- The active Vutto runtime has been cut over to a Supabase PostgreSQL database.
- The portal applies migrations at startup and records them in `schema_migrations`.
- `portal/models.py` declares 23 persisted tables.
- Current migration sequence: `0001_initial`, `0002_crm_origins`, `0003_campaign_revenue_rollup`, `0004_paid_reporting_indexes`, `0005_account_mfa`.
- Rollback refuses to drop populated tables unless a maintainer explicitly uses `force=True`.
- PostgreSQL backfill tooling exists in `scripts/backfill_postgres.py`.

Core persisted groups:

- Paid, web, search, Ahrefs, CRM lead, daily, city, and campaign metrics.
- Connections and sync runs.
- Users, sessions, invitations, and audit log.
- Metric definitions, transformation rules, saved explorer views, and dashboards.
- Destinations, scheduled jobs, job runs, settings, and annotations.

See `docs/postgresql-migration.md` before changing persistence behavior.

## Marketing Analytics automation

The automation was built around the Demand and Supply test workbooks and the Superleap Marketing DB exports. Its safety model is deliberate:

1. Read and reconcile source data.
2. Validate workbook structure, formulas, campaign mappings, and date ranges.
3. Stage changes in controlled test or generated sheets.
4. Block writes when reconciliation or workbook safety checks fail.
5. Never silently overwrite production workbook data.

Named workbook work completed in the automation chat:

- `Marketing Analytics (Demand)-Oct'26- Aniket`
- `Marketing Analytics (Supply)-Oct'26- Aniket`
- AP ROUGH WORK campaign mapping and cost support
- Next-month workbook and rollover support
- Superleap Demand and Supply export handling
- PostgreSQL persistence for portal and automation state

Historical test and mapping references used during development:

| Workbook | Reference |
|---|---|
| Demand private test copy | `1ijVhe0wTsUIYKN6mmd-WISi7VVBZZrnARPXijhFiAz0` |
| Supply private test copy | `10IC3uyDYQvd0r1APYMeiNdWkHbw9GHyYNLgnaccfS-I` |
| AP ROUGH WORK mapping | `1SC0Igh4Y2iif7T-ST4Io3pKh66ulSihJpYJ1hddxjq0` |

These IDs document the test history. They are not permission to write. Current allowlists and workbook identity must be checked in code and live Drive metadata before every run.

User-directed campaign handling:

- Skip `Google_Hyyv_Search_Campaign_1`.
- Skip `Google_Hyyv_Search_Campaign_2`.
- Skip `Hyyv_demand_demandgen_`.
- `Vutto_SearchAIMax_Demand_Faridabad` was updated in AP ROUGH WORK by the user and should use that mapping.

Do not convert an old chat statement that a workbook was completed into proof that its current data is still correct. Recheck the live workbook, Superleap export dates, and reconciliation output before any new write.

## Completed major work

| Date | Commit | Result |
|---|---|---|
| 2026-10-22 | `0c96043` | Kept analytics test workbooks view-only by default |
| 2026-10-22 | `62c182a` | Added funnel classification and staging-write guards |
| 2026-10-27 | `18a712b` | Completed Marketing Analytics automation and PostgreSQL persistence implementation |
| 2026-10-27 | `fe4bf73` | Added PostgreSQL data backfill and CRM-origin migration |
| 2026-10-27 | `8cf0cab` | Fixed PostgreSQL runtime compatibility |
| 2026-10-27 | `6305108` | Recovered PostgreSQL campaign reporting and scheduler behavior |
| 2026-10-27 | `57c084f` | Removed repeated transformation-rule reads, coalesced duplicate campaign requests, and isolated tests from the production database |
| 2026-10-28 | `e5ae92b` | Added a 15-second sign-in request timeout so the login screen cannot spin forever |
| 2026-10-28 | uncommitted | Added a CRM campaign-revenue rollup so Paid reporting can avoid scanning raw CRM leads after the next CRM sync or backfill |
| 2026-10-28 | uncommitted | Migrated 16 connector credentials to Fernet encryption for Docker compatibility; retained Keychain rollback references and displayed no secrets |
| 2026-10-28 | uncommitted | Removed connection-card and channel-report N+1 queries, added five-minute report caches, reused PostgreSQL connections, and consolidated initial dashboard data into one bootstrap request |
| 2026-10-28 | uncommitted | Repaired PostgreSQL governance seeding, CRM `HAVING`, Data Quality grouping, schema introspection, Explorer transformations, and duplicate-connection conflict handling while retaining SQLite support |
| 2026-10-28 | uncommitted | Added production TOTP MFA with encrypted seeds, one-time hashed recovery codes, enrollment verification, challenge throttling, session revocation, a PostgreSQL migration, and fail-closed network startup |
| 2026-10-28 | uncommitted | Verified `vortex-qa-test@vutto.in` was the named Viewer QA account, removed it with guarded identity predicates, and confirmed no matching account remained; no password was read or displayed |
| 2026-10-28 | uncommitted | Replaced inconsistent decorative glyphs with a first-party accessible SVG icon set after the supplied Flaticon login URL failed to provide asset-level licensing evidence |

## Last verified measurements

These are dated observations, not permanent guarantees.

### Data state observed on 2026-10-27

- Paid media: 1,853 rows, current through 2026-10-27.
- Web analytics: 4,971 rows, current through 2026-10-27.
- CRM leads: 118,347 rows, current through 2026-10-27.
- CRM campaign rows: 19,775 rows, current through 2026-10-27.

### Paid page behavior verified on 2026-10-27

- All platforms: 32 campaigns for the verified 30-day view.
- Google: 24 campaigns.
- Meta: 8 campaigns.
- Demand filter: 15 campaigns.
- Supply filter: 15 campaigns.

### Portal performance verified on 2026-10-28

- `/api/connections` fell from 66.01 seconds to 4.15 seconds cold by replacing more than 170 sequential queries with three aggregate queries.
- The Channels report fell from 58.83 seconds to 4.81 seconds cold by loading transformation rules once instead of once per row.
- The initial Overview payload, profile, UI preferences, connection state, and automation rules now use one authenticated bootstrap request.
- An immediate Docker browser reload displayed live Overview data in 3.75 seconds after the report cache was warm.
- The live Docker Paid page displayed 32 campaigns and a populated campaign table in about 5.25 seconds during the final browser check.
- A cold browser load after the five-minute cache expired took about 25 seconds. This remains an open performance limitation caused by rebuilding reports against remote PostgreSQL. Do not describe cold loading as instant.
- Full regression result: 330 tests passed and 1 test was skipped.
- Both live deployments returned HTTP 200 from `/readyz`: macOS on port 8787 and Docker on port 8789.
- Enabled filter: 26 campaigns.
- Paused filter: 6 campaigns.
- Search for `Faridabad`: 2 campaigns.
- Seven-day and 90-day ranges loaded successfully during the browser test.

### Performance observed after commit `57c084f`

- Earlier direct campaign reporting took about 42 seconds because transformation rules were repeatedly queried and the browser issued duplicate requests.
- After the fix, direct warm reporting was about 3.0 to 3.9 seconds.
- Browser testing showed the Paid page loading 32 campaigns without console errors before the later sign-in incident.
- A later forced refresh of the 32-campaign Paid page took about 6.3 seconds. The primary remaining cost is campaign-level aggregation over remote PostgreSQL. The new revenue rollup removes one raw-CRM-lead aggregation after it has been populated.

### Test state observed on 2026-10-27

- Full suite: 326 tests passed, 1 skipped.
- Tests were changed to use isolated temporary SQLite databases instead of inheriting a production PostgreSQL URL.

### Repair verification observed on 2026-10-28

- Focused database, authentication, access, reporting, and UI integration: 122 tests passed.
- Full regression suite: 342 tests passed and 1 test was skipped.
- Rebuilt SQLite Docker verification returned HTTP 200 from `/healthz` in 0.018 seconds and `/readyz` in 0.005 seconds.
- A loopback-only, scheduler-disabled PostgreSQL verification container returned HTTP 200 from `/healthz` in 0.009 seconds and `/readyz` in 0.700 seconds. PostgreSQL mode was active and migration `0005_account_mfa` was recorded.
- Direct cold PostgreSQL timings for the selected reporting range were: Connections 4.108 seconds, CRM 22.699 seconds, Workspace governance 14.031 seconds, Data Explorer 4.929 seconds, and Data Quality 88.369 seconds.
- Data Quality at 88.369 seconds is an open performance defect. The repaired query is correct but not production-fast against the current remote PostgreSQL dataset.
- Browser accessibility inspection confirmed the CRM, Workspace, Data Explorer, Portal accounts, Connections, and Data Hub structures remained keyboard-addressable with visible labels. The source checkout's icon and mobile-navigation regressions passed automated tests. The installed macOS runtime was not replaced during this repair, so its browser page can still show pre-repair errors until deployment.

### MFA operating requirements

- Public access remains possible only behind HTTPS through the documented loopback reverse-proxy or tunnel pattern.
- Set a dedicated `PORTAL_MFA_ENCRYPTION_KEY` in the deployment secret store. Do not commit it or reuse it outside MFA seed protection.
- Every enabled password account must complete MFA enrollment before network exposure. Non-loopback startup fails when the key is absent or any enabled password account is unenrolled.
- Recovery codes are returned once at enrollment, stored only as keyed hashes, and consumed individually. They must never be logged or copied into this file.
- Run one portal instance. Authentication throttles and MFA challenges are process-local.

## Major issues and risks

### 1. The application is still local-only

Current severity: open architecture limitation.

The active application listens on `127.0.0.1:8787` and works only while this Mac user is signed in and the Mac is awake. Supabase hosts the database, not the web application. A public or independent deployment still needs a reachable Linux or managed application host, HTTPS, secure cookies, proxy configuration, and an encrypted connector secret store.

### 2. Supabase network and pooler instability can delay startup and requests

Current severity: intermittent external dependency risk.

Runtime logs have shown DNS resolution failures, connection resets, closed PostgreSQL sessions, and `Can't assign requested address` errors for the Supabase pooler. These failures have affected the scheduler, campaign APIs, startup, and authentication response time.

The database itself was reachable during later direct checks. This means the problem has been intermittent, not a permanent bad URL.

### 3. A browser tab can remain stale after a portal restart

Current severity: partially mitigated.

If the backend dies while a request is in flight, the browser can retain an old spinner or show `ERR_EMPTY_RESPONSE` or `ERR_CONNECTION_REFUSED`. The login form now aborts after 15 seconds and re-enables the button, but a browser-generated network error page cannot repair itself. Reopen or reload the portal after `/readyz` returns 200.

### 4. Background provider syncs depend on external APIs

Current severity: ongoing operational risk.

Google, Meta, Superleap, GA4, GSC, and other providers can fail because of token expiry, API rate limits, connection resets, schema changes, or missing export data. Google OAuth applications left in testing can expire refresh tokens after seven days.

### 5. Supply workbook input can be empty or structurally incomplete

Current severity: verify before the next run.

The automation history reported an empty Supply workbook `Backend` tab for a later-month workbook. Scheduled sync cannot produce valid Supply results from an empty source tab. Do not mark this resolved without checking the current workbook.

Historical workbook defects also included:

- Supply test copy had 27 formula errors initially. Verified Jaipur and budget-reference fixes reduced the visible count to 16. The remaining division errors lacked real CPL inputs and were not safely replaceable with zero.
- Demand `1. Nawada!B2` excluded the final four October days in one inspected copy.
- Monthly copies mixed fixed October dates with formulas, so copy-and-rename alone was unsafe.
- New Supply workbooks required their Demand `IMPORTRANGE` links to be updated.
- One location dashboard included a `goals` row among daily data.
- A Campaign Cost comparison found Google Supply HR Outside on 2026-10-18 at `₹340.19` in the sheet and `₹267.85` in the portal.
- An October 22 Demand comparison found 76 sheet leads versus 89 or 91 from different current Superleap snapshots.

Those values are historical mismatch evidence. Recheck the current source report before acting on them.

### 6. Source and installed runtime can drift

Current severity: operational risk.

The LaunchAgent runs the copy under Application Support, not the source checkout. A Git commit or source edit alone does not update the running portal. Deploy the changed files or rerun the installer, restart the service, and verify the live asset or endpoint.

### 7. Uncommitted deployment-file changes exist

Current severity: ownership unknown.

At the initial creation of this document, these files were already modified and were deliberately left untouched:

- `Dockerfile`
- `Start Portal.command`
- `deploy/README.md`
- `deploy/production.env.example`
- `docker-compose.production.yml`

Do not discard, overwrite, or commit these changes without checking their owner and intent.

### 8. Marketing Analytics test connections once polluted reporting data

Current severity: resolved incident with a permanent guard requirement.

Normal sync was accidentally enabled for view-only test workbooks. It imported 1,692 Demand rows and 1,505 Supply rows, inflating the reporting database by 3,197 rows. The affected connections were disabled, the database was backed up, only their rows were removed, and integrity checks passed.

Permanent rule: workbook viewing and reporting ingestion are separate permissions. Test workbook connections must remain view-only unless a specific ingestion run is approved and reconciled.

### 9. Ask AI had security and truthfulness findings

Current severity: requires regression verification before expanding assistant permissions.

Historical QA reported prompt-injection susceptibility, system-prompt echo, fabricated action confirmations, malformed-request connection corruption, raw error-page exposure, and status text that did not match actual privacy behavior. Some were external review findings and must be reproduced before being called active defects. The safe boundary remains read-only reporting with deterministic portal calculations outside the model.

### 10. Production Marketing Analytics scheduling is not proven by code alone

Current severity: verify end to end.

Runner code and an 08:00 Asia/Kolkata timer definition exist. That does not prove off-laptop execution. Do not call the schedule production-ready until a real run verifies source refresh, exact Superleap report semantics, campaign mapping, Backend writes, Campaign Cost, monthly rollover, and post-write workbook values on an independent host.

## Operating instructions

### Check the local service

```bash
launchctl print gui/$(id -u)/com.vortexveri.portal
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/readyz
```

`/healthz` proves the HTTP process is alive. `/readyz` also checks dependencies and can take several seconds.

### Restart the local service

```bash
launchctl kickstart -k gui/$(id -u)/com.vortexveri.portal
```

Wait for `/readyz` to return 200 before testing browser flows.

### Inspect logs

```bash
tail -n 200 "$HOME/Library/Logs/VortexVeri/portal.log"
tail -n 200 "$HOME/Library/Logs/VortexVeri/portal.err.log"
```

Do not paste database URLs, passwords, tokens, cookies, or connector credentials into logs, issues, commits, or this file.

### Run tests

```bash
cd /Users/macbookpro/Documents/marketing-portal
python3 -m unittest discover -s tests -t .
```

Before accepting a persistence change, also run the PostgreSQL migration tests and confirm tests do not inherit the production database URL.

### Deploy source changes to the local runtime

Preferred full deployment:

```bash
cd /Users/macbookpro/Documents/marketing-portal
python3 -m scripts.local_service install
```

After deployment, restart the service, verify `/readyz`, inspect the actual live browser page, and confirm the installed runtime contains the intended change.

## Verification checklist for portal changes

- Confirm the correct source repository and current Git status.
- Preserve unrelated user changes.
- Check whether the installed runtime differs from source.
- Run focused tests for the changed area.
- Run the full test suite for database, authentication, reporting, or scheduler changes.
- Restart the installed service when runtime code changes.
- Verify `/healthz` and `/readyz`.
- Test the affected browser flow in the live portal.
- Check browser console errors and server logs.
- Confirm no secret or production database URL entered Git history.
- Update this file with the change, result, remaining risk, date, and commit.

## Verification checklist for Marketing Analytics runs

- Confirm the exact Demand and Supply workbook names and target month.
- Confirm whether the run is read-only, staged, or explicitly authorized to write.
- Confirm the Superleap export covers the requested dates.
- Check Demand and Supply source fields, including Search Leads, Attempted, Dropped, qualification, campaign, city, and lead type.
- Confirm AP ROUGH WORK campaign mapping and cost inputs.
- Apply the three explicit campaign skips listed above.
- Reconcile totals before enabling writes.
- Refuse writes when a source tab is empty, formulas are missing, dates disagree, or workbook identity is uncertain.
- Reopen the output workbook and verify actual cell values after a write.
- Record the run result and evidence in this file.

## Maintenance rules

Update this file whenever any of the following happens:

- Portal code is committed or deployed.
- A database migration or backfill runs.
- A connector is added, removed, or materially changed.
- A production incident is found or resolved.
- A dashboard metric definition changes.
- A Marketing Analytics workbook is generated, reconciled, or written.
- A previously listed issue is proven fixed or becomes active again.
- Deployment location, runtime path, database host, or operating command changes.

For every update:

1. Change the `Last updated` date.
2. Add the verified result under completed work or the relevant section.
3. State whether an issue is open, mitigated, resolved, or requires verification.
4. Include evidence such as a commit, test result, endpoint result, row count, or workbook check.
5. Keep old decisions when they explain current behavior. Do not erase incident history merely because the immediate symptom disappeared.
6. Never add secrets.

## Current status summary

- Portal source, PostgreSQL persistence, models, migrations, backfill, authentication, reporting, scheduler, and Marketing Analytics automation exist.
- The active portal uses Supabase PostgreSQL and a local macOS LaunchAgent runtime.
- Paid reporting performance and test database isolation were fixed and verified.
- The sign-in infinite-spinner failure was mitigated with a request timeout and the live portal was recovered.
- The largest remaining structural limitation is application hosting. The database is remote, but the web application remains tied to one Mac.
- External API and Supabase network reliability remain operational risks and require monitoring.
- Workbook freshness and write safety must be reverified for every Marketing Analytics run.

