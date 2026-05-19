import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "blueprints" / "automation" / "zehnder_filter_alert.yaml"

MQTT_CONTRACT_SENSORS = {
    "sensor.zehnder_filter_health",
    "sensor.zehnder_sfp",
    "sensor.zehnder_duty_ratio",
    "sensor.zehnder_heat_recovery",
    "sensor.zehnder_sfp_trend",
    "sensor.zehnder_sample_quality",
    "sensor.zehnder_conditioned_samples",
    "sensor.zehnder_raw_sfp",
    "sensor.zehnder_raw_heat_recovery",
    "sensor.zehnder_heat_recovery_quality",
    "sensor.zehnder_filter_capacity_remaining",
    "sensor.zehnder_sfp_capacity_remaining",
    "sensor.zehnder_duty_capacity_remaining",
    "sensor.zehnder_baseline_system_resistance",
    "sensor.zehnder_baseline_quality",
}


def input_default(text, input_name):
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line == f"    {input_name}:"),
        None,
    )
    if start is None:
        raise AssertionError(f"Missing input {input_name}")
    for line in lines[start + 1:]:
        if line.startswith("    ") and not line.startswith("      "):
            break
        if line.startswith("      default: "):
            return line.split("default: ", 1)[1].strip().strip('"')
    raise AssertionError(f"Missing default for {input_name}")


class BlueprintContractTests(unittest.TestCase):
    def test_blueprint_declares_automation_numeric_state_trigger(self):
        text = BLUEPRINT.read_text()

        self.assertIn("domain: automation", text)
        self.assertIn("platform: numeric_state", text)
        self.assertIn("entity_id: !input health_sensor", text)

    def test_alert_default_uses_baseline_aware_capacity_sensor(self):
        text = BLUEPRINT.read_text()

        self.assertEqual(
            input_default(text, "health_sensor"),
            "sensor.zehnder_filter_capacity_remaining",
        )
        self.assertEqual(
            input_default(text, "absolute_health_sensor"),
            "sensor.zehnder_filter_health",
        )

    def test_sensor_defaults_stay_inside_mqtt_contract(self):
        text = BLUEPRINT.read_text()
        defaults = set(re.findall(r"^      default: (sensor\.zehnder[^\n]+)$", text, re.MULTILINE))

        self.assertTrue(defaults)
        self.assertEqual(set(), defaults - MQTT_CONTRACT_SENSORS)

    def test_blueprint_does_not_reference_native_zehnder_entities(self):
        text = BLUEPRINT.read_text()

        self.assertNotIn("zehnder_comfoair_q", text)
        self.assertNotIn("comfoair_q", text)

    def test_notification_templates_use_inputs_not_hardcoded_state_lookups(self):
        text = BLUEPRINT.read_text()

        self.assertEqual([], re.findall(r"states\(['\"]sensor\.zehnder", text))
        self.assertEqual([], re.findall(r"state_attr\(['\"]sensor\.zehnder", text))


if __name__ == "__main__":
    unittest.main()
