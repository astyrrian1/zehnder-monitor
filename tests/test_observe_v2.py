"""Read-only production observation calculations."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parent / 'e2e/observe_v2.py'
SPEC = importlib.util.spec_from_file_location('observe_v2', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ObservationTest(unittest.TestCase):
    def test_current_sfp_is_checked_against_source_snapshot(self):
        now = datetime.now(timezone.utc)
        quality = {'state': 'current', 'attributes': {'calculated_at': now.isoformat()}}
        sfp = {'state': '0.7406', 'attributes': {'inputs': {
            'power': {'value': 72}, 'supply_flow': {'value': 350},
            'exhaust_flow': {'value': 350}}}}
        result = MODULE.evaluate(quality, sfp, now)
        self.assertTrue(result['publication_delivered'])
        self.assertFalse(result['malformed_numeric'])
        self.assertLessEqual(result['software_sfp_error'], .00005)

    def test_expired_current_is_rejected(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=181)
        result = MODULE.evaluate({'state': 'current',
            'attributes': {'calculated_at': old.isoformat()}}, {'state': 'NaN'}, now)
        self.assertTrue(result['expired_presented_current'])
        self.assertTrue(result['malformed_numeric'])


if __name__ == '__main__':
    unittest.main()
