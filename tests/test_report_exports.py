import csv
import io
import shutil
import subprocess
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from portal import api, automation


class ReportExportTests(unittest.TestCase):
    def test_report_normalizes_dates_and_exports_source_formula_as_text(self):
        with patch.object(api, "portal_today", return_value=date(2026, 10, 9)), \
             patch.object(api, "overview", return_value={"current":{"spend":100}}) as overview, \
             patch.object(api, "channels", return_value=[]), \
             patch.object(api, "campaigns", return_value=[{"campaign":"=1+2", "spend":-12}]):
            filename, content = automation.build_csv("2026-10-01", "2035-01-01")
        self.assertIn("2026-10-09", filename)
        self.assertNotIn("2035", filename)
        overview.assert_called_once_with("2026-10-01", "2026-10-09")
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        campaign = next(row for row in rows if row and "=1+2" in row[0])
        self.assertEqual(campaign, ["'\t=1+2", "-12"])

    def test_report_default_uses_reporting_timezone_yesterday(self):
        with patch.object(automation, "date", wraps=date) as host_date, \
             patch.object(api, "portal_today", return_value=date(2026, 10, 9)), \
             patch.object(api, "overview", return_value={"current":{}}), \
             patch.object(api, "channels", return_value=[]), \
             patch.object(api, "campaigns", return_value=[]):
            host_date.today.return_value = date(2026, 10, 8)
            filename, _ = automation.build_csv()
        self.assertEqual(filename, "marketing-report-2026-09-09-to-2026-10-08.csv")

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_browser_csv_quotes_and_neutralizes_formula_text(self):
        script = r'''
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const begin=source.indexOf('function csvCell('),end=source.indexOf('function csvBlob(',begin);
const ctx=vm.createContext({});vm.runInContext(source.slice(begin,end)+';this.cell=csvCell',ctx);
for(const value of ['=1+2','+SUM(A1:A2)','-1+2','@SUM(A1)','＝1+2',' \t=1+2']) {
 const result=ctx.cell(value);assert(result.startsWith('"\'\t'),result);
}
assert.strictEqual(ctx.cell('plain,"name"'),'"plain,""name"""');
assert.strictEqual(ctx.cell(-12),'"-12"');
'''
        app = Path(__file__).resolve().parents[1] / "portal/web/app.js"
        result = subprocess.run(["node", "-e", script, str(app)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    unittest.main()
