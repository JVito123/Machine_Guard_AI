import copy
import json
import math
import unittest
import numpy as np
from engine import Engine
from messages import make_message
from config import PROJECT_ID,MACHINE_ID
from simulation import (FEATURES, Motor, default_controls, update_controls, check_limits,
                        DEFAULT_LIMITS, waveform, features_vibration)

class Clock:
    def __init__(self):self.now=0.
    def __call__(self):return self.now
    def advance(self,n=1.):self.now+=n

class Detector:
    def __init__(self):self.inputs=[]
    def score_samples(self,rows):self.inputs.extend(rows);return np.array([-.5]*len(rows))

def engine(offline=True):
    clock=Clock();model=Detector()
    live=Engine({'version':'machineguard-detector-1','features':FEATURES,'model':model,'limit':1.},
                offline=offline,clock=clock)
    live.control({'random_faults':False})
    return live,clock,model

def manual(live,temp=30,vibration=.18,rpm=1500):
    live.control({'sensors':{k:{'mode':'manual','value':v} for k,v in
                            zip(('temperature','vibration','rpm'),(temp,vibration,rpm))}})

def steps(live,clock,n,emulate=True):
    packets=[]
    for _ in range(n):
        msg=live.tick()
        if msg:
            packets.append(msg)
            if emulate:live.emulate(msg)
        clock.advance()
    return packets

def reply(packet,avg,device='esp32'):
    return make_message('temperature_feature',device,packet['run_id'],packet['seq'],packet['sim_time_s'],
                        {'temp_ma10_c':avg,'window_n':10,'last_sample_sim_time_s':packet['sim_time_s']},
                        project_id=PROJECT_ID,machine_id=MACHINE_ID)

class EngineTests(unittest.TestCase):
    def test_temperature_boundaries(self):
        for t,want in [(20,0),(40,0),(40.01,1),(41,1),(45,1),(45.01,2),(55,2),(55.01,3),(19,1),(0,3)]:
            with self.subTest(t=t):self.assertEqual(check_limits({'temp_c':t,'rms':.18,'rpm':1500},DEFAULT_LIMITS)['level'],want)

    def test_zero_rpm_valid_when_stopped(self):
        row={'temp_c':30,'rms':0,'rpm':0}
        self.assertEqual(check_limits(row,DEFAULT_LIMITS,False)['level'],0)
        self.assertEqual(check_limits(row,DEFAULT_LIMITS,True)['level'],3)

    def test_controls_atomic_and_numeric(self):
        c=default_controls();before=copy.deepcopy(c)
        for value in (True,'41',float('nan'),float('inf'),151):
            with self.assertRaises(ValueError): update_controls(c,{'random_faults':False,'sensors':{'temperature':{'value':value}}})
            self.assertEqual(c,before)
        with self.assertRaises(ValueError):update_controls(c,{'limits':{'temperature':{'min':60,'max':40}}})

    def test_mixed_manual_auto(self):
        c=update_controls(default_controls(),{'random_faults':False,'sensors':{'temperature':{'mode':'manual','value':41}}})
        m=Motor(7);rows=[m.step(c) for _ in range(12)]
        self.assertTrue(all(x['temp_c']==41 for x in rows))
        self.assertGreater(len(set(x['rpm'] for x in rows)),1)

    def test_waveform_rms_and_frequency(self):
        x=waveform(1500,.3,np.random.default_rng(1));rms,peak=features_vibration(x)
        self.assertAlmostEqual(rms,.3,places=9);self.assertEqual(peak,25.)
        self.assertEqual(features_vibration(np.zeros(512)),(0.,0.))

    def test_random_generator_reproducible(self):
        a,b=Motor(2026),Motor(2026);c=default_controls();seen=set()
        for _ in range(160):
            x,y=a.step(c),b.step(c);self.assertEqual(x,y);seen.add(x['scenario'])
        self.assertGreater(len(seen),1)

    def test_manual_41_alarms_before_ma10(self):
        live,clock,_=engine();manual(live,41);live.tick();s=live.snapshot()
        self.assertEqual(s['alarm']['status'],'RENDAH');self.assertIsNone(s['temp_ma10']);self.assertEqual(s['ai'],'WAITING_EDGE')

    def test_waits_ten_samples(self):
        live,clock,model=engine();manual(live);steps(live,clock,9)
        self.assertIsNone(live.snapshot()['temp_ma10']);steps(live,clock,1)
        self.assertEqual(live.snapshot()['temp_ma10'],30.);self.assertEqual(model.inputs[-1],[30.,.18,1500.])

    def test_automatic_stop_without_http_or_browser(self):
        live,clock,_=engine();manual(live,65);steps(live,clock,1)
        self.assertTrue(live.running);self.assertEqual(live.snapshot()['countdown_s'],9.)
        clock.advance(9);live.tick();s=live.snapshot()
        self.assertFalse(s['running']);self.assertIsNone(s['countdown_s']);self.assertEqual(s['rpm'],0.)
        self.assertGreater(s['temperature'],40.);self.assertEqual(s['ai'],'NOT_RUNNING')
        steps(live,clock,30);self.assertFalse(live.running)

    def test_manual_stop_cancels_timer(self):
        live,clock,_=engine();manual(live,65);steps(live,clock,2);live.control({'action':'stop'})
        clock.advance(15);live.tick();self.assertIsNone(live.high_since)
        self.assertEqual(live.stop_reason,'Stop manual oleh operator.')

    def test_recovery_cancels_and_restarts_timer(self):
        live,clock,_=engine();manual(live,65);steps(live,clock,3)
        manual(live,30);steps(live,clock,1);self.assertIsNone(live.high_since)
        manual(live,65);steps(live,clock,1);self.assertEqual(live.snapshot()['countdown_s'],9.)

    def test_timer_change_uses_elapsed_time(self):
        live,clock,_=engine();manual(live,65);steps(live,clock,5)
        live.control({'shutdown_delay_s':3});self.assertFalse(live.running)

    def test_start_after_stop_requires_new_edge_window(self):
        live,clock,_=engine();manual(live);steps(live,clock,11);old=live.run_id
        live.control({'action':'stop'});live.control({'action':'start'})
        self.assertNotEqual(live.run_id,old);self.assertIsNone(live.snapshot()['anomaly_score'])
        steps(live,clock,10);self.assertEqual(live.snapshot()['ai'],'NORMAL')

    def test_new_raw_sample_does_not_erase_score(self):
        live,clock,_=engine();manual(live);steps(live,clock,10)
        live.tick();self.assertEqual(live.snapshot()['anomaly_score'],.5)

    def test_stale_age_uses_capture_time(self):
        live,clock,_=engine(False);manual(live)
        live.connection('CONNECTED');live.subscription='READY'
        msgs=steps(live,clock,10,False);clock.advance(12)
        self.assertTrue(live.accept(reply(msgs[-1],30)))
        self.assertEqual(live.snapshot()['ai'],'STALE');self.assertIsNone(live.snapshot()['anomaly_score'])

    def test_mqtt_never_emulates(self):
        live,clock,_=engine(False)
        with self.assertRaises(RuntimeError):live.emulate(live.tick())

    def test_wrong_session_duplicate_and_wrong_average(self):
        live,clock,_=engine(False);manual(live);live.connection('CONNECTED');live.subscription='READY'
        msgs=steps(live,clock,12,False)
        m=reply(msgs[-1],30);old=copy.deepcopy(m);old['run_id']='old'
        self.assertFalse(live.accept(old));self.assertFalse(live.accept(reply(msgs[-1],31)))
        self.assertTrue(live.accept(m));self.assertFalse(live.accept(m));self.assertEqual(live.matched,1)

    def test_out_of_order_does_not_roll_back_result(self):
        live,clock,_=engine(False);manual(live);live.connection('CONNECTED');live.subscription='READY'
        msgs=steps(live,clock,13,False)
        self.assertTrue(live.accept(reply(msgs[-1],30)));self.assertTrue(live.accept(reply(msgs[-2],30)))
        self.assertEqual(live.snapshot()['ai_sequence'],12)

    def test_labels_and_alarm_limits_not_model_inputs(self):
        live,clock,model=engine();manual(live);steps(live,clock,10)
        old=model.inputs[-1]
        live.control({'preset':{'kind':'overheating','severity':3},'limits':{'temperature':{'max':25}}})
        steps(live,clock,1);self.assertEqual(model.inputs[-1],old)

    def test_json_finite_and_no_waveform_in_history(self):
        live,clock,_=engine();steps(live,clock,12)
        json.dumps(live.snapshot(),allow_nan=False)
        self.assertNotIn('waveform',live.snapshot()['history'][0])

    # Auto RPM ramps up from 0 after Start; that ramp must not look like a TINGGI fault.
    def test_auto_rpm_start_ramp_is_not_a_fault(self):
        live,clock,_=engine();steps(live,clock,3)
        live.control({'action':'stop'});steps(live,clock,3)
        live.control({'action':'start'})
        ramp,levels=[],[]
        for _ in range(6):
            steps(live,clock,1);ramp.append(live.current['rpm']);levels.append(live.alarm['level'])
        self.assertLess(min(ramp),live.controls['limits']['rpm']['min'])  # the ramp really happened
        self.assertEqual(max(levels),0)
        self.assertIsNone(live.high_since)
        self.assertNotIn('COUNTDOWN',[e['kind'] for e in live.events])

    def test_manual_low_rpm_alarms_immediately_after_start(self):
        live,clock,_=engine();manual(live,rpm=700);steps(live,clock,2)
        live.control({'action':'stop'});steps(live,clock,2)
        live.control({'action':'start'});steps(live,clock,1)
        self.assertEqual(live.alarm['sensors']['rpm']['level'],3)

    def test_start_grace_expires_for_truly_low_auto_rpm(self):
        live,clock,_=engine();steps(live,clock,2)
        live.control({'action':'stop'});steps(live,clock,2)
        live.control({'limits':{'rpm':{'min':2200,'max':2400}}})
        live.control({'action':'start'});steps(live,clock,8)
        self.assertGreater(live.alarm['sensors']['rpm']['level'],0)
        self.assertFalse(live.starting)

if __name__=='__main__':unittest.main()
