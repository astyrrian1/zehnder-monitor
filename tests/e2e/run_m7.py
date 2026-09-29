"""Isolated heating, cooling, unit, invalidity, and anomaly recovery journey."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STACK = '/opt/stacks/zehnder-monitor-e2e'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    subprocess.run(['scp', str(ROOT / 'apps/zehnder_monitor/zehnder_monitor.py'),
                    str(ROOT / 'apps/zehnder_monitor/corrected.py'),
                    str(HERE / 'appdaemon/apps/apps.yaml'), host + ':' + STACK + '/appdaemon/apps/'], check=True)
    subprocess.run(['scp', str(HERE / 'ha/ui-lovelace.yaml'), host + ':' + STACK + '/ha/'], check=True)
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose stop appdaemon && docker compose restart ha'], check=True)
    for _ in range(45):
        try:
            urllib.request.urlopen(base + '/', timeout=2).close()
            break
        except Exception:
            time.sleep(1)
    tokens_path = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    tokens = json.loads(tokens_path.read_text())
    form = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': base + '/'}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + '/auth/token', data=form)) as response:
        tokens.update(json.load(response))
    tokens_path.write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}

    def api(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     method='GET' if body is None else 'POST')
        with urllib.request.urlopen(req) as response:
            return json.load(response)

    def state(entity):
        try:
            return api('/api/states/sensor.zehnder_corrected_' + entity)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {'state': 'unknown', 'attributes': {}}
            raise

    def seed(*extra):
        subprocess.run([sys.executable, str(HERE / 'seed.py'), '72', *map(str, extra)], env=env, check=True, stdout=subprocess.DEVNULL)

    def until(predicate, limit=10):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.5)
        raise AssertionError('Timed out awaiting recovery state')

    # Disable any virtual clock left by the trend replay.
    api('/api/states/sensor.zehnder_monitor_test_clock', {'state': 'unknown', 'attributes': {}})
    seed()
    subprocess.run(['ssh', host, "sh -c 'cat > /opt/stacks/zehnder-monitor-e2e/appdaemon/secrets.yaml'"], input=('ha_token: ' + token + '\n').encode(), check=True)
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose --profile publisher start appdaemon'], check=True)
    until(lambda: state('recovery_raw')['state'] == '80.0')
    assert float(state('recovery_raw')['state']) == 80
    assert state('sfp')['state'] not in ('unknown', 'unavailable')

    seed('--supply-temp', 60.8, '--outdoor-temp', 32, '--extract-temp', 68, '--temp-unit', '°F')
    until(lambda: state('recovery_raw')['attributes']['temperatures_c']['outdoor_temp'] == 0)
    assert float(state('recovery_raw')['state']) == 80
    seed('--supply-temp', 22, '--outdoor-temp', 30, '--extract-temp', 20)
    until(lambda: state('recovery_raw')['attributes']['temperatures_c']['outdoor_temp'] == 30)
    assert float(state('recovery_raw')['state']) == 80

    for _ in range(6):
        seed()
        time.sleep(2.1)
    until(lambda: state('recovery_conditioned')['state'] not in ('unknown', 'unavailable'))
    assert float(state('recovery_conditioned')['state']) == 80
    seed('--supply-temp', 4, '--outdoor-temp', 0, '--extract-temp', 4.9)
    until(lambda: state('recovery_quality')['attributes'].get('reason') == 'small_temperature_difference')
    assert state('recovery_raw')['state'] in ('unknown', 'unavailable')
    assert state('recovery_conditioned')['attributes']['conditioned_state'] == 'historical'
    assert state('sfp')['state'] not in ('unknown', 'unavailable')
    seed('--bypass', 10)
    until(lambda: state('recovery_quality')['attributes'].get('reason') == 'bypass_open')
    seed()
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    subprocess.run([sys.executable, str(HERE / 'set_source.py'), 'supply_temp', '16', '°C', '--reported-at', old], env=env, check=True, stdout=subprocess.DEVNULL)
    until(lambda: state('recovery_quality')['state'] == 'stale')
    assert state('sfp')['state'] not in ('unknown', 'unavailable')
    seed('--supply-temp', 24)
    until(lambda: state('recovery_quality')['state'] == 'anomalous')
    assert float(state('recovery_raw')['state']) == 120
    assert state('sfp')['state'] not in ('unknown', 'unavailable')
    print(json.dumps({'result': 'pass', 'heating_pct': 80, 'cooling_pct': 80,
                      'fahrenheit_pct': 80, 'anomalous_raw_pct': 120,
                      'sfp_independent': True}))


if __name__ == '__main__':
    main()
