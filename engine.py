"""Machine state, alarms with delayed auto-stop, and joining the ESP32 MA10 with laptop features."""
import copy
import math
import threading
import time
import uuid
from collections import deque

from config import PROJECT_ID, MACHINE_ID
from messages import validate_message, make_message
from simulation import Motor, default_controls, update_controls, check_limits, FEATURES, LEVELS

MAX_AGE = 10.
# Auto RPM ramps up from 0 after Start (~730, ~1140, ~1360 RPM). The low-RPM limit is held
# for at most this many samples so the ramp is not reported as a TINGGI fault.
START_GRACE_SAMPLES = 5

class Engine:
    def __init__(self, bundle, offline=False, seed=2026, clock=time.monotonic):
        if bundle.get('features') != FEATURES or bundle.get('version') != 'machineguard-detector-1':
            raise ValueError('Format model tidak cocok. Jalankan train_model.py untuk melatih ulang.')
        self.model, self.limit = bundle['model'], bundle['limit']
        self.offline, self.seed, self.clock = offline, seed, clock
        self.lock = threading.RLock()
        self.controls = default_controls()
        self.motor = Motor(seed)
        self.running = True
        self.started = self.clock()
        self.last_tick = -math.inf
        self.history = deque(maxlen=240)
        self.events = deque(maxlen=100)
        self.current = None
        self.latest = None
        self.connection_floor = -1
        self.broker = 'OFFLINE_PREVIEW' if offline else 'CONNECTING'
        self.subscription = 'EMULATED' if offline else 'PENDING'
        self.matched, self.tx_acked = 0,0
        self.last_rejection = None
        self.error = None
        self.alarm = {'level':0,'status':'NORMAL','sensors':{}}
        self.high_since = None
        self.stop_reason = None
        self.starting, self.start_samples = False, 0
        self._new_run()
        self._event('START','Simulator dimulai.')

    def _event(self, kind, detail):
        self.events.appendleft({'time':round(self.clock()-self.started,1),'kind':kind,'detail':detail})

    def _new_run(self):
        self.run_id = 'run-'+uuid.uuid4().hex[:8]
        self.seq = 0
        self.latest = None
        self.connection_floor = -1
        self.emulated = deque(maxlen=10)

    def connection(self, state):
        with self.lock:
            self.broker = state
            self.subscription = 'PENDING'
            self.latest = None
            self.connection_floor = self.seq-1

    def _stop(self, reason):
        if self.running:
            self.running = False
            self.motor.rpm, self.motor.vibration = 0.,0.
            self.high_since = None
            self.starting = False
            self.stop_reason = reason
            self._new_run()
            self._event('STOP',reason)

    def _safety(self, now):
        if (self.running and self.high_since is not None and
                now-self.high_since >= self.controls['shutdown_delay_s']):
            self._stop('Stop otomatis: gangguan tinggi bertahan '+
                       f"{self.controls['shutdown_delay_s']:g} detik.")

    def _alarm(self, row, now):
        previous = self.alarm['level']
        alarm = check_limits(row,self.controls['limits'],self.running)
        # Start phase: hold only the LOW-RPM check while Auto RPM ramps up. High limits,
        # temperature and vibration are still checked; Manual RPM gets no grace.
        if (self.running and self.starting and self.controls['sensors']['rpm']['mode']=='auto'
                and alarm['sensors']['rpm']['direction']=='low'):
            alarm['sensors']['rpm'] = {'level':0,'status':LEVELS[0],'direction':'starting'}
            highest = max(x['level'] for x in alarm['sensors'].values())
            alarm['level'], alarm['status'] = highest, LEVELS[highest]
        self.alarm = alarm
        if self.running and self.alarm['level']==3:
            if self.high_since is None:
                self.high_since = now
                self._event('COUNTDOWN','Gangguan tinggi; operator dapat menekan Stop.')
        elif self.high_since is not None:
            self.high_since = None
            self._event('CANCEL','Gangguan turun dari tinggi; hitung mundur dibatalkan.')
        if self.alarm['level'] != previous:
            self._event('ALARM','Alarm batas: '+self.alarm['status'])
        self._safety(now)

    def control(self, body):
        with self.lock:
            if not isinstance(body,dict): raise ValueError('Permintaan harus objek JSON.')
            allowed = {'action','preset','sensors','random_faults','shutdown_delay_s','limits'}
            if set(body)-allowed: raise ValueError('Perintah tidak dikenal.')
            action = body.get('action')
            if action is not None and action not in ('start','stop','reset'):
                raise ValueError('Tindakan tidak dikenal.')
            preset = body.get('preset')
            if preset is not None:
                if (not isinstance(preset,dict) or set(preset)!={'kind','severity'} or
                    preset['kind'] not in ('normal','overheating','unbalance','overspeed') or
                    type(preset['severity']) is not int or not 1 <= preset['severity'] <= 3):
                    raise ValueError('Preset memerlukan kind dan severity 1–3.')
            patch = {k:v for k,v in body.items() if k not in ('action','preset')}
            nxt = update_controls(self.controls,patch)
            # Do not combine reset with settings: no silently discarded writes.
            if action=='reset' and (patch or preset): raise ValueError('Kirim reset tersendiri.')
            self.controls = nxt
            if patch:
                self._event('CONTROL','Pengaturan diterapkan.')
                if self.current: self._alarm(self.current,self.clock())
            if action=='stop': self._stop('Stop manual oleh operator.')
            elif action=='start' and not self.running:
                self.running, self.stop_reason = True,None
                self.high_since = None
                self.starting, self.start_samples = True, 0
                self._new_run()
                self.last_tick = -math.inf
                self._event('START','Mesin dinyalakan operator; buffer edge diisi ulang. '
                            f'Batas bawah RPM ditahan selama fase start (maks. {START_GRACE_SAMPLES} sampel).')
            elif action=='reset':
                self.controls = default_controls()
                self.motor = Motor(self.seed)
                self.running, self.stop_reason = True,None
                self.high_since, self.current = None,None
                self.starting, self.start_samples = False, 0
                self.history.clear()
                self.alarm = {'level':0,'status':'NORMAL','sensors':{}}
                self.last_tick = -math.inf
                self._new_run()
                self._event('RESET','Simulasi dan kontrol kembali ke awal.')
            if preset is not None:
                self.motor.inject(preset['kind'],preset['severity'],40)
                self._event('INJECT',f"Preset {preset['kind']} level {preset['severity']}; hanya kanal Auto terpengaruh.")
            self._safety(self.clock())
            return self.snapshot()

    def tick(self):
        """Called every 100ms. Sampling at 1 Hz; protection also runs between samples."""
        with self.lock:
            now = self.clock()
            self._safety(now)
            if now-self.last_tick < 1.: return None
            self.last_tick = now
            row = self.motor.step(self.controls,self.running)
            row.update(seq=self.seq, sim_time_s=float(self.seq+1), run_id=self.run_id,
                       captured=now, elapsed=round(now-self.started,2), running=self.running,
                       ma10=None, score=None, ai=None)
            self.seq += 1
            self.current = row
            self.history.append(row)
            if self.starting:
                self.start_samples += 1
                if (self.controls['sensors']['rpm']['mode']!='auto' or self.start_samples > START_GRACE_SAMPLES
                        or row['rpm'] >= self.controls['limits']['rpm']['min']):
                    self.starting = False
            self._alarm(row,now)
            return make_message('temperature_sample','plant',row['run_id'],row['seq'],row['sim_time_s'],
                                {'temp_c':row['temp_c'],'sample_sim_time_s':row['sim_time_s']},
                                project_id=PROJECT_ID,machine_id=MACHINE_ID)

    def emulate(self, message):
        """Explicit offline preview only. Never called by the MQTT path."""
        if not self.offline: raise RuntimeError('Emulasi hanya tersedia pada pratinjau offline.')
        with self.lock:
            if message['run_id']!=self.run_id: return
            self.emulated.append(message['payload']['temp_c'])
            if len(self.emulated) < 10: return
            feature = make_message('temperature_feature','emulator',self.run_id,message['seq'],message['sim_time_s'],
                         {'temp_ma10_c':round(sum(self.emulated)/10,3),'window_n':10,
                          'last_sample_sim_time_s':message['sim_time_s']},project_id=PROJECT_ID,machine_id=MACHINE_ID)
        self.accept(feature)

    def accept(self, message):
        validate_message(message)
        with self.lock:
            data=message['payload']
            expected_device='emulator' if self.offline else 'esp32'
            if (message['project_id'],message['machine_id'],message['device_id'],message['msg_type']) != (
                    PROJECT_ID,MACHINE_ID,expected_device,'temperature_feature'):
                self.last_rejection='Identitas fitur berbeda.'; return False
            if message['run_id']!=self.run_id:
                self.last_rejection='Balasan berasal dari sesi lama.'; return False
            if (set(data)!={'window_n','last_sample_sim_time_s','temp_ma10_c'} or
                type(data['window_n']) is not int or data['window_n']!=10 or
                type(data['last_sample_sim_time_s']) not in (float,int) or
                data['last_sample_sim_time_s']!=message['sim_time_s'] or
                type(data['temp_ma10_c']) not in (float,int) or not 0<=data['temp_ma10_c']<=150):
                self.last_rejection='Payload MA10 tidak valid.'; return False
            seq=message['seq']
            window=[x for x in self.history if x['run_id']==self.run_id and seq-9<=x['seq']<=seq]
            available=self.offline or (self.broker=='CONNECTED' and self.subscription=='READY')
            if (not available or len(window)!=10 or [x['seq'] for x in window]!=list(range(seq-9,seq+1)) or
                seq<=self.connection_floor or window[-1]['sim_time_s']!=message['sim_time_s']):
                self.last_rejection='Jendela sampel tidak cocok.'; return False
            row=window[-1]
            if row['ma10'] is not None: return False
            # Cross-check remote calculation, but never substitute a local mean for missing remote data.
            if abs(sum(x['temp_c'] for x in window)/10-data['temp_ma10_c']) > .004:
                self.last_rejection='MA10 tidak sesuai 10 suhu sumber.'; return False
            row['ma10']=float(data['temp_ma10_c'])
            self.matched+=1
            self.last_rejection=None
            if row['running'] and all(x['running'] for x in window):
                vector=[[row['ma10'],row['rms'],row['rpm']]]
                score=float(-self.model.score_samples(vector)[0])/self.limit
                if not math.isfinite(score): raise ValueError('Skor ML tidak finite.')
                row['score'],row['ai']=score,'ANOMALY' if score>1 else 'NORMAL'
            if self.latest is None or row['seq']>self.latest['seq']: self.latest=row
            return True

    def snapshot(self):
        with self.lock:
            now=self.clock()
            row=self.current
            last=self.latest
            age=now-last['captured'] if last else None
            fresh=age is not None and age<=MAX_AGE
            if not self.running: ai='NOT_RUNNING'
            elif not fresh: ai='STALE' if last else 'WAITING_EDGE'
            else: ai=last['ai'] or 'WARMUP'
            hist=[{k:x[k] for k in ('seq','run_id','elapsed','temp_c','rpm','rms','peak_hz','ma10','score','ai','running')}
                  for x in self.history]
            count=(max(0.,self.controls['shutdown_delay_s']-(now-self.high_since))
                   if self.high_since is not None and self.running else None)
            return {'version':'1.0','server_time':now,'mode':'offline' if self.offline else 'mqtt','run_id':self.run_id,
                'running':self.running,'stop_reason':self.stop_reason,'controls':copy.deepcopy(self.controls),
                'temperature':self.motor.temp,'rpm':self.motor.rpm,'rms':row['rms'] if row and self.running else 0.,
                'peak_hz':row['peak_hz'] if row and self.running else 0.,
                'waveform':row['waveform'] if row and self.running else [0]*128,
                'sequence':row['seq'] if row and row['run_id']==self.run_id else -1,
                'source_age_s':now-row['captured'] if row else None,
                'alarm':copy.deepcopy(self.alarm),'countdown_s':count,
                'ai':ai,'anomaly_score':last['score'] if fresh and self.running else None,
                'threshold':1.,'temp_ma10':last['ma10'] if fresh else None,
                'ai_sequence':last['seq'] if fresh and self.running and last['score'] is not None else None,
                'ai_inputs':({'temp_ma10_c':last['ma10'],'rms':last['rms'],'rpm':last['rpm'],'peak_hz':last['peak_hz']}
                             if fresh and self.running and last['score'] is not None else None),
                'feature_age_s':age,'edge':('RECEIVED' if fresh else 'STALE' if last else 'WAITING'),
                'broker':self.broker,'subscription':self.subscription,'matched':self.matched,'tx_acked':self.tx_acked,
                'last_rejection':self.last_rejection,'error':self.error,
                'scenario':self.motor.fault,'injection_severity':self.motor.severity,
                'history':hist,'events':list(self.events)}
