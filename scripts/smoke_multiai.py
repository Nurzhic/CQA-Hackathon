"""One explicitly approved RU-chat test, both configured models; no bulk/calls.
Run from repository root with .venv/bin/python -m scripts.smoke_multiai.
Credentials stay in .env; spend authorization applies only to this process.
"""
import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from datetime import datetime,timezone
from backend.app import main
from backend.app.multiai import configuration,connection_status
from backend.app.gateway_budget import stats
from backend.app.storage import ConversationRow

async def run(args):
    rate=Decimal(args.rub_per_usd);price=Decimal(args.rub_per_million)
    budget=Decimal(args.budget_usd)
    if not rate.is_finite() or rate<=0 or not price.is_finite() or price<=0 or not budget.is_finite() or not 0<budget<=Decimal('0.50'):
        raise ValueError('Positive verified rates and a budget <= $0.50 are required')
    if not args.approved:raise ValueError('This tool requires explicit user approval before --approved')
    config=configuration();models=[config['primary_model'],config['secondary_model']]
    if len(set(models))!=2 or not all(models):raise ValueError('Two distinct configured models required')
    with main.Session() as s:
        c=s.get(ConversationRow,args.conversation)
        if not c or c.dataset!='DEV' or c.payload['channel']!='agent chat' or c.payload['language']!='RU':
            raise ValueError('Approved smoke scope: one synthetic DEV RU chat only')
    before=stats()
    if before['request_count']!=0 and not args.resume:
        raise ValueError('Prior requests exist. Inspect ledger before explicitly resuming a test')
    # Keep .env and other processes locked. No persistent ALLOW_PAID_CALLS change.
    os.environ.update({'ALLOW_PAID_CALLS':'true','API_BUDGET_USD':str(budget),'MULTIAI_USD_PER_MILLION_CREDITS':str(price/rate),'MULTIAI_PRICING_CONFIRMED':'true','MULTIAI_MAX_BATCH':'1','MULTIAI_ALLOW_BULK':'false','MULTIAI_ALLOW_REAL_CALLS':'false','MULTIAI_MAX_REQUESTS':str(before['request_count']+8)})
    quotes=[]
    for model in models:
        quotes.append(await main.estimate_evaluation(main.EvaluateBody(conversation_ids=[args.conversation],provider='multiai',model=model)))
    total=sum(Decimal(str(q['estimated_usd_upper'])) for q in quotes)
    if total+Decimal(str(before['reserved_usd']))>budget:raise ValueError('Combined conservative reservation exceeds the approved budget')
    report={'scope':{'conversation_id':args.conversation,'models':models,'language':'RU','channel':'agent chat'},'started':datetime.now(timezone.utc).isoformat(),'pricing':{'rub_per_million_credits':str(price),'rub_per_usd':str(rate),'usd_per_million_credits':str(price/rate),'source':args.rate_source,'approved_budget_usd':str(budget)},'preflight':quotes,'evaluations':[]}
    out=Path(args.report);out.parent.mkdir(parents=True,exist_ok=True)
    def save():out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    baseline=await connection_status()
    initial_spent=baseline['key_limits'].get('tokens_spent')
    report['billing_mode']=baseline['billing_mode'];save()
    print(json.dumps({'event':'preflight','estimated_usd_upper':float(total),'estimated_rub_upper':float(total*rate),'max_requests':8}),flush=True)
    try:
        for model in models:
            print(json.dumps({'event':'start','model':model}),flush=True)
            rows=await main.evaluate_batch(main.EvaluateBody(conversation_ids=[args.conversation],provider='multiai',model=model,checklist_id=1,confirm_cost=True))
            e=rows[0]
            summary={k:e.get(k) for k in ('id','model','status','score','error','latency_ms','attempts','usage','cost_usd','provider_metadata')}
            summary['criteria']=[{k:a[k] for k in ('criterion_id','value','evidence_turns')} for a in e.get('criteria',[])]
            report['evaluations'].append(summary);save()
            print(json.dumps({'event':'result',**{k:summary[k] for k in ('id','model','status','score','error','latency_ms','attempts')}}),flush=True)
        final=await connection_status()
        final_spent=final['key_limits'].get('tokens_spent')
        delta=final_spent-initial_spent if type(initial_spent) in (int,float) and type(final_spent) in (int,float) else None
        report['key_counter_delta_credits']=delta
        report['key_counter_delta_rub']=float(Decimal(str(delta))*price/1_000_000) if delta is not None else None
        report['key_counter_delta_usd']=float(Decimal(str(delta))*price/rate/1_000_000) if delta is not None else None
        report['ledger_after']=stats();report['finished']=datetime.now(timezone.utc).isoformat();save()
        print(json.dumps({'event':'finished','key_counter_delta_credits':delta,'key_counter_delta_rub':report['key_counter_delta_rub'],'key_counter_delta_usd':report['key_counter_delta_usd'],'request_count':report['ledger_after']['request_count'],'reserved_usd':report['ledger_after']['reserved_usd']}),flush=True)
    finally:
        os.environ['ALLOW_PAID_CALLS']='false'
        report['authorization_closed']=True;save()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--approved',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--conversation',default='p1-ru-001');p.add_argument('--rub-per-million',required=True);p.add_argument('--rub-per-usd',required=True);p.add_argument('--budget-usd',default='0.50');p.add_argument('--rate-source',required=True);p.add_argument('--report',default='data/multiai-approved-smoke.json')
    asyncio.run(run(p.parse_args()))
