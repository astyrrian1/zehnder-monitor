"""Isolated real-time reporting-fault journey; holds the full ten-minute threshold."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
STACK = '/opt/stacks/zehnder-monitor-e2e'
NOTICE_ID = 'zehnder_monitor_reporting_fault'


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

    def api(path, body=None):
        request = urllib.request.Request(base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            method='GET' if body is None else 'POST')
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def state(entity):
        return api('/api/states/' + entity)['state']

    def notice():
        items = json.loads(subprocess.check_output(
            ['node', str(HERE / 'get_notifications.mjs')], env=env, text=True))
        matching = [item for item in items if item['notification_id'] == NOTICE_ID]
        assert len(matching) <= 1
        return matching[0] if matching else None

    def until(predicate, limit=200):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            try:
                value = predicate()
            except (OSError, urllib.error.HTTPError, subprocess.CalledProcessError):
                value = False
            if value:
                return value
            time.sleep(1)
        raise AssertionError('Timed out waiting for reporting-fault state')

    def compose(command):
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose {command}'], check=True)

    def sync_publisher_token():
        subprocess.run(['ssh', host, "sh -c 'cat > " + STACK + "/appdaemon/secrets.yaml'"],
                       input=('ha_token: ' + token + '\n').encode(), check=True)

    # Red case before installing the watchdog: automation entity is absent.
    subprocess.run(['scp', str(HERE / 'ha/packages/zehnder_monitor_fault_v2.yaml'),
                    host + ':' + STACK + '/ha/packages/'], check=True)
    subprocess.run(['scp', str(HERE / 'ha/ui-lovelace.yaml'), host + ':' + STACK + '/ha/'], check=True)
    compose('restart ha')
    until(lambda: state('automation.zehnder_monitor_reporting_watchdog') == 'on', 60)
    subprocess.run([sys.executable, str(HERE / 'seed.py'), '72'], env=env, check=True)
    sync_publisher_token()
    compose('restart appdaemon')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current')
    until(lambda: state('input_text.zehnder_monitor_fault_incident') == '')
    assert notice() is None

    # A stopped fan is ordinary ineligibility, not a reporting fault.
    subprocess.run([sys.executable, str(HERE / 'set_source.py'),
                    'supply_flow', '0', 'm³/h'], env=env, check=True)
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'stopped', 70)
    assert state('input_text.zehnder_monitor_fault_incident') == ''
    assert notice() is None
    subprocess.run([sys.executable, str(HERE / 'seed.py'), '72'], env=env, check=True)
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current', 70)

    compose('stop appdaemon')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'unavailable', 190)
    incident = until(lambda: (s if (s := state('input_text.zehnder_monitor_fault_incident')) not in ('', 'unknown') else None), 70)
    first = state('input_datetime.zehnder_monitor_fault_first_observed')
    assert notice() is None
    compose('restart ha')
    until(lambda: state('automation.zehnder_monitor_reporting_watchdog') == 'on', 70)
    assert state('input_text.zehnder_monitor_fault_incident') == incident
    assert state('input_datetime.zehnder_monitor_fault_first_observed') == first
    assert notice() is None

    # Wait real time: no test clock or reduced production threshold is used.
    due = datetime.fromisoformat(first.replace(' ', 'T')).replace(tzinfo=timezone.utc).timestamp() + 600
    while time.time() < due:
        assert notice() is None, 'Premature monitor fault notice'
        time.sleep(min(30, max(1, due - time.time())))
    started = time.monotonic()
    reported = until(notice, 70)
    notification_delay = time.monotonic() - started
    assert notification_delay <= 70
    assert 'reporting' in reported['title'].lower()
    assert 'does not establish' in reported['message']
    assert state('input_boolean.zehnder_monitor_fault_delivered') == 'on'
    for _ in range(3):
        api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_reporting_watchdog'})
    assert notice()['created_at'] == reported['created_at']
    compose('restart ha')
    until(lambda: state('automation.zehnder_monitor_reporting_watchdog') == 'on', 70)
    restored = until(notice, 70)
    assert restored['notification_id'] == NOTICE_ID
    for _ in range(3):
        api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_reporting_watchdog'})
    assert notice()['created_at'] == restored['created_at']

    # Recover with three valid evaluations; a later outage can open a new incident.
    subprocess.run([sys.executable, str(HERE / 'seed.py'), '72'], env=env, check=True)
    sync_publisher_token()
    compose('start appdaemon')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current')
    for _ in range(3):
        api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_reporting_watchdog'})
        time.sleep(1)
    until(lambda: state('input_text.zehnder_monitor_fault_incident') == '', 70)
    assert notice() is None
    assert state('input_boolean.zehnder_monitor_fault_delivered') == 'off'
    print(json.dumps({'result': 'pass', 'incident': incident, 'first_observed': first,
                      'threshold_seconds': 600, 'notification_after_threshold_seconds': round(notification_delay, 1),
                      'notice_id': NOTICE_ID, 'restart_preserved_incident': True,
                      'normal_stopped_no_fault': True, 'recovered_after_three_valid': True}))


if __name__ == '__main__':
    main()
