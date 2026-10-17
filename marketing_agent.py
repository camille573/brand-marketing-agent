#!/usr/bin/env python3
"""Stellar Mill research agent. Python standard library; no paid research subscription."""
import argparse, csv, html, io, json, os, re, secrets, threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCES = [
 ('offer', 'Stellar Mill Tax Checkup', 'https://checkup.stellarmill.com/'),
 ('gig', 'IRS: Gig Economy Tax Center', 'https://www.irs.gov/businesses/gig-economy-tax-center'),
 ('records', 'IRS: Recordkeeping', 'https://www.irs.gov/taxtopics/tc305'),
 ('competitor', 'H&R Block: Self-employed tax filing', 'https://www.hrblock.com/online-tax-filing/self-employed-online-tax-filing/'),
 ('competitor', 'TurboTax: Self-employed tax filing', 'https://turbotax.intuit.com/personal-taxes/online/self-employed/'),
]
TERMS = ['tax preparation', 'self employed tax', 'bookkeeping', 'past due tax returns']

def now(): return datetime.now(timezone.utc).isoformat()

class VisibleText(HTMLParser):
 def __init__(self):
  super().__init__(); self.skip=0; self.parts=[]
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style','noscript','svg'): self.skip+=1
 def handle_endtag(self,tag):
  if tag in ('script','style','noscript','svg'): self.skip=max(0,self.skip-1)
 def handle_data(self,data):
  if not self.skip and data.strip(): self.parts.append(' '.join(data.split()))

def fetch_source(kind,title,url):
 try:
  with urlopen(Request(url,headers={'User-Agent':'StellarMillResearch/1.0'}),timeout=25) as response:
   data=response.read(2_000_001)
   if len(data)>2_000_000: raise ValueError('Source exceeds size limit')
  parser=VisibleText(); parser.feed(data.decode('utf-8',errors='replace'))
  return {'kind':kind,'title':title,'url':url,'retrieved_at':now(),'status':'verified','text':'\n'.join(parser.parts)}
 except (HTTPError,URLError,ValueError,TimeoutError) as error:
  return {'kind':kind,'title':title,'url':url,'retrieved_at':now(),'status':'unavailable','error':type(error).__name__}

def meta_get(endpoint,params=None):
 token=os.environ.get('META_ACCESS_TOKEN')
 if not token: raise ValueError('Meta token is not configured in this environment.')
 version=os.environ.get('META_API_VERSION','v23.0')
 if not re.fullmatch(r'v\d+\.\d+',version): raise ValueError('Invalid API version')
 url='https://graph.facebook.com/'+version+'/'+endpoint
 if params: url+='?'+urlencode(params)
 try:
  with urlopen(Request(url,headers={'Authorization':'Bearer '+token}),timeout=30) as response: return json.load(response)
 except HTTPError as error:
  try: body=json.load(error).get('error',{}); msg=str(body.get('message','Request denied')).replace(token,'[redacted]')
  except (ValueError,AttributeError): msg='Request denied'
  raise ValueError('Meta rejected this request (HTTP '+str(error.code)+'): '+msg) from None

def meta_pages(endpoint,params,limit=100):
 rows=[]; params=dict(params)
 while len(rows)<limit:
  result=meta_get(endpoint,params); rows.extend(result.get('data',[]))
  paging=result.get('paging',{}); after=paging.get('cursors',{}).get('after')
  if not paging.get('next') or not after: return rows[:limit],False
  # Never follow token-bearing paging URLs or write them to reports.
  params['after']=after
 return rows[:limit],True

def competitor_search(term,country='US'):
 if not re.fullmatch('[A-Z]{2}',country): raise ValueError('Use a two-letter country code.')
 rows,capped=meta_pages('ads_archive',{'search_terms':term[:200],'ad_reached_countries':json.dumps([country]),'ad_type':'ALL','ad_active_status':'ACTIVE','fields':'id,page_name,ad_creative_bodies,ad_snapshot_url,ad_delivery_start_time','limit':25})
 return {'ads':rows,'capped':capped,'note':'Creative references only. No competitor conversion rates are available; availability depends on Meta coverage and authorization.'}

def own_ads(account):
 account=account.removeprefix('act_')
 if not account.isdigit(): raise ValueError('Invalid ad account ID')
 ads,capped=meta_pages('act_'+account+'/ads',{'fields':'id,name,creative{body,title,object_story_spec}','limit':50})
 insights,insight_cap=meta_pages('act_'+account+'/insights',{'fields':'ad_id,ad_name,spend,impressions,clicks,inline_link_clicks,ctr,cpc,actions','level':'ad','date_preset':'last_90d','limit':50})
 return {'ads':ads,'performance':insights,'capped':capped or insight_cap,'period':'last_90d','note':'Clicks and CTR do not establish lead quality or completed checkups. Inspect the recorded conversion action and attribution before ranking by leads.'}

def analyze_reference(item):
 text=str(item.get('copy','')).strip()
 if not text: raise ValueError('Paste the actual ad copy before importing a reference.')
 url=str(item.get('url','')).strip()
 if url and (urlparse(url).scheme not in ('http','https') or not urlparse(url).netloc): raise ValueError('Reference URL must be a valid HTTP(S) URL.')
 low=text.lower(); formats=[]
 if '?' in text: formats.append('Question hook')
 if '\n' in text or re.search(r'\b\d+[.)]',text): formats.append('List or short-line structure')
 if re.search(r'\b(free|no.cost)\b',low): formats.append('Free-offer framing')
 if re.search(r'\b(1099|freelanc|self.employed|gig|business owner)',low): formats.append('Income-profile relevance')
 if re.search(r'\b(refund|save|saving|guarantee|deduction)',low): formats.append('Financial claim: verify before adapting')
 return {'brand':str(item.get('brand','Unknown'))[:200],'url':url[:2000],'copy':text[:15000],'visual_notes':str(item.get('visual_notes',''))[:5000],'saved_at':now(),'observations':formats,'hook':text.splitlines()[0][:300],'performance':'Unknown; do not label this a winner without conversion evidence.'}

def summarize_csv(raw):
 reader=csv.DictReader(io.StringIO(raw.lstrip('\ufeff')))
 columns=set(reader.fieldnames or [])
 if 'Amount spent (USD)' not in columns or 'Impressions' not in columns:
  raise ValueError('Export an Ads Manager CSV with Amount spent (USD) and Impressions columns. Keep the original column headings.')
 def number(v):
  value=str(v or '').replace(',','').replace('$','').strip()
  return float(value) if value else None
 result=[]
 for i,row in enumerate(reader):
  if i>=1000: raise ValueError('Import at most 1000 rows.')
  spend=number(row.get('Amount spent (USD)')); impressions=number(row.get('Impressions'))
  clicks=number(row.get('Link clicks')); leads=number(row.get('Website leads')); lpv=number(row.get('Landing page views'))
  result.append({'name':row.get('Ad name') or row.get('Campaign name') or 'Unnamed','spend_usd':spend,'impressions':impressions,'link_clicks':clicks,'link_ctr_percent':round(clicks/impressions*100,2) if clicks is not None and impressions else None,'website_leads':leads,'cost_per_website_lead':round(spend/leads,2) if spend is not None and leads else None,'landing_page_views':lpv})
 return {'ads':result,'note':'Blank lead fields remain unknown, not zero. These are reported results, not causal proof. Check date range, attribution, conversion setup, and sample size.'}

def make_briefs(sources):
 offer=next((s for s in sources if s['kind']=='offer' and s['status']=='verified'),None)
 if not offer: return []
 text=offer['text']
 verified=['60-second' in text,'contact' in text.lower(),'Self-Employed Tax Preparation' in text]
 if not all(verified): return []
 base={'status':'Untested hypothesis — not a proven high-converting ad','offer_source':offer['url'],'destination':'https://checkup.stellarmill.com/#checkup','cta':'Take the Free Tax Checkup','disclosure':'Contact details are requested before results. The checkup recommends a service path; it does not prepare or file a return.'}
 concepts=[
  {'id':'paperwork','headline':'A shoebox is not a tax strategy.','audience':'Filers unsure where to start with records','copy':'Income forms. Receipts. Last year’s return. Answer five quick questions to find a starting point for tax help. Free 60-second Tax Checkup from Stellar Mill. Contact details requested to view results.','visual':'A single shoebox overflowing with receipts, photographed against charcoal. Cream oversized headline; restrained gold offer badge. No fake tax form or refund amount.','evidence':'IRS recordkeeping guidance says organized records help with tax preparation. The checkup routes users to a service; it does not analyze their documents.','source':'https://www.irs.gov/taxtopics/tc305'},
  {'id':'multiple-income','headline':'One person. More than one kind of income.','audience':'People with employment, freelance, or gig income','copy':'Paychecks, freelance work, business income—tax help starts with the full picture. Find your service path with Stellar Mill’s free 60-second Tax Checkup. Contact details requested to view results.','visual':'Three visually distinct paper cards labelled W-2, 1099, and Business Income converging on a gold START HERE card. Bold editorial composition, little supporting text.','evidence':'IRS explains that gig income must be reported, including side work. The landing page has a self-employed route.','source':'https://www.irs.gov/businesses/gig-economy-tax-center'},
  {'id':'routing','headline':'Tax prep? Catch-up? Start here.','audience':'People comparing tax-service options','copy':'Different tax situations need different next steps. Answer five quick questions for a recommended Stellar Mill service path. Free Tax Checkup; contact details requested to view results.','visual':'Cream background, giant black typographic choices TAX PREP / PAST-DUE RETURNS / BUSINESS TAXES, gold line guiding toward START HERE. No fake testimonial, rating, or result.','evidence':'The page offers personal tax preparation, past-due return support, business preparation, and tax-resolution review.','source':offer['url']}
 ]
 for concept in concepts:
  concept.update(base)
  concept['generation_prompt']=f"Create an original Meta static feed ad in 4:5 portrait. Brand: Stellar Mill; warm ivory, charcoal, muted gold. Mobile-readable editorial typography with ample margins. Exact headline: {concept['headline']} Visual: {concept['visual']} Small offer: Free 60-second Tax Checkup. CTA: {base['cta']}. Footer: checkup.stellarmill.com. Do not invent testimonials, star ratings, guaranteed refunds, deadline urgency, or savings. This is an advertisement, not an official IRS document."
 return concepts

def research():
 sources=[fetch_source(*source) for source in SOURCES]
 return {'generated_at':now(),'sources':sources,'briefs':make_briefs(sources),'competitor_searches':[{'term':term,'url':'https://www.facebook.com/ads/library/?'+urlencode({'active_status':'active','ad_type':'all','country':'US','q':term,'search_type':'keyword_unordered','media_type':'image'})} for term in TERMS],'limitations':['Competitor conversion rates are not public. Ad longevity and spend are signals, not proof.','Creative briefs are rule-based hypotheses grounded in retrieved offer text, not an AI model’s evaluation of competitor images.','Reference imports retain source links and user-provided text; this tool does not bypass Facebook login or scrape restricted pages.']}

def load(path,default):
 try:return json.loads(path.read_text())
 except FileNotFoundError:return default

def save(path,value):
 path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2,ensure_ascii=False));temp.replace(path)

def render(report,references=None,metrics=None):
 esc=lambda value:html.escape(str(value))
 sources=''.join(f'<li><a href="{esc(s["url"])}">{esc(s["title"])}</a> — {esc(s["status"])}</li>' for s in report.get('sources',[]))
 competitor_notes=''
 for source in report.get('sources',[]):
  if source['kind']=='competitor':
   lines=[line for line in source.get('text','').splitlines() if any(word in line.lower() for word in ['self-employed','expert','deduction','guarantee']) and len(line)>30][:6]
   competitor_notes+='<article><h3>'+esc(source['title'])+'</h3><p>Public landing-page observations, not ad-performance evidence.</p><ul>'+''.join('<li>'+esc(line[:500])+'</li>' for line in lines)+'</ul></article>'
 cards=''.join('<article><p class="eyebrow">UNTESTED CREATIVE HYPOTHESIS</p><h2>'+esc(b['headline'])+'</h2><p>'+esc(b['copy'])+'</p><h3>Visual direction</h3><p>'+esc(b['visual'])+'</p><h3>Evidence</h3><p>'+esc(b['evidence'])+'</p><details><summary>Image generation prompt</summary><pre>'+esc(b['generation_prompt'])+'</pre></details></article>' for b in report.get('briefs',[]))
 searches=''.join(f'<a class="chip" target="_blank" rel="noopener" href="{esc(x["url"])}">{esc(x["term"])} ↗</a>' for x in report.get('competitor_searches',[]))
 refs=''.join('<article><h3>'+esc(x['brand'])+'</h3><p>'+esc(x['copy'])+'</p><p>'+esc(', '.join(x['observations']))+'</p><p>'+esc(x['performance'])+'</p></article>' for x in references or [])
 controls='''<section><h2>Save a competitor reference</h2><p>Open a search above, then save an actual ad here. The tool records structure and claims; conversion performance remains unknown.</p><form id="reference"><label>Brand<input name="brand" required></label><label>Ad Library link<input name="url" type="url" required></label><label>Actual ad copy<textarea name="copy" required></textarea></label><label>Visual notes<textarea name="visual_notes"></textarea></label><button>Save and analyze reference</button></form></section><section><h2>Import your own results</h2><p>Upload an Ads Manager CSV with Ad name, Amount spent (USD), Impressions and optional Link clicks, Website leads, Landing page views columns. Missing leads stay unknown.</p><form id="csv"><input type="file" name="file" accept=".csv" required><button>Analyze results</button></form><pre id="csv-result"></pre></section>'''
 js='''<script>let csrf='';async function api(route,data){const r=await fetch('/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const b=await r.json();if(!r.ok)throw Error(b.error);return b;}fetch('/api/state').then(r=>r.json()).then(s=>{csrf=s.csrf;document.getElementById('live').hidden=false;}).catch(()=>{});function notice(s){document.getElementById('notice').textContent=s;}document.getElementById('reference').onsubmit=async e=>{e.preventDefault();try{await api('reference',Object.fromEntries(new FormData(e.target)));location.reload();}catch(e){notice(e.message);}};document.getElementById('csv').onsubmit=async e=>{e.preventDefault();try{const file=new FormData(e.target).get('file');if(file.size>2000000)throw Error('CSV must be under 2 MB');document.getElementById('csv-result').textContent=JSON.stringify(await api('csv',{csv:await file.text()}),null,2);}catch(e){notice(e.message);}};document.getElementById('refresh').onclick=async()=>{notice('Refreshing verified sources…');try{await api('research',{});location.reload();}catch(e){notice(e.message);}};document.getElementById('meta').onclick=async()=>{notice('Checking Meta research access…');try{const result=await api('meta',{term:'tax preparation',country:'US'});document.getElementById('meta-result').textContent=JSON.stringify(result,null,2);notice('Research response received.');}catch(e){notice(e.message);}};</script>'''
 return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Stellar Mill Ad Research</title><style>body{background:#f5f2eb;color:#24221e;font:16px/1.6 Arial;margin:0}main{max-width:1050px;margin:auto;padding:40px 24px}h1{font:700 64px/1.05 Georgia;margin:15px 0}h2{font:700 30px Georgia}h3{font-size:15px}article,section{background:white;border:1px solid #ded8cc;border-radius:12px;padding:25px;margin:20px 0}.eyebrow{color:#997011;font-size:12px;letter-spacing:2px;font-weight:bold}.chip,button{display:inline-block;padding:12px 18px;background:#e6bc50;border:0;border-radius:7px;color:#272014;margin:5px;text-decoration:none;font-weight:bold}input,textarea{display:block;box-sizing:border-box;width:100%;padding:12px;margin:8px 0 15px;border:1px solid #cfc7b6;border-radius:6px}textarea{min-height:100px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#805b12}#notice{position:sticky;top:0;background:#fff0c5;padding:10px}footer{font-size:13px;color:#696357}[hidden]{display:none!important}@media(max-width:600px){h1{font-size:44px}}</style><main><p class="eyebrow">STELLAR MILL · AD RESEARCH AGENT</p><h1>Research first.<br>Then create. Then test.</h1><p>Actual sources, original creative briefs, and measured results. No invented “winning ad” claims.</p><p>Updated: '''+esc(report.get('generated_at',''))+'''</p><div id="notice" role="status"></div><section><h2>Verified sources</h2><ul>'''+sources+'''</ul></section>'''+competitor_notes+'''<section><h2>Find competitor static ads</h2><p>These are real Meta Ad Library search links, not collected or analyzed ad results. Public access and login requirements vary.</p>'''+searches+'''</section>'''+cards+'''<div id="live" hidden><button id="refresh">Refresh source research</button><button id="meta">Try Meta competitor API</button><pre id="meta-result"></pre>'''+controls+'''</div><section><h2>Saved references</h2>'''+(refs or '<p>No references imported yet.</p>')+'''</section><section><h2>What we can and cannot conclude</h2><ul>'''+''.join('<li>'+esc(x)+'</li>' for x in report.get('limitations',[]))+'''</ul></section><footer>This tool makes read-only research requests. It never creates or launches ads and never spends ad budget. Research data stays on this machine.</footer></main>'''+js+'''</html>'''

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def response(self,status,data,kind='application/json'):
  raw=data.encode() if isinstance(data,str) else json.dumps(data).encode()
  self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  if self.path=='/api/state':return self.response(200,{'csrf':self.server.csrf,'meta_token_present':bool(os.environ.get('META_ACCESS_TOKEN'))})
  if self.path!='/':return self.response(404,{'error':'Not found'})
  return self.response(200,render(load(ROOT/'research/latest.json',{}),load(ROOT/'.data/references.json',[])),'text/html')
 def do_POST(self):
  try:
   if self.headers.get('Origin') not in (None,'http://'+self.headers.get('Host','')):return self.response(403,{'error':'Origin rejected'})
   if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf):return self.response(403,{'error':'Refresh the page before continuing'})
   length=int(self.headers.get('Content-Length',0))
   if length>2_000_000:raise ValueError('Request exceeds 2 MB')
   data=json.loads(self.rfile.read(length))
   with self.server.write_lock:
    if self.path=='/api/reference':
     item=analyze_reference(data);items=load(ROOT/'.data/references.json',[]);items.append(item);save(ROOT/'.data/references.json',items);return self.response(200,item)
    if self.path=='/api/csv':
     result=summarize_csv(data['csv']);save(ROOT/'.data/metrics.json',result);return self.response(200,result)
    if self.path=='/api/research':
     report=research();save(ROOT/'research/latest.json',report);(ROOT/'research/report.html').write_text(render(report));return self.response(200,{'refreshed':True})
    if self.path=='/api/meta':return self.response(200,competitor_search(data['term'],data.get('country','US')))
   return self.response(404,{'error':'Not found'})
  except (ValueError,KeyError,URLError,TimeoutError) as error:return self.response(400,{'error':str(error)})

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['research','serve','own-ads']);parser.add_argument('--port',type=int,default=3000);parser.add_argument('--account',default='506078191226626');args=parser.parse_args()
 if args.command=='research':
  report=research();save(ROOT/'research/latest.json',report);(ROOT/'research/report.html').write_text(render(report));print(json.dumps({'sources':[{k:s[k] for k in ['title','status']} for s in report['sources']],'brief_count':len(report['briefs']),'report':'research/report.html'},indent=2))
 elif args.command=='own-ads':
  try:result=own_ads(args.account);save(ROOT/'.data/own-ads.json',result);print('Saved authorized account data to .data/own-ads.json')
  except (ValueError,URLError) as error:print(str(error));raise SystemExit(1)
 else:
  server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.csrf=secrets.token_hex(32);server.write_lock=threading.Lock();print('Research agent listening on port '+str(server.server_address[1]),flush=True);server.serve_forever()
if __name__=='__main__':main()
