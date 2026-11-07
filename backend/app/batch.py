"""Offline plans and explicitly approved bounded runs. Creating a plan never calls an API."""
import asyncio
import json
import math
import os
import uuid
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from pydantic import Field,StrictInt
from sqlalchemy import select,update
from .domain import StrictModel,Conversation
from .checklist import OFFICIAL
from .storage import ConversationRow,EvaluationRow,BenchmarkRunRow,RunBudgetRow,GatewayBudgetRow,GatewayRequestRow
from .benchmark_suite import ROOT,load,digest,model_input
from .annotations import context
from .benchmark_report import pipeline_digest
from .multiai import configuration,request_body,FACT_SYSTEM,SYSTEM,fact_schema,assessment_schema,prompt,MultiAIProvider,discover,connection_status
from .gateway_budget import estimate,stats
from .engine import evaluate
from .exporter import validate_export

class PlanOptions(StrictModel):
    mode: Literal['preflight','benchmark','prod']='preflight'
    model: str | None=None
    concurrency: StrictInt=Field(default=1,ge=1,le=4)
    timeout_seconds: StrictInt=Field(default=90,ge=15,le=300)
    max_attempts: StrictInt=Field(default=2,ge=1,le=3)
    max_backoff_seconds: StrictInt=Field(default=30,ge=0,le=60)
    max_output_tokens: StrictInt=Field(default=6000,ge=512,le=12000)
    response_format: Literal['json_schema','prompt']='json_schema'
    deadline_seconds: StrictInt=Field(default=3300,ge=60,le=3600)
    limit_usd: Decimal=Field(default=Decimal('0.50'),gt=0,le=10)

class Approval(StrictModel):
    approved: bool=False
    plan_sha256: str
    evaluation_count: StrictInt
    limit_usd: Decimal=Field(gt=0,le=10)

CATALOG=ROOT/'data/multiai-models.json'

def cached_catalog():
    if not CATALOG.exists():raise ValueError('No cached catalog. Read-only connection discovery must be saved before planning; no prices guessed')
    b=json.loads(CATALOG.read_text());return b['data'] if isinstance(b,dict) else b

def pipeline_options(options):
    return {k:getattr(options,k) for k in ('max_output_tokens','response_format','max_attempts','timeout_seconds','max_backoff_seconds')}

def estimate_one(c,meta,options):
    normalized=json.loads(prompt(model_input(c),OFFICIAL,''))
    bodies=[request_body(meta['id'],FACT_SYSTEM,{**normalized,'validation_feedback':'x'*512},fact_schema(OFFICIAL),options.response_format,options.max_output_tokens),request_body(meta['id'],SYSTEM,{**normalized,'verified_facts':'x'*(options.max_output_tokens*12),'validation_feedback':'x'*512},assessment_schema(OFFICIAL),options.response_format,options.max_output_tokens)]
    quotes=[estimate(len(json.dumps(b,ensure_ascii=False).encode()),options.max_output_tokens,meta) for b in bodies]
    return {'estimated_credits_upper':sum(q['estimated_credits_upper'] for q in quotes)*options.max_attempts,'estimated_usd_upper':sum(q['estimated_usd_upper'] for q in quotes)*options.max_attempts if all(q['estimated_usd_upper'] is not None for q in quotes) and all(q['usd_conversion_verified'] for q in quotes) else None,'requests_expected':2,'requests_max':2*options.max_attempts}

def catalog_signature(meta):return {k:meta.get(k) for k in ('id','availability','model_kind','input_multiplier','output_multiplier','tariff_revision','catalog_revision')}

def create_plan(s,options:PlanOptions,catalog=None):
    config=configuration();models=[config['primary_model'],config['secondary_model']]
    if len(set(models))!=2 or not all(models):raise ValueError('Two exact configured model IDs are required')
    if options.mode=='prod':
        if options.deadline_seconds>3300:raise ValueError('PROD processing deadline must leave 5 minutes before the one-hour limit')
        if options.model not in models:raise ValueError('PROD requires one explicit configured model ID')
        models=[options.model]
        rows=list(s.scalars(select(ConversationRow).where(ConversationRow.dataset=='PROD').order_by(ConversationRow.id)))
        if len(rows)!=10:raise ValueError('PROD plan blocked: import exactly 10 genuine control chats first')
        cases={r.id:Conversation.model_validate(r.payload) for r in rows};manifest=None
    else:
        manifest,all_cases=load(s)
        ids=manifest['preflight_ids'] if options.mode=='preflight' else list(all_cases)
        cases={cid:all_cases[cid] for cid in ids}
    if any(c.channel!='agent chat' for c in cases.values()):raise ValueError('Batch scope excludes real calls')
    catalog=cached_catalog() if catalog is None else catalog
    metas={m:next((x for x in catalog if x.get('id')==m),None) for m in models}
    if any(x is None or x.get('availability')!='available' or x.get('model_kind','text')!='text' for x in metas.values()):raise ValueError('A requested model is unavailable in the catalog; no substitution')
    pipeline=pipeline_digest(pipeline_options(options));jobs=[]
    for model in models:
        for cid,c in cases.items():
            ctx=context(manifest,c) if manifest else {'suite_id':'PROD','manifest_sha256':digest(sorted(cases)),'conversation_sha256':digest(c.model_dump()),'checklist_sha256':digest(OFFICIAL.model_dump())}
            jobs.append({'conversation_id':cid,'model':model,'language':c.language,'context':{**ctx,'pipeline_sha256':pipeline},**estimate_one(c,metas[model],options)})
    usd=sum(j['estimated_usd_upper'] for j in jobs) if all(j['estimated_usd_upper'] is not None for j in jobs) else None
    waves=math.ceil(len(jobs)/options.concurrency)
    worst=waves*(options.max_attempts*2*options.timeout_seconds+(options.max_attempts-1)*options.max_backoff_seconds)+60
    current=stats()
    pricing={'confirmed':os.getenv('MULTIAI_PRICING_CONFIRMED','false').lower()=='true','usd_per_million_credits':os.getenv('MULTIAI_USD_PER_MILLION_CREDITS') or None,'rub_per_million_credits':os.getenv('MULTIAI_RUB_PER_MILLION_CREDITS') or None,'fx_date':os.getenv('MULTIAI_FX_DATE') or None}
    plan={'schema_version':'1','nonce':uuid.uuid4().hex,'mode':options.mode,'options':options.model_dump(mode='json'),'jobs':jobs,'evaluation_count':len(jobs),'dialogue_count':len(cases),'models':models,'catalog':{m:catalog_signature(meta) for m,meta in metas.items()},'pipeline_sha256':pipeline,'pricing':pricing,'estimated_credits_upper':sum(j['estimated_credits_upper'] for j in jobs),'estimated_usd_upper':usd,'provider_requests_expected':len(jobs)*2,'provider_requests_max':len(jobs)*2*options.max_attempts,'deadline_bound_seconds':worst,'fits_deadline':worst<=options.deadline_seconds,'paid_calls_enabled':os.getenv('ALLOW_PAID_CALLS','false').lower()=='true','required_total_request_limit':current['request_count']+len(jobs)*2*options.max_attempts,'existing_reserved_usd':current['reserved_usd'],'catalog_snapshot':'cached, revalidated before execution','warning':'6 evaluations are 12 expected HTTP generations (two stages), not 6 HTTP requests. Credit estimates are conservative reserves, not an invoice. Execution needs a new explicit plan/count/USD approval.'}
    sha=digest(plan);row=BenchmarkRunRow(status='planned',payload={'plan':plan,'plan_sha256':sha});s.add(row);s.commit()
    return {'id':row.id,'plan_sha256':sha,**plan}

def validate_run(s,row,approval):
    plan=row.payload['plan'];options=PlanOptions.model_validate(plan['options'])
    if row.status!='planned':raise ValueError('Plan already consumed; automatic reruns are forbidden')
    if not approval.approved or approval.plan_sha256!=row.payload['plan_sha256'] or digest(plan)!=approval.plan_sha256:raise ValueError('Explicit approval must match the immutable plan digest')
    if approval.evaluation_count!=plan['evaluation_count'] or approval.limit_usd!=options.limit_usd:raise ValueError('Approval count/budget does not match the plan')
    if os.getenv('ALLOW_PAID_CALLS','false').lower()!='true' or os.getenv('MULTIAI_BATCH_APPROVED_PLAN')!=approval.plan_sha256:raise ValueError('Paid execution disabled: separate user approval and plan-bound server authorization are required')
    if plan['estimated_usd_upper'] is None or not plan['pricing']['confirmed'] or Decimal(str(plan['estimated_usd_upper']))>approval.limit_usd:raise ValueError('Verified conservative cost estimate must fit approved USD budget')
    if not plan['fits_deadline']:raise ValueError('Worst-case timeout/retry bound exceeds deadline; increase approved concurrency')
    if pipeline_digest(pipeline_options(options))!=plan['pipeline_sha256']:raise ValueError('Pipeline changed after planning; create and approve a new plan')
    if os.getenv('MULTIAI_PRICING_CONFIRMED','false').lower()!='true' or Decimal(os.getenv('MULTIAI_USD_PER_MILLION_CREDITS','0'))!=Decimal(plan['pricing']['usd_per_million_credits']):raise ValueError('Pricing confirmation changed after plan approval')
    current=stats()
    if current['request_limit']-current['request_count']<plan['provider_requests_max']:raise ValueError('Global request limit has insufficient room for the approved retry bound')
    if current['reserved_usd']+plan['estimated_usd_upper']>current['limit_usd']:raise ValueError('Global USD reserve has insufficient room')
    configured=configuration()
    if not set(plan['models'])<={configured['primary_model'],configured['secondary_model']}:raise ValueError('Configured model IDs changed after planning')
    cases={}
    for job in plan['jobs']:
        c=s.get(ConversationRow,job['conversation_id'])
        if not c or digest(Conversation.model_validate(c.payload).model_dump())!=job['context']['conversation_sha256']:raise ValueError('Planned transcript changed')
        if c.dataset!=('PROD' if options.mode=='prod' else 'BENCHMARK' if c.id.startswith('cqa-synthetic-') else 'DEV'):raise ValueError('Planned dataset provenance changed')
        cases[c.id]=model_input(Conversation.model_validate(c.payload))
    if options.mode!='prod':
        manifest,_=load(s)
        if any(j['context']['manifest_sha256']!=digest(manifest) for j in plan['jobs']):raise ValueError('Benchmark manifest changed after planning')
    if options.mode=='prod':
        actual={r.id for r in s.scalars(select(ConversationRow).where(ConversationRow.dataset=='PROD'))}
        if actual!=set(cases) or len(cases)!=10:raise ValueError('PROD set changed after approval')
    return plan,options,cases

async def execute(factory,plan_id,approval:Approval):
    with factory() as s:
        row=s.get(BenchmarkRunRow,plan_id)
        if not row:raise ValueError('Plan not found')
        plan,options,cases=validate_run(s,row,approval)
    # Read-only checks occur only after explicit approval; no model prompts contain labels.
    live=await discover()
    for model,expected in plan['catalog'].items():
        actual=next((m for m in live if m['id']==model),None)
        if actual is None or catalog_signature(actual)!=expected:raise ValueError('Live catalog/tariff changed; re-plan and re-approve before spending')
    baseline=await connection_status()
    start_counter=baseline['key_limits'].get('tokens_spent')
    with factory() as s:
        changed=s.execute(update(BenchmarkRunRow).where(BenchmarkRunRow.id==plan_id,BenchmarkRunRow.status=='planned').values(status='running'))
        if changed.rowcount!=1:raise ValueError('Plan was already started')
        s.add(RunBudgetRow(id=plan_id,requests=0,request_limit=plan['provider_requests_max'],reserved_microusd=0,limit_microusd=int(approval.limit_usd*1_000_000)));s.commit()
    started=asyncio.get_running_loop().time();sem=asyncio.Semaphore(options.concurrency)
    async def worker(job):
        async with sem:
            remaining=options.deadline_seconds-(asyncio.get_running_loop().time()-started)
            result=None;provider=None
            job_started=asyncio.get_running_loop().time()
            try:
                if remaining<=0:raise asyncio.TimeoutError()
                provider=MultiAIProvider(job['model'],timeout_seconds=options.timeout_seconds,max_output_tokens=options.max_output_tokens,response_format=options.response_format,run_id=plan_id,max_calls=2*options.max_attempts,max_backoff_seconds=options.max_backoff_seconds)
                # Strict input reconstruction is the label leakage boundary.
                result=await asyncio.wait_for(evaluate(model_input(cases[job['conversation_id']]),OFFICIAL,provider,attempts=options.max_attempts,max_backoff_seconds=options.max_backoff_seconds),remaining)
            except asyncio.TimeoutError:result={'status':'error','criteria':[],'score':None,'error':'Approved batch deadline exceeded; processing/billing may be unknown for a cancelled request','model':job['model'],'mock':False}
            except Exception:result={'status':'error','criteria':[],'score':None,'error':'Batch evaluation failed; inspect safe request audit','model':job['model'],'mock':False}
            if 'provider_metadata' not in result and provider is not None:
                meta=provider.telemetry();result={**result,'provider_metadata':meta,'usage':meta['usage'],'cost_usd':meta['cost_usd'],'attempts_unknown':True}
            result.setdefault('latency_ms',round((asyncio.get_running_loop().time()-job_started)*1000))
            result={**result,'benchmark_context':job['context'],'batch_run_id':plan_id}
            with factory() as s:
                e=EvaluationRow(conversation_id=job['conversation_id'],checklist_id=1,payload=result);s.add(e);s.commit()
                return {'evaluation_id':e.id,'conversation_id':job['conversation_id'],'model':job['model'],'status':result['status']}
    results=await asyncio.gather(*(worker(j) for j in plan['jobs']))
    delta=None;counter_error=None
    try:
        final=await connection_status();end_counter=final['key_limits'].get('tokens_spent')
        if type(start_counter) in (int,float) and type(end_counter) in (int,float) and end_counter>=start_counter:delta=end_counter-start_counter
    except Exception:counter_error='Read-only counter unavailable; no provider credit delta or inferred cost reported'
    cost=float(Decimal(str(delta))*Decimal(plan['pricing']['usd_per_million_credits'])/1_000_000) if delta is not None and plan['pricing']['confirmed'] else None
    with factory() as s:
        pending=sum(r.status=='pending' and r.payload.get('run_id')==plan_id for r in s.scalars(select(GatewayRequestRow)))
    outcome={'pending_billing_requests':pending,'billing_complete':pending==0,'results':results,'provider_credit_delta':delta,'converted_cost_usd':cost,'credit_delta_scope':'whole API-key counter window; cannot attribute to a model with concurrent requests or exclude outside key activity','counter_error':counter_error,'latency_ms':round((asyncio.get_running_loop().time()-started)*1000),'pricing':plan['pricing']}
    with factory() as s:
        row=s.get(BenchmarkRunRow,plan_id);row.status='complete';row.payload={**row.payload,'outcome':outcome};s.commit()
        if options.mode=='prod':
            export_prod(s,plan_id)
            row.payload={**row.payload,'export_path':'results/prod/results.json'};s.commit()
    return {'id':plan_id,'status':'complete',**outcome}

def export_prod(s,run_id,output=None):
    run=s.get(BenchmarkRunRow,run_id)
    if not run or run.status!='complete' or run.payload['plan']['mode']!='prod':raise ValueError('Completed genuine PROD run required')
    plan=run.payload['plan'];results=[]
    for item in run.payload['outcome']['results']:
        e=s.get(EvaluationRow,item['evaluation_id'])
        if not e or e.payload.get('batch_run_id')!=run_id or e.payload.get('mock') or e.checklist_id!=1:raise ValueError('Invalid PROD evaluation provenance')
        p=e.payload
        if p['status']=='ok':results.append({'conversation_id':e.conversation_id,'status':'ok','criteria':[{k:a[k] for k in ('criterion_id','value','rationale','evidence_turns')} for a in p['criteria']],'score':p['score']})
        else:results.append({'conversation_id':e.conversation_id,'status':'error','criteria':[],'score':None,'error':p.get('error','Evaluation failed')})
    cases=[Conversation.model_validate(s.get(ConversationRow,j['conversation_id']).payload) for j in plan['jobs']]
    payload={'schema_version':'1.0','team_name':os.getenv('TEAM_NAME','CQA'),'model':{'mode':'external_api','name':plan['models'][0]},'results':results}
    validate_export(payload,cases,'PROD')
    path=Path(output or ROOT/'results/prod/results.json');path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
    return payload
