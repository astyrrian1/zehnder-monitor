"""Failure states must never masquerade as a current corrected reading."""

import unittest

from test_corrected_sfp import load_calculation
from test_zehnder_monitor import ZehnderMonitor


NOW = "2026-09-28T18:00:00+00:00"


def source(power=72, flow=350, reported_at="2026-09-28T17:59:00+00:00"):
    return {
        "power": {"value": power, "unit": "W", "reported_at": reported_at},
        "supply_flow": {"value": flow, "unit": "m³/h", "reported_at": reported_at},
        "exhaust_flow": {"value": flow, "unit": "m³/h", "reported_at": reported_at},
    }


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.calc = load_calculation()

    def test_valid_sfp_has_distinct_report_fingerprint(self):
        inputs = source()
        first = self.calc.evaluate_sfp(inputs, NOW)
        repeated = self.calc.evaluate_sfp(inputs, NOW)
        self.assertEqual(first["quality"], "current")
        self.assertEqual(first["fingerprint"], repeated["fingerprint"])
        next_report = source(reported_at="2026-09-28T17:59:30+00:00")
        self.assertNotEqual(first["fingerprint"], self.calc.evaluate_sfp(next_report, NOW)["fingerprint"])

    def test_invalid_and_stopped_inputs_withdraw_measurement(self):
        cases = [
            (source(power="NaN"), "invalid"),
            (source(power="inf"), "invalid"),
            (source(power=-1), "invalid"),
            (source(flow=0), "stopped"),
            (source(reported_at=None), "unknown_freshness"),
            (source(reported_at="2026-09-28T17:49:00+00:00"), "stale"),
        ]
        unsupported = source()
        unsupported["power"]["unit"] = "horsepower"
        cases.append((unsupported, "unsupported"))
        for inputs, expected in cases:
            with self.subTest(expected=expected, inputs=inputs):
                result = self.calc.evaluate_sfp(inputs, NOW)
                self.assertEqual(result["quality"], expected)
                self.assertIsNone(result["sfp"])

    def test_optional_rpm_is_independent_of_sfp(self):
        inputs = source()
        inputs["supply_rpm"] = None
        self.assertEqual(self.calc.evaluate_sfp(inputs, NOW)["quality"], "current")

    def test_supported_units_normalize_without_changing_source_snapshot(self):
        inputs = source(power=0.072, flow=350 / 3600)
        inputs["power"]["unit"] = "kW"
        inputs["supply_flow"]["unit"] = "m³/s"
        inputs["exhaust_flow"]["unit"] = "m³/s"
        result = self.calc.evaluate_sfp(inputs, NOW)
        self.assertAlmostEqual(result["sfp"], 0.740571, places=6)
        self.assertIs(result["inputs"], inputs)

    def test_missing_required_input_is_unavailable(self):
        inputs = source()
        del inputs["power"]
        result = self.calc.evaluate_sfp(inputs, NOW)
        self.assertEqual(result["quality"], "unavailable")
        self.assertIsNone(result["sfp"])

    def test_optional_metric_quality_is_independent(self):
        inputs = source()
        stamp = "2026-09-28T17:59:00+00:00"
        inputs.update({
            "supply_duty": {"value": 45, "unit": "%", "reported_at": stamp},
            "exhaust_duty": {"value": 42, "unit": "%", "reported_at": stamp},
            "supply_rpm": {"value": 1250, "unit": "rpm", "reported_at": stamp},
            "exhaust_rpm": {"value": 1250, "unit": "rpm", "reported_at": stamp},
            "bypass": {"value": 0, "unit": "%", "reported_at": stamp},
            "supply_temp": {"value": 16, "unit": "°C", "reported_at": stamp},
            "outdoor_temp": {"value": 0, "unit": "°C", "reported_at": stamp},
            "extract_temp": {"value": 20, "unit": "°C", "reported_at": stamp},
        })
        self.assertEqual(self.calc.evaluate_fan_effort(inputs, NOW)["quality"], "current")
        self.assertEqual(self.calc.evaluate_recovery_inputs(inputs, NOW)["quality"], "current")
        inputs["supply_rpm"] = None
        self.assertEqual(self.calc.evaluate_fan_effort(inputs, NOW)["quality"], "unavailable")
        self.assertEqual(self.calc.evaluate_sfp(inputs, NOW)["quality"], "current")
        inputs["bypass"]["value"] = "unknown"
        self.assertEqual(self.calc.evaluate_recovery_inputs(inputs, NOW)["quality"], "unsupported")
        self.assertEqual(self.calc.evaluate_sfp(inputs, NOW)["quality"], "current")

    def test_out_of_range_duty_is_invalid_for_fan_effort(self):
        inputs = source()
        inputs["supply_duty"] = {"value": 120, "unit": "%", "reported_at": "2026-09-28T17:59:00+00:00"}
        self.assertEqual(self.calc.evaluate_fan_effort(inputs, NOW)["quality"], "invalid")

    def test_baseline_eligibility_needs_three_distinct_stable_reports(self):
        recent = []
        for second in (0, 10, 20):
            stamp = f"2026-09-28T17:59:{second:02d}+00:00"
            inputs = source(reported_at=stamp)
            inputs["fan_level"] = {"value": "Medium", "unit": None, "reported_at": stamp}
            inputs["bypass"] = {"value": 0, "unit": "%", "reported_at": stamp}
            current = self.calc.evaluate_sfp(inputs, NOW)
            eligible, reason, recent = self.calc.evaluate_sampling_eligibility(inputs, current, recent, NOW)
            self.assertEqual(eligible, second == 20)
            if second == 0:
                self.assertEqual((eligible, reason), (False, "warming_up"))
                _, _, repeated = self.calc.evaluate_sampling_eligibility(inputs, current, recent, NOW)
                self.assertEqual(len(repeated), 1)

    def test_baseline_eligibility_does_not_erase_valid_raw_sfp(self):
        inputs = source()
        inputs["fan_level"] = {"value": "High", "unit": None, "reported_at": NOW}
        inputs["bypass"] = {"value": "unknown", "unit": "%", "reported_at": NOW}
        current = self.calc.evaluate_sfp(inputs, NOW)
        self.assertEqual(current["quality"], "current")
        eligible, reason, _ = self.calc.evaluate_sampling_eligibility(inputs, current, [], NOW)
        self.assertFalse(eligible)
        self.assertEqual(reason, "unsupported_fan_level")

    def test_missing_optional_rpm_does_not_raise_in_legacy_path(self):
        monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        reading = {
            "power": 72, "supply_flow": 350, "exhaust_flow": 350,
            "supply_duty": 45, "exhaust_duty": 42,
            "supply_rpm": None, "exhaust_rpm": None,
            "supply_temp": None, "outdoor_temp": None, "extract_temp": None,
            "bypass": 0, "temp_ages": {},
        }
        monitor._compute(reading)
        self.assertEqual(monitor.supply_rpm_per_flow, 0)
        self.assertEqual(monitor.exhaust_rpm_per_flow, 0)

    def test_missing_rpm_skips_legacy_sampling_but_keeps_v2(self):
        monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        monitor.tick_count = 0
        monitor.log = lambda *args, **kwargs: None
        monitor._publish_corrected_sfp = lambda: "current"
        monitor._read = lambda: {"supply_rpm": None, "exhaust_rpm": 1200}
        calls = []
        monitor._compute = lambda reading: calls.append("compute")
        monitor._sample = lambda reading: calls.append("sample")
        monitor._detect_change = lambda reading: None
        monitor._health = lambda reading: None
        monitor.discovery_published = True
        monitor._publish_mqtt = lambda reading: None
        monitor._tick({})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
