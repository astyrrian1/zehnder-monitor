"""Apparent sensible recovery is screened independently from SFP."""
from datetime import datetime, timezone
import unittest
import test_zehnder_monitor
from corrected import evaluate_recovery_inputs, conditioned_recovery

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc).isoformat()


def source(value, unit):
    return {'value': value, 'unit': unit, 'reported_at': NOW}


def fixture(outdoor=0, extract=20, supply=16, unit='°C'):
    return {'outdoor_temp': source(outdoor, unit), 'extract_temp': source(extract, unit),
            'supply_temp': source(supply, unit), 'bypass': source(0, '%'),
            'supply_flow': source(350, 'm³/h'), 'exhaust_flow': source(350, 'm³/h'),
            'fan_level': {'value': 'Medium'}}


class RecoveryTests(unittest.TestCase):
    def test_heating_cooling_and_fahrenheit_agree(self):
        heating = evaluate_recovery_inputs(fixture(), NOW)
        cooling = evaluate_recovery_inputs(fixture(30, 20, 22), NOW)
        fahrenheit = evaluate_recovery_inputs(fixture(32, 68, 60.8, '°F'), NOW)
        for result in (heating, cooling, fahrenheit):
            self.assertEqual(result['quality'], 'current')
            self.assertAlmostEqual(result['apparent_sensible_recovery_pct'], 80, places=5)

    def test_small_delta_bypass_and_unknown_mode_are_ineligible(self):
        self.assertEqual(evaluate_recovery_inputs(fixture(0, 4.9, 4), NOW)['reason'], 'small_temperature_difference')
        bypass = fixture();bypass['bypass']['value'] = 10
        self.assertEqual(evaluate_recovery_inputs(bypass, NOW)['reason'], 'bypass_open')
        mode = fixture();mode['fan_level']['value'] = 'unknown'
        self.assertEqual(evaluate_recovery_inputs(mode, NOW)['reason'], 'unsupported_fan_level')

    def test_out_of_range_ratio_remains_raw_and_flagged(self):
        result = evaluate_recovery_inputs(fixture(supply=24), NOW)
        self.assertEqual(result['quality'], 'anomalous')
        self.assertAlmostEqual(result['apparent_sensible_recovery_pct'], 120)

    def test_conditioned_average_needs_five_distinct_recent_observations(self):
        samples = []
        from datetime import timedelta
        t = datetime.fromisoformat(NOW)
        for i in range(5):
            value, samples = conditioned_recovery(80, str(i), t + timedelta(minutes=i), samples)
        self.assertAlmostEqual(value, 80)
        value, samples = conditioned_recovery(None, None, t + timedelta(minutes=20), samples)
        self.assertIsNone(value)
        self.assertEqual(samples, [])


if __name__ == '__main__':
    unittest.main()
