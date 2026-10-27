import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal import marketing_db_exports as exports
from portal.providers.base import Connection


class MarketingDBExportTests(unittest.TestCase):
    def make_export(self, role="demand", month="2026-10"):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", delete=False)
        with handle:
            writer = csv.writer(handle)
            writer.writerow(exports.HEADERS[role])
            writer.writerow([month + "-22T05:30:00+05:30", "Campaign", "google"] + ["1"] * 16)
        return Path(handle.name)

    def test_exact_demand_export_is_parsed(self):
        path = self.make_export()
        rows = exports.read_export(path, "demand", "2026-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "2026-10-22T05:30:00+05:30")
        self.assertEqual(rows[0][3:], [1] * 16)

    def test_wrong_headers_or_month_are_rejected(self):
        path = self.make_export()
        with self.assertRaisesRegex(ValueError, "outside 2026-09"):
            exports.read_export(path, "demand", "2026-09")
        text = path.read_text().replace("Search Leads", "Search Lead")
        path.write_text(text)
        with self.assertRaisesRegex(ValueError, "headers"):
            exports.read_export(path, "demand", "2026-10")

    @patch("portal.marketing_db_exports.request")
    @patch("portal.marketing_db_exports.get")
    @patch("portal.marketing_db_exports._token", return_value="token")
    @patch("portal.marketing_db_exports.conns.get")
    def test_backend_write_targets_only_c_through_u_in_test_copy(self, get_conn, _token, get, request):
        get_conn.return_value = Connection(17, "google_sheets", "Demand Test", {
            "spreadsheet_url": exports.TEST_WORKBOOKS["demand"], "analytics_role": "demand",
        }, {}, True)
        get.return_value = {"sheets": [{"properties": {
            "title": "Backend", "sheetId": exports.BACKEND_SHEET_IDS["demand"],
            "gridProperties": {"rowCount": 50500, "columnCount": 47},
        }}]}
        result = exports.write_backend(17, "demand", [["2026-10-22", "C", "google"] + [1] * 16])
        update = request.call_args.kwargs["json_body"]["requests"][0]["updateCells"]
        self.assertEqual(update["range"]["startRowIndex"], 2)
        self.assertEqual(update["range"]["startColumnIndex"], 2)
        self.assertEqual(update["range"]["endColumnIndex"], 21)
        self.assertEqual(result["range"], "C3:U3")


if __name__ == "__main__":
    unittest.main()
