"""Exercise unchanged bypass and per-metric rejection through isolated HA/MQTT."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent

def main():
    base = os.environ['ZMON_TEST_HA_URL'].rstrip('/')
    if not base.endswith(':18123'):
        raise SystemExit('Isolated HA required')
    tokens = json.loads(Path(os.environ['ZMON_TEST_TOKEN_FILE']).read_text())
    request = urllib.request.Request(base+'/auth/token', data=urllib.parse.urlencode({
        'grant_type':'refresh_token','refresh_token':tokens['refresh_token'],'client_id':base+'/'}).encode())
    tokens.update(json.load(urllib.request.urlopen(request)))
    Path(os.environ['ZMON_TEST_TOKEN_FILE']).write_text(json.dumps(tokens))
    token = tokens['access_token']
    env = {**os.environ, 'ZMON_TEST_HA_TOKEN':token}
    def api(path, data=None):
        r=urllib.request.Request(base+path, data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        return json.load(urllib.request.urlopen(r, timeout=15))
    def state(name):
        return api('/api/states/sensor.zehnder_corrected_'+name)
    def until(predicate):
        deadline=time.monotonic()+65
        while time.monotonic()<deadline:
            result=predicate()
            if result:return result
            time.sleep(1)
        raise AssertionError('Isolated quality did not reach expected state')
    api('/api/states/sensor.zehnder_monitor_test_clock',{'state':'unknown','attributes':{}})
    subprocess.run([sys.executable,str(HERE/'seed.py'),'72'],env=env,check=True)
    now=datetime.now(timezone.utc)
    def inject(entity,value,unit,stamp):
        api('/api/states/'+entity,{'state':str(value),'attributes':{
            'unit_of_measurement':unit,'source_reported_at':stamp.isoformat()}})
    inject('binary_sensor.zehnder_comfoair_q_a4cb9c_status','on',None,now-timedelta(hours=4))
    inject('sensor.zehnder_comfoair_q_a4cb9c_bypass_state',0,'%',now-timedelta(hours=3))
    until(lambda: state('sfp_quality')['attributes'].get('baseline_eligible'))
    until(lambda: state('recovery_raw')['state']=='80.0')
    assert abs(float(state('sfp')['state'])-.740571)<.00005
    inject('sensor.zehnder_comfoair_q_a4cb9c_supply_fan_duty',120,'%',now)
    until(lambda: state('sfp_quality')['attributes'].get('fan_effort_quality')=='invalid')
    assert state('sfp_quality')['state']=='current'
    inject('sensor.zehnder_comfoair_q_a4cb9c_supply_fan_speed',1250,'rpm',now-timedelta(hours=3))
    api('/api/events/zehnder_monitor_clean_filters_confirmed', {
        'event_id':'isolated-quality-'+now.isoformat(), 'both_filter_paths_clean':True})
    # Real publication cadence; only the isolated calibration durations are shortened.
    for _ in range(28):
        stamp=datetime.now(timezone.utc)
        for suffix,value,unit in [('power',72,'W'),('supply_fan_flow',350,'m³/h'),
                                  ('exhaust_fan_flow',350,'m³/h')]:
            inject('sensor.zehnder_comfoair_q_a4cb9c_'+suffix,value,unit,stamp)
        time.sleep(2)
    refs=state('calibration')['attributes']['references']
    assert refs and any('sfp' in metrics for metrics in refs.values()), refs
    assert all('duty' not in metrics and 'rpm_flow' not in metrics for metrics in refs.values()), refs
    inject('binary_sensor.zehnder_comfoair_q_a4cb9c_status','off',None,now)
    until(lambda: state('recovery_quality')['attributes'].get('reason')=='device_offline')
    assert state('recovery_raw')['state']=='unavailable'
    inject('binary_sensor.zehnder_comfoair_q_a4cb9c_status','on',None,now)
    # A pre-reconnect bypass position is still ineligible.
    time.sleep(3)
    assert state('recovery_raw')['state']=='unavailable'
    inject('sensor.zehnder_comfoair_q_a4cb9c_bypass_state',0,'%',datetime.now(timezone.utc))
    until(lambda: state('recovery_quality')['state']=='current')
    print(json.dumps({'result':'pass','old_bypass_with_live_evidence':'usable',
        'offline_or_pre_reconnect_bypass':'unavailable','invalid_duty_sfp':'current','qualified_reference':'sfp_only'}))

if __name__ == '__main__':
    main()
