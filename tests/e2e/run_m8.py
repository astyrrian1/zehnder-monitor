"""Isolated manufacturer countdown reminder and reconciliation journey."""
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
NOTICE_ID = 'zehnder_filter_maintenance'


def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Refusing non-isolated HA URL')
    host = os.environ['ZMON_TEST_STACK_SSH']
    path = Path(os.environ['ZMON_TEST_TOKEN_FILE'])
    tokens = json.loads(path.read_text())
    form = urllib.parse.urlencode({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': base + '/'}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + '/auth/token', data=form)) as response:
        tokens.update(json.load(response))
    path.write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}

    def api(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     method='GET' if body is None else 'POST')
        try:
            with urllib.request.urlopen(req) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def state(entity):
        return api('/api/states/' + entity)

    def notifications():
        return json.loads(subprocess.check_output(['node', str(HERE / 'get_notifications.mjs')], env=env, text=True))

    def notice():
        matching = [item for item in notifications() if item['notification_id'] == NOTICE_ID]
        assert len(matching) <= 1
        return matching[0] if matching else None

    def until(predicate, limit=20):
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.5)
        raise AssertionError('Timed out awaiting maintenance state')

    def countdown(value):
        subprocess.run([sys.executable, str(HERE / 'set_source.py'), 'filter_days', str(value), 'd'], env=env, check=True, stdout=subprocess.DEVNULL)

    # Restore a pending corrected cycle and install only into the named E2E stack.
    subprocess.run([sys.executable, str(HERE / 'run_m3.py')], env=env, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['scp', str(HERE / 'ha/configuration.yaml'), host + ':' + STACK + '/ha/'], check=True)
    subprocess.run(['ssh', host, 'mkdir -p ' + STACK + '/ha/packages'], check=True)
    subprocess.run(['scp', str(HERE / 'ha/packages/zehnder_monitor_v2.yaml'), host + ':' + STACK + '/ha/packages/'], check=True)
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose restart ha'], check=True)
    for _ in range(45):
        try:
            urllib.request.urlopen(base + '/', timeout=2).close()
            break
        except Exception:
            time.sleep(1)
    else:
        raise AssertionError('Isolated HA did not start')
    until(lambda: (s if (s := state('automation.zehnder_monitor_maintenance_reminder')) and s['state'] == 'on' else None))
    until(lambda: state('input_text.zehnder_monitor_maintenance_notice_key'))
    for entity in ('notice_key', 'seen_cycle', 'suppressed_cycle'):
        api('/api/services/input_text/set_value', {'entity_id': 'input_text.zehnder_monitor_maintenance_' + entity, 'value': ''})
    api('/api/services/input_boolean/turn_off', {'entity_id': 'input_boolean.zehnder_monitor_maintenance_notice_live'})
    api('/api/services/persistent_notification/dismiss', {'notification_id': NOTICE_ID})
    countdown('unknown')
    assert notice() is None
    started = time.monotonic()
    countdown(7)
    first = until(lambda: notice())
    initial_latency = time.monotonic() - started
    assert initial_latency <= 60
    assert 'due soon' in first['title']
    assert state('input_text.zehnder_monitor_maintenance_notice_key')['state'] == 'unconfirmed|soon'
    for _ in range(3):
        api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_maintenance_reminder'})
    assert notice()['created_at'] == first['created_at']

    # HA restart clears its in-memory notice; restored helper state reinstates the same ID once.
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose restart ha'], check=True)
    for _ in range(45):
        try:
            urllib.request.urlopen(base + '/', timeout=2).close()
            break
        except Exception:
            time.sleep(1)
    countdown(7)
    restored = until(lambda: notice())
    assert restored['notification_id'] == NOTICE_ID
    for _ in range(3):
        api('/api/services/automation/trigger', {'entity_id': 'automation.zehnder_monitor_maintenance_reminder'})
    assert notice()['created_at'] == restored['created_at']

    countdown(0)
    due = until(lambda: (n if (n := notice()) and 'due now' in n['title'] else None))
    assert due['notification_id'] == NOTICE_ID
    countdown('unknown')
    assert notice()['created_at'] == due['created_at']
    countdown(180)
    assert notice()['created_at'] == due['created_at'], 'Timer reset alone dismissed maintenance'
    countdown(0)
    event_id = 'e2e-m8-confirmed-' + datetime.now(timezone.utc).strftime('%H%M%S')
    api('/api/events/zehnder_monitor_clean_filters_confirmed',
        {'event_id': event_id, 'both_filter_paths_clean': True})
    until(lambda: state('sensor.zehnder_corrected_calibration')['attributes'].get('cycle_id') == event_id)
    until(lambda: notice() is None)
    assert state('input_text.zehnder_monitor_maintenance_suppressed_cycle')['state'] == event_id
    countdown(7)
    assert notice() is None
    countdown(180)
    until(lambda: state('input_text.zehnder_monitor_maintenance_suppressed_cycle')['state'] == '')
    countdown(7)
    later = until(lambda: notice())
    assert later['notification_id'] == NOTICE_ID
    assert state('input_text.zehnder_monitor_maintenance_notice_key')['state'] == event_id + '|soon'
    # Retained MQTT test source is available as HA starts already due.
    # The discovery topic is removed afterward; this source is never production.
    container = 'zehnder-monitor-e2e-mqtt-1'
    config_topic = 'homeassistant/sensor/zehnder_e2e_countdown/config'
    state_topic = 'zehnder/e2e/countdown'
    def mqtt_publish(topic, payload):
        subprocess.run(['ssh', host, 'docker', 'exec', '-i', container, 'mosquitto_pub',
                        '-h', 'localhost', '-t', topic, '-s', '-r'],
                       input=payload.encode(), check=True)
    def mqtt_clear(topic):
        subprocess.run(['ssh', host, 'docker', 'exec', container, 'mosquitto_pub',
                        '-h', 'localhost', '-t', topic, '-n', '-r'], check=True)
    subprocess.run(['ssh', host, f'cd {STACK} && docker compose stop ha'], check=True)
    try:
        mqtt_publish(config_topic, json.dumps({
            'name': 'Zehnder ComfoAir Q A4CB9C Filter Replacement Remaining Days',
            'unique_id': 'zehnder_e2e_countdown',
            'default_entity_id': 'sensor.zehnder_comfoair_q_a4cb9c_filter_replacement_remaining_days',
            'state_topic': state_topic, 'unit_of_measurement': 'd'}))
        mqtt_publish(state_topic, '0')
        started_due = time.monotonic()
        subprocess.run(['ssh', host, f'cd {STACK} && docker compose start ha'], check=True)
        def already_due():
            try:
                source = state('sensor.zehnder_comfoair_q_a4cb9c_filter_replacement_remaining_days')
                current = notice()
                return source and source['state'] == '0' and current and 'due now' in current['title']
            except (OSError, subprocess.CalledProcessError):
                return False
        until(already_due, limit=60)
        startup_due_seconds = time.monotonic() - started_due
        assert startup_due_seconds <= 60
    finally:
        mqtt_clear(config_topic)
        mqtt_clear(state_topic)
    print(json.dumps({'result': 'pass', 'initial_seconds': round(initial_latency, 1),
                      'single_notice_id': NOTICE_ID, 'restart_reinstated': True,
                      'escalated': True, 'unknown_suppressed': True,
                      'confirmed_cycle': event_id, 'later_episode_notified': True,
                      'already_due_startup_seconds': round(startup_due_seconds, 1)}))


if __name__ == '__main__':
    main()
