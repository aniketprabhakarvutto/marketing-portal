from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from portal import api, assistant, db
from portal.config import APP


class AssistantReportingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        db.close_connection()
        self.old_path = APP.db_path
        self.old_database_url = APP.database_url
        self.temp = TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "assistant-reporting.db")
        APP.database_url = ""
        db.init_db()
        self.today_patch = patch.object(api, "portal_today", return_value=date(2026, 10, 9))
        self.today_patch.start()
        self.model_patch = patch.object(assistant, "_model_available", return_value=False)
        self.model_patch.start()
        assistant._limits.clear()
        db.execute("INSERT INTO connections (id, provider, name) VALUES (14, 'superleap_crm', 'Direct Superleap')")

        paid = []
        crm_campaign = []
        crm_daily = []
        for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
            spend = {"2026-10-07": 100, "2026-10-08": 200, "2026-10-09": 300}[day]
            paid.append({"date": day, "source": "google_ads", "account_id": "google",
                         "campaign_id": "g-supply-jaipur", "campaign_name": "Google_Supply_Jaipur",
                         "spend": spend, "clicks": spend // 10, "impressions": spend * 10,
                         "conversion_value": spend * 2, "daily_budget": 500,
                         "currency": "INR", "connection_id": 1})
            crm_campaign.append({"date": day, "channel": "google_ads",
                                 "campaign_id": "g-supply-jaipur", "campaign_name": "Google_Supply_Jaipur",
                                 "lead_type": "sell", "leads": 5, "won": 1,
                                 "connection_id": 14})
            crm_daily.append({"date": day, "channel": "google_ads", "source": "google",
                              "lead_type": "sell", "leads": 5, "qualified": 4,
                              "contacted": 3, "booked": 2, "completed": 1, "won": 1,
                              "connection_id": 14})
        crm_daily.append({"date": "2026-10-08", "channel": "meta_ads", "source": "facebook",
                          "lead_type": "buy", "leads": 10, "qualified": 0,
                          "contacted": 4, "booked": 2, "completed": 1, "won": 0,
                          "connection_id": 14})
        paid.extend([
            {"date": "2026-10-08", "source": "google_ads", "account_id": "google",
             "campaign_id": "g-demand-jaipur", "campaign_name": "Google_Demand_Jaipur",
             "spend": 50, "clicks": 5, "impressions": 0, "conversion_value": 0,
             "daily_budget": 100, "currency": "INR",
             "connection_id": 1},
            {"date": "2026-10-08", "source": "meta_ads", "account_id": "meta",
             "campaign_id": "m-supply-jaipur", "campaign_name": "Meta_Supply_Jaipur",
             "spend": 80, "clicks": 8, "impressions": 0, "conversion_value": 0,
             "daily_budget": 100, "currency": "INR",
             "connection_id": 2},
            {"date": "2026-10-08", "source": "meta_ads", "account_id": "meta",
             "campaign_id": "m-demand-jaipur", "campaign_name": "Meta_Demand_Jaipur",
             "spend": 70, "clicks": 7, "impressions": 0, "conversion_value": 0,
             "daily_budget": 100, "currency": "INR",
             "connection_id": 2},
        ])
        db.upsert("paid_metrics", paid)
        db.upsert("crm_campaign_daily", crm_campaign)
        db.upsert("crm_daily", crm_daily)
        # Campaign-tagged opportunity without a verified revenue field. The
        # CRM schema stores unmapped revenue as zero.
        db.upsert("crm_leads", [{"external_id": "lead-no-revenue", "created_date": "2026-10-08",
                                 "utm_campaign": "Google_Supply_Jaipur", "channel": "google_ads",
                                 "lead_type": "sell", "revenue": 0, "connection_id": 14}])

    def tearDown(self) -> None:
        db.close_connection()
        self.model_patch.stop()
        self.today_patch.stop()
        APP.db_path = self.old_path
        APP.database_url = self.old_database_url
        self.temp.cleanup()

    def answer(self, question: str) -> str:
        return assistant.ask(26000 + len(question), question, "2026-10-01", "2026-10-09")["answer"]

    def test_named_crm_roas_abstains_when_revenue_is_unmapped(self):
        self.assertIsNone(api.overview("2026-10-07", "2026-10-09")["current"]["revenue"])
        self.assertEqual(api.campaigns("2026-10-07", "2026-10-09", "google_ads")[0]["crm_revenue"], 0)
        answer = self.answer("CRM ROAS for Google_Supply_Jaipur campaign last 3 days")
        self.assertIn("unavailable", answer)
        self.assertNotIn("0×", answer)

    def test_named_campaign_pacing_does_not_use_other_campaign_budgets(self):
        # Jaipur supply spend is 100+200+300. Its daily budget is 500 for
        # three days, so its true pacing is 40%, while all-campaign pacing is
        # (600+50+80+70)/(1500+100+100+100) = 44.4%.
        answer = self.answer("What is budget pacing for Google_Supply_Jaipur campaign last 3 days?")
        self.assertIn("unavailable", answer)
        self.assertNotIn("44.4%", answer)

    def test_manual_campaign_revenue_per_lead_uses_that_campaigns_leads(self):
        # A user supplies hypothetical campaign revenue, not a CRM mapping.
        # The campaign has 15 matched CRM leads. Organisation leads are 25.
        answer = self.answer("Calculate revenue per lead for Google_Supply_Jaipur campaign "
                             "last 3 days using CRM revenue INR 1,200")
        self.assertIn("INR 80.00", answer)
        self.assertNotIn("INR 48.00", answer)

    def test_google_ads_ltv_cac_abstains_on_partial_crm_matching(self):
        # Google supply Jaipur has CRM matches. Google demand Jaipur has none.
        # Dividing both campaigns' spend by the matched campaign's sales would
        # manufacture a platform CAC and therefore a false LTV:CAC ratio.
        answer = self.answer("What is Google Ads LTV:CAC with lifetime value INR 1,000 "
                             "for last 3 days?")
        self.assertIn("unavailable", answer)
        self.assertNotIn("4.62×", answer)

    def test_google_supply_cac_last_three_days_names_and_calculates_campaign(self):
        answer = self.answer("What is the CAC for Google supply campaign of last 3 days with campaign name")
        self.assertIn("2026-10-07 through 2026-10-09", answer)
        self.assertIn("Google_Supply_Jaipur", answer)
        self.assertIn("spend INR 600.00; CRM sales 3; CAC (cost per sale) INR 200.00", answer)
        self.assertNotIn("Google_Demand_Jaipur", answer)

    def test_yesterday_facebook_supply_spend_excludes_demand_and_google(self):
        answer = self.answer("How much money did I spend on Facebook in supply yesterday?")
        self.assertIn("INR 80.00", answer)
        self.assertIn("2026-10-08 through 2026-10-08", answer)
        self.assertIn("Meta_Supply_Jaipur", answer)

    def test_jaipur_yesterday_splits_demand_supply_with_classification_evidence(self):
        answer = self.answer("How much money did I spend in Jaipur yesterday in demand and supply both?")
        self.assertIn("Demand: INR 120.00; Supply: INR 280.00", answer)
        self.assertIn("do not prove geographic targeting", answer)

    def test_named_campaign_cpc_and_cpl_use_campaign_totals(self):
        answer = self.answer("What is CPC and CPL for Google_Supply_Jaipur campaign last 3 days?")
        self.assertIn("CPL (cost per lead): INR 40.00", answer)
        self.assertIn("CPC (cost per click): INR 10.00", answer)

    def test_partial_budget_pacing_compares_only_budgeted_campaign_spend(self):
        db.execute("UPDATE paid_metrics SET daily_budget=NULL WHERE campaign_id='m-demand-jaipur'")
        pacing = api.budget_pacing("2026-10-07", "2026-10-09")
        self.assertFalse(pacing["complete"])
        self.assertAlmostEqual(pacing["pacing_percent"], 730 / 1700 * 100)
        self.assertAlmostEqual(pacing["budgeted_spend"], 730)


if __name__ == "__main__":
    unittest.main()
