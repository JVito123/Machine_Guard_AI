'use strict';
// MachineGuard dashboard (single-screen console). Talks to the local server (api/status, api/control),
// or to the browser-only demo engine (window.MGDemo) when the page is opened as the public demo.
const $=id=>document.getElementById(id);
const channels=['temperature','vibration','rpm'];
const names={temperature:'Suhu',vibration:'Getaran',rpm:'Putaran'};
const units={temperature:'°C',vibration:'g',rpm:'RPM'};
const decimals={temperature:1,vibration:2,rpm:0};
const full={temperature:100,vibration:1.2,rpm:2800}; // gauge and slider full scale (display only)
const RING_FULL=2; // AI ring full scale; the threshold 1,0 sits at the top of the ring
const aiLabels={NORMAL:'Normal',ANOMALY:'Anomali',WAITING_EDGE:'Menunggu ESP32',STALE:'Data ESP32 basi',NOT_RUNNING:'Mesin berhenti',WARMUP:'Mengisi buffer'};
const aiWaiting={WAITING_EDGE:'Menunggu rata-rata suhu (MA10) dari ESP32.',STALE:'Balasan ESP32 terlambat lebih dari 10 detik, jadi AI tidak menilai.',NOT_RUNNING:'Mesin berhenti, AI tidak menilai.',WARMUP:'Menunggu 10 sampel berjalan pertama.'};
const glyphs={
 ok:'M24 4a20 20 0 1 1 0 40a20 20 0 1 1 0-40zM21 31l-8-8 3-3 5 5 11-11 3 3z',
 warn:'M24 3L46 42H2zM22 16h4v13h-4zM22 32h4v4h-4z',
 stop:'M16 4h16l12 12v16L32 44H16L4 32V16zM13 22h22v4H13z',
 pause:'M24 4a20 20 0 1 1 0 40a20 20 0 1 1 0-40zM17 15h5v18h-5zM26 15h5v18h-5z',
 ai:'M24 2l5.5 16.5L46 24l-16.5 5.5L24 46l-5.5-16.5L2 24l16.5-5.5z',
 lost:'M24 4a20 20 0 1 1 0 40a20 20 0 1 1 0-40zM12 15l3-3 21 21-3 3z',
 wait:'M24 4a20 20 0 1 1 0 40a20 20 0 1 1 0-40zM13 22h5v5h-5zM21.5 22h5v5h-5zM30 22h5v5h-5z'
};
const lampColor={'s-ok':'#3DD68C','s-low':'#F7B84B','s-count':'#FF5C5C','s-mid':'#FF8A3D','s-ai':'#A98BFF','s-stop':'#FF5C5C','s-manual':'#7D8899','s-lost':'#7D8899','s-wait':'#7D8899'};
const scenarioText={
 overheating:['Panas berlebih','Suhu naik bertahap. Perhatikan alarm batas, skor AI, dan hitung mundur kalau sampai tingkat tinggi.'],
 unbalance:['Getaran berlebih','Poros tidak seimbang sehingga getaran naik. Bodi motor ikut bergetar.'],
 overspeed:['Putaran berlebih','Motor berputar di atas batas aman. Lihat kopling dan nilai RPM.']
};
const demo=!!(window.MGDemo&&window.MGDemo.active());
const api={
 status:()=>demo?Promise.resolve(window.MGDemo.status()):jsonFetch('api/status'),
 control:body=>demo?Promise.resolve().then(()=>window.MGDemo.control(body)):jsonFetch('api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
};
let state=null,lastArrival=0,busy=0,initialized=false,angle=0,lastFrame=0,queue=Promise.resolve(),disconnected=false,shownAge=-1,statusClass='s-wait',prevAi=null,openSheet=null,lastTrigger=null;
const seenEvents=new Set();let eventsPrimed=false;
const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
const fmt=(n,d=1)=>n===null||n===undefined||!Number.isFinite(Number(n))?'—':Number(n).toLocaleString('id-ID',{minimumFractionDigits:d,maximumFractionDigits:d});
const actual=(s,k)=>k==='temperature'?s.temperature:k==='vibration'?s.rms:s.rpm;
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const clock=t=>`${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;

function showError(message){$('error').textContent=message;$('error').hidden=!message;}
function chip(id,text,kind){const el=$(id);el.querySelector('.chip-t').textContent=text;el.title=text;el.className='chip'+(kind?' '+kind:'');}
function friendly(error){
 if(error&&error.name==='AbortError')return 'Server tidak merespons dalam 5 detik. Pastikan jendela CMD MachineGuard masih terbuka.';
 if(error instanceof TypeError)return 'Tidak dapat menghubungi server lokal. Pastikan jendela CMD MachineGuard masih terbuka, lalu coba lagi.';
 return (error&&error.message)||'Server tidak merespons.';
}
async function jsonFetch(url,options={}){
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);
 try{const response=await fetch(url,{...options,signal:controller.signal});const data=await response.json();if(!response.ok)throw new Error(data.error||'Permintaan gagal');return data;}finally{clearTimeout(timeout);}
}
function command(body){
 queue=queue.catch(()=>{}).then(async()=>{busy++;try{render(await api.control(body));showError('');return true;}catch(error){showError(friendly(error));return false;}finally{busy--;}});return queue;
}
async function poll(){
 if(!busy){try{render(await api.status());}catch(error){chip('chip-server','Server terputus','bad');}}
 setTimeout(poll,1000);
}

function toast(title,text,hue){
 const box=$('toasts'),el=document.createElement('div');el.className='toast';el.style.setProperty('--hue',hue);
 const b=document.createElement('b');b.textContent=title;const span=document.createElement('span');span.textContent=text||'';
 el.append(b,span);box.prepend(el);while(box.children.length>1)box.lastChild.remove();setTimeout(()=>el.remove(),6500);
}
function eventToast(e){
 const d=e.detail||'';
 if(e.kind==='COUNTDOWN')toast('Gangguan tinggi','Mesin akan berhenti otomatis jika tidak dihentikan operator.','var(--alarm)');
 else if(e.kind==='STOP')d.startsWith('Stop otomatis')?toast('Mesin dimatikan otomatis',d,'var(--alarm)'):toast('Mesin dihentikan',d,'var(--mute)');
 else if(e.kind==='CANCEL')toast('Hitung mundur dibatalkan','Kondisi turun dari tingkat tinggi.','var(--ok)');
 else if(e.kind==='ALARM'&&/RENDAH|SEDANG/.test(d))toast('Alarm batas: '+d.split(': ').pop().toLowerCase(),'Ada sensor di luar batas aman.','var(--mid)');
 else if(e.kind==='ALARM'&&/NORMAL/.test(d))toast('Alarm batas kembali normal','Semua sensor di dalam batas aman.','var(--ok)');
 else if(e.kind==='INJECT')toast('Skenario dimulai',d,'var(--vib)');
 else if(e.kind==='START')toast('Mesin dinyalakan','Buffer ESP32 diisi ulang untuk sesi baru.','var(--ok)');
 else if(e.kind==='RESET')toast('Simulasi direset','Kontrol kembali ke awal.','var(--mute)');
}
function setDisconnected(on){
 if(on===disconnected)return;disconnected=on;shownAge=-1;
 document.body.classList.toggle('disconnected',on);$('disconnected').hidden=!on;
 if(on){chip('chip-server','Server terputus','bad');paintStatus({cls:'s-lost',glyph:'lost',title:'Status mesin tidak diketahui',detail:'Halaman tidak menerima data dari server lokal.'});}
 else if(state)render(state);
}

// Sheets: one panel at a time, opened from the dock (or from a readout). Esc or the close button closes.
function setSheet(name,force){
 const next=force?name:(openSheet===name?null:name);
 document.querySelectorAll('.sheet').forEach(el=>{el.hidden=el.id!=='sheet-'+next;});
 document.querySelectorAll('[data-sheet]').forEach(b=>b.setAttribute('aria-expanded',String(b.dataset.sheet===next)));
 const wasOpen=openSheet;openSheet=next;
 if(next){const close=$('sheet-'+next).querySelector('.close');if(close)close.focus({preventScroll:true});requestAnimationFrame(drawCharts);}
 else if(wasOpen&&lastTrigger)lastTrigger.focus({preventScroll:true});
}
document.querySelectorAll('[data-sheet]').forEach(b=>b.addEventListener('click',()=>{lastTrigger=b;setSheet(b.dataset.sheet);}));
document.querySelectorAll('[data-open]').forEach(b=>b.addEventListener('click',()=>{lastTrigger=document.querySelector(`[data-sheet="${b.dataset.open}"]`);setSheet(b.dataset.open,true);}));
document.querySelectorAll('.sheet .close').forEach(b=>b.addEventListener('click',()=>setSheet(null,true)));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&openSheet)setSheet(null,true);});

// Controls: sliders take over (switch to Manual) on release; Auto hands control back to the simulator.
for(const k of channels){
 const card=document.querySelector(`[data-sensor="${k}"]`),slider=$(k+'-slider');
 slider.addEventListener('input',()=>{slider.dataset.dirty='true';$(k+'-reading').textContent=fmt(Number(slider.value),decimals[k]);});
 slider.addEventListener('change',async()=>{const ok=await command({sensors:{[k]:{mode:'manual',value:Number(slider.value)}}});if(ok)slider.dataset.dirty='false';});
 card.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',async()=>{
  if(!state)return;const mode=button.dataset.mode;
  const value=state.running?actual(state,k):state.controls.sensors[k].value;
  slider.dataset.dirty='false';await command({sensors:{[k]:{mode,value:Number(Number(value).toFixed(decimals[k]))}}});
 }));
}
$('start').onclick=()=>command({action:'start'});
$('stop').onclick=()=>command({action:'stop'});
$('status-stop').onclick=()=>command({action:'stop'});
$('reset').onclick=async()=>{if(await command({action:'reset'})){initialized=false;channels.forEach(k=>$(k+'-slider').dataset.dirty='false');settingInputs().forEach(el=>el.dataset.dirty='false');render(state);}};
$('random-faults').onchange=event=>command({random_faults:event.target.checked});
document.querySelectorAll('.mission-btn[data-kind]').forEach(b=>b.addEventListener('click',()=>command({preset:{kind:b.dataset.kind,severity:Number($('preset-level').value)}})));
$('normal').onclick=()=>command({preset:{kind:'normal',severity:1},random_faults:false});
$('save-delay').onclick=async()=>{const el=$('delay-input');if(el.value!==''&&el.reportValidity()&&await command({shutdown_delay_s:Number(el.value)}))el.dataset.dirty='false';};
for(const k of channels){
 const row=document.createElement('div');row.className='limit-row';
 const label=document.createElement('span');label.textContent=names[k];row.append(label);
 for(const edge of ['min','max']){const input=document.createElement('input');input.type='number';input.step=k==='vibration'?'0.01':'1';input.id=k+'-'+edge;input.required=true;input.setAttribute('aria-label',`${names[k]} ${edge==='min'?'minimum':'maksimum'}`);row.append(input);}
 $('limit-fields').append(row);
}
const settingInputs=()=>[$('delay-input'),...channels.flatMap(k=>[$(k+'-min'),$(k+'-max')])];
settingInputs().forEach(el=>el.addEventListener('input',()=>{el.dataset.dirty='true';}));
$('limits-form').onsubmit=async event=>{event.preventDefault();const limits={};for(const k of channels)limits[k]={min:Number($(k+'-min').value),max:Number($(k+'-max').value)};if(await command({limits}))channels.forEach(k=>{$(k+'-min').dataset.dirty='false';$(k+'-max').dataset.dirty='false';});};
if(demo){$('csv-link').addEventListener('click',e=>{e.preventDefault();const blob=new Blob([window.MGDemo.csv()],{type:'text/csv'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='machineguard-demo.csv';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);});}

// Gauge geometry: 180° arc from left (value 0) to right (full scale).
const arcPoint=f=>{const a=Math.PI*(1-f);return [70+56*Math.cos(a),74-56*Math.sin(a)];};
function bandPath(f0,f1){f0=clamp(f0,0,1);f1=clamp(f1,0,1);if(f1<=f0)return '';const [x0,y0]=arcPoint(f0),[x1,y1]=arcPoint(f1);return `M${x0.toFixed(1)} ${y0.toFixed(1)}A56 56 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;}

function outOfLimit(s){return channels.filter(k=>s.alarm.sensors[k]?.level>0).map(k=>`${names[k]} ${fmt(actual(s,k),decimals[k])} ${units[k]}`);}
function heldManual(s){return channels.filter(k=>{const c=s.controls.sensors[k],l=s.controls.limits[k];return c.mode==='manual'&&(c.value<l.min||c.value>l.max);}).map(k=>`${names[k]} ${fmt(s.controls.sensors[k].value,decimals[k])} ${units[k]}`);}
function statusView(s){
 const bad=outOfLimit(s),held=heldManual(s);
 const heldText=held.length?` Nilai manual masih di luar batas (${held.join(', ')}); kembalikan ke zona aman sebelum Start.`:'';
 if(s.running&&s.countdown_s!==null)return{cls:'s-count',glyph:'warn',title:'Gangguan tinggi',detail:`${bad.join(', ')||'Sensor'} di luar batas. Tekan Stop sekarang atau pulihkan kondisi sebelum waktu habis.`,count:Math.ceil(s.countdown_s),left:s.countdown_s,stop:true};
 if(!s.running&&(s.stop_reason||'').startsWith('Stop otomatis'))return{cls:'s-stop',glyph:'stop',title:'Mesin dimatikan otomatis',detail:`${s.stop_reason} Periksa penyebabnya, lalu tekan Start.${heldText}`};
 if(!s.running)return{cls:'s-manual',glyph:'pause',title:'Mesin berhenti',detail:`${s.stop_reason||'Motor tidak berputar.'} Tekan Start untuk menyalakan.${heldText}`};
 if(s.alarm.level>=2)return{cls:'s-mid',glyph:'warn',title:'Gangguan sedang',detail:`Di luar batas: ${bad.join(', ')}.`};
 if(s.alarm.level===1)return{cls:'s-low',glyph:'warn',title:'Gangguan ringan',detail:`Di luar batas: ${bad.join(', ')}.`};
 if(s.ai==='ANOMALY')return{cls:'s-ai',glyph:'ai',title:'AI melihat pola tidak biasa',detail:`Semua sensor masih di dalam batas alarm, tetapi datanya jauh dari pola normal yang dipelajari (skor ${fmt(s.anomaly_score,2)}).`};
 if(s.ai==='NORMAL')return{cls:'s-ok',glyph:'ok',title:'Mesin berjalan normal',detail:'Sensor dalam batas aman dan pola datanya sesuai pola normal yang dipelajari AI.'};
 return{cls:'s-ok',glyph:'ok',title:'Mesin berjalan, alarm batas normal',detail:aiWaiting[s.ai]||''};
}
function paintStatus(v){
 statusClass=v.cls;document.body.dataset.state=v.cls;$('status').className='status '+v.cls;$('status-glyph').setAttribute('d',glyphs[v.glyph]);
 $('status-title').textContent=v.title;$('status-detail').textContent=v.detail||'';
 const counting=v.count!==undefined;$('status-count').hidden=!counting;
 if(counting){$('count-num').textContent=v.count;const total=(state&&state.controls.shutdown_delay_s)||10;$('count-ring').setAttribute('stroke-dasharray',`${(clamp(v.left/total,0,1)*100).toFixed(1)} 100`);}
 $('status-stop').hidden=!v.stop;
}

function render(s){
 if(state&&s.server_time<state.server_time)return;
 state=s;lastArrival=performance.now();
 $('demo-banner').hidden=!demo;
 if(demo){chip('chip-server','Demo di browser','warn');chip('chip-cloud','MQTT diemulasikan','warn');chip('chip-edge',s.edge==='RECEIVED'?'ESP32 diemulasikan':'Menunggu emulasi','warn');}
 else{
  chip('chip-server','Server lokal','ok');
  if(s.mode==='offline')chip('chip-cloud','Pratinjau offline','warn');
  else if(s.broker==='CONNECTED'&&s.subscription==='READY')chip('chip-cloud','MQTT HiveMQ','ok');
  else if(['CONNECTING','RETRYING','CONNECTED'].includes(s.broker))chip('chip-cloud','MQTT menghubungkan','warn');
  else chip('chip-cloud','MQTT '+String(s.broker).toLowerCase(),'bad');
  if(s.edge==='RECEIVED')chip('chip-edge',s.mode==='offline'?'MA10 emulasi':'ESP32 Wokwi',s.mode==='offline'?'warn':'ok');
  else if(s.edge==='STALE')chip('chip-edge','ESP32 basi '+fmt(s.feature_age_s,0)+' dtk','bad');
  else chip('chip-edge','Menunggu ESP32','warn');
 }
 $('offline-banner').hidden=demo||s.mode!=='offline';
 if(s.error)showError(s.error);
 if(!disconnected)paintStatus(statusView(s));
 $('start').disabled=s.running;$('stop').disabled=!s.running;
 $('run-line').textContent=`Sesi ${s.run_id}`;
 $('random-faults').checked=s.controls.random_faults;

 for(const k of channels){
  const sensor=s.controls.sensors[k],card=document.querySelector(`[data-sensor="${k}"]`),value=actual(s,k),limit=s.controls.limits[k],alarm=s.alarm.sensors[k];
  const slider=$(k+'-slider');
  if(slider.dataset.dirty!=='true')$(k+'-reading').textContent=fmt(value,decimals[k]);
  let label='—',cls='';
  if(alarm&&alarm.direction==='starting'){label='Mulai';cls='start';}
  else if(alarm){label=alarm.status.charAt(0)+alarm.status.slice(1).toLowerCase();cls='l'+alarm.level;}
  const pill=$(k+'-level');pill.textContent=label;pill.className='pill'+(cls?' '+cls:'');
  $('hero-'+k).textContent=fmt(value,decimals[k]);$('hero-'+k+'-level').textContent=label;$('ro-'+k).className='readout'+(cls?' '+cls:'');
  $(k+'-band').setAttribute('d',bandPath(limit.min/full[k],limit.max/full[k]));
  $(k+'-needle').style.transform=`rotate(${(clamp(value/full[k],0,1)*180-90).toFixed(1)}deg)`;
  $(k+'-range').textContent=`Aman ${fmt(limit.min,k==='vibration'?2:0)}–${fmt(limit.max,k==='vibration'?2:0)} ${units[k]}`;
  card.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===sensor.mode)));
  if(slider.dataset.dirty!=='true'&&document.activeElement!==slider)slider.value=String(sensor.mode==='manual'?sensor.value:value);
 }

 const settings={'delay-input':s.controls.shutdown_delay_s};
 for(const k of channels){settings[k+'-min']=s.controls.limits[k].min;settings[k+'-max']=s.controls.limits[k].max;}
 for(const [id,value] of Object.entries(settings)){const el=$(id);if(!initialized||(el.dataset.dirty!=='true'&&document.activeElement!==el))el.value=value;}
 initialized=true;

 // Scenario: what the simulator is doing (ground truth), separate from what the AI concludes.
 const lvl=['','rendah','sedang','tinggi'][s.injection_severity]||'';
 const manual=channels.filter(k=>s.controls.sensors[k].mode==='manual');
 if(s.scenario&&s.scenario!=='normal'){$('mission-title').textContent=`${scenarioText[s.scenario][0]}, ${lvl}`;$('mission-text').textContent=scenarioText[s.scenario][1];}
 else if(manual.length){$('mission-title').textContent='Kendali manual';$('mission-text').textContent=`${manual.map(k=>names[k]).join(', ')} diatur operator. Tekan Auto di panel Sensor untuk mengembalikan ke simulator.`;}
 else{$('mission-title').textContent='Operasi normal';$('mission-text').textContent='Pilih gangguan untuk menguji alarm, AI, dan stop otomatis.';}
 $('injection-label').textContent=`Gangguan yang disuntikkan simulator: ${s.scenario&&s.scenario!=='normal'?scenarioText[s.scenario][0].toLowerCase()+', '+lvl:'tidak ada'}`;

 // AI ring and metrics
 const score=s.anomaly_score;
 $('ring-fill').setAttribute('stroke-dasharray',`${score===null?0:(clamp(score/RING_FULL,0,1)*100).toFixed(1)} 100`);$('ring-fill').style.opacity=score===null?'0':'1';
 document.querySelector('.ai').style.setProperty('--ring',s.ai==='ANOMALY'?'var(--ai)':s.ai==='NORMAL'?'var(--ok)':'var(--idle)');
 $('score-value').textContent=fmt(score,2);
 $('ai-word').textContent=demo&&!window.MG_MODEL?'Memuat model':(aiLabels[s.ai]||s.ai);
 $('ai-sub').textContent=score===null?(aiWaiting[s.ai]||''):s.ai==='ANOMALY'?`Sampel ${s.ai_sequence} jauh dari pola normal yang dipelajari model.`:`Sampel ${s.ai_sequence} sesuai pola normal. Skor bukan probabilitas kerusakan.`;
 $('edge-label').textContent=demo?'MA10 (emulasi)':s.mode==='offline'?'MA10 dari laptop':'MA10 dari ESP32';
 $('ma10-value').textContent=fmt(s.temp_ma10,2);
 $('edge-detail').textContent=s.edge==='RECEIVED'?`${fmt(s.feature_age_s,1)} dtk lalu`:s.edge==='STALE'?`basi ${fmt(s.feature_age_s,0)} dtk`:'tunggu 10 suhu';
 $('rms-value').textContent=fmt(s.rms,3);$('rms-note').textContent=demo?'diolah browser':'diolah laptop';
 $('peak-value').textContent=fmt(s.peak_hz,1);
 $('sample-label').textContent=s.sequence>=0?String(s.sequence):'—';
 $('sample-note').textContent=s.ai_sequence!==null&&s.ai_sequence!==undefined?`AI: sampel ${s.ai_sequence}`:'sesi ini';
 if(prevAi!==null&&prevAi!=='ANOMALY'&&s.ai==='ANOMALY')toast('AI mendeteksi anomali',`Skor ${fmt(score,2)} melewati ambang 1,0 pada sampel ${s.ai_sequence}.`,'var(--ai)');
 prevAi=s.ai;

 const fresh=[];
 for(const e of s.events){const key=`${e.time}|${e.kind}|${e.detail}`;if(!seenEvents.has(key)){seenEvents.add(key);fresh.push(e);}}
 if(eventsPrimed&&fresh.length){const rank=e=>e.kind==='STOP'?6:e.kind==='COUNTDOWN'?5:e.kind==='ALARM'&&/RENDAH|SEDANG/.test(e.detail)?4:e.kind==='INJECT'?3:e.kind==='START'||e.kind==='RESET'?2:1;eventToast(fresh.reduce((a,b)=>rank(b)>rank(a)?b:a));}
 eventsPrimed=true;
 $('events').replaceChildren(...s.events.slice(0,30).map(e=>{const li=document.createElement('li');li.className='k-'+String(e.kind).toLowerCase();const text=document.createElement('span');text.textContent=e.detail;const time=document.createElement('time');time.textContent=clock(e.time);li.append(text,time);return li;}));
 $('diagnostic-text').textContent=`Mode ${demo?'demo browser':s.mode} | ${s.run_id} | langganan ${s.subscription} | diakui broker ${s.tx_acked} | MA10 cocok ${s.matched} | umur sumber ${fmt(s.source_age_s)} s | ${s.last_rejection||'Tidak ada pesan ditolak'}${s.error?' | ERROR: '+s.error:''}`;
 const a=s.ai_inputs;$('ai-inputs').textContent=a?`Input AI sampel ${s.ai_sequence}: MA10 ${fmt(a.temp_ma10_c,3)} °C, RMS ${fmt(a.rms,4)} g, RPM ${fmt(a.rpm,1)}.`:`Input AI belum tersedia: ${(aiWaiting[s.ai]||'menunggu data.').toLowerCase()}`;
 drawCharts();
}

function plot(id,rows,series,unit,baseMin,baseMax,xKey='elapsed',guides=[]){
 const canvas=$(id),rect=canvas.getBoundingClientRect();if(rect.width<10)return;
 const dpr=Math.min(window.devicePixelRatio||1,2),w=rect.width,h=rect.height;
 canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
 const left=44,right=10,top=24,bottom=28,pw=w-left-right,ph=h-top-bottom;
 ctx.font='12px "Plus Jakarta Sans", Segoe UI, sans-serif';ctx.fillStyle='#97A2B4';ctx.fillText(unit,4,12);
 if(rows.length<2){ctx.fillText('Menunggu sampel…',left,h/2);return;}
 const values=rows.flatMap(r=>series.map(s=>r[s.key])).filter(v=>v!==null&&v!==undefined&&Number.isFinite(v));
 let lo=Math.min(baseMin,...values),hi=Math.max(baseMax,...values);if(hi===lo)hi=lo+1;
 const pad=(hi-lo)*.08;hi+=pad;if(lo!==0)lo-=pad;
 const first=rows[0][xKey],last=rows[rows.length-1][xKey],span=Math.max(.01,last-first);
 const px=v=>left+(v-first)/span*pw,py=v=>top+(hi-v)/(hi-lo)*ph;
 for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3,y=py(v);ctx.strokeStyle='#262F3D';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();ctx.fillStyle='#97A2B4';ctx.textAlign='right';ctx.fillText(fmt(v,hi-lo<3?2:0),left-7,y+4);}
 for(let i=0;i<3;i++){const v=first+span*i/2;ctx.textAlign=['left','center','right'][i];ctx.fillText(xKey==='t'?fmt(v,1)+' s':clock(v),i===0?left:i===2?w-right:px(v),h-8);}
 ctx.save();ctx.setLineDash([5,5]);ctx.strokeStyle='#F7B84B';ctx.lineWidth=1.5;
 for(const g of guides){if(!Number.isFinite(g)||g<lo||g>hi)continue;const y=py(g);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();}
 ctx.restore();
 for(const sr of series){ctx.beginPath();ctx.strokeStyle=sr.color;ctx.lineWidth=sr.width||2.2;let open=false;for(const row of rows){const v=row[sr.key];if(v===null||v===undefined||!Number.isFinite(v)){open=false;continue;}if(!open){ctx.moveTo(px(row[xKey]),py(v));open=true;}else ctx.lineTo(px(row[xKey]),py(v));}ctx.stroke();}
}
function drawCharts(){
 if(!state||openSheet!=='grafik')return;const rows=state.history.slice(-90),L=state.controls.limits;
 plot('temp-chart',rows,[{key:'temp_c',color:'#FF8A5B'},{key:'ma10',color:'#62C8F0',width:3}],'°C',L.temperature.min,L.temperature.max,'elapsed',[L.temperature.min,L.temperature.max]);
 plot('wave-chart',state.waveform.map((v,i)=>({t:i*4/256,v})),[{key:'v',color:'#B49CFF'}],'g',-.1,.1,'t');
}
window.addEventListener('resize',drawCharts);

function tickClock(){$('clock').textContent=new Date().toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit'});}
tickClock();setInterval(tickClock,5000);

function animate(now){
 const dt=lastFrame?Math.min((now-lastFrame)/1000,.1):0;lastFrame=now;
 if(state){
  const alive=now-lastArrival<3500;
  setDisconnected(!alive);
  if(!alive){const age=Math.floor((now-lastArrival)/1000);if(age!==shownAge){shownAge=age;$('disconnected-age').textContent=`Data terakhir ${age} detik lalu.`;}}
  const moving=state.running&&alive&&!reduced.matches;
  if(moving)angle=(angle+state.rpm*.12*dt)%360;
  $('coupling').setAttribute('transform',`rotate(${angle})`);
  const amp=moving?Math.min(state.rms*10,8):0;
  $('motor-body').setAttribute('transform',`translate(${Math.sin(now/30)*amp} ${Math.sin(now/37)*amp*.4})`);
  const heat=clamp((state.temperature-35)/30,0,1);
  $('heat-glow').setAttribute('opacity',String(heat*.9));
  $('heat-waves').setAttribute('opacity',String(heat>.12?heat:0));
  const vmax=state.controls.limits.vibration.max;
  $('vib-marks').setAttribute('opacity',String(moving?clamp((state.rms-vmax*.8)/vmax,0,1):0));
  const lamp=lampColor[statusClass]||'#7D8899',blink=statusClass==='s-count'&&Math.floor(now/400)%2===0;
  $('status-lamp').setAttribute('fill',lamp);$('status-lamp-glow').setAttribute('fill',lamp);
  $('status-lamp-glow').setAttribute('opacity',blink?'.2':'.9');
 }
 requestAnimationFrame(animate);
}
poll();requestAnimationFrame(animate);
