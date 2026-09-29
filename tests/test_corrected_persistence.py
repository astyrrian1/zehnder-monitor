"""Corrected references must begin empty even when legacy baseline files exist."""

import json
import tempfile
import unittest
from pathlib import Path

from test_zehnder_monitor import ZehnderMonitor


class CorrectedPersistenceTests(unittest.TestCase):
    def test_legacy_baseline_is_never_promoted_to_corrected_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "baselines.json").write_text(json.dumps({
                "captured_at": "2026-05-18T12:36:53", "sfp": 0.5575,
                "per_fan_level": {"Medium": {"sfp": 0.5575, "baseline_quality": "conditioned"}},
            }))
            monitor = ZehnderMonitor.__new__(ZehnderMonitor)
            monitor.args = {"data_dir": directory}
            monitor.log = lambda *args, **kwargs: None
            corrected = monitor._load_corrected_state()
            self.assertEqual(corrected["schema_version"], 2)
            self.assertEqual(corrected["calibration"]["state"], "awaiting_confirmation")
            self.assertEqual(corrected["calibration"]["references"], {})
            self.assertIsNone(corrected["calibration"]["cycle_id"])
            self.assertTrue(Path(directory, "corrected_v2.json").exists())

    def test_corrupt_corrected_file_cannot_publish_a_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "corrected_v2.json").write_text("{broken")
            monitor = ZehnderMonitor.__new__(ZehnderMonitor)
            monitor.args = {"data_dir": directory}
            monitor.log = lambda *args, **kwargs: None
            corrected = monitor._load_corrected_state()
            self.assertEqual(corrected["calibration"]["references"], {})
            self.assertEqual(corrected["calibration"]["state"], "awaiting_confirmation")


if __name__ == "__main__":
    unittest.main()
