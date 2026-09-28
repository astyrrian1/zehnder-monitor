"""Repeatable isolated confirmed-maintenance to frozen-reference journey."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STACK = '/opt/stacks/zehnder-monitor-e2e'
CORRECTED = STACK + '/appdaemon/zehnder-monitor/corrected_v2.json'
EVENT = 'zehnder_monitor_clean_filters_confirmed'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    token_path = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    tokens = json.loads(token_path.read_text())
    form = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': base + '/'}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + '/auth/token', data=form)) as response:
        tokens.update(json.load(response))
    token_path.write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}
    subprocess.run(['scp', str(ROOT / 'apps/zehnder_monitor/zehnder_monitor.py'),
                    str(ROOT / 'apps/zehnder_monitor/corrected.py'),
                    str(HERE / 'appdaemon/apps/apps.yaml'),
                    host + ':' + STACK + '/appdaemon/apps/'], check=True)

    def seed(power='72'):
        subprocess.run([sys.executable, str(HERE / 'seed.py'), power], env=env, check=True, stdout=subprocess.DEVNULL)

    def set_source(*args):
        subprocess.run([sys.executable, str(HERE / 'set_source.py'), *args], env=env, check=True, stdout=subprocess.DEVNULL)

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base + path, data=data, headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST' if payload is not None else 'GET')
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def stack(command):
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose {command}'], check=True, stdout=subprocess.DEVNULL)

    def persisted():
        result = subprocess.run(['ssh', host, 'cat', CORRECTED], capture_output=True)
        return json.loads(result.stdout) if result.returncode == 0 else None

    def until(predicate, limit=20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.5)
        raise AssertionError('Timed out awaiting isolated state')

    # Only this named E2E volume is reset; production and legacy files are untouched.
    subprocess.run(['ssh', host, 'rm', '-f', CORRECTED], check=True)
    seed()
    subprocess.run(['ssh', host, "sh -c 'cat > /opt/stacks/zehnder-monitor-e2e/appdaemon/secrets.yaml'"], input=('ha_token: ' + token + '\n').encode(), check=True)
    started_at = datetime.now(timezone.utc)
    stack('restart appdaemon')
    until(lambda: (p if (p := persisted()) and p['calibration']['state'] == 'awaiting_confirmation' else None))
    until(lambda: (datetime.fromisoformat(t) > started_at if (t := request('/api/states/sensor.zehnder_corrected_calibration')['attributes'].get('calculated_at')) else False))
    event_id = 'e2e-m4-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    event = {'event_id': event_id, 'both_filter_paths_clean': True}
    request('/api/events/' + EVENT, event)
    first = until(lambda: (p if (p := persisted()) and p['calibration']['cycle_id'] == event_id else None))
    c = first['calibration']
    assert c['state'] == 'settling'
    assert (datetime.fromisoformat(c['settling_until']) - datetime.fromisoformat(c['confirmed_at'])).total_seconds() == 6
    assert (datetime.fromisoformat(c['learning_until']) - datetime.fromisoformat(c['confirmed_at'])).total_seconds() == 126
    request('/api/events/' + EVENT, event)
    assert persisted()['calibration'] == c, 'Duplicate event changed cycle'
    stack('restart appdaemon')
    assert persisted()['calibration'] == c, 'Restart changed deadlines'
    time.sleep(6)
    # Distinct HA source reports span the test-only 32-second qualification minimum.
    for _ in range(24):
        seed()
        time.sleep(2.1)
    p = persisted(); c = p['calibration']
    assert c['state'] == 'qualified', c['state']
    ref = c['references']['Medium:350']
    assert set(ref) == {'sfp', 'duty', 'rpm_flow'}
    assert ref['sfp']['count'] >= 20
    assert (datetime.fromisoformat(ref['sfp']['last_reported_at']) - datetime.fromisoformat(ref['sfp']['first_reported_at'])).total_seconds() >= 32
    frozen = json.dumps(ref, sort_keys=True)
    for _ in range(3):
        seed('79.2')
        time.sleep(2.1)
    assert json.dumps(persisted()['calibration']['references']['Medium:350'], sort_keys=True) == frozen
    assert request('/api/states/sensor.zehnder_corrected_calibration')['state'] == 'qualified'
    assert request('/api/states/sensor.zehnder_corrected_sfp')['state'] not in ('unknown', 'unavailable')
    if '--reference-only' in sys.argv:
        print(json.dumps({'result': 'pass', 'cycle_id': event_id, 'reference_metrics': list(ref), 'accepted_sfp_samples': ref['sfp']['count']}))
        return
    # New confirmed cycle with missing optional RPM still learns SFP and duty.
    second_id = event_id + '-partial'
    request('/api/events/' + EVENT, {'event_id': second_id, 'both_filter_paths_clean': True})
    until(lambda: (p if (p := persisted()) and p['calibration']['cycle_id'] == second_id else None))
    time.sleep(6)
    for _ in range(24):
        seed()
        set_source('supply_rpm', 'unavailable', 'rpm')
        time.sleep(2.1)
    p2 = persisted()
    ref2 = p2['calibration']['references']['Medium:350']
    assert set(ref2) == {'sfp', 'duty'}, ref2
    assert p2['archived_references'][-1]['cycle_id'] == event_id
    assert p2['archived_references'][-1]['references']['Medium:350']['sfp'] == ref['sfp']
    # A damaged v2 file cannot silently revive a corrected reference.
    stack('stop appdaemon')
    subprocess.run(['ssh', host, f"printf '{{broken' > {CORRECTED}"], check=True)
    seed()
    stack('--profile publisher up -d appdaemon')
    until(lambda: request('/api/states/sensor.zehnder_corrected_calibration')['state'] == 'awaiting_confirmation')
    assert request('/api/states/sensor.zehnder_corrected_sfp_change')['state'] in ('unknown', 'unavailable')
    set_source('filter_days', '7', 'd')
    time.sleep(2.2)
    set_source('filter_days', '180', 'd')
    until(lambda: request('/api/states/sensor.zehnder_corrected_calibration')['attributes'].get('timer_confirmation_requested'))
    assert request('/api/states/sensor.zehnder_corrected_calibration')['state'] == 'awaiting_confirmation'
    print(json.dumps({'result': 'pass', 'cycle_id': event_id, 'settling_seconds': 6, 'learning_seconds': 120, 'reference_metrics': list(ref), 'accepted_sfp_samples': ref['sfp']['count'], 'archived_cycles': len(p['archived_references'])}))


if __name__ == '__main__':
    main()
