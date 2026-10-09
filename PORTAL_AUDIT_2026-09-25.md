# Marketing Data Portal audit — 25 September 2026

## Executive finding

The portal renders without browser console errors and its reporting endpoints respond quickly, but it is not ready for organisation-wide reliance. The main risks are incorrect comparisons, missing data shown as zero, sync failures hidden by freshness labels, connector data that can overwrite another connector, and automation options that are saved but never executed.

## Critical — fix before relying on the numbers

1. **Previous-period comparisons are mathematically misleading.** The selected 30-day range has 30 days of paid and GA4 data, while the previous period has only 8. The Overview still reports spend `+447.2%`, paid clicks `+352%`, and sessions `+302.6%`. Deltas must be suppressed or calculated over equal covered days.

2. **Missing CRM dates are charted as zero.** CRM data stops on 18 September, but the chart continues through 25 September with zero leads and sales. This visually claims there were no outcomes when the data is actually missing. Missing points must be gaps, with the missing interval clearly marked.

3. **Connection health is false-positive.** The Overview marks Google Ads, Meta Ads, and GA4 as “Current” because stored rows are recent. Their latest sync attempts failed. Demand and Supply Sheets also failed. The header says only two sources need attention even though five enabled connectors currently have failed sync status. Freshness and last-sync health need separate signals.

4. **CRM revenue and ROAS are misleading.** The CRM dataset has no revenue values, yet Overview displays `CRM revenue ₹0` and `CRM ROAS 0×`. These should show “Unavailable — revenue is not mapped,” not zero.

5. **The CRM funnel is internally inconsistent.** For Supply/Sell data, `Qualified = 11,207` while `Contacted = 8,955`. A funnel stage cannot exceed its preceding stage if the chart represents progression. The stage mapping or the funnel interpretation must be corrected before using conversion rates.

6. **Multiple connectors can overwrite one another.** Natural unique keys in several warehouse tables omit `connection_id`. An upsert from a second connection can replace the first connection’s row and ownership. This breaks isolated connector pages and makes deletion by connection unreliable. Add `connection_id` to the natural key where separate accounts/sources must coexist, then migrate and re-sync.

7. **Most “Data automation” workflows do not run.** Report emails, alerts, budget monitoring, recommendations, CRM follow-up, data-quality checks, offline conversion preparation, snapshots, and custom rules are only stored in browser `localStorage`. No backend scheduler executes them. Only the full sync/top-up schedule is real. The UI must label unavailable actions as planned, or implement them server-side.

8. **LAN access can expose destructive controls without authentication.** When `PORTAL_ACCESS_KEY` is empty, every local/private-network request is authorised. Any device on the LAN can edit or delete connections and trigger syncs. State-changing routes also have no CSRF protection. Organisation use needs mandatory authentication, CSRF protection, roles, and an audit log.

## High priority — functionality and data trust

9. **Connector-page search is incomplete.** Source pages fetch at most 300 rows. Search, column filtering, and export operate only on those loaded rows, although the page may contain thousands. A campaign outside the first 300 cannot be found. Add server-side search, sorting, pagination, and full export.

10. **The Overview blends incompatible channel taxonomies.** Paid data uses `google_ads`/`meta_ads`, CRM uses a small normalised channel set, and GA4 contributes source/medium combinations. The result is 47 rows including `facebook/cpm`, `(not set)/(not set)`, `chatgpt.com/ai-assistant`, and `Direct`, which fragments the same business channel and makes cross-channel CPL/CAC hard to interpret. Introduce one governed channel mapping table and an “Unmapped” review queue.

11. **The header row count is wrong for the whole portal.** It shows 7,658 rows because it counts only paid, web, and CRM daily rows. All provenance tables currently contain 17,194 rows. Rename it to “core reporting rows” or count every included dataset.

12. **Search Console has no data.** This is expected until access is granted, but Overview and Search remain incomplete. Keep the empty state, and exclude Search Console from any “all sources healthy” summary.

13. **The connector navigation does not scale.** There are already 15 top-level tabs. At an 892 px viewport, the navigation content is 2,391 px wide. On mobile it becomes a four-column grid with many rows and consumes a large part of the screen. Keep primary sections in the main navigation and place connected sources in a compact switcher/menu.

14. **Important values are truncated.** The Overview table ellipsises channel names and large currency figures at normal laptop width, so CAC and other figures cannot be read without resizing. Numeric columns need minimum widths, alignment, and a hover/focus value disclosure.

15. **Saved views, custom metrics, column settings, presets, and automation rules are device-local.** They live in `localStorage`, so another employee/device cannot see them and clearing browser data deletes them. Organisation features must be stored in the portal database and scoped by user/team.

16. **“Direct API” is a generic mapper, not a verified connector.** Hundreds of directory entries show “API / File,” but only ten provider implementations exist. Unsupported services open a generic REST/CSV mapping form. The catalogue should clearly distinguish tested native connectors, configurable generic imports, and unavailable/planned integrations.

## Medium priority — usability, accessibility, and maintainability

17. **Tabs do not implement expected keyboard behavior.** Elements use `role=tab`, but there is no arrow-key navigation or roving `tabindex`. With 15 tabs, keyboard users must tab through every item.

18. **Several controls are smaller than the 44×44 px touch target.** Desktop and calendar controls commonly use 28–38 px heights; some text is 8.5–10 px. This is difficult on touch devices and weak for low-vision users.

19. **Column and row resizing depends on dragging.** There is no keyboard-accessible resize alternative or direct size input for individual columns/rows.

20. **Large raw tables are rendered directly into the DOM.** Connector pages render 300 rows at once, making navigation and assistive-technology output very large. Use pagination or virtualised rendering with an accessible fallback.

21. **Technical names leak into the user interface.** Connector pages expose `crm_campaign_daily`, `meta_ads`, raw field names, and ingest timestamps without business-friendly explanations.

22. **Modal focus handling is incomplete.** Modals do not visibly establish a focus trap, restore focus to the trigger, or set initial focus consistently. Background controls can remain in the keyboard sequence.

23. **The API returns raw exception type/message for unexpected failures.** This can reveal implementation details. Log the full error locally and return a stable public error code/message.

24. **Credentials are plaintext inside SQLite.** File permissions reduce local exposure, but credentials are not encrypted at rest. This is insufficient for a shared organisation deployment or backups.

25. **There is no automated regression suite and the folder is not a Git repository.** The portal has accumulated many interconnected UI, sync, and reporting changes without repeatable tests or change history. Add focused API/data tests, browser smoke tests, and version control before wider rollout.

## Verified working

- All core reporting functions completed without exceptions in direct backend checks.
- Normal API computations complete in milliseconds on the current database.
- The Overview, Paid, Website, CRM, Connections, connector pages, and Data Hub render without browser console warnings/errors during the audit.
- Connected-source pages resolve their provider table and display stored rows.
- Search Console correctly shows an empty/unavailable state rather than fabricated data.

## Recommended repair order

1. Fix period comparison, missing-date rendering, sync-health reporting, CRM revenue availability, and funnel mapping.
2. Migrate warehouse uniqueness keys to preserve connector ownership.
3. Secure LAN access and state-changing routes.
4. Make connector search/export server-side and replace the expanding tab bar with a source switcher.
5. Move saved views, metrics, and real automation rules to the backend; hide workflow types that are not implemented.
6. Complete accessibility, responsive, and automated regression testing.
