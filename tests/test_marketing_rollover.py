import unittest

from portal.marketing_rollover import month_bounds, plan_import_link_updates, review_snapshot


class MonthlyRolloverTests(unittest.TestCase):
    def test_month_boundaries_and_invalid_dates(self):
        self.assertEqual(tuple(map(str, month_bounds("2024-02"))), ("2024-02-01", "2024-03-01"))
        self.assertEqual(tuple(map(str, month_bounds("2026-12"))), ("2026-12-01", "2027-01-01"))
        for value in ("2026-13", "2026-1", "0000-01", "9999-12"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                month_bounds(value)

    def test_snapshot_flags_old_sheet_links_and_outdated_dates(self):
        result = review_snapshot("2026-11", [
            {"sheet": "Campaign Cost", "address": "A1", "formula": '=IMPORTRANGE("https://docs.google.com/spreadsheets/d/old-id/edit", "Cost!A1")'},
            {"sheet": "Backend", "address": "B2", "date": "2026-10-31"},
            {"sheet": "Backend", "address": "B3", "date": "2026-11-01"},
            {"sheet": "Control Room", "address": "C1", "formula": "=DATE(2026,10,1)"},
        ], ["old-id"])
        self.assertEqual([entry["issue"] for entry in result["findings"]],
                         ["old_workbook_link", "import_range_review", "date_outside_target_month", "formula_date_review"])
        self.assertFalse(result["complete"])

    def test_malformed_snapshot_fails_closed(self):
        with self.assertRaises(ValueError):
            review_snapshot("2026-11", [{"sheet": "Backend", "address": "a0"}], [])
        with self.assertRaises(ValueError):
            review_snapshot("2026-11", [{"sheet": "Backend", "address": "A1", "date": "not a date"}], [])

    def test_link_plan_preserves_formula_and_only_replaces_matching_imports(self):
        formula = ('=IF(A1=1,IMPORTRANGE("https://docs.google.com/spreadsheets/d/old-id/edit",'
                   '"Costs!A1:B5"),IMPORTRANGE("https://docs.google.com/spreadsheets/d/other-id/edit",'
                   '"Costs!A1"))')
        result = plan_import_link_updates([{"sheet": "Campaign Cost", "address": "A1", "formula": formula}],
                                          {"old-id": "new-id"})
        self.assertEqual(result[0]["before"], formula)
        self.assertEqual(result[0]["after"], formula.replace('/d/old-id/', '/d/new-id/'))
        self.assertEqual(result[0]["replaced_workbooks"], ["old-id"])

    def test_link_plan_does_not_rewrite_urls_in_text_or_dynamic_imports(self):
        cells = [
            {"sheet": "Tab", "address": "A1", "formula": '=HYPERLINK("https://docs.google.com/spreadsheets/d/old-id/edit","link")'},
            {"sheet": "Tab", "address": "A2", "formula": '=IMPORTRANGE(A1,"Sheet1!A1")'},
            {"sheet": "Tab", "address": "A3", "formula": '=IF(TRUE,"IMPORTRANGE(""https://docs.google.com/spreadsheets/d/old-id/edit"",""A1"")",0)'},
        ]
        self.assertEqual(plan_import_link_updates(cells, {"old-id": "new-id"}), [])

    def test_link_plan_rejects_invalid_or_duplicate_targets_and_cells(self):
        cell = {"sheet": "Tab", "address": "A1", "formula": '=1'}
        for replacements in ({"old": "old"}, {"old": "bad/id"}, {"one": "new", "two": "new"}):
            with self.subTest(replacements=replacements), self.assertRaises(ValueError):
                plan_import_link_updates([cell], replacements)
        with self.assertRaises(ValueError):
            plan_import_link_updates([cell, cell], {"old": "new"})


if __name__ == "__main__":
    unittest.main()
