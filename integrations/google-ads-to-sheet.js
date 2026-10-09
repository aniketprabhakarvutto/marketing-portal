/**
 * Google Ads → Google Sheet → Marketing Data Portal
 * ------------------------------------------------
 * A way to get campaign data out of Google Ads WITHOUT the Google Ads API.
 *
 * Google Ads Scripts run inside the account itself, so they need no developer
 * token, no API access level, and no approval — if you can open the account,
 * you can run this. It writes daily campaign stats to a Google Sheet, and the
 * portal reads that sheet over HTTPS.
 *
 * SETUP
 *
 *  1. Create a Google Sheet. Copy its URL into SHEET_URL below.
 *
 *  2. In Google Ads (the account with the data — not the manager account):
 *       Tools → Bulk actions → Scripts → +
 *     Paste this file in, press Authorise, then Preview to check it runs.
 *
 *  3. Schedule it: on the Scripts list, set Frequency to Daily (early morning,
 *     after the previous day has settled).
 *
 *  4. Publish the sheet as CSV:
 *       File → Share → Publish to web → choose the sheet → Comma-separated
 *       values (.csv) → Publish
 *     Copy the published link.
 *
 *  5. In the portal: Connections → Add a connection → CSV file, folder or URL
 *       File, folder or URL   the published link from step 4
 *       Load into table       paid_metrics
 *       Source label          google_ads
 *       Field mapping         the JSON block at the bottom of this file
 *
 * The portal re-reads the sheet on every sync and upserts on date + campaign,
 * so the script overwriting the sheet each day is safe and never double-counts.
 *
 * NOTE ON PUBLISHING: "Publish to web" makes the sheet readable by anyone with
 * the link. It will contain campaign names, spend and conversions. If that is
 * more than you want exposed, keep the sheet private and have the script write
 * a CSV to a local folder via Drive sync instead, then point the portal at the
 * file path rather than a URL.
 */

var SHEET_URL = 'PASTE_YOUR_SHEET_URL_HERE';
var TAB_NAME = 'data';        // created if missing
var LOOKBACK_DAYS = 30;       // matches the portal's rolling refresh window

var HEADERS = [
  'date', 'account_id', 'account_name', 'currency',
  'campaign_id', 'campaign_name', 'channel',
  'impressions', 'clicks', 'cost', 'conversions', 'conv_value'
];

function main() {
  var account = AdsApp.currentAccount();
  var timezone = account.getTimeZone();

  var end = new Date();
  var start = new Date(end.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000);
  var from = Utilities.formatDate(start, timezone, 'yyyy-MM-dd');
  var to = Utilities.formatDate(end, timezone, 'yyyy-MM-dd');

  var query =
    'SELECT segments.date, campaign.id, campaign.name, ' +
    'campaign.advertising_channel_type, metrics.impressions, metrics.clicks, ' +
    'metrics.cost_micros, metrics.conversions, metrics.conversions_value ' +
    'FROM campaign ' +
    "WHERE segments.date BETWEEN '" + from + "' AND '" + to + "' " +
    'AND metrics.impressions > 0';

  var rows = [HEADERS];
  var results = AdsApp.search(query);

  while (results.hasNext()) {
    var row = results.next();
    var metrics = row.metrics || {};
    var campaign = row.campaign || {};

    rows.push([
      row.segments.date,
      account.getCustomerId(),
      account.getName(),
      account.getCurrencyCode(),
      String(campaign.id || ''),
      campaign.name || '',
      campaign.advertisingChannelType || '',
      Number(metrics.impressions || 0),
      Number(metrics.clicks || 0),
      // Google reports cost in micros; the portal wants currency units.
      Number(metrics.costMicros || 0) / 1000000,
      Number(metrics.conversions || 0),
      Number(metrics.conversionsValue || 0)
    ]);
  }

  writeSheet(rows);
  Logger.log('Wrote ' + (rows.length - 1) + ' rows for ' + from + ' → ' + to);
}

function writeSheet(rows) {
  var spreadsheet = SpreadsheetApp.openByUrl(SHEET_URL);
  var sheet = spreadsheet.getSheetByName(TAB_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(TAB_NAME);
  }

  // Replace the contents wholesale. The portal upserts on date + campaign, so
  // a full rewrite of the window is the simplest thing that stays correct.
  sheet.clear();
  if (rows.length > 1) {
    sheet.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  } else {
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
  }
}

/**
 * FIELD MAPPING — paste this into the portal's "Field mapping" box:
 *
 * {
 *   "date": "date",
 *   "account_id": "account_id",
 *   "account_name": "account_name",
 *   "currency": "currency",
 *   "campaign_id": "campaign_id",
 *   "campaign_name": "campaign_name",
 *   "channel": "channel",
 *   "impressions": "impressions",
 *   "clicks": "clicks",
 *   "spend": "cost",
 *   "conversions": "conversions",
 *   "conversion_value": "conv_value"
 * }
 *
 * RUNNING ACROSS SEVERAL ACCOUNTS
 * From a manager account, wrap the body in an account iterator instead:
 *
 *   function main() {
 *     var accounts = AdsManagerApp.accounts().withIds(['1234567890']).get();
 *     var rows = [HEADERS];
 *     while (accounts.hasNext()) {
 *       AdsManagerApp.select(accounts.next());
 *       rows = rows.concat(collectRows());
 *     }
 *     writeSheet(rows);
 *   }
 *
 * where collectRows() returns this file's row-building loop without the header.
 */
