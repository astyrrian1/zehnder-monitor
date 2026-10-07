import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from test_zehnder_monitor import ZehnderMonitor
from test_corrected_sfp import load_calculation

T = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)

def inputs(now):
    vals = {'power':(72,'W'),'supply_flow':(350,'m³/h'),'exhaust_flow':(350,'m³/h'),
            'bypass':(0,'%'),'fan_level':('Medium',None),'status':('on',None),
            'supply_duty':(45,'%'),'exhaust_duty':(42,'%'),'supply_rpm':(1250,'rpm'),
            'exhaust_rpm':(1250,'rpm'),'supply_temp':(16,'°C'),'outdoor_temp':(0,'°C'),'extract_temp':(20,'°C')}
    return {k:{'value':v,'unit':u,'reported_at':now.isoformat()} for k,(v,u) in vals.items()}

class AuditRegressions(unittest.TestCase):
    def test_unchanged_bypass_needs_connected_fresh_device_evidence(self):
        c = load_calculation(); x = inputs(T)
        x['status']['reported_at'] = (T-timedelta(hours=4)).isoformat()
        x['bypass']['reported_at'] = (T-timedelta(hours=3)).isoformat()
        self.assertEqual(c.evaluate_recovery_inputs(x,T.isoformat())['quality'],'current')
        x['status']['value']='off'
        self.assertNotEqual(c.evaluate_recovery_inputs(x,T.isoformat())['quality'],'current')
        x['status']['value']='on'; x['status']['reported_at']=T.isoformat()
        self.assertNotEqual(c.evaluate_recovery_inputs(x,T.isoformat())['quality'],'current')
        x['status']['reported_at']=(T-timedelta(hours=4)).isoformat()
        x['power']['reported_at']=(T-timedelta(minutes=11)).isoformat()
        self.assertNotEqual(c.evaluate_recovery_inputs(x,T.isoformat())['quality'],'current')

    def test_fan_change_restarts_stability(self):
        c=load_calculation(); recent=[]
        for i,level in enumerate(['Medium','Medium','Low','Low','Low']):
            now=T+timedelta(minutes=i); x=inputs(now); x['fan_level']['value']=level
            ok,_,recent=c.evaluate_sampling_eligibility(x,c.evaluate_sfp(x,now.isoformat()),recent,now.isoformat())
            self.assertEqual(ok,i==4)

    def test_bad_optional_inputs_never_enter_references(self):
        m=ZehnderMonitor.__new__(ZehnderMonitor); m.args={}; m.v2_state=m._corrected_defaults()
        m._save_corrected_state=lambda:None
        m._confirm_clean_filters('isolated-audit',(T-timedelta(hours=2)).isoformat())
        m.call_service=lambda *a,**kw:None
        for i in range(24):
            now=T+timedelta(minutes=i*2); x=inputs(now)
            x['supply_duty']['value']=120
            x['supply_rpm']['reported_at']=(now-timedelta(hours=3)).isoformat()
            states={m.E[k]:{'state':z['value'],'attributes':{'unit_of_measurement':z['unit']},'last_reported':z['reported_at']} for k,z in x.items()}
            m.get_state=lambda eid,attribute=None:states.get(eid); m._corrected_now=lambda:now
            m._publish_corrected_sfp()
        refs=m.v2_state['calibration']['references']['Medium:350']
        self.assertIn('sfp',refs)
        self.assertNotIn('duty',refs)
        self.assertNotIn('rpm_flow',refs)

    def test_structural_corruption_recovers_before_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'corrected_v2.json').write_text(json.dumps({'schema_version':2,'calibration':{'state':'settling','references':{}}}))
            m=ZehnderMonitor.__new__(ZehnderMonitor); m.args={'data_dir':directory}; m.log=lambda *a,**kw:None
            self.assertEqual(m._load_corrected_state()['calibration']['state'],'awaiting_confirmation')
