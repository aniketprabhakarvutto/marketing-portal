import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_marketing_analytics as runner


def source(provider, **config):
    item = SimpleNamespace(provider=provider)
    item.list_of = lambda key: config.get(key, [])
    return item


class SourceGuardTests(unittest.TestCase):
    @patch.object(runner.sync, 'run_connection')
    @patch.object(runner.connections, 'all_connections')
    def test_missing_required_source_stops_before_sync(self, connections, sync):
        connections.return_value = [source('google_ads', customer_ids=['7329923258'])]
        with self.assertRaisesRegex(RuntimeError, 'meta_ads.*superleap_crm'):
            runner.run()
        sync.assert_not_called()

    @patch.object(runner, '_mapping_connection')
    @patch.object(runner.sync, 'run_connection')
    @patch.object(runner.connections, 'all_connections')
    def test_empty_source_stops_before_mapping_or_writes(self, connections, sync, mapping):
        connections.return_value = [source('google_ads', customer_ids=['7329923258']),
                                    source('meta_ads', account_ids=['1023092196493019']),
                                    source('superleap_crm')]
        sync.side_effect = [{'status': 'ok', 'name': 'Google'},
                            {'status': 'empty', 'name': 'Meta'},
                            {'status': 'ok', 'name': 'CRM'}]
        with self.assertRaisesRegex(RuntimeError, 'Meta'):
            runner.run()
        mapping.assert_not_called()

    def test_unrelated_ad_accounts_are_not_synced(self):
        relevant_google = source('google_ads', customer_ids=['7329923258'])
        unrelated_google = source('google_ads', customer_ids=['1283626194'])
        relevant_meta = source('meta_ads', account_ids=['act_1023092196493019'])
        crm = source('superleap_crm')
        self.assertEqual(runner._source_connections(
            [unrelated_google, relevant_google, relevant_meta, crm]),
            [relevant_google, relevant_meta, crm])

    @patch.object(runner, 'portal_today')
    @patch.object(runner.marketing_analytics, 'paid_rows', return_value=[])
    @patch.object(runner, '_backend_exports')
    @patch.object(runner, '_mapping_values', return_value=[])
    @patch.object(runner, '_mapping_connection')
    @patch.object(runner.sync, 'run_connection')
    @patch.object(runner.connections, 'all_connections')
    def test_export_month_must_match_cost_month(self, connections, sync, mapping,
                                                _values, exports, _rows, today):
        from datetime import date
        today.return_value = date(2026, 10, 25)
        connections.return_value = [source('google_ads', customer_ids=['7329923258']),
                                    source('meta_ads', account_ids=['1023092196493019']),
                                    source('superleap_crm')]
        sync.side_effect = [{'status': 'ok', 'name': 'Google'},
                            {'status': 'ok', 'name': 'Meta'},
                            {'status': 'ok', 'name': 'CRM'}]
        mapping.return_value = SimpleNamespace(get=lambda key: {'mapping_source_column': 'A',
                                                                 'mapping_target_column': 'B',
                                                                 'mapping_side_column': 'D'}.get(key))
        exports.return_value = ('demand.csv', 'supply.csv', '2026-09')
        with self.assertRaisesRegex(RuntimeError, 'does not match campaign-cost month'):
            runner.run()


if __name__ == '__main__':
    unittest.main()
