"""Replay UTC source-report hours through the isolated live publisher."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STACK = '/opt/stacks/zehnder-monitor-e2e'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    if '--reuse-reference' in sys.argv:
        subprocess.run(['scp', str(ROOT / 'apps/zehnder_monitor/zehnder_monitor.py'),
                        str(ROOT / 'apps/zehnder_monitor/corrected.py'),
                        host + ':' + STACK + '/appdaemon/apps/'], check=True)
    if '--reuse-reference' not in sys.argv:
        subprocess.run(['scp', str(HERE / 'ha/ui-lovelace.yaml'), host + ':' + STACK + '/ha/'], check=True)
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose restart ha'], check=True)
        for _ in range(45):
            try:
                urllib.request.urlopen(base + '/', timeout=2).close()
                break
            except Exception:
                time.sleep(1)
        else:
            raise AssertionError('Isolated HA did not start')
        subprocess.run([sys.executable, str(HERE / 'run_m4.py'), '--reference-only'], check=True)
    tokens = json.loads(Path(os.environ['ZMON_TEST_TOKEN_FILE']).read_text())
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}

    def state(entity):
        req = urllib.request.Request(base + '/api/states/sensor.zehnder_corrected_' + entity,
                                     headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def set_clock(timestamp):
        req = urllib.request.Request(base + '/api/states/sensor.zehnder_monitor_test_clock',
                                     data=json.dumps({'state': timestamp.isoformat(), 'attributes': {}}).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req) as response:
            assert response.status in (200, 201)

    accepted = [0]

    def report(timestamp, power):
        before = accepted[0]
        fresh_after = datetime.now(timezone.utc)
        set_clock(timestamp)
        subprocess.run([sys.executable, str(HERE / 'seed.py'), str(power), '--reported-at', timestamp.isoformat()],
                       env=env, check=True, stdout=subprocess.DEVNULL)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            observed = state('sfp_quality')
            count = int(observed['attributes'].get('accepted_reports', 0))
            snapshot = state('sfp')
            inputs = snapshot['attributes'].get('inputs') or {}
            reports_match = all((inputs.get(key) or {}).get('reported_at') == timestamp.isoformat()
                                for key in ('power', 'supply_flow', 'exhaust_flow'))
            reported_at = observed.get('last_reported') or observed.get('last_updated')
            fresh = reported_at and datetime.fromisoformat(reported_at) >= fresh_after
            if (count > before and fresh and reports_match
                    and observed['attributes'].get('calculated_at') == timestamp.isoformat()):
                accepted[0] = count
                return
            time.sleep(.25)
        raise AssertionError(f'Virtual source report at {timestamp.isoformat()} was not accepted')

    def reset_trend():
        # Test-only fixture reset preserves the learned reference and its cycle.
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose stop appdaemon'], check=True)
        code = ("import json,pathlib\n"
                f"p=pathlib.Path('{STACK}/appdaemon/zehnder-monitor/corrected_v2.json')\n"
                "x=json.loads(p.read_text());x['trend_reports']=[];p.write_text(json.dumps(x))\n")
        subprocess.run(['ssh', host, 'python3 -'], input=code.encode(), check=True)
        # REST-created clock must exist before AppDaemon takes its snapshot.
        set_clock(datetime.now(timezone.utc))
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose --profile publisher start appdaemon'], check=True)
        accepted[0] = 0

    def replay(slope, hours):
        reset_trend()
        for minute in (0, 1, 2):
            report(start + timedelta(minutes=minute), 72)
        for hour in hours:
            power = 72 + slope * (hour / 24) * (350 / 3600) * 1000
            report(start + timedelta(hours=hour), power)
        return state('trend_quality'), state('sfp_trend')

    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    quality, trend = replay(.010, range(4, 113, 4))
    assert quality['state'] == 'ready', quality
    assert quality['attributes']['bucket_count'] >= 24
    assert quality['attributes']['span_hours'] >= 72
    assert abs(float(trend['state']) - 10) <= .1, trend
    assert trend['attributes']['point'] == 'Medium:350'
    sparse_quality, sparse_trend = replay(.010, range(4, 49, 4))
    assert sparse_quality['state'] == 'insufficient_coverage'
    assert sparse_trend['state'] in ('unknown', 'unavailable')
    negative_quality, negative_trend = replay(-.010, range(4, 113, 4))
    assert negative_quality['state'] == 'ready'
    assert abs(float(negative_trend['state']) + 10) <= .1
    flat_quality, flat_trend = replay(0, range(4, 113, 4))
    assert flat_quality['state'] == 'ready'
    assert abs(float(flat_trend['state'])) <= .1
    event = {'event_id': 'e2e-m6-next-cycle-' + datetime.now(timezone.utc).strftime('%H%M%S'),
             'both_filter_paths_clean': True}
    req = urllib.request.Request(base + '/api/events/zehnder_monitor_clean_filters_confirmed',
                                 data=json.dumps(event).encode(),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req):
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and state('trend_quality')['state'] != 'no_matching_reference':
        time.sleep(.5)
    assert state('sfp_trend')['state'] in ('unknown', 'unavailable')
    set_clock(datetime.now(timezone.utc))
    subprocess.run([sys.executable, str(HERE / 'seed.py'), '72'], env=env,
                   check=True, stdout=subprocess.DEVNULL)
    print(json.dumps({'result': 'pass', 'slope_w_per_m3s_day': trend['state'],
                      'bucket_count': quality['attributes']['bucket_count'],
                      'span_hours': quality['attributes']['span_hours'],
                      'point': trend['attributes']['point'],
                      'sparse': sparse_quality['state'],
                      'negative': negative_trend['state'], 'flat': flat_trend['state'],
                      'new_cycle_trend': state('sfp_trend')['state']}))


if __name__ == '__main__':
    main()
