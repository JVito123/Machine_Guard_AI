"""Optional integration check: real HTTP + Paho/TLS against a protocol/ESP32 test double.
Requires openssl on PATH. This does not prove public HiveMQ/Wokwi availability.
Run: python tests/check_mqtt_tls.py
"""
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from urllib.error import URLError,HTTPError
from urllib.request import ProxyHandler,Request,build_opener

def main():
    root=Path(__file__).resolve().parents[1]
    failures=[];totals={'temperature':0,'ma10':0}
    with tempfile.TemporaryDirectory(prefix='mg-tls-') as temp:
        net=Path(temp)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-keyout',str(net/'key.pem'),'-out',str(net/'cert.pem'),'-subj','/CN=localhost',
                        '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],
                        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(net/'cert.pem',net/'key.pem')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
        mqtt_port=listener.getsockname()[1]
        with socket.socket() as pick:pick.bind(('127.0.0.1',0));http_port=pick.getsockname()[1]
        def exact(sock,n):
            data=bytearray()
            while len(data)<n:
                part=sock.recv(n-len(data))
                if not part:raise EOFError
                data.extend(part)
            return bytes(data)
        def send(sock,command,body):
            n=len(body);head=bytearray([command])
            while True:
                n,r=divmod(n,128);head.append(r|(128 if n else 0))
                if not n:break
            sock.sendall(head+body)
        def serve():
            try:
                plain,_=listener.accept();history=deque(maxlen=10);run=None;last=-1
                with ctx.wrap_socket(plain,server_side=True) as c:
                    while True:
                        cmd=exact(c,1)[0];remaining=0
                        for power in range(4):
                            d=exact(c,1)[0];remaining+=(d&127)*128**power
                            if d<128:break
                        body=exact(c,remaining);kind=cmd>>4
                        if kind==1:send(c,0x20,b'\0\0')
                        elif kind==8:send(c,0x90,body[:2]+b'\1')
                        elif kind==12:send(c,0xd0,b'')
                        elif kind==14:break
                        elif kind==3:
                            size=int.from_bytes(body[:2],'big');topic=body[2:2+size];i=2+size
                            assert topic.endswith(b'/sensor/temperature') and cmd&6==2
                            send(c,0x40,body[i:i+2]);raw=json.loads(body[i+2:]);totals['temperature']+=1
                            if raw['run_id']!=run:run=raw['run_id'];last=-1;history.clear()
                            if raw['seq']<=last:continue
                            if last>=0 and raw['seq']!=last+1:history.clear()
                            last=raw['seq'];history.append(raw['payload']['temp_c'])
                            if len(history)<10:continue
                            reply=dict(raw,device_id='esp32',msg_type='temperature_feature',
                                       payload={'temp_ma10_c':round(sum(history)/10,3),'window_n':10,
                                                'last_sample_sim_time_s':raw['sim_time_s']})
                            topic=topic.replace(b'/sensor/',b'/feature/')
                            send(c,0x30,len(topic).to_bytes(2,'big')+topic+json.dumps(reply).encode());totals['ma10']+=1
            except (EOFError,BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:failures.append(repr(exc))
        worker=threading.Thread(target=serve,daemon=True);worker.start()
        env=dict(os.environ,SSL_CERT_FILE=str(net/'cert.pem'),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
        code=f"import sys,app;app.BROKER='127.0.0.1';app.PORT_TLS={mqtt_port};sys.argv=['app.py','--port','{http_port}'];app.main()"
        with (net/'app.log').open('w') as log:
            app=subprocess.Popen([sys.executable,'-B','-u','-c',code],cwd=root,env=env,stdout=log,stderr=log)
            client=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{http_port}'
            def get(path='/api/status'):
                with client.open(url+path,timeout=3) as response:return response.read()
            def control(payload):
                with client.open(Request(url+'/api/control',json.dumps(payload).encode(),{'Content-Type':'application/json'}),timeout=3) as response:return json.load(response)
            def until(test,seconds=20):
                deadline=time.monotonic()+seconds;row=None
                while time.monotonic()<deadline:
                    row=json.loads(get())
                    if test(row):return row
                    time.sleep(.15)
                raise AssertionError(row)
            try:
                for _ in range(100):
                    if app.poll() is not None:raise RuntimeError((net/'app.log').read_text())
                    try:get('/');break
                    except URLError:time.sleep(.1)
                assert b'MachineGuard' in get('/') and b'.readouts' in get('/style.css') and get('/model.js').startswith(b'window.MG_MODEL=')
                control({'random_faults':False,'sensors':{k:{'mode':'manual','value':v} for k,v in
                               zip(('temperature','vibration','rpm'),(30,.18,1500))}})
                row=until(lambda s:s['ai']=='NORMAL' and s['matched']>0)
                assert row['mode']=='mqtt' and row['broker']=='CONNECTED'
                print('NORMAL via HTTP + TLS + MA10:',row['ai_sequence'],row['anomaly_score'],flush=True)
                control({'sensors':{'temperature':{'value':41}}})
                row=until(lambda s:s['temperature']==41)
                assert row['alarm']['status']=='RENDAH' and row['countdown_s'] is None
                row=until(lambda s:s['ai']=='ANOMALY' and s['temp_ma10']==41)
                print('41C alarm then aligned ML:',row['ai_sequence'],row['anomaly_score'],flush=True)
                before=row['controls']
                try:control({'sensors':{'rpm':{'value':True}},'random_faults':True});raise AssertionError('invalid accepted')
                except HTTPError as err:assert err.code==400
                assert json.loads(get())['controls']==before
                control({'shutdown_delay_s':3,'sensors':{'temperature':{'value':65}}})
                row=until(lambda s:s['countdown_s'] is not None)
                row=until(lambda s:not s['running'],seconds=6)
                assert row['rpm']==0 and row['rms']==0 and row['temperature']>40 and row['ai']=='NOT_RUNNING'
                assert 'otomatis' in row['stop_reason']
                print('AUTO STOP without browser:',row['stop_reason'],flush=True)
                assert b'run_id,seq' in get('/api/export.csv')
            finally:
                if app.poll() is None:
                    app.send_signal(signal.SIGINT)
                    try:app.wait(timeout=8)
                    except subprocess.TimeoutExpired:app.kill();app.wait()
                listener.close();worker.join(timeout=2)
        assert not failures,failures
        print('PASS local integration',totals,flush=True)

if __name__=='__main__':main()
