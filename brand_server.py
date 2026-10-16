#!/usr/bin/env python3
"""Local multi-brand control room; bind to loopback, not the public Internet."""
import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from brand_platform import Engine,ROOT

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 @property
 def engine(self):return self.server.engine
 def respond(self,status,data,kind='application/json'):
  raw=data if isinstance(data,bytes) else data.encode() if isinstance(data,str) else json.dumps(data).encode()
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'");self.end_headers();self.wfile.write(raw)
 def valid_host(self):return self.headers.get('Host') in ('127.0.0.1:'+str(self.server.server_address[1]),'localhost:'+str(self.server.server_address[1]))
 def do_GET(self):
  if not self.valid_host():return self.respond(403,{'error':'Local access only'})
  path=urlparse(self.path).path
  try:
   if path=='/api/state':return self.respond(200,{'csrf':self.server.csrf,'brands':self.engine.store.brands(),'worker_running':self.server.worker_running})
   parts=path.strip('/').split('/')
   if len(parts)==3 and parts[:2]==['api','brands']:
    brand_id=parts[2];return self.respond(200,{'brand':self.engine.store.brand(brand_id),'creatives':self.engine.store.list_items('creatives',brand_id),'campaigns':self.engine.store.list_items('campaigns',brand_id),'events':self.engine.store.events(brand_id),'token_present':bool(__import__('os').environ.get(self.engine.store.brand(brand_id)['token_env']))})
   if len(parts)==5 and parts[:2]==['api','brands'] and parts[3]=='assets':
    item=self.engine.store.item('creatives',parts[2],parts[4]);file=self.engine.store.directory/item['image'];return self.respond(200,file.read_bytes(),'image/png' if file.suffix=='.png' else 'image/jpeg')
   if path=='/research':
    from marketing_agent import render,load
    return self.respond(200,render(load(ROOT/'research/latest.json',{})),'text/html; charset=utf-8')
   files={'/':('control.html','text/html; charset=utf-8'),'/control.js':('control.js','text/javascript'),'/control.css':('control.css','text/css')}
   if path in files:
    name,mime=files[path];return self.respond(200,(ROOT/'public'/name).read_bytes(),mime)
   return self.respond(404,{'error':'Not found'})
  except (ValueError,FileNotFoundError) as error:return self.respond(404,{'error':str(error)})
 def do_POST(self):
  try:
   if not self.valid_host():return self.respond(403,{'error':'Local access only'})
   origin=self.headers.get('Origin')
   if origin and origin!='http://'+self.headers.get('Host',''):return self.respond(403,{'error':'Origin rejected'})
   if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf):return self.respond(403,{'error':'Refresh the page before continuing'})
   size=int(self.headers.get('Content-Length','0'))
   if size<0 or size>14_000_000:raise ValueError('Request exceeds 14 MB')
   data=json.loads(self.rfile.read(size))
   parts=self.path.strip('/').split('/')
   if parts==['api','brands']:
    with self.engine.lock:return self.respond(200,self.engine.store.add_brand(data.get('name','')))
   if len(parts)<4 or parts[:2]!=['api','brands']:return self.respond(404,{'error':'Not found'})
   brand_id=parts[2];action=parts[3];self.engine.store.brand(brand_id)
   with self.engine.lock:
    if len(parts)==4:
     if action=='settings':
      data['id']=brand_id;return self.respond(200,self.engine.store.save_brand(data))
     if action=='verify':return self.respond(200,self.engine.verify(self.engine.store.brand(brand_id)))
     if action=='creatives':return self.respond(200,self.engine.store.creative(brand_id,data))
     if action=='plan':return self.respond(200,self.engine.plan(brand_id,data))
     if action=='command':return self.respond(200,self.engine.command(brand_id,data.get('text','')))
    if len(parts)==6 and action=='creatives' and parts[5]=='approve':
     if not isinstance(data.get('approved'),bool):raise ValueError('Approval must be true or false')
     return self.respond(200,self.engine.store.approve(brand_id,parts[4],data['approved']))
    if len(parts)==6 and action=='campaigns':
     plan_id,operation=parts[4:]
     if operation=='create':result=self.engine.create(brand_id,plan_id,data.get('review_hash'))
     elif operation=='launch':result=self.engine.launch(brand_id,plan_id,data.get('confirmation'))
     elif operation=='pause':result=self.engine.pause(brand_id,plan_id)
     elif operation=='report':result=self.engine.report(brand_id,plan_id)
     else:return self.respond(404,{'error':'Not found'})
     return self.respond(200,result)
   return self.respond(404,{'error':'Not found'})
  except (ValueError,KeyError,TypeError,FileNotFoundError) as error:return self.respond(400,{'error':str(error)})
  except Exception:return self.respond(500,{'error':'Operation did not complete. Inspect the campaign history and Ads Manager before retrying a write.'})

def monitor(engine,stop):
 while not stop.wait(60):engine.monitor_once()

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=3001);parser.add_argument('--monitor',action='store_true',help='Poll delivery and spend every 60 seconds; pause at the saved total cap. Never launch automatically.');args=parser.parse_args()
 server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.engine=Engine();server.csrf=secrets.token_hex(32);server.worker_running=args.monitor
 stop=threading.Event()
 if args.monitor:threading.Thread(target=monitor,args=(server.engine,stop),daemon=True).start()
 print('Brand control room listening on port '+str(server.server_address[1]),flush=True)
 try:server.serve_forever()
 finally:stop.set();server.server_close()
if __name__=='__main__':main()
