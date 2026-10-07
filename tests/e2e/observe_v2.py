"""Read-only 24-hour acceptance observer for a deployed corrected monitor."""
from datetime import datetime, timedelta, timezone
import argparse
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


def get_state(base, token, entity):
    request = urllib.request.Request(base + '/api/states/' + entity,
        headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def evaluate(quality, sfp, now):
    result = {'reachable': True, 'publication_delivered': False,
              'expired_presented_current': False, 'malformed_numeric': False,
              'software_sfp_error': None, 'quality': quality['state']}
    attributes = quality.get('attributes') or {}
    calculated_at = attributes.get('calculated_at')
    try:
        age = (now - datetime.fromisoformat(calculated_at)).total_seconds()
        result['publication_age_seconds'] = round(age, 1)
        result['publication_delivered'] = -5 <= age <= 125
    except (TypeError, ValueError):
        result['publication_age_seconds'] = None
    if quality['state'] == 'current':
        result['expired_presented_current'] = (
            result['publication_age_seconds'] is None
            or result['publication_age_seconds'] > 180)
        try:
            value = float(sfp['state'])
            if not math.isfinite(value):
                raise ValueError('nonfinite')
            inputs = (sfp.get('attributes') or {})['inputs']
            power = float(inputs['power']['value'])
            supply = float(inputs['supply_flow']['value'])
            exhaust = float(inputs['exhaust_flow']['value'])
            expected = power * 3.6 / ((supply + exhaust) / 2)
            result['software_sfp_error'] = round(abs(value - expected), 7)
            if result['software_sfp_error'] > 0.00005:
                result['malformed_numeric'] = True
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            result['malformed_numeric'] = True
    return result


def summarize(rows, finished, hours, interval):
    reachable = [row for row in rows if row.get('reachable')]
    delivery = sum(row.get('publication_delivered', False) for row in reachable)
    report = {'started_at': rows[0]['observed_at'] if rows else None,
              'finished_at': finished.isoformat(),
              'total_polls': len(rows), 'reachable_polls': len(reachable),
              'delivery_ratio_reachable': delivery / len(reachable) if reachable else 0,
              'expired_presented_current': sum(row.get('expired_presented_current', False) for row in rows),
              'malformed_numeric': sum(row.get('malformed_numeric', False) for row in rows)}
    times = [datetime.fromisoformat(row['observed_at']) for row in rows]
    elapsed = (finished - times[0]).total_seconds() if times else 0
    gaps = [(b-a).total_seconds() for a,b in zip(times,times[1:])]
    tail = (finished-times[-1]).total_seconds() if times else float('inf')
    expected = math.ceil(hours * 3600 / interval)
    report.update(expected_polls=expected, sampling_ratio=len(rows)/expected,
                  max_sampling_gap_seconds=max([tail, *gaps]), observed_seconds=elapsed)
    report['passed'] = (report['delivery_ratio_reachable'] >= .995
                        and report['expired_presented_current'] == 0
                        and report['malformed_numeric'] == 0
                        and len(reachable) > 0
                        and elapsed >= hours * 3600
                        and len(rows) >= expected * .995
                        and report['max_sampling_gap_seconds'] <= interval * 2 + 5)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=float, default=24)
    parser.add_argument('--interval-seconds', type=float, default=60)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-isolated-test', action='store_true')
    args = parser.parse_args()
    base = os.environ['ZMON_OBSERVE_HA_URL'].rstrip('/')
    if base.endswith(':18123') and not args.allow_isolated_test:
        raise SystemExit('Isolated URL requires --allow-isolated-test')
    if not base.endswith(':18123') and args.hours < 24:
        raise SystemExit('Production observation must run at least 24 hours')
    token = os.environ['ZMON_OBSERVE_HA_TOKEN']
    deadline = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with args.output.open('w') as stream:
        while datetime.now(timezone.utc) < deadline:
            began = time.monotonic()
            now = datetime.now(timezone.utc)
            try:
                quality = get_state(base, token, 'sensor.zehnder_corrected_sfp_quality')
                sfp = get_state(base, token, 'sensor.zehnder_corrected_sfp')
                row = evaluate(quality, sfp, now)
            except (OSError, urllib.error.HTTPError, KeyError, ValueError) as exc:
                row = {'reachable': False, 'error': type(exc).__name__}
            row['observed_at'] = now.isoformat()
            rows.append(row)
            stream.write(json.dumps(row) + '\n')
            stream.flush()
            time.sleep(max(0, args.interval_seconds - (time.monotonic() - began)))
    report = summarize(rows, datetime.now(timezone.utc), args.hours, args.interval_seconds)
    report_path = args.output.with_suffix('.summary.json')
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
