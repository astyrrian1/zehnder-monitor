"""Current effort is comparable only to the same confirmed operating point."""
from datetime import datetime, timedelta, timezone
import unittest
import test_zehnder_monitor  # installs the app directory on the test path
from corrected import select_reference, calculate_reference_change, conditioned_comparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.calibration = {'cycle_id': 'cycle-a', 'references': {
            'Medium:350': {'sfp': {'sfp': .740571, 'supply_flow_m3h': 350, 'exhaust_flow_m3h': 350},
                           'duty': {'supply_duty': 45, 'exhaust_duty': 42, 'supply_flow_m3h': 350, 'exhaust_flow_m3h': 350},
                           'rpm_flow': {'supply_rpm': 1250, 'exhaust_rpm': 1250, 'supply_flow_m3h': 350, 'exhaust_flow_m3h': 350}}}}
        self.sample = {'sfp': .8146281, 'supply_flow_m3h': 350, 'exhaust_flow_m3h': 350,
                       'supply_duty': 47, 'exhaust_duty': 46, 'supply_rpm': 1375, 'exhaust_rpm': 1375,
                       'fan_level': 'Medium', 'fingerprint': 'a'}

    def test_same_level_and_both_flows_within_five_percent(self):
        selected = select_reference(self.calibration, 'Medium', 351, 349)
        self.assertEqual(selected['point'], 'Medium:350')
        self.assertIsNone(select_reference(self.calibration, 'Low', 350, 350))
        self.assertIsNotNone(select_reference(self.calibration, 'Medium', 367.5, 332.5))
        self.assertIsNone(select_reference(self.calibration, 'Medium', 367.51, 350))
        self.assertIsNone(select_reference(self.calibration, 'Medium', 350, 332.49))

    def test_index_boundary_does_not_hide_eligible_reference(self):
        self.assertEqual(select_reference(self.calibration, 'Medium', 362.6, 362.6)['point'], 'Medium:350')

    def test_signed_changes_are_directional(self):
        ref = select_reference(self.calibration, 'Medium', 350, 350)
        change = calculate_reference_change(self.sample, ref)
        self.assertAlmostEqual(change['sfp_change_pct'], 10, places=3)
        self.assertAlmostEqual(change['supply_duty_change_pp'], 2)
        self.assertAlmostEqual(change['exhaust_duty_change_pp'], 4)
        self.assertAlmostEqual(change['supply_rpm_flow_change_pct'], 10)
        self.assertAlmostEqual(change['exhaust_rpm_flow_change_pct'], 10)

    def test_window_requires_five_distinct_reports_and_recovers(self):
        ref = select_reference(self.calibration, 'Medium', 350, 350)
        t = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        samples = []
        for i in range(4):
            report = {**self.sample, 'fingerprint': str(i), 'reported_at': (t + timedelta(minutes=i)).isoformat()}
            result, samples = conditioned_comparison(report, ref, samples, t + timedelta(minutes=i))
            self.assertIsNone(result)
        report = {**self.sample, 'fingerprint': '4', 'reported_at': (t + timedelta(minutes=4)).isoformat()}
        result, samples = conditioned_comparison(report, ref, samples, t + timedelta(minutes=4))
        self.assertAlmostEqual(result['sfp_change_pct'], 10, places=3)
        restored = {**self.sample, 'sfp': .740571, 'supply_duty': 45, 'exhaust_duty': 42,
                    'supply_rpm': 1250, 'exhaust_rpm': 1250}
        for i in range(20, 25):
            report = {**restored, 'fingerprint': str(i), 'reported_at': (t + timedelta(minutes=i)).isoformat()}
            result, samples = conditioned_comparison(report, ref, samples, t + timedelta(minutes=i))
        self.assertAlmostEqual(result['sfp_change_pct'], 0, places=2)


if __name__ == '__main__':
    unittest.main()
