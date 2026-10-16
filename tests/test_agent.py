import json, threading, unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import marketing_agent as agent

class ResearchTests(unittest.TestCase):
 def test_visible_text_and_source_based_briefs(self):
  parser=agent.VisibleText();parser.feed('<h1>Real offer</h1><script>fake promise</script><style>bad</style>Visible')
  self.assertEqual(parser.parts,['Real offer','Visible'])
  self.assertEqual(agent.make_briefs([]),[])
  self.assertEqual(agent.make_briefs([{'kind':'offer','status':'unavailable'}]),[])
  source={'kind':'offer','status':'verified','text':'60-second contact Self-Employed Tax Preparation','url':'https://checkup.stellarmill.com/'}
  briefs=agent.make_briefs([source]);self.assertEqual(len(briefs),3)
  self.assertTrue(all('Untested' in b['status'] and 'Contact details' in b['disclosure'] for b in briefs))
 def test_reference_claims_are_observations_not_performance(self):
  result=agent.analyze_reference({'brand':'Example','copy':'1099 income?\nFree check. Save money.','url':'https://www.facebook.com/ads/library/?id=123'})
  self.assertIn('Question hook',result['observations']);self.assertIn('Income-profile relevance',result['observations'])
  self.assertIn('Financial claim: verify before adapting',result['observations']);self.assertIn('Unknown',result['performance'])
  with self.assertRaises(ValueError):agent.analyze_reference({'copy':'ad','url':'javascript:alert(1)'})
 def test_csv_does_not_invent_leads_or_mix_clicks_with_conversions(self):
  data='Ad name,Amount spent (USD),Impressions,Link clicks,Website leads\nA,25,1000,50,\nB,100,2000,10,4\nC,15,100,0,0\n'
  rows=agent.summarize_csv(data)['ads'];self.assertEqual(rows[0]['link_ctr_percent'],5)
  self.assertIsNone(rows[0]['website_leads']);self.assertIsNone(rows[0]['cost_per_website_lead'])
  self.assertEqual(rows[1]['cost_per_website_lead'],25);self.assertIsNone(rows[2]['cost_per_website_lead'])
  with self.assertRaises(ValueError):agent.summarize_csv('Name,Clicks\nA,5')
 def test_reference_text_cannot_inject_html(self):
  item=agent.analyze_reference({'brand':'<script>alert(1)</script>','copy':'<img src=x onerror=alert(1)>'})
  page=agent.render({},[item]);self.assertNotIn('<img src=x',page);self.assertIn('&lt;script&gt;',page)
 def test_meta_pagination_uses_cursors_not_external_urls(self):
  with patch.object(agent,'meta_get',side_effect=[{'data':[{'id':'1'}],'paging':{'next':'https://evil.example/?access_token=secret','cursors':{'after':'cursor'}}},{'data':[{'id':'2'}]}]) as mock:
   rows,capped=agent.meta_pages('ads_archive',{},100)
   self.assertEqual(len(rows),2);self.assertFalse(capped);self.assertEqual(mock.call_args_list[1].args[0],'ads_archive');self.assertEqual(mock.call_args_list[1].args[1]['after'],'cursor')
 def test_http_csrf_and_real_import_validation(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),agent.Handler);server.csrf='test-token';server.write_lock=threading.Lock()
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_address[1])
  try:
   with urlopen(base+'/') as r:self.assertIn('Ad Research',r.read().decode())
   with self.assertRaises(HTTPError) as ctx:urlopen(Request(base+'/api/reference',data=b'{}',headers={'Content-Type':'application/json'}))
   self.assertEqual(ctx.exception.code,403)
   with self.assertRaises(HTTPError) as ctx:urlopen(Request(base+'/api/csv',data=json.dumps({'csv':'Clicks\n1'}).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':'test-token'}))
   self.assertEqual(ctx.exception.code,400)
   self.assertIn('Export an Ads Manager CSV',ctx.exception.read().decode())
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
