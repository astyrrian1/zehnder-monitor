"""The v2 reading must preserve the exact measurements used for SFP."""

import importlib.util
import pathlib
import unittest
import json

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
        monitor.get_state = lambda eid, attribute=None: {
            "state": states[eid][0],
            "attributes": {"unit_of_measurement": states[eid][1]},
            "last_updated": "2026-09-28T12:00:00+00:00",
        }
        monitor.call_service = lambda service, **kwargs: calls.append((service, kwargs))
        monitor._publish_corrected_sfp()
        discovery = [json.loads(k["payload"]) for _, k in calls if k["topic"].endswith("/config")]
        state_calls = [k for _, k in calls if k["topic"] == "zehnder/monitor/v2/state"]
        self.assertEqual(len(discovery), 1)
        self.assertEqual(discovery[0]["unique_id"], "zehnder_monitor_v2_sfp")
        self.assertEqual(discovery[0]["unit_of_measurement"], "kW/(m³/s)")
        self.assertEqual(discovery[0]["suggested_display_precision"], 3)
        self.assertEqual(len(state_calls), 1)
        payload = json.loads(state_calls[0]["payload"])
        self.assertEqual(payload["sfp"], 0.7406)
        self.assertEqual(payload["inputs"]["power"]["value"], 72.0)
        self.assertEqual(payload["inputs"]["power"]["unit"], "W")
        self.assertEqual(payload["inputs"]["power"]["reported_at"], "2026-09-28T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
