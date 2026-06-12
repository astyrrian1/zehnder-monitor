import importlib.util
import json
import pathlib
import sys
import tempfile
import time
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "apps" / "zehnder_monitor"
sys.path.insert(0, str(APP_DIR))


def load_monitor_module():
    appdaemon = types.ModuleType("appdaemon")
    plugins = types.ModuleType("appdaemon.plugins")
    hass = types.ModuleType("appdaemon.plugins.hass")
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")

    class DummyHass:
        pass

    hassapi.Hass = DummyHass
    sys.modules.setdefault("appdaemon", appdaemon)
    sys.modules.setdefault("appdaemon.plugins", plugins)
    sys.modules.setdefault("appdaemon.plugins.hass", hass)
    sys.modules.setdefault("appdaemon.plugins.hass.hassapi", hassapi)

    spec = importlib.util.spec_from_file_location(
        "zehnder_monitor", APP_DIR / "zehnder_monitor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capability_spec = importlib.util.spec_from_file_location(
    "capability", APP_DIR / "capability.py"
)
capability = importlib.util.module_from_spec(capability_spec)
capability_spec.loader.exec_module(capability)
monitor_module = load_monitor_module()
ZehnderMonitor = monitor_module.ZehnderMonitor


class CapabilityMathTests(unittest.TestCase):
    def test_current_clean_baseline_reports_full_capacity(self):
        cap = capability.compute_capability(
            current_sfp=0.5575,
            current_ratio=1.231,
            baseline_sfp=0.5575,
            baseline_ratio=1.231,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertEqual(cap["filter_capacity_remaining"], 100.0)
        self.assertEqual(cap["sfp_capacity_remaining"], 100.0)
        self.assertEqual(cap["duty_capacity_remaining"], 100.0)
        self.assertEqual(cap["baseline_system_resistance"], 46.1)

    def test_current_near_baseline_remains_near_full_capacity(self):
        cap = capability.compute_capability(
            current_sfp=0.5543,
            current_ratio=1.249,
            baseline_sfp=0.5575,
            baseline_ratio=1.231,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertGreaterEqual(cap["filter_capacity_remaining"], 99.0)
        self.assertLessEqual(cap["filter_capacity_remaining"], 100.0)
        self.assertEqual(cap["sfp_capacity_remaining"], 100.0)

    def test_replace_thresholds_report_zero_capacity(self):
        cap = capability.compute_capability(
            current_sfp=0.80,
            current_ratio=2.50,
            baseline_sfp=0.5575,
            baseline_ratio=1.231,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertEqual(cap["filter_capacity_remaining"], 0.0)
        self.assertEqual(cap["sfp_capacity_remaining"], 0.0)
        self.assertEqual(cap["duty_capacity_remaining"], 0.0)

    def test_values_better_than_baseline_cap_at_full_capacity(self):
        cap = capability.compute_capability(
            current_sfp=0.50,
            current_ratio=1.20,
            baseline_sfp=0.5575,
            baseline_ratio=1.231,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertEqual(cap["filter_capacity_remaining"], 100.0)
        self.assertEqual(cap["sfp_capacity_remaining"], 100.0)
        self.assertEqual(cap["duty_capacity_remaining"], 100.0)

    def test_fallback_baseline_does_not_publish_capacity_numbers(self):
        cap = capability.compute_capability(
            current_sfp=0.55,
            current_ratio=1.25,
            baseline_sfp=0.45,
            baseline_ratio=1.50,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="fallback",
        )

        self.assertIsNone(cap["filter_capacity_remaining"])
        self.assertIsNone(cap["sfp_capacity_remaining"])
        self.assertEqual(cap["baseline_quality"], "fallback")

    def test_invalid_baseline_threshold_does_not_publish_capacity_numbers(self):
        cap = capability.compute_capability(
            current_sfp=0.55,
            current_ratio=1.25,
            baseline_sfp=0.81,
            baseline_ratio=1.50,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertIsNone(cap["filter_capacity_remaining"])
        self.assertIsNone(cap["sfp_capacity_remaining"])
        self.assertIsNotNone(cap["duty_capacity_remaining"])

    def test_missing_current_metric_does_not_publish_composite_capacity(self):
        cap = capability.compute_capability(
            current_sfp=None,
            current_ratio=1.25,
            baseline_sfp=0.5575,
            baseline_ratio=1.231,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="single_sample",
        )

        self.assertIsNone(cap["filter_capacity_remaining"])
        self.assertIsNone(cap["sfp_capacity_remaining"])
        self.assertIsNotNone(cap["duty_capacity_remaining"])

    def test_capacity_floor_keeps_remaining_capacity_from_increasing(self):
        worse = capability.compute_capability(
            current_sfp=0.57,
            current_ratio=1.30,
            baseline_sfp=0.5228,
            baseline_ratio=1.265,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="conditioned",
        )
        floored, floor_values, changed = capability.floor_capacity_payload(worse, {})

        self.assertTrue(changed)
        self.assertEqual(
            floored["filter_capacity_remaining"],
            floored["instant_filter_capacity_remaining"],
        )

        better = capability.compute_capability(
            current_sfp=0.53,
            current_ratio=1.27,
            baseline_sfp=0.5228,
            baseline_ratio=1.265,
            sfp_pristine=0.35,
            sfp_replace=0.80,
            ratio_replace=2.50,
            baseline_quality="conditioned",
        )
        floored_better, _, changed = capability.floor_capacity_payload(
            better, floor_values
        )

        self.assertFalse(changed)
        self.assertGreater(
            floored_better["instant_filter_capacity_remaining"],
            floored_better["filter_capacity_remaining"],
        )
        self.assertEqual(
            floored_better["filter_capacity_remaining"],
            floored["filter_capacity_remaining"],
        )


class MonitorIntegrationTests(unittest.TestCase):
    def make_monitor(self):
        mon = ZehnderMonitor.__new__(ZehnderMonitor)
        mon.baseline_candidate_buffer = []
        mon.args = {}
        mon.sample_quality = "conditioned"
        mon.health_floor = None
        mon.instant_health_score = 100.0
        mon.capacity_floor = {}
        mon.log = lambda *args, **kwargs: None
        return mon

    def test_migrates_existing_single_baseline_and_keeps_may_18_valid(self):
        mon = self.make_monitor()
        old_baseline = {
            "sfp": 0.5575,
            "duty_ratio": 1.231,
            "supply_duty": 68.5,
            "exhaust_duty": 55.7,
            "supply_rpm_per_flow": 8.024,
            "fan_level_at_capture": "Medium",
            "captured_at": "2026-05-18T12:36:53.461672",
            "filter_days_at_capture": 180.0,
        }

        migrated = mon._migrate_baselines(old_baseline)
        mon.baselines = migrated
        mon.health_sfp = 0.5543
        mon.health_duty_ratio = 1.249

        cap = mon._baseline_capability({"fan_level": "Medium"})

        self.assertEqual(migrated["version"], 2)
        self.assertEqual(
            migrated["per_fan_level"]["Medium"]["baseline_quality"],
            "single_sample",
        )
        self.assertGreaterEqual(cap["filter_capacity_remaining"], 99.0)
        self.assertEqual(cap["baseline_quality"], "single_sample")

    def test_baseline_capability_keeps_capacity_remaining_from_increasing(self):
        mon = self.make_monitor()
        mon.baselines = {
            "version": 2,
            "sfp": 0.5228,
            "duty_ratio": 1.265,
            "fan_level_at_capture": "Medium",
            "baseline_quality": "conditioned",
            "captured_at": "2026-05-19T13:02:08.590968",
            "per_fan_level": {
                "Medium": {
                    "sfp": 0.5228,
                    "duty_ratio": 1.265,
                    "fan_level_at_capture": "Medium",
                    "baseline_quality": "conditioned",
                },
            },
        }
        mon.health_sfp = 0.57
        mon.health_duty_ratio = 1.30
        first = mon._baseline_capability({"fan_level": "Medium", "filter_days": 154})

        mon.health_sfp = 0.53
        mon.health_duty_ratio = 1.27
        second = mon._baseline_capability({"fan_level": "Medium", "filter_days": 154})

        self.assertGreater(
            second["instant_filter_capacity_remaining"],
            second["filter_capacity_remaining"],
        )
        self.assertEqual(
            second["filter_capacity_remaining"],
            first["filter_capacity_remaining"],
        )

    def test_filter_health_keeps_cycle_minimum_when_samples_improve(self):
        mon = self.make_monitor()
        mon.baselines = {
            "version": 2,
            "sfp": 0.5228,
            "duty_ratio": 1.265,
            "fan_level_at_capture": "Medium",
            "baseline_quality": "conditioned",
            "captured_at": "2026-05-19T13:02:08.590968",
            "per_fan_level": {},
        }

        mon.health_sfp = 0.57
        mon.health_duty_ratio = 1.30
        mon._health({"filter_days": 154, "fan_level": "Medium"})
        floor = mon.health_score

        mon.health_sfp = 0.53
        mon.health_duty_ratio = 1.27
        mon._health({"filter_days": 154, "fan_level": "Medium"})

        self.assertGreater(mon.instant_health_score, floor)
        self.assertEqual(mon.health_score, floor)

    def test_warming_up_sample_does_not_seed_new_cycle_floors(self):
        mon = self.make_monitor()
        mon.sample_quality = "warming_up"
        mon.health_floor = None
        mon.capacity_floor = {}
        mon.baselines = {
            "version": 2,
            "sfp": 0.5228,
            "duty_ratio": 1.265,
            "fan_level_at_capture": "Medium",
            "baseline_quality": "conditioned",
            "captured_at": "2026-05-19T13:02:08.590968",
            "per_fan_level": {},
        }
        mon.health_sfp = 0.57
        mon.health_duty_ratio = 1.30

        mon._health({"filter_days": 180, "fan_level": "Medium"})

        self.assertIsNone(mon.health_floor)
        self.assertEqual(mon.capability["capacity_mode"], "untrusted_warming_up")
        self.assertIsNone(mon.capability["filter_capacity_remaining"])

    def test_missing_baseline_reports_learning_when_clean_samples_are_accumulating(self):
        mon = self.make_monitor()
        mon.baselines = mon._defaults()
        mon.baseline_candidate_buffer = [{"fan_level": "Medium"}]
        mon.health_sfp = 0.5543
        mon.health_duty_ratio = 1.249

        cap = mon._baseline_capability({"fan_level": "Medium"})

        self.assertEqual(cap["baseline_quality"], "learning")
        self.assertIsNone(cap["filter_capacity_remaining"])

    def test_invalid_baseline_reports_invalid_quality(self):
        mon = self.make_monitor()
        mon.baselines = {
            "version": 2,
            "sfp": 0.82,
            "duty_ratio": 1.231,
            "captured_at": "2026-05-18T12:36:53.461672",
            "per_fan_level": {},
        }
        mon.health_sfp = 0.5543
        mon.health_duty_ratio = 1.249

        cap = mon._baseline_capability({"fan_level": "Medium"})

        self.assertEqual(cap["baseline_quality"], "invalid")
        self.assertIsNone(cap["filter_capacity_remaining"])

    def test_persistence_keeps_full_168_hour_trend_window(self):
        mon = self.make_monitor()
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            mon._data_dir = lambda: tmp
            mon.sfp_buffer = [(now - (25 * 3600), 0.55), (now - (169 * 3600), 0.6)]
            mon.ratio_buffer = [(now - (25 * 3600), 1.2), (now - (169 * 3600), 1.3)]
            mon.rpm_ratio_buffer = [(now - (25 * 3600), 8.0)]
            mon.heat_recovery_buffer = [(now - (25 * 3600), 90.0)]
            mon.baseline_candidate_buffer = [
                {"time": now - (23 * 3600), "fan_level": "Medium"},
                {"time": now - (25 * 3600), "fan_level": "Medium"},
            ]
            mon.health_floor = 74.2
            mon.capacity_floor = {
                "values": {"filter_capacity_remaining": 91.3},
                "updated_at": "2026-06-12T00:00:00",
                "filter_days": 154,
            }
            mon.last_filter_days = 180
            mon.last_conditioned_sample_at = now

            mon._persist()

            with open(pathlib.Path(tmp) / "state.json", "r") as f:
                saved = json.load(f)

        self.assertEqual(saved["sfp_buffer"], [[now - (25 * 3600), 0.55]])
        self.assertEqual(len(saved["baseline_candidate_buffer"]), 1)
        self.assertEqual(saved["health_floor"], 74.2)
        self.assertEqual(
            saved["capacity_floor"]["values"]["filter_capacity_remaining"], 91.3
        )

    def test_conditioned_samples_promote_per_fan_level_baseline(self):
        mon = self.make_monitor()
        now = time.time()
        mon.baselines = mon._defaults()
        mon.baseline_candidate_buffer = [
            {
                "time": now - i,
                "fan_level": "Medium",
                "sfp": 0.557 + (i * 0.00001),
                "duty_ratio": 1.23 + (i * 0.0001),
                "supply_duty": 68.5,
                "exhaust_duty": 55.7,
                "supply_rpm_per_flow": 8.02,
                "filter_days": 179.0,
            }
            for i in range(mon.BASELINE_MIN_CONDITIONED_SAMPLES)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            mon._data_dir = lambda: tmp
            mon._maybe_promote_conditioned_baseline("Medium")

            with open(pathlib.Path(tmp) / "baselines.json", "r") as f:
                saved = json.load(f)

        baseline = saved["per_fan_level"]["Medium"]
        self.assertEqual(baseline["baseline_quality"], "conditioned")
        self.assertEqual(baseline["sample_count"], mon.BASELINE_MIN_CONDITIONED_SAMPLES)
        self.assertEqual(saved["baseline_quality"], "conditioned")

    def test_default_data_dir_lives_outside_hacs_app_directory(self):
        mon = self.make_monitor()
        mon._dir = lambda: "/homeassistant/appdaemon/apps/zehnder-monitor"

        self.assertEqual(
            mon._data_dir(),
            "/homeassistant/appdaemon/zehnder-monitor",
        )

    def test_relative_configured_data_dir_is_appdaemon_config_relative(self):
        mon = self.make_monitor()
        mon.args = {"data_dir": "runtime/zehnder"}
        mon._dir = lambda: "/homeassistant/appdaemon/apps/zehnder-monitor"

        self.assertEqual(
            mon._data_dir(),
            "/homeassistant/appdaemon/runtime/zehnder",
        )

    def test_load_json_migrates_legacy_app_directory_file_to_data_dir(self):
        mon = self.make_monitor()
        with tempfile.TemporaryDirectory() as tmp:
            legacy = pathlib.Path(tmp) / "apps" / "zehnder-monitor"
            data_dir = pathlib.Path(tmp) / "zehnder-monitor"
            legacy.mkdir(parents=True)
            mon._dir = lambda: str(legacy)
            mon._data_dir = lambda: str(data_dir)

            legacy_baseline = {
                "sfp": 0.5575,
                "duty_ratio": 1.231,
                "captured_at": "2026-05-18T12:36:53.461672",
            }
            with open(legacy / "baselines.json", "w") as f:
                json.dump(legacy_baseline, f)

            loaded = mon._load_json("baselines.json", mon._defaults())

            self.assertEqual(loaded["sfp"], 0.5575)
            with open(data_dir / "baselines.json", "r") as f:
                migrated = json.load(f)
            self.assertEqual(migrated["duty_ratio"], 1.231)

    def test_mqtt_discovery_preserves_existing_unique_ids_and_adds_only_new_sensors(self):
        mon = self.make_monitor()
        published = {}

        def call_service(service, **kwargs):
            self.assertEqual(service, "mqtt/publish")
            key = kwargs["topic"].split("/")[-2]
            published[key] = json.loads(kwargs["payload"])

        mon.call_service = call_service

        self.assertTrue(mon._publish_mqtt_discovery())

        expected_existing = {
            "sfp": "zehnder_monitor_sfp",
            "filter_health": "zehnder_monitor_filter_health",
            "duty_ratio": "zehnder_monitor_duty_ratio",
            "heat_recovery": "zehnder_monitor_heat_recovery",
            "sfp_trend": "zehnder_monitor_sfp_trend",
            "sample_quality": "zehnder_monitor_sample_quality",
            "conditioned_samples": "zehnder_monitor_conditioned_samples",
            "raw_sfp": "zehnder_monitor_raw_sfp",
            "raw_heat_recovery": "zehnder_monitor_raw_heat_recovery",
            "heat_recovery_quality": "zehnder_monitor_heat_recovery_quality",
        }
        expected_new = {
            "filter_capacity_remaining": "zehnder_monitor_filter_capacity_remaining",
            "sfp_capacity_remaining": "zehnder_monitor_sfp_capacity_remaining",
            "duty_capacity_remaining": "zehnder_monitor_duty_capacity_remaining",
            "baseline_system_resistance": "zehnder_monitor_baseline_system_resistance",
            "baseline_quality": "zehnder_monitor_baseline_quality",
        }

        for key, unique_id in expected_existing.items():
            self.assertEqual(published[key]["unique_id"], unique_id)

        for key, unique_id in expected_new.items():
            self.assertEqual(published[key]["unique_id"], unique_id)

    def test_mqtt_payload_preserves_existing_sections_and_adds_capability(self):
        mon = self.make_monitor()
        published = {}
        mon.health_sfp = 0.5543
        mon.health_duty_ratio = 1.249
        mon.duty_asymmetry_abs = 12.8
        mon.supply_rpm_per_flow = 8.024
        mon.exhaust_rpm_per_flow = 7.1
        mon.heat_recovery_eta = 90.0
        mon.heat_recovery_quality = "conditioned"
        mon.health_score = 76.2
        mon.instant_health_score = 76.2
        mon.sfp_trend_slope = 0.0001
        mon.sfp_buffer = [(time.time(), 0.5543)]
        mon.sample_quality = "conditioned"
        mon.last_conditioned_sample_at = time.time()
        mon.sfp = 0.5328
        mon.duty_ratio = 1.249
        mon.heat_recovery_raw = 89.0
        mon.capability = {
            "filter_capacity_remaining": 99.5,
            "baseline_quality": "single_sample",
        }
        mon.baselines = {"version": 2, "sfp": 0.5575}

        def call_service(service, **kwargs):
            self.assertEqual(service, "mqtt/publish")
            published.update(json.loads(kwargs["payload"]))

        mon.call_service = call_service
        mon._publish_mqtt({
            "power": 80.0,
            "supply_flow": 270.0,
            "exhaust_flow": 270.0,
            "supply_duty": 68.5,
            "exhaust_duty": 55.7,
            "supply_rpm": 2166.0,
            "exhaust_rpm": 1917.0,
            "fan_level": "Medium",
            "bypass": 0.0,
            "filter_days": 180.0,
            "wifi": -52.0,
            "energy_ytd": 100.0,
            "temp_ages": {},
        })

        self.assertIn("metrics", published)
        self.assertIn("health", published)
        self.assertIn("raw", published)
        self.assertIn("baselines", published)
        self.assertEqual(
            published["capability"]["filter_capacity_remaining"], 99.5
        )
        self.assertEqual(published["health"]["instant_score"], 76.2)


if __name__ == "__main__":
    unittest.main()
