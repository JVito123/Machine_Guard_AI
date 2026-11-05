"""Train on synthetic normal sessions; choose threshold on validation, then freeze/test."""
import json
import pickle
import os
import hashlib
from collections import deque
from pathlib import Path
import numpy as np
import sklearn
from detector import NormalPatternDetector
from simulation import Motor, default_controls, FEATURES, check_limits, DEFAULT_LIMITS

ROOT=Path(__file__).resolve().parent

def session(seed,kind='normal',steps=200):
    motor=Motor(seed)
    controls=default_controls()
    controls['random_faults']=False
    window=deque(maxlen=10)
    rows=[]; truths=[]; rules=[]
    for i in range(steps):
        if kind!='normal' and i==70: motor.inject(kind,2,duration=steps)
        row=motor.step(controls)
        window.append(row['temp_c'])
        if len(window)==10 and i>=30:
            rows.append([round(sum(window)/10,3),row['rms'],row['rpm']])
            truths.append(kind!='normal' and i>=90)
            rules.append(check_limits(row,DEFAULT_LIMITS)['level']>0)
    return np.array(rows),np.array(truths),np.array(rules)

def normal_set(seeds): return np.vstack([session(seed)[0] for seed in seeds])

def train():
    train_seeds=list(range(1000,1096)); val_seeds=list(range(2000,2032))
    x=normal_set(train_seeds); val=normal_set(val_seeds)
    forest=NormalPatternDetector().fit(x,val)
    limit=1.
    report={'version':'machineguard-detector-1','features':FEATURES,'threshold_ratio':1.,'raw_threshold':limit,
            'training':'normal only; disjoint session seeds; final combined validation 99.5th percentile; FFT displayed separately, RPM used once',
            'score_rule':'max(IF / forest_scale, learned_Mahalanobis_distance / distance_scale) / joint_scale',
            'calibration':{'forest_scale':forest.forest_scale,'distance_scale':forest.distance_scale,'joint_scale':forest.joint_scale},
            'development_note':'Development explored seeds 100–127/200–207/300–367 and 4000–4319. Pure IF missed large single-sensor departures. Final evaluation uses new seeds 6000+. Threshold fixed before that test.',
            'train_seeds':train_seeds,'validation_seeds':val_seeds,'train_rows':len(x),'validation_rows':len(val),
            'test':{},'notice':'Synthetic evaluation, not industrial safety validation; ML and threshold alarms are separate.',
            'numpy_version':np.__version__,'sklearn_version':sklearn.__version__}
    for j,kind in enumerate(('normal','unbalance','overheating','overspeed')):
        seeds=list(range(6000+j*100,6020+j*100)); samples=[]; baseline=[]
        for seed in seeds:
            arr,truth,rule=session(seed,kind)
            mask=np.ones(len(arr),dtype=bool) if kind=='normal' else truth
            samples.append(arr[mask]); baseline.extend(rule[mask].tolist())
        arr=np.vstack(samples); scores=-forest.score_samples(arr)/limit
        report['test'][kind]={'seeds':seeds,'samples':len(arr),'ai_alarms':int(np.sum(scores>1)),
                              'rule_alarms':sum(baseline),'ai_alarm_rate':float(np.mean(scores>1))}
    report['manual_checks']={}
    # The in-limit rows show that the learned normal band is narrower than the editable alarm limits.
    for label,vector in {'normal':[30,.18,1500],'hot':[65,.18,1500],
                         'vibration':[30,.9,1500],'overspeed':[30,.18,2450],
                         'in_limit_temp_37':[37,.18,1500],'in_limit_vibration_0_25':[30,.25,1500],
                         'in_limit_combination':[35,.25,1300]}.items():
        score=float(-forest.score_samples([vector])[0]/limit)
        rule=check_limits({'temp_c':vector[0],'rms':vector[1],'rpm':vector[2]},DEFAULT_LIMITS)['status']
        report['manual_checks'][label]={'features':vector,'score':score,'ai':'ANOMALY' if score>1 else 'NORMAL',
                                        'alarm_limit':rule}
    bundle={'version':'machineguard-detector-1','features':FEATURES,'model':forest,'limit':limit,
            'sklearn_version':sklearn.__version__,'report':report}
    raw=pickle.dumps(bundle,protocol=4)
    report['model_sha256']=hashlib.sha256(raw).hexdigest()
    for path,data in ((ROOT/'evaluation.json',json.dumps(report,indent=2).encode()),(ROOT/'model.pkl',raw)):
        temp=path.with_suffix(path.suffix+'.tmp'); temp.write_bytes(data); os.replace(temp,path)
    print(json.dumps(report,indent=2))
    return report

if __name__=='__main__': train()
