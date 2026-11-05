"""MachineGuard laptop edge: simulator, vibration processing, AI and the local dashboard.

Default mode talks to the ESP32 over MQTT. The offline preview (--offline) computes MA10 on the
laptop instead and is clearly labelled in the dashboard."""
import argparse
import csv
import io
import json
import pickle
import sys
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

from config import BROKER, PORT_TLS, TOPIC_ROOT
from engine import Engine
from messages import encode_message,decode_message

ROOT=Path(__file__).resolve().parent
SENSOR=TOPIC_ROOT+'/sensor/temperature'
FEATURE=TOPIC_ROOT+'/feature/temperature'

def load_model():
    import sklearn
    path=ROOT/'model.pkl'
    if path.exists():
        with path.open('rb') as f: bundle=pickle.load(f)
        if bundle.get('sklearn_version')==sklearn.__version__ and bundle.get('version')=='machineguard-detector-1':
            return bundle
    print('Melatih model AI dari data normal (hanya saat pertama kali). Tunggu sebentar...',flush=True)
    from train_model import train
    train()
    with path.open('rb') as f: return pickle.load(f)

def mqtt_client(live):
    import paho.mqtt.client as mqtt
    c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='machineguard-laptop-'+uuid.uuid4().hex[:10])
    c.tls_set()
    c.reconnect_delay_set(min_delay=1,max_delay=15)
    def connected(client,userdata,flags,reason,props):
        if reason.is_failure: live.connection('ERROR'); return
        live.connection('CONNECTED')
        rc,_=client.subscribe(FEATURE,qos=1)
        if rc!=mqtt.MQTT_ERR_SUCCESS:
            with live.lock: live.subscription='ERROR'
    def subscribed(client,userdata,mid,reasons,props):
        with live.lock: live.subscription='READY' if reasons and all(not r.is_failure for r in reasons) else 'REFUSED'
    def incoming(client,userdata,packet):
        if packet.topic!=FEATURE or packet.retain: return
        try: live.accept(decode_message(packet.payload))
        except (ValueError,TypeError,OverflowError,KeyError) as exc:
            with live.lock: live.last_rejection=str(exc)
    def published(client,userdata,mid,reason,props):
        if not reason.is_failure:
            with live.lock: live.tx_acked+=1
    c.on_connect=connected
    c.on_subscribe=subscribed
    c.on_message=incoming
    c.on_publish=published
    c.on_connect_fail=lambda client,u:live.connection('RETRYING')
    c.on_disconnect=lambda client,u,flags,reason,props:live.connection('DISCONNECTED')
    return c

def produce(live,client,stop):
    while not stop.is_set():
        try:
            msg=live.tick()
            if msg:
                if live.offline: live.emulate(msg)
                elif client.is_connected(): client.publish(SENSOR,encode_message(msg),qos=1,retain=False)
        except Exception as exc:
            with live.lock:
                live.error=f'{type(exc).__name__}: {exc}'
                live._stop('Stop karena kesalahan pemrosesan. Lihat diagnostik.')
            print('[ERROR]',live.error,flush=True)
        stop.wait(.1)

class Http(BaseHTTPRequestHandler):
    def reply(self,status,data,kind='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError): pass

    def json(self,status,obj): self.reply(status,json.dumps(obj,allow_nan=False).encode())

    def do_GET(self):
        path=urlsplit(self.path).path
        static={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/demo-engine.js':'demo-engine.js','/model.js':'model.js'}
        fonts={f'/fonts/plus-jakarta-sans-latin-{w}-normal.woff2' for w in (300,400,500,600,700)}
        if path in fonts:
            self.reply(200,(ROOT/'dashboard'/path.lstrip('/')).read_bytes(),'font/woff2')
        elif path in static:
            name=static[path]
            kind={'html':'text/html','js':'application/javascript','css':'text/css'}[name.split('.')[-1]]
            self.reply(200,(ROOT/'dashboard'/name).read_bytes(),kind+'; charset=utf-8')
        elif path=='/api/status': self.json(200,self.server.live.snapshot())
        elif path=='/api/export.csv':
            rows=self.server.live.snapshot()['history']
            buffer=io.StringIO(newline='')
            cols=['run_id','seq','elapsed','temp_c','rms','rpm','peak_hz','ma10','score','ai','running']
            writer=csv.DictWriter(buffer,fieldnames=cols)
            writer.writeheader();writer.writerows(rows)
            self.reply(200,buffer.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8')
        else: self.json(404,{'error':'Halaman tidak ditemukan.'})

    def do_POST(self):
        if urlsplit(self.path).path!='/api/control': return self.json(404,{'error':'Endpoint tidak dikenal.'})
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
            return self.json(403,{'error':'Origin tidak cocok.'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.json(415,{'error':'Gunakan JSON.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096: return self.json(413,{'error':'Ukuran permintaan tidak valid.'})
            body=json.loads(self.rfile.read(length))
            result=self.server.live.control(body)
            self.json(200,result)
        except (ValueError,TypeError,UnicodeDecodeError) as exc: self.json(400,{'error':str(exc)})

    def log_message(self,*args): pass

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true',help='Pratinjau lokal; MA10 diemulasi di laptop.')
    parser.add_argument('--open',action='store_true')
    parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--seed',type=int,default=2026)
    args=parser.parse_args()
    if not 1<=args.port<=65535: parser.error('Port tidak valid.')
    try: server=ThreadingHTTPServer((args.host,args.port),Http)
    except OSError as exc:
        raise SystemExit(f'Port {args.port} sedang dipakai. Tekan Ctrl+C di server lama dahulu. ({exc})')
    with server:
        live=Engine(load_model(),offline=args.offline,seed=args.seed)
        server.live=live
        stop=Event()
        client=None if args.offline else mqtt_client(live)
        if client:
            client.connect_async(BROKER,PORT_TLS,keepalive=45)
            client.loop_start()
        worker=Thread(target=produce,args=(live,client,stop),daemon=True)
        worker.start()
        print('PRATINJAU OFFLINE: MA10 dihitung laptop, ESP32 tidak dipakai.' if args.offline else
              f'MQTT: {BROKER}:{PORT_TLS}; menunggu MA10 ESP32 Wokwi.',flush=True)
        url=f'http://127.0.0.1:{args.port}/'
        print('DASHBOARD:',url,flush=True)
        if args.open:webbrowser.open(url)
        try: server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt: pass
        finally:
            stop.set();worker.join(timeout=3)
            if client:client.disconnect();client.loop_stop()

if __name__=='__main__':main()
