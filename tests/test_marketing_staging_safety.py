import unittest
from unittest.mock import patch

from portal import marketing_analytics
from portal.providers.base import Connection
from scripts import run_marketing_analytics as runner


def workbook(connection_id, role, spreadsheet_id, tab="__vortex_cost_test"):
    return Connection(connection_id, "google_sheets", role, {
        "analytics_role": role,
        "spreadsheet_url": spreadsheet_id,
        "campaign_cost_sheet": tab,
    }, {}, True)


class StagingSafetyTests(unittest.TestCase):
    @patch("scripts.run_marketing_analytics.connections.all_connections")
    def test_only_approved_workbooks_are_selected(self, connections):
        original = workbook(1, "demand", "original_workbook_1234567890")
        demand = workbook(2, "demand", runner.TEST_WORKBOOKS["demand"])
        supply = workbook(3, "supply", runner.TEST_WORKBOOKS["supply"])
        connections.return_value = [original, demand, supply]
        self.assertEqual([item[1].id for item in runner._test_destinations()], [2, 3])

    @patch("scripts.run_marketing_analytics.connections.all_connections")
    def test_missing_or_duplicate_test_copy_stops_before_any_writes(self, connections):
        demand = workbook(2, "demand", runner.TEST_WORKBOOKS["demand"])
        connections.return_value = [demand]
        with self.assertRaisesRegex(RuntimeError, "supply test workbook"):
            runner._test_destinations()
        connections.return_value = [demand, demand]
        with self.assertRaisesRegex(RuntimeError, "demand test workbook"):
            runner._test_destinations()

    @patch("portal.marketing_analytics.request")
    @patch("portal.marketing_analytics.get")
    @patch("portal.marketing_analytics._token", return_value="token")
    @patch("portal.marketing_analytics.conns.get")
    def test_staging_values_replace_all_old_rows_without_formulas(self, get_conn, _token, get, request):
        get_conn.return_value = workbook(2, "demand", runner.TEST_WORKBOOKS["demand"])
        get.return_value = {"sheets": [{"properties": {"title": "__vortex_cost_test", "sheetId": 8,
                            "gridProperties": {"rowCount": 100, "columnCount": 10}}}]}
        request.return_value = {}
        marketing_analytics.write_cost_tab(2, "__vortex_cost_test", [["=1+1", 42.5]])
        requests = request.call_args.kwargs["json_body"]["requests"]
        update = requests[0]["updateCells"]
        self.assertEqual(update["range"]["endRowIndex"], 1)
        self.assertEqual(update["range"]["endColumnIndex"], 2)
        self.assertEqual(update["rows"][0]["values"][0]["userEnteredValue"], {"stringValue": "=1+1"})
        self.assertEqual(update["rows"][0]["values"][1]["userEnteredValue"], {"numberValue": 42.5})
        self.assertEqual(update["fields"], "userEnteredValue")
        self.assertEqual(requests[1]["repeatCell"]["range"]["startColumnIndex"], 2)
        self.assertEqual(requests[2]["repeatCell"]["range"]["startRowIndex"], 1)

    @patch("portal.marketing_analytics._token")
    @patch("portal.marketing_analytics.conns.get")
    def test_writer_rejects_other_workbooks_before_fetching_credentials(self, get_conn, token):
        get_conn.return_value = workbook(1, "demand", "original_workbook_1234567890")
        with self.assertRaisesRegex(ValueError, "approved private test workbook"):
            marketing_analytics.write_cost_tab(1, "__vortex_cost_test", [["Date"]])
        token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
