"""Run the ten-outcome isolated acceptance chain before any production change."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    token_file = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    os.environ['ZMON_TEST_STACK_SSH']
    token = json.loads(token_file.read_text())['access_token']
    clock_reset = urllib.request.Request(
        base + '/api/states/sensor.zehnder_monitor_test_clock',
        data=json.dumps({'state': 'unknown', 'attributes': {}}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='POST')
    urllib.request.urlopen(clock_reset).close()
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'journeys': []}
    for name, args in (
        ('milestone-1', ('run_m1.py',)),
        ('milestone-2', ('run_m2.py',)),
        ('milestone-3', ('run_m3.py',)),
        ('milestone-3-quiet', ('assert_pending_quiet.py', '--seconds', '601')),
        ('milestone-4', ('run_m4.py',)),
        ('milestone-5', ('run_m5.py',)),
        ('milestone-6', ('run_m6.py',)),
        ('milestone-7', ('run_m7.py',)),
        ('quality-regressions', ('run_quality_regressions.py',)),
        ('milestone-8', ('run_m8.py',)),
        ('milestone-9', ('run_m9.py',)),
        ('milestone-9-edge', ('run_m9_edge.py',)),
        ('milestone-10-rollback', ('run_m10_rollback.py',)),
    ):
        env = {**os.environ, 'ZMON_TEST_HA_TOKEN': json.loads(token_file.read_text())['access_token']}
        started = time.monotonic()
        print(json.dumps({'journey': name, 'phase': 'start'}), flush=True)
        result = subprocess.run([sys.executable, '-u', str(HERE / args[0]), *args[1:]],
                                env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        outcome = {'journey': name, 'exit_code': result.returncode,
                   'seconds': round(time.monotonic() - started, 1),
                   'last_output': result.stdout.strip().splitlines()[-1:]}
        report['journeys'].append(outcome)
        print(json.dumps(outcome), flush=True)
        if result.returncode:
            print(result.stdout, flush=True)
            report['result'] = 'fail'
            break
    else:
        report['result'] = 'pass'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    output = Path(os.environ.get('ZMON_TEST_REPORT', '/private/tmp/zehnder-m10-chain.json'))
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({'result': report['result'], 'report': str(output)}), flush=True)
    if report['result'] != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
