import base64,copy,json,tempfile,threading,unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from brand_platform import Store,Engine,DEFAULT_BRANDS,validate_brand,money
from brand_server import Handler
PNG=base64.b64encode(b'\x89PNG\r\n\x1a\n'+b'test image fixture').decode()

class FakeMeta:
 def __init__(self):self.calls=[];self.objects={};self.fail_on=None;self.index=0;self.spend='0'
 def __call__(self,brand,endpoint,params):
  params=copy.deepcopy(params);self.calls.append((brand['id'],endpoint,params))
  if endpoint==self.fail_on:raise ValueError('Injected Meta failure')
  if params.get('_method')!='POST':
   if endpoint.startswith('act_'):return {'id':endpoint,'name':'Account','currency':'USD','account_status':1}
   if endpoint==brand['page_id']:return {'id':endpoint,'name':'Page'}
   if endpoint=='me/permissions':return {'data':[{'permission':'ads_management','status':'granted'}]}
   if endpoint.endswith('/insights'):return {'data':[{'spend':self.spend,'clicks':'10'}]}
   return copy.deepcopy(self.objects[endpoint])
  if endpoint.endswith('/adimages'):return {'images':{'fixture.png':{'hash':'image-hash'}}}
  if endpoint in self.objects:self.objects[endpoint].update(params);return {'success':True}
  self.index+=1;item_id=str(self.index);self.objects[item_id]={'id':item_id,**params};return {'id':item_id}

class PlatformTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.store=Store(self.temp.name);self.meta=FakeMeta();self.engine=Engine(self.store,self.meta)
  b=self.store.brand('stellar-mill');b.update(countries=['US'],daily_budget=10,spend_cap=50);self.store.save_brand(b)
 def tearDown(self):self.temp.cleanup()
 def creative(self,brand='stellar-mill',approved=True):
  c=self.store.creative(brand,{'name':'Test','headline':'Tax checkup','copy':'Free readiness check','image_base64':PNG})
  return self.store.approve(brand,c['id'],True) if approved else c
 def plan(self):return self.engine.plan('stellar-mill',{'name':'Test campaign','creative_ids':[self.creative()['id']]})
 def created(self):
  plan=self.plan();return self.engine.create('stellar-mill',plan['id'],plan['review_hash'])
 def test_brand_isolation_and_durability(self):
  creative=self.creative();self.assertEqual(self.store.list_items('creatives','in-and-out'),[])
  with self.assertRaises(ValueError):self.store.item('creatives','in-and-out',creative['id'])
  with self.assertRaises(ValueError):self.engine.plan('in-and-out',{'creative_ids':[creative['id']]})
  restarted=Store(self.temp.name);self.assertEqual(len(restarted.brands()),4);self.assertEqual(restarted.item('creatives','stellar-mill',creative['id'])['status'],'APPROVED')
 def test_new_brands_start_empty_and_do_not_inherit_tax_settings(self):
  brand=self.store.add_brand('New shop');self.assertEqual(brand['destination'],'');self.assertIsNone(brand['daily_budget']);self.assertEqual(brand['countries'],[])
  self.assertNotEqual(brand['token_env'],'META_ACCESS_TOKEN');self.assertEqual(self.store.list_items('creatives',brand['id']),[])
  with self.assertRaises(ValueError):self.store.add_brand('new shop')
 def test_budget_boundaries_and_missing_configuration(self):
  for bad in (0,-1,'nan','infinity',True,'10.001',100001):
   with self.assertRaises(ValueError):money(bad,'Budget')
  self.assertEqual(money('12.34','Budget'),1234)
  brand=self.store.brand('stellar-mill');brand['spend_cap']=5
  with self.assertRaises(ValueError):validate_brand(brand)
  brand=self.store.brand('stellar-mill');brand['countries']=[];self.store.save_brand(brand)
  with self.assertRaises(ValueError):self.plan()
 def test_plan_freezes_brand_settings_and_requires_approval(self):
  c=self.creative(approved=False)
  with self.assertRaises(ValueError):self.engine.plan('stellar-mill',{'creative_ids':[c['id']]})
  p=self.plan();brand=self.store.brand('stellar-mill');brand['daily_budget']=30;self.store.save_brand(brand)
  created=self.engine.create('stellar-mill',p['id'],p['review_hash']);self.assertEqual(created['brand_snapshot']['daily_budget'],10)
  adsets=[params for _,endpoint,params in self.meta.calls if endpoint.endswith('/adsets')];self.assertEqual(adsets[0]['daily_budget'],1000)
 def test_creation_is_paused_capped_and_not_repeatable(self):
  p=self.created();self.assertEqual(p['status'],'PAUSED');self.assertTrue(p['complete'])
  writes=[(endpoint,params) for _,endpoint,params in self.meta.calls if params.get('_method')=='POST']
  for endpoint,params in writes:
   if endpoint.endswith(('/campaigns','/adsets','/ads')):self.assertEqual(params['status'],'PAUSED')
  self.assertEqual(writes[0][1]['spend_cap'],5000)
  with self.assertRaises(ValueError):self.engine.create('stellar-mill',p['id'],p['review_hash'])
 def test_review_hash_and_revoked_approval_block_mutations(self):
  p=self.plan()
  with self.assertRaises(ValueError):self.engine.create('stellar-mill',p['id'],'wrong')
  self.store.approve('stellar-mill',p['creatives'][0]['id'],False)
  with self.assertRaises(ValueError):self.engine.create('stellar-mill',p['id'],p['review_hash'])
  self.assertEqual(self.meta.calls,[])
 def test_partial_failure_retains_ids_and_cannot_launch(self):
  p=self.plan();self.meta.fail_on='act_506078191226626/adsets'
  with self.assertRaises(ValueError):self.engine.create('stellar-mill',p['id'],p['review_hash'])
  partial=self.store.item('campaigns','stellar-mill',p['id']);self.assertEqual(partial['status'],'INCOMPLETE');self.assertTrue(partial['remote']['campaign_id'])
  with self.assertRaises(ValueError):self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p))
  self.engine.pause('stellar-mill',p['id']);self.assertEqual(self.meta.objects[partial['remote']['campaign_id']]['status'],'PAUSED')
 def test_launch_requires_exact_spending_and_activates_campaign_last(self):
  p=self.created()
  with self.assertRaises(ValueError):self.engine.launch('stellar-mill',p['id'],'yes')
  active=self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p));self.assertEqual(active['status'],'ACTIVE')
  self.assertEqual(self.meta.calls[-1][1],p['remote']['campaign_id']);self.assertEqual(self.meta.calls[-1][2]['status'],'ACTIVE')
  self.engine.pause('stellar-mill',p['id']);self.assertEqual(self.meta.calls[-1][2]['status'],'PAUSED')
 def test_external_budget_or_cap_change_blocks_launch(self):
  p=self.created();self.meta.objects[p['remote']['campaign_id']]['spend_cap']=99999
  with self.assertRaises(ValueError):self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p))
  self.meta.objects[p['remote']['campaign_id']]['spend_cap']=5000;self.meta.objects[p['remote']['adset_id']]['daily_budget']=2000
  with self.assertRaises(ValueError):self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p))
 def test_activation_error_is_not_reported_as_paused(self):
  p=self.created();self.meta.fail_on=p['remote']['campaign_id']
  # The preflight read would fail too; inject only the final write instead.
  self.meta.fail_on=None;original=self.meta
  def fail_write(brand,endpoint,params):
   if endpoint==p['remote']['campaign_id'] and params.get('_method')=='POST':raise ValueError('Response lost')
   return original(brand,endpoint,params)
  self.engine.meta=fail_write
  with self.assertRaises(ValueError):self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p))
  self.assertEqual(self.store.item('campaigns','stellar-mill',p['id'])['status'],'DELIVERY_UNCERTAIN')
 def test_monitor_pauses_at_total_cap_and_retains_errors(self):
  p=self.created();self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p));self.meta.spend='50.00';result=self.engine.monitor_once()
  self.assertTrue(result[0]['ok']);self.assertEqual(self.store.item('campaigns','stellar-mill',p['id'])['status'],'PAUSED')
  self.engine.launch('stellar-mill',p['id'],self.engine.launch_phrase(p));self.meta.fail_on=p['remote']['campaign_id'];result=self.engine.monitor_once();self.assertFalse(result[0]['ok']);self.assertEqual(self.store.events('stellar-mill')[0]['type'],'monitor_error')
 def test_commands_never_interpret_free_text_as_spending_authority(self):
  with self.assertRaises(ValueError):self.engine.command('stellar-mill','launch whatever at 100 per day')
  self.assertEqual(self.engine.command('in-and-out','show campaigns')['result'],[])
  self.assertEqual(self.engine.command('stellar-mill','pause all campaigns')['result'],[])
 def test_http_brand_isolation_csrf_and_host(self):
  c=self.creative();server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.engine=self.engine;server.csrf='test-csrf';server.worker_running=False
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_address[1])
  try:
   with urlopen(base+'/') as response:self.assertIn('Your Marketing Agents',response.read().decode())
   with self.assertRaises(HTTPError) as error:urlopen(Request(base+'/api/brands/stellar-mill/verify',data=b'{}'))
   self.assertEqual(error.exception.code,403)
   with self.assertRaises(HTTPError) as error:urlopen(base+'/api/brands/in-and-out/assets/'+c['id'])
   self.assertEqual(error.exception.code,404)
   with self.assertRaises(HTTPError) as error:urlopen(Request(base+'/api/state',headers={'Host':'evil.example'}))
   self.assertEqual(error.exception.code,403)
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
