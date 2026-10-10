"""Recovery evidence must represent fresh temperature sets, not airflow ticks."""
import json
import unittest
from datetime import timedelta
from test_zehnder_monitor import ZehnderMonitor
from test_v2_audit_regressions import T, inputs

TEMPS = ('supply_temp', 'outdoor_temp', 'extract_temp')

class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        self.monitor.args = {}
        self.monitor.v2_state = self.monitor._corrected_defaults()
        self.monitor._save_corrected_state = lambda: None
        self.monitor.call_service = lambda service, **kw: setattr(self, 'payload', json.loads(kw['payload']))
        self.sources = inputs(T)

    def publish(self, seconds):
        now = T + timedelta(seconds=seconds)
        states = {self.monitor.E[k]: {'state': v['value'], 'attributes': {'unit_of_measurement': v['unit']},
                  'last_reported': v['reported_at']} for k, v in self.sources.items()}
        self.monitor.get_state = lambda eid, attribute=None: states.get(eid)
        self.monitor._corrected_now = lambda: now
        self.monitor._publish_corrected_sfp()
        return self.payload['recovery']

    def report(self, keys, seconds):
        for key in keys:
            self.sources[key]['reported_at'] = (T + timedelta(seconds=seconds)).isoformat()

    def test_age_uses_oldest_temperature_not_new_airflow(self):
        self.report(('supply_temp',), -300)
        result = self.publish(0)
        self.assertEqual(result['age_seconds'], 300)
        self.assertEqual(result['last_reported_at'], (T-timedelta(seconds=300)).isoformat())

    def test_airflow_only_reports_cannot_qualify_conditioned_recovery(self):
        for i in range(5):
            self.report(('power', 'supply_flow', 'exhaust_flow'), i*30)
            result = self.publish(i*30)
        self.assertEqual(result['conditioned_count'], 1)
        self.assertIsNone(result['conditioned_pct'])
        self.assertEqual(result['age_seconds'], 120)
        self.assertEqual(self.payload['sfp'], .7406)

    def test_async_temperatures_require_every_sensor_to_advance(self):
        self.publish(0)
        for i, key in enumerate(TEMPS, 1):
            self.report((key,), i*30)
            result = self.publish(i*30)
            self.assertEqual(result['conditioned_count'], 2 if i == 3 else 1)
        self.assertEqual(result['age_seconds'], 60)

    def test_unchanged_values_with_new_temperature_reports_qualify(self):
        for i in range(5):
            self.report(TEMPS, i*30)
            result = self.publish(i*30)
        self.assertEqual(result['conditioned_count'], 5)
        self.assertEqual(result['conditioned_pct'], 80)

    def test_invalid_current_age_is_not_age_of_historical_median(self):
        for i in range(5):
            self.report(TEMPS, i*30)
            self.publish(i*30)
        self.sources['bypass']['value'] = 10
        result = self.publish(150)
        self.assertEqual(result['conditioned_state'], 'historical')
        self.assertIsNone(result['age_seconds'])
        self.assertEqual(result['conditioned_age_seconds'], 30)
        self.assertEqual(result['conditioned_last_reported_at'], (T+timedelta(seconds=120)).isoformat())
        result = self.publish(1100)
        self.assertEqual(result['conditioned_count'], 0)
        self.assertIsNone(result['conditioned_pct'])

if __name__ == '__main__':
    unittest.main()
