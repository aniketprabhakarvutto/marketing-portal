# Marketing Data Portal audit, 7 October 2026

## What was checked

- Read the portal server, database, sync, connector, automation, and browser code.
- Tested all nine main sections in the live local portal at a 762 px viewport. No page-level horizontal overflow or broken connector images appeared.
- Opened the native connector picker and Ahrefs setup. Confirmed the field help identifies the Ahrefs API key path.
- Ran 60 automated tests covering authentication boundaries, data calculations, connector setup, removal, scheduled jobs, database migration, and reporting. All passed.
- Reviewed official documentation for Google Ads, GA4, Search Console, Ahrefs, and LinkedIn access requirements.

This is a bounded audit, not proof that every external account and all 349 generic templates work. The generic entries are configuration templates, not platform-specific integrations.

## Fixed in this pass

1. Saved explorer views, transformation rules, destinations, and scheduled jobs can now be removed by an administrator. Removal uses a confirmation dialog and an audited server action. A destination used by an active job cannot be deleted first.
2. Case-insensitive duplicate connection names are rejected. Saving a workspace object or transformation rule with the same name updates it. Browser-saved views and calculated columns no longer show repeated names from older local data. The catalogue no longer shows duplicate names or its two junk entries.
3. Connector imagery no longer depends on external favicon or icon services. The nine native connectors have local vector marks; templates use stable initials. The overlapping letters on the Superleap mark were removed.
4. The status banner counts Superleap CRM as one source even though it has multiple datasets. It reports the worst CRM freshness state rather than inflating the count.
5. A scheduled source refresh now fails visibly if any source fails or is skipped, and its history names the affected source. It no longer reports success based only on the number of attempted sources.
6. Database migration now permits the backup job type already offered in the interface. The migration makes a database backup and preserves job history.
7. Google Ads API fallback versions now include currently supported versions. The connector help explains the developer token and production access requirement.
8. Generic connector setup now states what is required: an API endpoint, authentication, field mapping, and an allowlisted HTTPS host, or an export file. Removing an OAuth connection now explains that the remote grant must also be revoked at the source platform.

## Current live source state

| Source | Current state | Evidence or constraint |
| --- | --- | --- |
| Google Ads | Current through 2026-10-07 | Saved connection and successful sync history |
| Meta Ads | Current through 2026-10-07 | Saved connection and successful sync history |
| GA4 | Current through 2026-10-07 | Saved connection and successful sync history |
| Google Sheets | Two active connections | Both have successful sync history |
| Superleap CRM | Campaign leads through 2026-09-30; funnel stages through 2026-09-18 | The available detailed CRM export stops on 2026-09-18. UI code cannot produce newer source records. |
| Ahrefs | Delayed through 2026-09-24 | One stored historical row, no current saved Ahrefs connection. Reconnection needs an API key and API units. |
| Search Console | Turned off | Account permission has not been granted. |
| LinkedIn Ads and TikTok Ads | Setup available, unverified | No saved account or authorization to test. |

## Remaining work, in priority order

1. **Replace stale source data.** Obtain a fresh Superleap funnel extract or documented direct API access. Configure an Ahrefs API key. Grant Search Console property access. Do not relabel stale data as current.
2. **Reconcile metrics.** The quality view still flags 220 unclassified campaign rows. Revenue and customer lifetime value coverage need source mapping before CRM ROAS and LTV:CAC can be trusted. Compare report totals with source-platform totals at a fixed date range and timezone.
3. **Harden credential storage.** Connector secrets are currently JSON in the local SQLite database, protected by file permissions but not encrypted at rest. Production hosting needs a managed secret store or envelope encryption, key rotation, and backups with the same controls.
4. **Validate external connectors with owner credentials.** Google Ads needs an approved developer token for production reporting. LinkedIn needs an approved Advertising API product and the member's `r_ads_reporting` permission. Search Console needs property access. A UI test cannot substitute for these grants.
5. **Productionize the platform.** Move the app, scheduler, database, and backups off the Mac to managed hosting. Add monitoring for sync duration, failed rows, freshness, API quotas, and report delivery. Replace any temporary public tunnel with managed access.
6. **Reduce template claims.** Keep native connections separate from generic API/file templates. A catalogue entry should not show as “connected” until its endpoint, credentials, schema mapping, and a sample sync have passed.

## Documentation used

- [Google Ads API starter checklist](https://developers.google.com/google-ads/api/starter-checklist)
- [Google Ads API authentication](https://developers.google.com/google-ads/api/rest/auth)
- [Google Ads API sunset dates](https://developers.google.com/google-ads/api/docs/sunset-dates)
- [GA4 Data API error handling](https://developers.google.com/analytics/devguides/reporting/data/v1/errors)
- [Search Console authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing)
- [Ahrefs API key management](https://docs.ahrefs.com/en/api/docs/api-keys-creation-and-management)
- [LinkedIn Marketing API access and permissions](https://learn.microsoft.com/en-us/linkedin/marketing/increasing-access)
- [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
