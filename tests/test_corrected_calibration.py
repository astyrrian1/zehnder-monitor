"""Clean-filter confirmation must precede every corrected reference."""
import unittest
from datetime import datetime, timedelta, timezone

from test_zehnder_monitor import ZehnderMonitor


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        self.monitor.args = {}
        self.monitor.v2_state = self.monitor._corrected_defaults()
        self.saved = []
        self.monitor._save_corrected_state = lambda: self.saved.append(self.monitor.v2_state.copy())

    def test_confirmation_is_idempotent_and_sets_exact_deadlines(self):
        t = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.assertTrue(self.monitor._confirm_clean_filters('event-1', t.isoformat()))
        c = self.monitor.v2_state['calibration']
        self.assertEqual(c['state'], 'settling')
        self.assertEqual(c['confirmed_at'], t.isoformat())
        self.assertEqual(c['settling_until'], (t + timedelta(hours=2)).isoformat())
        self.assertEqual(c['learning_until'], (t + timedelta(hours=74)).isoformat())
        self.assertFalse(self.monitor._confirm_clean_filters('event-1', t.isoformat()))
        self.assertEqual(len(self.saved), 1)

    def test_new_confirmation_archives_old_reference(self):
        t = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.monitor._confirm_clean_filters('first', t.isoformat())
        self.monitor.v2_state['calibration']['references'] = {'Medium': {'sfp': 0.74}}
        self.monitor._confirm_clean_filters('second', (t + timedelta(days=100)).isoformat())
        self.assertEqual(self.monitor.v2_state['archived_references'][0]['cycle_id'], 'first')
        self.assertEqual(self.monitor.v2_state['calibration']['references'], {})

    def test_timer_increase_only_requests_confirmation(self):
        self.monitor._observe_corrected_timer({'value': 7})
        self.monitor._observe_corrected_timer({'value': 180})
        self.assertTrue(self.monitor.v2_state['timer_confirmation_requested'])
        self.assertEqual(self.monitor.v2_state['calibration']['state'], 'awaiting_confirmation')
        self.assertIsNone(self.monitor.v2_state['calibration']['cycle_id'])


if __name__ == '__main__':
    unittest.main()

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        self.monitor.args = {}
        self.monitor.v2_state = self.monitor._corrected_defaults()
        self.monitor._save_corrected_state = lambda: None
        self.t = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        self.monitor._confirm_clean_filters('cycle', self.t.isoformat())

    def sample(self, minute, rpm=True, supply=350.0, exhaust=350.0, level='Medium'):
        at = self.t + timedelta(hours=2, minutes=minute)
        return {'reported_at': at.isoformat(), 'fingerprint': f'report-{minute}',
                'fan_level': level, 'supply_flow_m3h': supply,
                'exhaust_flow_m3h': exhaust, 'sfp': 0.740571,
                'supply_duty': 45, 'exhaust_duty': 42,
                'supply_rpm': 1250 if rpm else None,
                'exhaust_rpm': 1250 if rpm else None}

    def test_count_alone_cannot_qualify_before_span(self):
        for i in range(20):
            self.monitor._collect_corrected_candidate(self.sample(i), self.t + timedelta(hours=2, minutes=i))
        self.assertEqual(self.monitor.v2_state['calibration']['references'], {})
        self.monitor._collect_corrected_candidate(self.sample(30), self.t + timedelta(hours=2, minutes=30))
        self.assertIn('sfp', self.monitor.v2_state['calibration']['references']['Medium:350'])

    def test_distinct_reports_and_optional_rpm(self):
        for i in range(21):
            sample = self.sample(i * 2, rpm=False)
            self.monitor._collect_corrected_candidate(sample, self.t + timedelta(hours=2, minutes=i * 2))
            self.monitor._collect_corrected_candidate(sample, self.t + timedelta(hours=2, minutes=i * 2))
        ref = self.monitor.v2_state['calibration']['references']['Medium:350']
        self.assertEqual(ref['sfp']['count'], 20)
        self.assertNotIn('rpm_flow', ref)

    def test_flow_dispersion_blocks_reference(self):
        for i in range(20):
            flow = 362 if i == 0 else 338
            self.monitor._collect_corrected_candidate(self.sample(i * 2, supply=flow), self.t + timedelta(hours=2, minutes=i * 2))
        self.assertEqual(self.monitor.v2_state['calibration']['references'], {})

    def test_reference_freezes_after_qualification(self):
        for i in range(20):
            self.monitor._collect_corrected_candidate(self.sample(i * 2), self.t + timedelta(hours=2, minutes=i * 2))
        original = self.monitor.v2_state['calibration']['references']['Medium:350']['sfp'].copy()
        self.monitor._collect_corrected_candidate({**self.sample(40), 'sfp': 0.9}, self.t + timedelta(hours=2, minutes=40))
        self.assertEqual(self.monitor.v2_state['calibration']['references']['Medium:350']['sfp'], original)

    def test_learning_closes_without_fabricating_an_unvisited_point(self):
        for i in range(20):
            self.monitor._collect_corrected_candidate(self.sample(i * 2), self.t + timedelta(hours=2, minutes=i * 2))
        self.monitor._collect_corrected_candidate(self.sample(40, level='Low'), self.t + timedelta(hours=2, minutes=40))
        self.monitor._advance_corrected_calibration(self.t + timedelta(hours=75))
        self.assertNotIn('Low:350', self.monitor.v2_state['calibration']['references'])
        self.assertEqual(self.monitor.v2_state['calibration']['state'], 'qualified')

    def test_no_eligible_reports_leaves_partial_state_at_deadline(self):
        self.monitor._advance_corrected_calibration(self.t + timedelta(hours=75))
        self.assertEqual(self.monitor.v2_state['calibration']['state'], 'partial')
        self.assertEqual(self.monitor.v2_state['calibration']['references'], {})
