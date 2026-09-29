"""Rehearse a full v2-to-legacy-to-v2 rollback on the isolated stack."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STACK = '/opt/stacks/zehnder-monitor-e2e'
LEGACY = 'sensor.zehnder_sfp'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    token_path = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    tokens = json.loads(token_path.read_text())
    form = urllib.parse.urlencode({'grant_type': 'refresh_token',
                                   'refresh_token': tokens['refresh_token'],
                                   'client_id': base + '/'}).encode()
    tokens.update(json.load(urllib.request.urlopen(urllib.request.Request(base + '/auth/token', data=form))))
    token_path.write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}

    def api(route):
        request = urllib.request.Request(base + route,
            headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def state(entity):
        try:
            return api('/api/states/' + entity)['state']
        except (OSError, urllib.error.HTTPError):
            return None

    def until(predicate, seconds=150):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(1)
        raise AssertionError('Rollback state did not arrive')

    def ssh(command):
        subprocess.run(['ssh', host, command], check=True)

    def compose(command):
        ssh(f'cd {STACK} && docker compose {command}')

    def seed():
        subprocess.run([sys.executable, str(HERE / 'seed.py'), '72'], env=env,
                       check=True, stdout=subprocess.DEVNULL)

    def sync_token():
        subprocess.run(['ssh', host, "sh -c 'cat > " + STACK + "/appdaemon/secrets.yaml'"],
                       input=('ha_token: ' + token + '\n').encode(), check=True)

    def history():
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        end = datetime.now(timezone.utc).isoformat()
        query = urllib.parse.urlencode({'filter_entity_id': LEGACY,
                                        'end_time': end})
        return api('/api/history/period/' + urllib.parse.quote(start) + '?' + query)

    started = time.monotonic()
    sync_token()
    seed()
    compose('restart appdaemon')
    until(lambda: state('sensor.zehnder_corrected_sfp') not in (None, 'unavailable', 'unknown'))
    until(lambda: state(LEGACY) not in (None, 'unavailable', 'unknown'))
    prior = {(item['last_changed'], item['state']) for series in history() for item in series}
    assert prior, 'Expected legacy recorder history before rollback'
    backup = STACK + '/rollback-m10-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.tar.gz'
    ssh(f'cd {STACK} && tar -czf {backup} appdaemon/apps ha/ui-lovelace.yaml ha/packages appdaemon/zehnder-monitor')
    print(json.dumps({'phase': 'backup', 'path': backup, 'legacy_history_records': len(prior)}), flush=True)
    try:
        with tempfile.TemporaryDirectory() as directory:
            for name in ('zehnder_monitor.py', 'capability.py'):
                source = subprocess.check_output(['git', 'show',
                    '4302542^:apps/zehnder_monitor/' + name], cwd=ROOT)
                path = Path(directory) / name
                path.write_bytes(source)
                subprocess.run(['scp', str(path), host + ':' + STACK + '/appdaemon/apps/'], check=True)
        ssh(f'rm -f {STACK}/appdaemon/apps/corrected.py')
        ssh(f'mv {STACK}/ha/packages {STACK}/ha/packages-rollback && mkdir {STACK}/ha/packages')
        legacy_dashboard = 'title: Legacy Zehnder\nviews:\n  - title: Legacy\n    path: zehnder\n    cards:\n      - type: entities\n        entities:\n          - sensor.zehnder_sfp\n'
        subprocess.run(['ssh', host, "sh -c 'cat > " + STACK + "/ha/ui-lovelace.yaml'"],
                       input=legacy_dashboard.encode(), check=True)
        compose('restart ha')
        until(lambda: state(LEGACY) is not None, 70)
        seed()
        compose('restart appdaemon')
        until(lambda: state(LEGACY) not in (None, 'unavailable', 'unknown'))
        until(lambda: state('sensor.zehnder_corrected_sfp') in (None, 'unavailable'), 190)
        assert state('automation.zehnder_monitor_reporting_watchdog') in (None, 'unavailable')
        elapsed = time.monotonic() - started
        assert elapsed <= 600, f'Rollback exceeded ten minutes: {elapsed:.1f}s'
        current = {(item['last_changed'], item['state']) for series in history() for item in series}
        assert prior <= current, 'Existing legacy recorder observations were lost'
    finally:
        ssh(f'cd {STACK} && tar -xzf {backup} && rm -rf ha/packages-rollback')
        compose('restart ha')
        until(lambda: state(LEGACY) is not None, 70)
        seed()
        sync_token()
        compose('restart appdaemon')
    until(lambda: state('sensor.zehnder_corrected_sfp') not in (None, 'unavailable', 'unknown'))
    print(json.dumps({'result': 'pass', 'rollback_seconds': round(elapsed, 1),
                      'legacy_history_preserved': len(prior), 'v2_restored': True,
                      'backup': backup}), flush=True)


if __name__ == '__main__':
    main()
