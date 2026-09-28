"""Isolated brief-fault, failed-delivery retry, and new-incident journey."""
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
PACKAGE = HERE / 'ha/packages/zehnder_monitor_fault_v2.yaml'
REMOTE = '/opt/stacks/zehnder-monitor-e2e/ha/packages/zehnder_monitor_fault_v2.yaml'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    path = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    tokens = json.loads(path.read_text())
    form = urllib.parse.urlencode({'grant_type': 'refresh_token',
                                   'refresh_token': tokens['refresh_token'],
                                   'client_id': base + '/'}).encode()
    tokens.update(json.load(urllib.request.urlopen(urllib.request.Request(base + '/auth/token', data=form))))
    path.write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}
    subprocess.run(['ssh', host, "sh -c 'cat > /opt/stacks/zehnder-monitor-e2e/appdaemon/secrets.yaml'"],
                   input=('ha_token: ' + token + '\n').encode(), check=True)
    subprocess.run(['ssh', host, 'cd /opt/stacks/zehnder-monitor-e2e && docker compose restart appdaemon'],
                   check=True, stdout=subprocess.DEVNULL)

    def api(route, body=None):
        request = urllib.request.Request(base + route,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
            method='GET' if body is None else 'POST')
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def state(entity):
        return api('/api/states/' + entity)['state']

    def until(predicate, limit=80):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            try:
                result = predicate()
            except (OSError, urllib.error.HTTPError):
                result = False
            if result:
                return result
            time.sleep(.5)
        raise AssertionError('Timed out awaiting watchdog edge state')

    def notice():
        notifications = json.loads(subprocess.check_output(
            ['node', str(HERE / 'get_notifications.mjs')], env=env, text=True))
        return next((n for n in notifications if n['notification_id'] == 'zehnder_monitor_reporting_fault'), None)

    def trigger():
        try:
            api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_reporting_watchdog'})
        except urllib.error.HTTPError:
            pass  # The deliberately missing test service rejects this trigger.

    def publish_source(script, *args):
        subprocess.run([sys.executable, str(HERE / script), *args], env=env,
                       check=True, stdout=subprocess.DEVNULL)

    def load_package(source):
        subprocess.run(['ssh', host, 'sh -c "cat > ' + REMOTE + '"'],
                       input=source.encode(), check=True)
        api('/api/services/automation/reload', {})
        until(lambda: state('automation.zehnder_monitor_reporting_watchdog') == 'on')

    original = PACKAGE.read_text()
    load_package(original)
    publish_source('seed.py', '72')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current')
    publish_source('set_source.py', 'power', 'NaN', 'W')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'invalid')
    until(lambda: state('input_text.zehnder_monitor_fault_incident') != '')
    assert notice() is None
    publish_source('seed.py', '72')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current')
    for _ in range(3):
        trigger()
        time.sleep(.5)
    until(lambda: state('input_text.zehnder_monitor_fault_incident') == '')
    assert notice() is None

    publish_source('set_source.py', 'power', 'NaN', 'W')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'invalid')
    incident = until(lambda: state('input_text.zehnder_monitor_fault_incident') or None)
    # Backdate only the isolated helper to exercise notification-service failure/retry.
    api('/api/services/input_datetime/set_datetime', {
        'entity_id': 'input_datetime.zehnder_monitor_fault_first_observed',
        'timestamp': time.time() - 660})
    broken = original.replace('action: persistent_notification.create',
                              'action: persistent_notification.missing_test_service')
    assert broken != original
    try:
        load_package(broken)
        trigger()
        time.sleep(2)
        assert state('input_boolean.zehnder_monitor_fault_delivered') == 'off'
        assert notice() is None
    finally:
        load_package(original)
    trigger()
    created = until(notice)
    assert state('input_boolean.zehnder_monitor_fault_delivered') == 'on'
    for _ in range(3):
        trigger()
    assert notice()['created_at'] == created['created_at']
    publish_source('seed.py', '72')
    until(lambda: state('sensor.zehnder_corrected_sfp_quality') == 'current')
    for _ in range(3):
        trigger()
        time.sleep(.5)
    until(lambda: state('input_text.zehnder_monitor_fault_incident') == '')
    assert notice() is None
    print(json.dumps({'result': 'pass', 'brief_interruption_notified': False,
                      'failed_delivery_marked_success': False,
                      'retry_notice_id': created['notification_id'],
                      'new_incident': incident, 'recovered': True}))


if __name__ == '__main__':
    main()
