"""Reproducible virtual motor for the project. Units are synthetic g, degrees C and RPM."""
import copy
import math
import numpy as np

RATE = 256
N = 512
CHANNELS = ('temperature', 'vibration', 'rpm')
INPUT_RANGES = {'temperature': (0, 150), 'vibration': (0, 2), 'rpm': (0, 3600)}
DEFAULT_LIMITS = {
    'temperature': {'min': 20., 'max': 40., 'low_margin': 5., 'medium_margin': 15.},
    'vibration': {'min': 0., 'max': .30, 'low_margin': .10, 'medium_margin': .30},
    'rpm': {'min': 1200., 'max': 1800., 'low_margin': 150., 'medium_margin': 400.},
}
LEVELS = ('NORMAL', 'RENDAH', 'SEDANG', 'TINGGI')
FEATURES = ['temp_ma10_c', 'vibration_rms_g', 'rpm']

def default_controls():
    return {'sensors': {k: {'mode': 'auto', 'value': v} for k,v in
            zip(CHANNELS, (30., .18, 1500.))},
            'random_faults': True, 'shutdown_delay_s': 10.,
            'limits': copy.deepcopy(DEFAULT_LIMITS)}

def numeric(value, lo, hi, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{name}: masukkan angka {lo} sampai {hi}.')
    return float(value)

def update_controls(current, patch):
    """Validate entire request on a copy, avoiding partial writes on invalid input."""
    if not isinstance(patch, dict) or set(patch) - {'sensors','random_faults','shutdown_delay_s','limits'}:
        raise ValueError('Pengaturan tidak dikenal.')
    nxt = copy.deepcopy(current)
    if 'sensors' in patch:
        if not isinstance(patch['sensors'], dict) or set(patch['sensors']) - set(CHANNELS):
            raise ValueError('Sensor tidak dikenal.')
        for k, row in patch['sensors'].items():
            if not isinstance(row, dict) or set(row) - {'mode','value'}:
                raise ValueError('Format sensor tidak valid.')
            if 'mode' in row:
                if row['mode'] not in ('manual','auto'): raise ValueError('Mode harus manual atau auto.')
                nxt['sensors'][k]['mode'] = row['mode']
            if 'value' in row:
                nxt['sensors'][k]['value'] = numeric(row['value'], *INPUT_RANGES[k], k)
    if 'random_faults' in patch:
        if type(patch['random_faults']) is not bool: raise ValueError('random_faults harus boolean.')
        nxt['random_faults'] = patch['random_faults']
    if 'shutdown_delay_s' in patch:
        nxt['shutdown_delay_s'] = numeric(patch['shutdown_delay_s'], 3, 60, 'Jeda stop')
    if 'limits' in patch:
        if not isinstance(patch['limits'], dict) or set(patch['limits']) - set(CHANNELS):
            raise ValueError('Batas sensor tidak dikenal.')
        for k, row in patch['limits'].items():
            if not isinstance(row, dict) or set(row) - {'min','max'}:
                raise ValueError('Batas hanya min dan max.')
            for field, value in row.items():
                nxt['limits'][k][field] = numeric(value, *INPUT_RANGES[k], f'{k} {field}')
            if nxt['limits'][k]['min'] >= nxt['limits'][k]['max']:
                raise ValueError('Batas minimum harus lebih kecil dari maksimum.')
    return nxt

def check_limits(row, limits, running=True):
    values = {'temperature':row['temp_c'], 'vibration':row['rms'], 'rpm':row['rpm']}
    readings = {}
    for k,v in values.items():
        b = limits[k]
        delta = max(b['min']-v, v-b['max'], 0.) if (running or k=='temperature') else 0.
        # Float roundoff at exact boundaries is not a real exceedance.
        if delta <= 1e-9: level = 0
        elif delta <= b['low_margin']+1e-9: level = 1
        elif delta <= b['medium_margin']+1e-9: level = 2
        else: level = 3
        readings[k] = {'level':level, 'status':LEVELS[level],
                       'direction':'low' if v < b['min'] else 'high' if v > b['max'] else 'in_range'}
    highest = max(x['level'] for x in readings.values())
    return {'level':highest, 'status':LEVELS[highest], 'sensors':readings}

def waveform(rpm, rms, rng):
    if rms <= 0: return np.zeros(N)
    t = np.arange(N)/RATE
    x = np.sin(2*np.pi*rpm/60*t) + .12*np.sin(4*np.pi*rpm/60*t) + rng.normal(0,.035,N)
    x -= x.mean()
    return x*(rms/np.sqrt(np.mean(x*x)))

def features_vibration(signal):
    x = np.asarray(signal,dtype=float)
    x = x-x.mean()
    rms = float(np.sqrt(np.mean(x*x)))
    if rms < 1e-12: return 0., 0.
    power = np.abs(np.fft.rfft(x*np.hanning(len(x))))
    peak = float(np.fft.rfftfreq(len(x),1/RATE)[1+np.argmax(power[1:])])
    return rms,peak

class Motor:
    def __init__(self, seed=2026):
        self.rng = np.random.default_rng(seed)
        self.tick = 0
        self.phase = float(self.rng.uniform(0,6))
        self.nominal = float(self.rng.uniform(1380,1620))
        self.ambient_offset = float(self.rng.uniform(-2,2))
        self.temp = 30. + self.ambient_offset
        self.rpm = 1500.
        self.vibration = .18
        self.fault = 'normal'
        self.severity = 0
        self.until = 0
        self.next_event = int(self.rng.integers(30,51))

    def inject(self, kind='normal', severity=1, duration=30):
        if kind not in ('normal','overheating','unbalance','overspeed'):
            raise ValueError('Skenario tidak dikenal.')
        if type(severity) is not int or not 1 <= severity <= 3:
            raise ValueError('Kekuatan gangguan 1–3.')
        self.fault, self.severity = kind, severity if kind != 'normal' else 0
        self.until = self.tick + duration
        self.next_event = self.until + int(self.rng.integers(25,46))

    def step(self, controls, running=True):
        self.tick += 1
        if self.tick >= self.until: self.fault, self.severity = 'normal',0
        if running and controls['random_faults'] and self.tick >= self.next_event:
            self.inject(str(self.rng.choice(['overheating','unbalance','overspeed'])),
                        int(self.rng.integers(1,4)), int(self.rng.integers(22,36)))
        sensors = controls['sensors']
        if running:
            rpm_target = self.nominal + 100*np.sin(self.tick/23+self.phase) + self.rng.normal(0,3)
            if self.fault == 'overspeed': rpm_target = (1900,2100,2450)[self.severity-1]
            self.rpm = float(sensors['rpm']['value']) if sensors['rpm']['mode']=='manual' else .45*rpm_target+.55*self.rpm
            temp_target = 31.+.008*(self.rpm-1500)+self.ambient_offset
            vib_target = max(.02,.18+.00010*(self.rpm-1500)+.010*np.sin(self.tick/15+self.phase)+self.rng.normal(0,.002))
            if self.fault=='overheating': temp_target = (44,51,75)[self.severity-1]
            if self.fault=='unbalance': vib_target = (.36,.52,.9)[self.severity-1]
            if sensors['temperature']['mode']=='manual': self.temp = float(sensors['temperature']['value'])
            else: self.temp += .14*(temp_target-self.temp)+self.rng.normal(0,.035)
            self.vibration = float(sensors['vibration']['value']) if sensors['vibration']['mode']=='manual' else .60*vib_target+.40*self.vibration
        else:
            self.rpm, self.vibration = 0.,0.
            self.temp += .045*(25.-self.temp)
        x = waveform(self.rpm,self.vibration,self.rng)
        rms,peak = features_vibration(x)
        return {'temp_c':round(float(self.temp),4), 'rpm':round(float(self.rpm),2),
                'rms':rms, 'peak_hz':peak, 'waveform':x[::4].round(5).tolist(),
                'scenario':self.fault, 'injection_severity':self.severity}
