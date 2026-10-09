import unittest

from portal.providers.shape import build_row, to_date


class DateMappingTests(unittest.TestCase):
    def test_ambiguous_sheet_dates_follow_configured_order(self):
        self.assertEqual(to_date("9/5/2026", "month_first"), "2026-09-05")
        self.assertEqual(to_date("9/5/2026", "day_first"), "2026-05-09")
        self.assertEqual(to_date("9/30/2026", "month_first"), "2026-09-30")

    def test_sheet_mapping_applies_date_order_to_report_row(self):
        row = build_row(
            {"B": "9/5/2026", "C": "Example campaign"},
            "crm_campaign_daily", {"date": "B", "campaign_name": "C"},
            date_order="month_first",
        )
        self.assertEqual(row["date"], "2026-09-05")
        self.assertEqual(row["campaign_name"], "Example campaign")


if __name__ == "__main__":
    unittest.main()
