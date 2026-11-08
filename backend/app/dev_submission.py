"""Explicitly approved, resumable DEV-only submission runner using the existing AI pipeline."""
import asyncio,json,os,time
from pathlib import Path
from decimal import Decimal
from dotenv import load_dotenv
from sqlalchemy import select
from .storage import setup,ConversationRow,EvaluationRow,BenchmarkRunRow,RunBudgetRow
from .domain import Conversation
from .checklist import OFFICIAL
from .batch import PlanOptions,estimate_one,catalog_signature,pipeline_options
from .benchmark_suite import digest,model_input
from .benchmark_report import pipeline_digest
from .multiai import connection_status,MultiAIProvider
from .gateway_budget import stats
from .engine import evaluate
from .exporter import validate_export

async def run():
 load_dotenv()
 if os.getenv('CQA_DEV_APPROVED')!='62:2.00:248':raise ValueError('Explicit DEV authorization required')
 factory=setup();saved=json.loads(Path('data/dev-emergency-plan.json').read_text())
 assert digest({k:v for k,v in saved.items() if k!='plan_sha256'})==saved['plan_sha256']
 options=PlanOptions.model_validate({**saved['options'],'limit_usd':'2.00'})
 assert pipeline_digest(pipeline_options(options))==saved['pipeline_sha256']
 live=await connection_status();meta=next(m for m in live['models'] if m['id']==saved['model'])
 assert catalog_signature(meta)==saved['catalog'],'Tariff changed'
 assert os.getenv('MULTIAI_PRICING_CONFIRMED','false').lower()=='true'
 assert live['billing_mode']=='tokens'
 assert Decimal(str(live['key_limits']['limit_remaining']))>=saved['estimated_credits_upper'],'Insufficient key limit'
 assert Decimal(str(live['account_balance']['tokens']))>=saved['estimated_credits_upper'],'Insufficient wallet'
 assert saved['evaluation_count']==62 and saved['http_max']==248 and saved['estimated_usd_upper']<=2
 with factory() as s:
  cases={r.id:Conversation.model_validate(r.payload) for r in s.scalars(select(ConversationRow).where(ConversationRow.dataset=='DEV'))}
  assert len(cases)==65 and s.get(__import__('backend.app.storage',fromlist=['ChecklistRow']).ChecklistRow,1).payload==OFFICIAL.model_dump()
  for j in saved['jobs']:
   assert digest(cases[j['conversation_id']].model_dump())==j['conversation_sha256']
   assert estimate_one(cases[j['conversation_id']],meta,options)['estimated_credits_upper']==j['estimated_credits_upper']
  assert set(cases)==set(saved['reused'])|{j['conversation_id'] for j in saved['jobs']}
  for cid,eid in saved['reused'].items():
   e=s.get(EvaluationRow,eid);assert e.conversation_id==cid and e.payload['status']=='ok' and not e.payload.get('mock') and e.payload['provider_metadata']['provider']=='MultiAI'
  existing=list(s.scalars(select(BenchmarkRunRow)))
  assert not any(r.payload.get('dev_submission_plan')==saved['plan_sha256'] for r in existing),'Plan already started: inspect saved results, never rerun automatically'
  current=stats(factory)
  assert current['request_count']==saved['required_global_request_limit']-248,'Ledger changed'
  os.environ['MULTIAI_MAX_REQUESTS']=str(current['request_count']+248)
  assert current['reserved_usd']+saved['estimated_usd_upper']<=current['limit_usd']
  row=BenchmarkRunRow(status='running',payload={'dev_submission_plan':saved['plan_sha256'],'approval':{'evaluations':62,'limit_usd':2,'http_max':248},'plan':saved});s.add(row);s.flush();rid=row.id
  s.add(RunBudgetRow(id=rid,requests=0,request_limit=248,reserved_microusd=0,limit_microusd=2_000_000));s.commit()
 os.environ['ALLOW_PAID_CALLS']='true'
 os.environ['MULTIAI_ALLOW_REAL_CALLS']='true'  # Explicitly authorized five DEV call transcripts.
 sem=asyncio.Semaphore(4);started=time.monotonic();done=[]
 print(json.dumps({'started':True,'run_id':rid,'evaluations':62,'concurrency':4,'remaining_credits':live['key_limits']['limit_remaining']}),flush=True)
 async def worker(job):
  async with sem:
   provider=MultiAIProvider(saved['model'],timeout_seconds=90,max_output_tokens=6000,response_format='json_schema',run_id=rid,max_calls=4,max_backoff_seconds=30)
   result=await evaluate(model_input(cases[job['conversation_id']]),OFFICIAL,provider,attempts=2,max_backoff_seconds=30)
   result.update(dev_submission_run_id=rid,source_conversation_sha256=job['conversation_sha256'])
   with factory() as s:
    e=EvaluationRow(conversation_id=job['conversation_id'],checklist_id=1,payload=result);s.add(e);s.commit();item={'conversation_id':job['conversation_id'],'evaluation_id':e.id,'status':result['status']}
   done.append(item);Path('data/dev-emergency-progress.json').write_text(json.dumps({'run_id':rid,'results':done},indent=2))
   print(json.dumps({**item,'done':len(done),'ok':sum(x['status']=='ok' for x in done),'elapsed':round(time.monotonic()-started)}),flush=True)
 await asyncio.gather(*(worker(j) for j in saved['jobs']))
 final=await connection_status();delta=final['key_limits']['tokens_spent']-live['key_limits']['tokens_spent']
 with factory() as s:
  chosen={**saved['reused'],**{i['conversation_id']:i['evaluation_id'] for i in done}};results=[];models=set()
  for cid in sorted(cases):
   e=s.get(EvaluationRow,chosen[cid]);p=e.payload;models.add(p['model'])
   if p['status']=='ok':results.append({'conversation_id':cid,'status':'ok','criteria':[{k:a[k] for k in ('criterion_id','value','rationale','evidence_turns')} for a in p['criteria']],'score':p['score']})
   else:results.append({'conversation_id':cid,'status':'error','criteria':[],'score':None,'error':p.get('error','AI evaluation failed')})
  payload={'schema_version':'1.0','team_name':os.getenv('TEAM_NAME','CQA'),'model':{'mode':'external_api','name':', '.join(sorted(models))},'results':results}
  validate_export(payload,list(cases.values()),'DEV')
  path=Path('results/dev/results.json');tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2));tmp.replace(path)
  outcome={'ok':sum(r['status']=='ok' for r in results),'total':65,'new_http_requests':stats(factory)['request_count']-current['request_count'],'provider_credit_delta':delta,'usd':float(Decimal(delta)*Decimal(os.getenv('MULTIAI_USD_PER_MILLION_CREDITS'))/1_000_000),'elapsed_seconds':round(time.monotonic()-started),'results':done}
  row=s.get(BenchmarkRunRow,rid);row.status='complete';row.payload={**row.payload,'outcome':outcome};s.commit()
 Path('data/dev-emergency-outcome.json').write_text(json.dumps(outcome,indent=2));print(json.dumps(outcome),flush=True)
if __name__=='__main__':asyncio.run(run())
