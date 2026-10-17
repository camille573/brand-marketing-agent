"""Multi-brand Meta campaign engine. Standard library only."""
import base64
import copy
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent
DEFAULT_BRANDS=[
 {'id':'stellar-mill','name':'Stellar Mill','offer':'Free 60-second Tax Checkup','destination':'https://checkup.stellarmill.com/#checkup','account_id':'506078191226626','page_id':'108719911745134','token_env':'META_ACCESS_TOKEN','currency':'USD','countries':[],'daily_budget':None,'spend_cap':None,'voice':'Premium, emotionally grounded, specific. Lead with organized business records, control and proactive tax conversations. Prioritize higher-earning business owners. No guaranteed refunds or tax savings.','notes':'Business-owner transformation is the primary campaign direction; address owners with existing preparers. One separate family post only. See strategy/stellar-mill-positioning.md. Contact details are requested before checkup results; the quiz routes to a service and does not calculate or file taxes.'},
 {'id':'in-and-out','name':'In & Out','offer':'','destination':'','account_id':'','page_id':'','token_env':'INOUT_META_ACCESS_TOKEN','currency':'USD','countries':[],'daily_budget':None,'spend_cap':None,'voice':'','notes':'Business details and naming need confirmation.'},
 {'id':'pap-ten','name':'Pap Ten','offer':'','destination':'','account_id':'','page_id':'','token_env':'PAPTEN_META_ACCESS_TOKEN','currency':'USD','countries':[],'daily_budget':None,'spend_cap':None,'voice':'','notes':'Business details and naming need confirmation.'},
 {'id':'dropshipping','name':'Future dropshipping brand','offer':'','destination':'','account_id':'','page_id':'','token_env':'DROPSHIP_META_ACCESS_TOKEN','currency':'USD','countries':[],'daily_budget':None,'spend_cap':None,'voice':'','notes':'Product, brand name, offer and accounts have not been selected.'},
]

def timestamp():return datetime.now(timezone.utc).isoformat()
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def money(value,label):
 try:
  amount=Decimal(str(value))
  if not amount.is_finite() or amount<=0 or amount>Decimal('100000') or amount.as_tuple().exponent < -2:raise ValueError()
  return int(amount*100)
 except (InvalidOperation,ValueError,TypeError):raise ValueError(label+' must be a positive USD amount with at most two decimals (maximum 100,000).') from None

def validate_brand(data):
 result={key:data.get(key) for key in DEFAULT_BRANDS[0]}
 if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,49}',str(result['id'])):raise ValueError('Invalid brand ID')
 for key in ('name','offer','destination','account_id','page_id','token_env','currency','voice','notes'):
  result[key]=str(result[key] or '').strip()
  if len(result[key])>4000:raise ValueError('Brand field is too long')
 if not result['name']:raise ValueError('Brand name is required')
 for key in ('account_id','page_id'):
  if result[key] and not result[key].isdigit():raise ValueError(key+' must contain digits only')
 if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',result['token_env']) or not result['token_env'].endswith('_META_ACCESS_TOKEN') and result['token_env']!='META_ACCESS_TOKEN':raise ValueError('Use a dedicated META_ACCESS_TOKEN environment variable for each brand')
 if result['currency']!='USD':raise ValueError('The first version supports USD ad accounts only')
 if result['destination']:
  url=urlparse(result['destination'])
  if url.scheme!='https' or not url.netloc or url.username or url.password:raise ValueError('Use a public HTTPS destination without embedded credentials')
 if not isinstance(result['countries'],list) or any(not isinstance(x,str) or not re.fullmatch(r'[A-Z]{2}',x) for x in result['countries']):raise ValueError('Use a list of two-letter target country codes')
 result['countries']=list(dict.fromkeys(result['countries']))
 for field in ('daily_budget','spend_cap'):
  if result[field] in ('',None):result[field]=None
  else:result[field]=money(result[field],field)/100
 if result['spend_cap'] and result['daily_budget'] and result['spend_cap']<result['daily_budget']:raise ValueError('Total campaign cap must be at least the daily budget')
 return result

class Store:
 def __init__(self,directory=None):
  self.directory=Path(directory or ROOT/'.data');self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.dbfile=self.directory/'brands.sqlite3'
  with self.db() as conn:
   conn.executescript('''CREATE TABLE IF NOT EXISTS brands(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS creatives(id TEXT PRIMARY KEY,brand_id TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY,brand_id TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,brand_id TEXT NOT NULL,created_at TEXT NOT NULL,data TEXT NOT NULL);''')
   for brand in DEFAULT_BRANDS:conn.execute('INSERT OR IGNORE INTO brands VALUES (?,?)',(brand['id'],json.dumps(brand)))
 @contextmanager
 def db(self):
  conn=sqlite3.connect(self.dbfile,timeout=30);conn.execute('PRAGMA busy_timeout=30000')
  try:
   with conn:yield conn
  finally:conn.close()
 def brands(self):
  with self.db() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM brands ORDER BY id')]
 def brand(self,brand_id):
  with self.db() as conn:row=conn.execute('SELECT data FROM brands WHERE id=?',(brand_id,)).fetchone()
  if not row:raise ValueError('Brand not found')
  return json.loads(row[0])
 def add_brand(self,name):
  name=str(name).strip()
  if not name or len(name)>200:raise ValueError('Enter a brand name under 200 characters')
  if any(b['name'].casefold()==name.casefold() for b in self.brands()):raise ValueError('A workspace with that brand name already exists')
  brand_id='brand-'+uuid.uuid4().hex[:12]
  brand={**DEFAULT_BRANDS[1],'id':brand_id,'name':name,'token_env':brand_id.upper().replace('-','_')+'_META_ACCESS_TOKEN','notes':'Business details have not been configured.'}
  brand=validate_brand(brand)
  with self.db() as conn:conn.execute('INSERT INTO brands VALUES(?,?)',(brand_id,json.dumps(brand)))
  self.event(brand_id,{'type':'brand_created'});return brand
 def save_brand(self,data):
  result=validate_brand(data);self.brand(result['id'])
  with self.db() as conn:conn.execute('UPDATE brands SET data=? WHERE id=?',(json.dumps(result),result['id']))
  self.event(result['id'],{'type':'brand_settings_saved'});return result
 def event(self,brand_id,data):
  with self.db() as conn:conn.execute('INSERT INTO events(brand_id,created_at,data) VALUES(?,?,?)',(brand_id,timestamp(),json.dumps(data)))
 def events(self,brand_id):
  self.brand(brand_id)
  with self.db() as conn:return [{'created_at':row[0],**json.loads(row[1])} for row in conn.execute('SELECT created_at,data FROM events WHERE brand_id=? ORDER BY id DESC LIMIT 100',(brand_id,))]
 def list_items(self,table,brand_id):
  if table not in ('creatives','campaigns'):raise ValueError('Invalid table')
  self.brand(brand_id)
  with self.db() as conn:return [json.loads(row[0]) for row in conn.execute('SELECT data FROM '+table+' WHERE brand_id=? ORDER BY rowid DESC',(brand_id,))]
 def item(self,table,brand_id,item_id):
  if table not in ('creatives','campaigns'):raise ValueError('Invalid table')
  with self.db() as conn:row=conn.execute('SELECT data FROM '+table+' WHERE brand_id=? AND id=?',(brand_id,item_id)).fetchone()
  if not row:raise ValueError('Item not found in this brand')
  return json.loads(row[0])
 def put(self,table,item):
  if table not in ('creatives','campaigns'):raise ValueError('Invalid table')
  with self.db() as conn:conn.execute('INSERT INTO '+table+' VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE '+table+'.brand_id=excluded.brand_id',(item['id'],item['brand_id'],json.dumps(item)))
  return item
 def creative(self,brand_id,data):
  self.brand(brand_id)
  for key in ('name','headline','copy','image_base64'):
   if not isinstance(data.get(key),str) or not data[key].strip():raise ValueError(key+' is required')
  if len(data['copy'])>10000 or len(data['headline'])>200 or len(data['name'])>200:raise ValueError('Creative text is too long')
  try:raw=base64.b64decode(data['image_base64'],validate=True)
  except ValueError:raise ValueError('Invalid image encoding') from None
  if len(raw)>10_000_000:raise ValueError('Image must be at most 10 MB')
  extension='png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if raw.startswith(b'\xff\xd8\xff') else None
  if not extension:raise ValueError('Use a PNG or JPEG image')
  item_id=uuid.uuid4().hex;directory=self.directory/'assets'/brand_id;directory.mkdir(parents=True,exist_ok=True)
  image=directory/(item_id+'.'+extension);image.write_bytes(raw)
  item={'id':item_id,'brand_id':brand_id,'name':data['name'].strip(),'headline':data['headline'].strip(),'copy':data['copy'].strip(),'image':str(image.relative_to(self.directory)),'image_sha256':hashlib.sha256(raw).hexdigest(),'status':'DRAFT','created_at':timestamp()}
  return self.put('creatives',item)
 def approve(self,brand_id,item_id,approved):
  item=self.item('creatives',brand_id,item_id)
  item['status']='APPROVED' if approved else 'DRAFT';item['approved_at']=timestamp() if approved else None
  self.event(brand_id,{'type':'creative_approval','creative_id':item_id,'approved':approved});return self.put('creatives',item)

class Meta:
 def __call__(self,brand,endpoint,params=None):
  token=os.environ.get(brand['token_env'])
  if not token:raise ValueError('Meta connection missing: configure '+brand['token_env']+' securely; do not paste it in chat.')
  version=os.environ.get('META_API_VERSION','v23.0')
  if not re.fullmatch(r'v\d+\.\d+',version):raise ValueError('Invalid Meta API version')
  if not re.fullmatch(r'[A-Za-z0-9_/-]+',endpoint):raise ValueError('Invalid Meta endpoint')
  url='https://graph.facebook.com/'+version+'/'+endpoint
  params=params or {};method=params.pop('_method','GET')
  clean={k:json.dumps(v) if isinstance(v,(dict,list)) else str(v) for k,v in params.items()}
  encoded=urlencode(clean)
  if method=='GET':url+='?'+encoded;body=None
  else:body=encoded.encode()
  try:
   with urlopen(Request(url,data=body,method=method,headers={'Authorization':'Bearer '+token,'Content-Type':'application/x-www-form-urlencoded'}),timeout=60) as response:return json.load(response)
  except HTTPError as error:
   try:
    info=json.load(error).get('error',{});message=str(info.get('message','Request denied')).replace(token,'[redacted]');code=info.get('code',error.code)
   except (ValueError,AttributeError):message='Request denied';code=error.code
   raise ValueError(f'Meta error {code}: {message}') from None
  except (URLError,TimeoutError):raise ValueError('Meta request did not complete. Check Ads Manager before retrying a write; the remote operation may have succeeded.') from None

class Engine:
 def __init__(self,store=None,meta=None):self.store=store or Store();self.meta=meta or Meta();self.lock=threading.RLock()
 def call(self,brand,endpoint,**params):return self.meta(brand,endpoint,params)
 def post(self,brand,endpoint,**params):return self.call(brand,endpoint,_method='POST',**params)
 def verify(self,brand):
  if not brand['account_id'] or not brand['page_id']:raise ValueError('Set this brand’s ad account ID and Facebook Page ID first')
  account=self.call(brand,'act_'+brand['account_id'],fields='id,name,currency,account_status')
  if account.get('account_status')!=1:raise ValueError('Meta says this ad account is inactive or restricted')
  if account.get('currency')!='USD':raise ValueError('This first version supports USD accounts only')
  page=self.call(brand,brand['page_id'],fields='id,name')
  permissions=self.call(brand,'me/permissions')
  granted={p['permission'] for p in permissions.get('data',[]) if p.get('status')=='granted'}
  if 'ads_management' not in granted:raise ValueError('Meta authorization lacks ads_management')
  return {'account':account,'page':page,'verified_at':timestamp(),'note':'Account and Page reads do not guarantee that Meta will accept a new ad or deliver it.'}
 def plan(self,brand_id,data):
  brand=validate_brand(self.store.brand(brand_id))
  if not all(brand[k] for k in ('account_id','page_id','destination','offer','countries','daily_budget','spend_cap')):raise ValueError('Set the offer, destination, account, Page, target countries, daily budget and total campaign cap before planning')
  ids=data.get('creative_ids',[])
  if not isinstance(ids,list) or not ids or len(ids)>10 or len(set(ids))!=len(ids):raise ValueError('Select 1–10 distinct approved creatives')
  creatives=[self.store.item('creatives',brand_id,item_id) for item_id in ids]
  if any(c['status']!='APPROVED' for c in creatives):raise ValueError('Approve each selected creative first')
  name=str(data.get('name',brand['name']+' | Static Test')).strip()
  if not name or len(name)>200:raise ValueError('Enter a campaign name under 200 characters')
  result={'id':uuid.uuid4().hex,'brand_id':brand_id,'status':'DRAFT','name':name,'brand_snapshot':brand,'creatives':copy.deepcopy(creatives),'objective':'OUTCOME_TRAFFIC','optimization':'LINK_CLICKS','created_at':timestamp(),'remote':{'campaign_id':None,'adset_id':None,'ads':[]},'note':'Website traffic, not completed-checkup optimization. Country-wide targeting, age 18+. No automatic budget increases.'}
  result['review_hash']=digest({k:result[k] for k in ('brand_snapshot','creatives','name','objective','optimization')})
  self.store.event(brand_id,{'type':'plan_created','plan_id':result['id']});return self.store.put('campaigns',result)
 def _save(self,plan):self.store.put('campaigns',plan)
 def _still_approved(self,plan):
  for c in plan['creatives']:
   current=self.store.item('creatives',plan['brand_id'],c['id'])
   if current['status']!='APPROVED' or digest(current)!=digest(c):raise ValueError('Creative approval has changed. Make a new reviewed plan before creation or launch.')
   raw=(self.store.directory/c['image']).read_bytes()
   if hashlib.sha256(raw).hexdigest()!=c['image_sha256']:raise ValueError('Creative image changed after approval')
 def create(self,brand_id,plan_id,review_hash):
  with self.lock:
   plan=self.store.item('campaigns',brand_id,plan_id)
   if plan['status']!='DRAFT':raise ValueError('Creation already attempted. Inspect the saved Meta IDs; retries could duplicate objects.')
   if review_hash!=plan['review_hash']:raise ValueError('Review the exact saved plan before creating it')
   self._still_approved(plan);brand=plan['brand_snapshot'];self.verify(brand)
   plan['status']='CREATING';self._save(plan)
   try:
    campaign=self.post(brand,'act_'+brand['account_id']+'/campaigns',name=plan['name'],objective=plan['objective'],status='PAUSED',special_ad_categories=[],spend_cap=money(brand['spend_cap'],'Total campaign cap'))
    plan['remote']['campaign_id']=campaign['id'];self._save(plan)
    adset=self.post(brand,'act_'+brand['account_id']+'/adsets',name=plan['name']+' | Audience',campaign_id=campaign['id'],daily_budget=money(brand['daily_budget'],'Daily budget'),billing_event='IMPRESSIONS',optimization_goal='LINK_CLICKS',bid_strategy='LOWEST_COST_WITHOUT_CAP',destination_type='WEBSITE',targeting={'geo_locations':{'countries':brand['countries']},'age_min':18},status='PAUSED')
    plan['remote']['adset_id']=adset['id'];self._save(plan)
    for creative in plan['creatives']:
     raw=(self.store.directory/creative['image']).read_bytes()
     uploaded=self.post(brand,'act_'+brand['account_id']+'/adimages',bytes=base64.b64encode(raw).decode())
     image_hash=next(iter(uploaded.get('images',{}).values()))['hash']
     remote_creative=self.post(brand,'act_'+brand['account_id']+'/adcreatives',name=creative['name'],object_story_spec={'page_id':brand['page_id'],'link_data':{'image_hash':image_hash,'link':brand['destination'],'message':creative['copy'],'name':creative['headline'],'call_to_action':{'type':'LEARN_MORE','value':{'link':brand['destination']}}}})
     # Save the creative ID before creating the ad for recoverable failures.
     entry={'creative_id':remote_creative['id'],'local_creative_id':creative['id'],'ad_id':None};plan['remote']['ads'].append(entry);self._save(plan)
     ad=self.post(brand,'act_'+brand['account_id']+'/ads',name=creative['name'],adset_id=adset['id'],creative={'creative_id':remote_creative['id']},status='PAUSED')
     entry['ad_id']=ad['id'];self._save(plan)
    plan['status']='PAUSED';plan['complete']=True
   except Exception as error:
    plan['status']='INCOMPLETE';plan['error']=str(error);self._save(plan);self.store.event(brand_id,{'type':'creation_failed','plan_id':plan_id,'error':str(error)});raise
   self._save(plan);self.store.event(brand_id,{'type':'campaign_created_paused','plan_id':plan_id});return plan
 def launch_phrase(self,plan):
  b=plan['brand_snapshot'];return f"LAUNCH {b['name']} {b['daily_budget']:.2f}/DAY {b['spend_cap']:.2f} TOTAL"
 def launch(self,brand_id,plan_id,confirmation):
  with self.lock:
   plan=self.store.item('campaigns',brand_id,plan_id)
   if plan['status']!='PAUSED' or not plan.get('complete'):raise ValueError('Only a complete paused campaign can launch')
   if confirmation!=self.launch_phrase(plan):raise ValueError('Enter the exact brand, daily budget, and total cap launch phrase')
   self._still_approved(plan);b=plan['brand_snapshot'];self.verify(b)
   remote=self.call(b,plan['remote']['campaign_id'],fields='id,status,spend_cap')
   if remote.get('status')!='PAUSED':raise ValueError('Campaign status changed in Meta. Reconcile it before launch.')
   if int(remote.get('spend_cap',0))!=money(b['spend_cap'],'Total campaign cap'):raise ValueError('Campaign cap changed in Meta; review before launching')
   remote_set=self.call(b,plan['remote']['adset_id'],fields='id,daily_budget,targeting')
   if int(remote_set.get('daily_budget',0))!=money(b['daily_budget'],'Daily budget') or remote_set.get('targeting',{}).get('geo_locations',{}).get('countries')!=b['countries']:raise ValueError('Meta budget or audience changed; review before launching')
   plan['status']='ACTIVATING';self._save(plan)
   try:
    for ad in plan['remote']['ads']:self.post(b,ad['ad_id'],status='ACTIVE')
    self.post(b,plan['remote']['adset_id'],status='ACTIVE')
    self.post(b,plan['remote']['campaign_id'],status='ACTIVE')
   except Exception as error:
    plan['status']='DELIVERY_UNCERTAIN';plan['error']=str(error);self._save(plan);self.store.event(brand_id,{'type':'activation_uncertain','plan_id':plan_id,'error':str(error)});raise
   plan['status']='ACTIVE';plan['launched_at']=timestamp();plan.pop('error',None);self._save(plan)
   self.store.event(brand_id,{'type':'launch_requested','plan_id':plan_id,'daily_budget':b['daily_budget'],'total_cap':b['spend_cap']});return plan
 def pause(self,brand_id,plan_id,reason='User requested pause'):
  with self.lock:
   plan=self.store.item('campaigns',brand_id,plan_id)
   if not plan['remote']['campaign_id']:raise ValueError('No remote campaign exists yet')
   self.post(plan['brand_snapshot'],plan['remote']['campaign_id'],status='PAUSED');plan['status']='PAUSED' if plan.get('complete') else 'INCOMPLETE';plan['pause_reason']=reason;self._save(plan)
   self.store.event(brand_id,{'type':'paused','plan_id':plan_id,'reason':reason});return plan
 def report(self,brand_id,plan_id):
  with self.lock:
   plan=self.store.item('campaigns',brand_id,plan_id)
   if not plan['remote']['campaign_id']:raise ValueError('Create the remote campaign first')
   b=plan['brand_snapshot'];remote=self.call(b,plan['remote']['campaign_id'],fields='id,status,effective_status')
   insights=self.call(b,plan['remote']['campaign_id']+'/insights',fields='spend,impressions,clicks,inline_link_clicks,ctr,cpc,actions',date_preset='maximum')
   report={'checked_at':timestamp(),'delivery':remote,'insights':insights.get('data',[]),'note':'Actions must be matched to a verified conversion event. Clicks are not completed checkups.'}
   plan['last_report']=report;self._save(plan);return report
 def command(self,brand_id,text):
  self.store.brand(brand_id);command=' '.join(str(text).lower().strip().split())
  if command in ('check connection','connect meta','verify meta'):
   return {'action':'verify','result':self.verify(self.store.brand(brand_id))}
  if command in ('show campaigns','campaign status','status'):
   return {'action':'status','result':self.store.list_items('campaigns',brand_id)}
  if command in ('pause all campaigns','pause all ads'):
   with self.lock:
    plans=[p for p in self.store.list_items('campaigns',brand_id) if p['status'] in ('ACTIVE','DELIVERY_UNCERTAIN','ACTIVATING') and p['remote']['campaign_id']]
    results=[]
    for plan in plans:
     try:self.pause(brand_id,plan['id']);results.append({'id':plan['id'],'paused':True})
     except Exception as error:results.append({'id':plan['id'],'paused':False,'error':str(error)})
    return {'action':'pause','result':results}
  if command in ('refresh results','report campaigns'):
   results=[]
   for plan in self.store.list_items('campaigns',brand_id):
    if not plan['remote']['campaign_id']:continue
    try:results.append({'id':plan['id'],'report':self.report(brand_id,plan['id'])})
    except Exception as error:results.append({'id':plan['id'],'error':str(error)})
   return {'action':'report','result':results}
  raise ValueError('Supported commands: check connection; show campaigns; refresh results; pause all campaigns. Create and launch through the reviewed plan controls. This is a constrained command runner, not an open-ended AI chat agent.')
 def monitor_once(self):
  results=[]
  for brand in self.store.brands():
   for plan in self.store.list_items('campaigns',brand['id']):
    if plan['status'] not in ('ACTIVE','DELIVERY_UNCERTAIN'):continue
    try:
     report=self.report(brand['id'],plan['id']);spent=sum(Decimal(str(row.get('spend','0'))) for row in report['insights'])
     if spent>=Decimal(str(plan['brand_snapshot']['spend_cap'])):self.pause(brand['id'],plan['id'],'Total campaign cap reached (monitor)')
     elif report['delivery'].get('status')=='PAUSED':
      with self.lock:
       current=self.store.item('campaigns',brand['id'],plan['id'])
       # Do not overwrite a more recent launch decision.
       if current['status']==plan['status']:current['status']='PAUSED';self._save(current)
     results.append({'brand_id':brand['id'],'campaign_id':plan['id'],'ok':True})
    except Exception as error:
     self.store.event(brand['id'],{'type':'monitor_error','plan_id':plan['id'],'error':str(error)})
     results.append({'brand_id':brand['id'],'campaign_id':plan['id'],'ok':False,'error':str(error)})
  return results
