'use strict';
/* MachineGuard public demo engine (browser only).
 * Active on GitHub Pages (*.github.io), when the page is opened as a local file, or with ?demo=1
 * (?demo=0 forces the real server). It mirrors engine.py + simulation.py with ESP32 and MQTT EMULATED
 * in the browser, and scores with the same trained model exported to model.js
 * (tools/export_model_js.py). It is a stand-in for trying the UI anywhere, not proof of two edges.
 */
(function(){
 const params=new URLSearchParams(location.search),forced=params.get('demo');
 const isActive=forced==='1'||(forced!=='0'&&(location.protocol==='file:'||/\.github\.io$/.test(location.hostname)));

 // ---------- Model: same maths as detector.NormalPatternDetector ----------
 function ifScore(m,x){ // = -IsolationForest.score_samples (sklearn compares float32 features)
  const xf=x.map(Math.fround);let depth=0;
  for(const t of m.trees){let n=0;while(t.L[n]!==-1)n=xf[t.F[n]]<=t.T[n]?t.L[n]:t.R[n];depth+=t.T[n];}
  return Math.pow(2,-depth/(m.trees.length*m.c_max));
 }
 function mahalanobis(m,x){const d=x.map((v,i)=>v-m.mean[i]);let s=0;for(let i=0;i<3;i++)for(let j=0;j<3;j++)s+=d[i]*m.precision[i][j]*d[j];return Math.sqrt(Math.max(0,s));}
 function score(m,x){return Math.max(ifScore(m,x)/m.forest_scale,mahalanobis(m,x)/m.distance_scale)/m.joint_scale/m.limit;}

 // ---------- Simulation: port of simulation.py ----------
 const RATE=256,N=512,CHANNELS=['temperature','vibration','rpm'];
 const INPUT_RANGES={temperature:[0,150],vibration:[0,2],rpm:[0,3600]};
 const DEFAULT_LIMITS={temperature:{min:20,max:40,low_margin:5,medium_margin:15},vibration:{min:0,max:.30,low_margin:.10,medium_margin:.30},rpm:{min:1200,max:1800,low_margin:150,medium_margin:400}};
 const LEVELS=['NORMAL','RENDAH','SEDANG','TINGGI'];
 const clone=o=>JSON.parse(JSON.stringify(o));
 function rngFrom(seed){let a=seed>>>0;const u=()=>{a=(a+0x6D2B79F5)>>>0;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return((t^(t>>>14))>>>0)/4294967296;};
  return{uniform:(lo,hi)=>lo+(hi-lo)*u(),integers:(lo,hi)=>lo+Math.floor(u()*(hi-lo)),normal:(mu,sd)=>{let r=0,v=0;while(r===0)r=u();while(v===0)v=u();return mu+sd*Math.sqrt(-2*Math.log(r))*Math.cos(2*Math.PI*v);},choice:a=>a[Math.floor(u()*a.length)]};}
 function defaultControls(){return{sensors:{temperature:{mode:'auto',value:30},vibration:{mode:'auto',value:.18},rpm:{mode:'auto',value:1500}},random_faults:true,shutdown_delay_s:10,limits:clone(DEFAULT_LIMITS)};}
 function numeric(v,lo,hi,name){if(typeof v!=='number'||!Number.isFinite(v)||v<lo||v>hi)throw new Error(`${name}: masukkan angka ${lo} sampai ${hi}.`);return v;}
 function updateControls(cur,patch){
  const keys=Object.keys(patch);if(keys.some(k=>!['sensors','random_faults','shutdown_delay_s','limits'].includes(k)))throw new Error('Pengaturan tidak dikenal.');
  const nxt=clone(cur);
  if('sensors' in patch){const s=patch.sensors;if(!s||typeof s!=='object'||Object.keys(s).some(k=>!CHANNELS.includes(k)))throw new Error('Sensor tidak dikenal.');
   for(const [k,row] of Object.entries(s)){if(!row||typeof row!=='object'||Object.keys(row).some(f=>!['mode','value'].includes(f)))throw new Error('Format sensor tidak valid.');
    if('mode' in row){if(!['manual','auto'].includes(row.mode))throw new Error('Mode harus manual atau auto.');nxt.sensors[k].mode=row.mode;}
    if('value' in row)nxt.sensors[k].value=numeric(row.value,...INPUT_RANGES[k],k);}}
  if('random_faults' in patch){if(typeof patch.random_faults!=='boolean')throw new Error('random_faults harus boolean.');nxt.random_faults=patch.random_faults;}
  if('shutdown_delay_s' in patch)nxt.shutdown_delay_s=numeric(patch.shutdown_delay_s,3,60,'Jeda stop');
  if('limits' in patch){const l=patch.limits;if(!l||typeof l!=='object'||Object.keys(l).some(k=>!CHANNELS.includes(k)))throw new Error('Batas sensor tidak dikenal.');
   for(const [k,row] of Object.entries(l)){if(!row||typeof row!=='object'||Object.keys(row).some(f=>!['min','max'].includes(f)))throw new Error('Batas hanya min dan max.');
    for(const [f,v] of Object.entries(row))nxt.limits[k][f]=numeric(v,...INPUT_RANGES[k],`${k} ${f}`);
    if(nxt.limits[k].min>=nxt.limits[k].max)throw new Error('Batas minimum harus lebih kecil dari maksimum.');}}
  return nxt;
 }
 function checkLimits(row,limits,running){
  const values={temperature:row.temp_c,vibration:row.rms,rpm:row.rpm},readings={};
  for(const [k,v] of Object.entries(values)){const b=limits[k];const delta=(running||k==='temperature')?Math.max(b.min-v,v-b.max,0):0;
   const level=delta<=1e-9?0:delta<=b.low_margin+1e-9?1:delta<=b.medium_margin+1e-9?2:3;
   readings[k]={level,status:LEVELS[level],direction:v<b.min?'low':v>b.max?'high':'in_range'};}
  const highest=Math.max(...Object.values(readings).map(r=>r.level));
  return{level:highest,status:LEVELS[highest],sensors:readings};
 }
 const COS=[],SIN=[],HANN=[];
 for(let n=0;n<N;n++)HANN.push(.5-.5*Math.cos(2*Math.PI*n/(N-1)));
 function waveform(rpm,rms,rng){if(rms<=0)return new Array(N).fill(0);const x=[];for(let n=0;n<N;n++){const t=n/RATE;x.push(Math.sin(2*Math.PI*rpm/60*t)+.12*Math.sin(4*Math.PI*rpm/60*t)+rng.normal(0,.035));}
  const mean=x.reduce((a,b)=>a+b,0)/N;const y=x.map(v=>v-mean);const r=Math.sqrt(y.reduce((a,b)=>a+b*b,0)/N);return y.map(v=>v*rms/r);}
 function featuresVibration(sig){const mean=sig.reduce((a,b)=>a+b,0)/N,x=sig.map(v=>v-mean),rms=Math.sqrt(x.reduce((a,b)=>a+b*b,0)/N);if(rms<1e-12)return[0,0];
  const w=x.map((v,n)=>v*HANN[n]);let best=1,bestP=-1;
  for(let k=1;k<=N/2;k++){let re=0,im=0;for(let n=0;n<N;n++){const a=2*Math.PI*k*n/N;re+=w[n]*Math.cos(a);im-=w[n]*Math.sin(a);}const p=re*re+im*im;if(p>bestP){bestP=p;best=k;}}
  return[rms,best*RATE/N];}
 class Motor{
  constructor(seed){this.rng=rngFrom(seed);this.tick=0;this.phase=this.rng.uniform(0,6);this.nominal=this.rng.uniform(1380,1620);this.ambient=this.rng.uniform(-2,2);this.temp=30+this.ambient;this.rpm=1500;this.vibration=.18;this.fault='normal';this.severity=0;this.until=0;this.nextEvent=this.rng.integers(30,51);}
  inject(kind,severity,duration){this.fault=kind;this.severity=kind!=='normal'?severity:0;this.until=this.tick+duration;this.nextEvent=this.until+this.rng.integers(25,46);}
  step(c,running){const r=this.rng;this.tick++;if(this.tick>=this.until){this.fault='normal';this.severity=0;}
   if(running&&c.random_faults&&this.tick>=this.nextEvent)this.inject(r.choice(['overheating','unbalance','overspeed']),r.integers(1,4),r.integers(22,36));
   const s=c.sensors;
   if(running){let rpmT=this.nominal+100*Math.sin(this.tick/23+this.phase)+r.normal(0,3);if(this.fault==='overspeed')rpmT=[1900,2100,2450][this.severity-1];
    this.rpm=s.rpm.mode==='manual'?s.rpm.value:.45*rpmT+.55*this.rpm;
    let tempT=31+.008*(this.rpm-1500)+this.ambient;let vibT=Math.max(.02,.18+.0001*(this.rpm-1500)+.010*Math.sin(this.tick/15+this.phase)+r.normal(0,.002));
    if(this.fault==='overheating')tempT=[44,51,75][this.severity-1];if(this.fault==='unbalance')vibT=[.36,.52,.9][this.severity-1];
    this.temp=s.temperature.mode==='manual'?s.temperature.value:this.temp+.14*(tempT-this.temp)+r.normal(0,.035);
    this.vibration=s.vibration.mode==='manual'?s.vibration.value:.60*vibT+.40*this.vibration;}
   else{this.rpm=0;this.vibration=0;this.temp+=.045*(25-this.temp);}
   const x=waveform(this.rpm,this.vibration,r),[rms,peak]=featuresVibration(x);
   return{temp_c:Math.round(this.temp*1e4)/1e4,rpm:Math.round(this.rpm*100)/100,rms,peak_hz:peak,waveform:x.filter((_,i)=>i%4===0).map(v=>Math.round(v*1e5)/1e5),scenario:this.fault,injection_severity:this.severity};}
 }

 // ---------- Engine: port of engine.py, ESP32 MA10 emulated in the browser ----------
 const MAX_AGE=10,START_GRACE_SAMPLES=5,SEED=2026;
 const clock=()=>performance.now()/1000;
 const E={controls:defaultControls(),motor:new Motor(SEED),running:true,started:clock(),lastTick:-Infinity,history:[],events:[],current:null,latest:null,
  alarm:{level:0,status:'NORMAL',sensors:{}},highSince:null,stopReason:null,starting:false,startSamples:0,matched:0,error:null,runId:'',seq:0,window:[]};
 function event(kind,detail){E.events.unshift({time:Math.round((clock()-E.started)*10)/10,kind,detail});if(E.events.length>100)E.events.pop();}
 function newRun(){E.runId='demo-'+Math.random().toString(16).slice(2,10);E.seq=0;E.latest=null;E.window=[];}
 function stop(reason){if(E.running){E.running=false;E.motor.rpm=0;E.motor.vibration=0;E.highSince=null;E.starting=false;E.stopReason=reason;newRun();event('STOP',reason);}}
 function safety(now){if(E.running&&E.highSince!==null&&now-E.highSince>=E.controls.shutdown_delay_s)stop(`Stop otomatis: gangguan tinggi bertahan ${E.controls.shutdown_delay_s} detik.`);}
 function alarm(row,now){const prev=E.alarm.level;const a=checkLimits(row,E.controls.limits,E.running);
  if(E.running&&E.starting&&E.controls.sensors.rpm.mode==='auto'&&a.sensors.rpm.direction==='low'){a.sensors.rpm={level:0,status:LEVELS[0],direction:'starting'};const h=Math.max(...Object.values(a.sensors).map(r=>r.level));a.level=h;a.status=LEVELS[h];}
  E.alarm=a;
  if(E.running&&a.level===3){if(E.highSince===null){E.highSince=now;event('COUNTDOWN','Gangguan tinggi; operator dapat menekan Stop.');}}
  else if(E.highSince!==null){E.highSince=null;event('CANCEL','Gangguan turun dari tinggi; hitung mundur dibatalkan.');}
  if(a.level!==prev)event('ALARM','Alarm batas: '+a.status);
  safety(now);}
 function tick(){
  try{const now=clock();safety(now);if(now-E.lastTick<1)return;E.lastTick=now;
   const row=Object.assign(E.motor.step(E.controls,E.running),{seq:E.seq,sim_time_s:E.seq+1,run_id:E.runId,captured:now,elapsed:Math.round((now-E.started)*100)/100,running:E.running,ma10:null,score:null,ai:null});
   E.seq++;E.current=row;E.history.push(row);if(E.history.length>240)E.history.shift();
   if(E.starting){E.startSamples++;if(E.controls.sensors.rpm.mode!=='auto'||E.startSamples>START_GRACE_SAMPLES||row.rpm>=E.controls.limits.rpm.min)E.starting=false;}
   alarm(row,now);
   // Emulated edge B: mean of the last 10 consecutive samples of this run (what the ESP32 computes).
   E.window.push(row);if(E.window.length>10)E.window.shift();
   if(E.window.length===10){row.ma10=Math.round(E.window.reduce((a,r)=>a+r.temp_c,0)/10*1000)/1000;E.matched++;
    if(row.running&&E.window.every(r=>r.running)&&window.MG_MODEL){row.score=score(window.MG_MODEL,[row.ma10,row.rms,row.rpm]);row.ai=row.score>1?'ANOMALY':'NORMAL';}
    E.latest=row;}
  }catch(err){E.error=`${err.name}: ${err.message}`;stop('Stop karena kesalahan pemrosesan. Lihat diagnostik.');}
 }
 function control(body){
  if(!body||typeof body!=='object'||Array.isArray(body))throw new Error('Permintaan harus objek JSON.');
  if(Object.keys(body).some(k=>!['action','preset','sensors','random_faults','shutdown_delay_s','limits'].includes(k)))throw new Error('Perintah tidak dikenal.');
  const action=body.action;if(action!==undefined&&!['start','stop','reset'].includes(action))throw new Error('Tindakan tidak dikenal.');
  const preset=body.preset;
  if(preset!==undefined&&(!preset||typeof preset!=='object'||Object.keys(preset).sort().join()!=='kind,severity'||!['normal','overheating','unbalance','overspeed'].includes(preset.kind)||!Number.isInteger(preset.severity)||preset.severity<1||preset.severity>3))throw new Error('Preset memerlukan kind dan severity 1–3.');
  const patch={};for(const [k,v] of Object.entries(body))if(k!=='action'&&k!=='preset')patch[k]=v;
  const nxt=updateControls(E.controls,patch),hasPatch=Object.keys(patch).length>0;
  if(action==='reset'&&(hasPatch||preset!==undefined))throw new Error('Kirim reset tersendiri.');
  E.controls=nxt;const now=clock();
  if(hasPatch){event('CONTROL','Pengaturan diterapkan.');if(E.current)alarm(E.current,now);}
  if(action==='stop')stop('Stop manual oleh operator.');
  else if(action==='start'&&!E.running){E.running=true;E.stopReason=null;E.highSince=null;E.starting=true;E.startSamples=0;newRun();E.lastTick=-Infinity;event('START',`Mesin dinyalakan operator; buffer edge diisi ulang. Batas bawah RPM ditahan selama fase start (maks. ${START_GRACE_SAMPLES} sampel).`);}
  else if(action==='reset'){E.controls=defaultControls();E.motor=new Motor(SEED);E.running=true;E.stopReason=null;E.highSince=null;E.current=null;E.starting=false;E.startSamples=0;E.history=[];E.alarm={level:0,status:'NORMAL',sensors:{}};E.lastTick=-Infinity;newRun();event('RESET','Simulasi dan kontrol kembali ke awal.');}
  if(preset!==undefined){E.motor.inject(preset.kind,preset.severity,40);event('INJECT',`Preset ${preset.kind} level ${preset.severity}; hanya kanal Auto terpengaruh.`);}
  safety(clock());return snapshot();
 }
 function snapshot(){
  const now=clock(),row=E.current,last=E.latest,age=last?now-last.captured:null,fresh=age!==null&&age<=MAX_AGE;
  const ai=!E.running?'NOT_RUNNING':!fresh?(last?'STALE':'WAITING_EDGE'):(last.ai||'WARMUP');
  const scored=fresh&&E.running&&last.score!==null;
  return{version:'3.3-demo',server_time:now,mode:'demo',run_id:E.runId,running:E.running,stop_reason:E.stopReason,controls:clone(E.controls),
   temperature:E.motor.temp,rpm:E.motor.rpm,rms:row&&E.running?row.rms:0,peak_hz:row&&E.running?row.peak_hz:0,waveform:row&&E.running?row.waveform:new Array(128).fill(0),
   sequence:row&&row.run_id===E.runId?row.seq:-1,source_age_s:row?now-row.captured:null,alarm:clone(E.alarm),
   countdown_s:E.highSince!==null&&E.running?Math.max(0,E.controls.shutdown_delay_s-(now-E.highSince)):null,
   ai,anomaly_score:fresh&&E.running?last.score:null,threshold:1,temp_ma10:fresh?last.ma10:null,ai_sequence:scored?last.seq:null,
   ai_inputs:scored?{temp_ma10_c:last.ma10,rms:last.rms,rpm:last.rpm,peak_hz:last.peak_hz}:null,feature_age_s:age,edge:fresh?'RECEIVED':last?'STALE':'WAITING',
   broker:'DEMO',subscription:'EMULATED',matched:E.matched,tx_acked:0,last_rejection:window.MG_MODEL?null:'Model AI demo sedang dimuat…',error:E.error,
   scenario:E.motor.fault,injection_severity:E.motor.severity,
   history:E.history.map(x=>({seq:x.seq,run_id:x.run_id,elapsed:x.elapsed,temp_c:x.temp_c,rpm:x.rpm,rms:x.rms,peak_hz:x.peak_hz,ma10:x.ma10,score:x.score,ai:x.ai,running:x.running})),
   events:E.events.slice()};
 }
 function csv(){const cols=['run_id','seq','elapsed','temp_c','rms','rpm','peak_hz','ma10','score','ai','running'];
  return '﻿'+[cols.join(',')].concat(E.history.map(r=>cols.map(c=>r[c]===null||r[c]===undefined?'':String(r[c])).join(','))).join('\r\n')+'\r\n';}

 window.MGDemo={active:()=>isActive,status:snapshot,control,csv,score:(x)=>window.MG_MODEL?score(window.MG_MODEL,x):null};
 if(isActive){
  newRun();event('START','Simulator demo dimulai di browser.');
  if(!window.MG_MODEL){const s=document.createElement('script');s.src='model.js';document.head.appendChild(s);}
  setInterval(tick,100);
 }
})();
