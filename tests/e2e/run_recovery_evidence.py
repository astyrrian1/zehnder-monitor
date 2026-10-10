"""Verify recovery evidence through simulated HA -> AppDaemon -> MQTT -> HA.

Requires the isolated stack initialized by run_m7.py. Never targets production.
"""
from datetime import datetime, timedelta, timezone
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
    token = json.loads(Path(os.environ['ZMON_TEST_TOKEN_FILE']).read_text())['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN': token}
    def api(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    def state(suffix):
        return api('/api/states/sensor.zehnder_corrected_'+suffix)
    def seed(stamp=None):
        args = [] if stamp is None else ['--reported-at',stamp]
        subprocess.run([sys.executable,str(HERE/'seed.py'),'72',*args],env=env,check=True,stdout=subprocess.DEVNULL)
    def set_source(key, value, unit):
        subprocess.run([sys.executable,str(HERE/'set_source.py'),key,str(value),unit],env=env,check=True,stdout=subprocess.DEVNULL)
    api('/api/states/sensor.zehnder_monitor_test_clock',{'state':'unknown','attributes':{}})
    stamp = (datetime.now(timezone.utc)-timedelta(seconds=8)).isoformat()
    seed(stamp)
    subprocess.run(['ssh',os.environ['ZMON_TEST_STACK_SSH'],
        'cd /opt/stacks/zehnder-monitor-e2e && docker compose restart appdaemon'],check=True)
    # Startup and five independent flow reports all fit inside the 30-second test window.
    time.sleep(5)
    first = state('recovery_raw')
    for _ in range(5):
        set_source('supply_flow',350,'m³/h')
        set_source('exhaust_flow',350,'m³/h')
        time.sleep(2.1)
    unchanged = state('recovery_raw')
    evidence={'initial':first,'airflow_only':unchanged}
    Path('/private/tmp/zehnder-recovery-evidence.json').write_text(json.dumps(evidence,indent=2))
    assert first['attributes']['age_seconds'] >= 8, 'Age falsely follows airflow rather than oldest temperature'
    assert unchanged['attributes']['conditioned_count'] == 1, 'Airflow-only reports inflated temperature evidence'
    assert state('recovery_conditioned')['state'] in ('unknown','unavailable')
    # All three sensors genuinely report again, with unchanged numeric values.
    for _ in range(6):
        seed()
        time.sleep(2.1)
    ready = state('recovery_conditioned')
    assert float(ready['state']) == 80
    assert ready['attributes']['conditioned_count'] >= 5
    assert abs(float(state('sfp')['state'])-.7405714286) <= .00005
    evidence['ready']=ready
    evidence['sfp']=state('sfp')
    Path('/private/tmp/zehnder-recovery-evidence.json').write_text(json.dumps(evidence,indent=2))
    print('PASS: airflow-only evidence rejected, oldest temperature age shown, genuine reports qualify, SFP unchanged')

if __name__ == '__main__':
    main()
