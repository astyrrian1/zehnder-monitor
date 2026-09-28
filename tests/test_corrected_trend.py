"""Trend needs hourly coverage, UTC elapsed time, and a single reference."""
from datetime import datetime, timedelta, timezone
import unittest
import test_zehnder_monitor
from corrected import hourly_sfp_trend


class TrendTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 21, 0, tzinfo=timezone.utc)

    def reports(self, slope=.010, hours=range(0, 97, 4)):
        return [{'reported_at': (self.start + timedelta(hours=h)).isoformat(),
                 'sfp': .7 + slope * h / 24, 'cycle_id': 'a', 'point': 'Medium:350'} for h in hours]

    def test_known_positive_negative_and_flat_slopes(self):
        now = self.start + timedelta(hours=97)
        for slope in (.010, -.010, 0):
            result = hourly_sfp_trend(self.reports(slope), 'a', 'Medium:350', now)
            self.assertEqual(result['quality'], 'ready')
            self.assertAlmostEqual(result['slope_w_per_m3s_day'], slope * 1000, places=6)
            self.assertGreaterEqual(result['bucket_count'], 24)

    def test_sparse_and_short_span_are_unavailable(self):
        now = self.start + timedelta(hours=97)
        self.assertEqual(hourly_sfp_trend(self.reports(hours=range(24)), 'a', 'Medium:350', now)['quality'], 'insufficient_coverage')
        self.assertEqual(hourly_sfp_trend(self.reports(hours=range(0, 73, 4)), 'a', 'Medium:350', now)['quality'], 'insufficient_coverage')

    def test_other_cycles_points_and_duplicate_density_cannot_skew(self):
        reports = self.reports()
        reports.extend([{**reports[0], 'sfp': 9, 'cycle_id': 'old'} for _ in range(50)])
        reports.extend([{**reports[0], 'sfp': 9, 'point': 'Low:350'} for _ in range(50)])
        reports.extend([{**reports[0], 'reported_at': (self.start + timedelta(minutes=i)).isoformat()} for i in range(30)])
        result = hourly_sfp_trend(reports, 'a', 'Medium:350', self.start + timedelta(hours=97))
        self.assertAlmostEqual(result['slope_w_per_m3s_day'], 10, places=6)

    def test_missing_hours_and_dst_do_not_create_synthetic_points(self):
        hours = list(range(0, 49, 2)) + [72, 76, 80, 84]
        result = hourly_sfp_trend(self.reports(hours=hours), 'a', 'Medium:350', self.start + timedelta(hours=97))
        self.assertEqual(result['bucket_count'], len(hours))
        self.assertAlmostEqual(result['slope_w_per_m3s_day'], 10, places=6)


if __name__ == '__main__':
    unittest.main()
