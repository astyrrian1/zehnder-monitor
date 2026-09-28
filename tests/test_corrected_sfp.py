"""The v2 reading must preserve the exact measurements used for SFP."""

import importlib.util
import pathlib
import unittest
import json
import time
from datetime import datetime, timezone

from test_zehnder_monitor import ZehnderMonitor


APP = pathlib.Path(__file__).resolve().parents[1] / "apps" / "zehnder_monitor"


def load_calculation():
    spec = importlib.util.spec_from_file_location("corrected", APP / "corrected.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CorrectedSfpTests(unittest.TestCase):
    def test_balanced_flow_and_power_change(self):
        calculation = load_calculation()
        for power, expected in ((72, 0.740571), (79.2, 0.814629)):
            with self.subTest(power=power):
                snapshot = {
                    "power": {"value": power, "unit": "W", "reported_at": "2026-09-28T12:00:00+00:00"},
                    "supply_flow": {"value": 350, "unit": "m³/h", "reported_at": "2026-09-28T12:00:00+00:00"},
                    "exhaust_flow": {"value": 350, "unit": "m³/h", "reported_at": "2026-09-28T12:00:00+00:00"},
                }
                result = calculation.corrected_sfp(snapshot, "2026-09-28T12:01:00+00:00")
                self.assertAlmostEqual(result["sfp"], expected, places=6)
                self.assertEqual(result["inputs"], snapshot)
                self.assertEqual(result["calculated_at"], "2026-09-28T12:01:00+00:00")

    def test_adapter_and_mqtt_publication(self):
        monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        calls = []
        states = {
            monitor.E["power"]: ("72", "W"),
            monitor.E["supply_flow"]: ("350", "m³/h"),
            monitor.E["exhaust_flow"]: ("350", "m³/h"),
        }
        reported_at = [datetime.now(timezone.utc).isoformat()]
        monitor.get_state = lambda eid, attribute=None: (
            {
                "state": states[eid][0],
                "attributes": {"unit_of_measurement": states[eid][1]},
                "last_reported": reported_at[0],
            } if eid in states else None
        )
        monitor.call_service = lambda service, **kwargs: calls.append((service, kwargs))
        monitor._publish_corrected_sfp()
        discovery = [json.loads(k["payload"]) for _, k in calls if k["topic"].endswith("/config")]
        state_calls = [k for _, k in calls if k["topic"] == "zehnder/monitor/v2/state"]
        sfp_config = next(c for c in discovery if c["unique_id"] == "zehnder_monitor_v2_sfp")
        self.assertEqual(sfp_config["unit_of_measurement"], "kW/(m³/s)")
        self.assertEqual(sfp_config["suggested_display_precision"], 3)
        self.assertEqual(sfp_config["expire_after"], 180)
        quality_config = next(c for c in discovery if c["unique_id"] == "zehnder_monitor_v2_sfp_quality")
        self.assertEqual(quality_config["expire_after"], 180)
        calibration_config = next(c for c in discovery if c["unique_id"] == "zehnder_monitor_v2_calibration")
        self.assertEqual(calibration_config["state_topic"], "zehnder/monitor/v2/state")
        comparison_config = next(c for c in discovery if c["unique_id"] == "zehnder_monitor_v2_sfp_change")
        self.assertIn("availability_template", comparison_config)
        self.assertEqual(len(state_calls), 1)
        payload = json.loads(state_calls[0]["payload"])
        self.assertEqual(payload["sfp"], 0.7406)
        self.assertEqual(payload["inputs"]["power"]["value"], 72.0)
        self.assertEqual(payload["inputs"]["power"]["unit"], "W")
        self.assertEqual(payload["inputs"]["power"]["reported_at"], reported_at[0])
        self.assertEqual(payload["quality"], "current")
        self.assertEqual(payload["calibration"]["state"], "awaiting_confirmation")
        self.assertEqual(payload["calibration"]["references"], {})
        self.assertIsNone(payload["comparison"]["sfp_change_pct"])
        self.assertEqual(payload["fan_effort_quality"], "unavailable")
        self.assertEqual(payload["recovery_quality"], "unsupported")
        self.assertFalse(state_calls[0]["retain"])

        monitor._publish_corrected_sfp()
        self.assertEqual(monitor.v2_accepted_reports, 1)
        reported_at[0] = datetime.now(timezone.utc).isoformat()
        monitor._publish_corrected_sfp()
        self.assertEqual(monitor.v2_accepted_reports, 2)
        states[monitor.E["power"]] = ("NaN", "W")
        monitor._publish_corrected_sfp()
        invalid = json.loads([k for _, k in calls if k["topic"] == "zehnder/monitor/v2/state"][-1]["payload"])
        self.assertIsNone(invalid["sfp"])
        self.assertEqual(invalid["quality"], "invalid")
        states[monitor.E["power"]] = (float("nan"), "W")
        monitor._publish_corrected_sfp()
        invalid_numeric = json.loads([k for _, k in calls if k["topic"] == "zehnder/monitor/v2/state"][-1]["payload"])
        self.assertEqual(invalid_numeric["quality"], "invalid")

    def test_offline_tick_still_prunes_expired_history(self):
        monitor = ZehnderMonitor.__new__(ZehnderMonitor)
        monitor.tick_count = 0
        old = time.time() - 8 * 24 * 3600
        recent = time.time() - 60
        monitor.sfp_buffer = [(old, 1.0), (recent, 0.8)]
        monitor.ratio_buffer = [(old, 1.2)]
        monitor.rpm_ratio_buffer = [(old, 2.0)]
        monitor.heat_recovery_buffer = [(old, 80)]
        monitor.baseline_candidate_buffer = [{"time": old, "sfp": 1.0}]
        monitor.log = lambda *args, **kwargs: None
        monitor._publish_corrected_sfp = lambda: "unavailable"
        monitor._tick({})
        self.assertEqual(monitor.sfp_buffer, [(recent, 0.8)])
        self.assertEqual(monitor.ratio_buffer, [])
        self.assertEqual(monitor.rpm_ratio_buffer, [])
        self.assertEqual(monitor.heat_recovery_buffer, [])
        self.assertEqual(monitor.baseline_candidate_buffer, [])


if __name__ == "__main__":
    unittest.main()
