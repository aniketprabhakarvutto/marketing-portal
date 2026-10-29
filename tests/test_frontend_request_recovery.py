import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRequestRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web = ROOT / "portal" / "web"
        cls.script = (web / "app.js").read_text(encoding="utf-8")
        cls.html = (web / "index.html").read_text(encoding="utf-8")
        cls.styles = (web / "styles.css").read_text(encoding="utf-8")

    def test_shared_api_helper_aborts_timed_out_requests(self):
        self.assertIn("const API_REQUEST_TIMEOUT_MS = 25000", self.script)
        self.assertIn("const controller = new AbortController()", self.script)
        self.assertIn("The portal took too long to respond. Please retry.", self.script)

    def test_marketing_workbook_refresh_is_explicit(self):
        self.assertIn('id="marketingRefreshWorkbooks"', self.html)
        self.assertIn('/api/marketing-analytics/workbooks${refreshWorkbooks ? "?refresh=1" : ""}', self.script)
        self.assertIn("Workbook list refreshed from Google Drive", self.script)

    def test_marketing_failure_has_accessible_retry_ui(self):
        self.assertIn('id="marketingAnalyticsRetry" class="marketing-retry-state" role="alert"', self.html)
        self.assertIn('id="marketingAnalyticsRetryButton"', self.html)
        self.assertIn("function setMarketingAnalyticsRetry", self.script)
        self.assertIn(".marketing-retry-state", self.styles)

    def test_data_quality_failure_keeps_a_visible_retry_control(self):
        self.assertIn('id="retryDataQuality"', self.script)
        self.assertIn("Data Quality could not load", self.script)


if __name__ == "__main__":
    unittest.main()
