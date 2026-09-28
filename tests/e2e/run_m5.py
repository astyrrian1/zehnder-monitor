"""Isolated matched-flow deterioration, recovery, and boundary journey."""
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


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    subprocess.run(['scp', str(HERE / 'ha/ui-lovelace.yaml'), host + ':' + STACK + '/ha/'], check=True)
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose restart ha'], check=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + '/', timeout=2) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(1)
    else:
        raise AssertionError('Isolated HA did not become ready')
    subprocess.run([sys.executable, str(HERE / 'run_m4.py'), '--reference-only'], check=True)
    tokens = json.loads(Path(os.environ['ZMON_TEST_TOKEN_FILE']).read_text())
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': tokens['access_token']}

    def state(entity):
        request = urllib.request.Request(base + '/api/states/sensor.zehnder_corrected_' + entity,
                                         headers={'Authorization': 'Bearer ' + env['ZMON_TEST_HA_TOKEN']})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def seed(power='72', *extra):
        subprocess.run([sys.executable, str(HERE / 'seed.py'), power, *map(str, extra)], env=env, check=True, stdout=subprocess.DEVNULL)

    def until(predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.5)
        raise AssertionError('Timed out awaiting comparison state')

    # Test-only 20-second window ensures prior clean observations have aged out.
    time.sleep(21)
    for _ in range(7):
        seed('79.2', '--exhaust-duty', 46, '--supply-duty', 47,
             '--supply-rpm', 1375, '--exhaust-rpm', 1375)
        time.sleep(2.1)
    until(lambda: state('comparison_quality')['state'] == 'ready')
    change = state('sfp_change')
    assert abs(float(change['state']) - 10) <= .1, change
    assert float(state('supply_duty_change_pp')['state']) == 2
    assert float(state('exhaust_duty_change_pp')['state']) == 4
    assert abs(float(state('supply_rpm_flow_change_pct')['state']) - 10) <= .1
    assert change['attributes']['selected_reference']['point'] == 'Medium:350'
    print('deterioration', change['state'], 'reference', change['attributes']['selected_reference']['point'], flush=True)

    time.sleep(21)
    for _ in range(7):
        seed('72')
        time.sleep(2.1)
    until(lambda: state('comparison_quality')['state'] == 'ready')
    assert abs(float(state('sfp_change')['state'])) <= .1
    assert abs(float(state('exhaust_duty_change_pp')['state'])) <= .1
    recovered_pct = float(state('sfp_change')['state'])
    print('recovered', recovered_pct, flush=True)

    seed('72', '--fan-level', 'Low')
    until(lambda: state('comparison_quality')['state'] == 'no_matching_reference')
    assert state('sfp_change')['state'] in ('unknown', 'unavailable')
    seed('72', '--supply-flow', 362.6, '--exhaust-flow', 362.6)
    until(lambda: state('comparison_quality')['state'] != 'no_matching_reference')
    seed('72', '--supply-flow', 367.6, '--exhaust-flow', 350)
    until(lambda: state('comparison_quality')['state'] == 'no_matching_reference')
    print(json.dumps({'result': 'pass', 'deterioration_pct': 10, 'recovered_pct': recovered_pct,
                      'fan_level_mismatch_rejected': True, 'boundary_match': True, 'outside_tolerance_rejected': True}))


if __name__ == '__main__':
    main()
