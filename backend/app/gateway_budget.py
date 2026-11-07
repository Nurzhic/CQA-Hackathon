"""MultiAI accounting: keep gateway credits distinct from raw usage and USD."""
import os
from decimal import Decimal, ROUND_CEILING, InvalidOperation
from sqlalchemy import update, select
from sqlalchemy.exc import IntegrityError
from .storage import setup, GatewayBudgetRow, GatewayRequestRow, RunBudgetRow

def positive(name,default=None):
    try:
        n=Decimal(os.getenv(name,str(default) if default is not None else ''))
        if not n.is_finite() or n<=0: raise ValueError()
        return n
    except (InvalidOperation,ValueError): raise ValueError(f'Configure positive {name}') from None

def credit_estimate(input_bytes,output_tokens,model):
    try:
        rates=[Decimal(str(model[k])) for k in ('input_multiplier','output_multiplier')]
        if any(not n.is_finite() or n<0 for n in rates):raise ValueError()
    except (KeyError,InvalidOperation,ValueError):raise ValueError('MultiAI model billing multipliers are unavailable') from None
    return int(((input_bytes+4096)*rates[0]+output_tokens*rates[1]).to_integral_value(rounding=ROUND_CEILING))

def estimate(input_bytes,output_tokens,model):
    credits=credit_estimate(input_bytes,output_tokens,model)
    rate=os.getenv('MULTIAI_USD_PER_MILLION_CREDITS','')
    usd=float(Decimal(credits)*positive('MULTIAI_USD_PER_MILLION_CREDITS')/1_000_000) if rate else None
    return {'estimated_credits_upper':credits,'estimated_usd_upper':usd,'usd_conversion_verified':os.getenv('MULTIAI_PRICING_CONFIRMED','false').lower()=='true','pricing_basis':'MultiAI catalog multipliers; UTF-8 input bound plus 4096 overhead; no cache discount','tariff_revision':model.get('tariff_revision')}

def reserve(model,stage,quote,run_id=None):
    if not quote['usd_conversion_verified'] or quote['estimated_usd_upper'] is None:
        raise ValueError('USD conversion is unverified; configure MULTIAI_USD_PER_MILLION_CREDITS and MULTIAI_PRICING_CONFIRMED before paid calls')
    limit=positive('API_BUDGET_USD','10')
    if limit>10:raise ValueError('API_BUDGET_USD cannot exceed $10')
    max_requests=int(positive('MULTIAI_MAX_REQUESTS','12'))
    credits=quote['estimated_credits_upper']
    usd=int((Decimal(str(quote['estimated_usd_upper']))*1_000_000).to_integral_value(rounding=ROUND_CEILING))
    factory=setup()
    with factory() as s:
        if not s.get(GatewayBudgetRow,1):
            try:s.add(GatewayBudgetRow(id=1));s.commit()
            except IntegrityError:s.rollback()
        result=s.execute(update(GatewayBudgetRow).where(GatewayBudgetRow.id==1,GatewayBudgetRow.requests<max_requests,GatewayBudgetRow.reserved_microusd+usd<=int(limit*1_000_000)).values(requests=GatewayBudgetRow.requests+1,reserved_microusd=GatewayBudgetRow.reserved_microusd+usd,reserved_credits=GatewayBudgetRow.reserved_credits+credits))
        if result.rowcount!=1:raise ValueError('MultiAI request/budget limit reached; no request sent')
        if run_id is not None:
            scoped=s.execute(update(RunBudgetRow).where(RunBudgetRow.id==run_id,RunBudgetRow.requests<RunBudgetRow.request_limit,RunBudgetRow.reserved_microusd+usd<=RunBudgetRow.limit_microusd).values(requests=RunBudgetRow.requests+1,reserved_microusd=RunBudgetRow.reserved_microusd+usd))
            if scoped.rowcount!=1:raise ValueError('Approved run request/budget limit reached; no request sent')
        row=GatewayRequestRow(model=model,stage=stage,payload={'reservation':quote,'run_id':run_id});s.add(row);s.commit();return row.id

def finish(request_id,status,payload):
    with setup()() as s:
        row=s.get(GatewayRequestRow,request_id)
        row.status=status;row.payload={**row.payload,**payload};s.commit()

def stats(factory=None):
    with (factory or setup())() as s:
        budget=s.get(GatewayBudgetRow,1)
        rows=list(s.scalars(select(GatewayRequestRow).order_by(GatewayRequestRow.id)))
        usage={};costs=[];billed=[]
        for r in rows:
            for k,v in r.payload.get('usage',{}).items():
                if type(v) in (int,float):usage[k]=usage.get(k,0)+v
            if r.payload.get('cost_usd') is not None:costs.append(r.payload['cost_usd'])
            if r.payload.get('billed_credits') is not None:billed.append(r.payload['billed_credits'])
        return {'request_count':budget.requests if budget else 0,'request_limit':int(positive('MULTIAI_MAX_REQUESTS','12')),'reserved_credits':budget.reserved_credits if budget else 0,'reserved_usd':budget.reserved_microusd/1_000_000 if budget else 0,'limit_usd':float(positive('API_BUDGET_USD','10')),'usage':usage,'reported_cost_usd':sum(costs) if costs else None,'reported_cost_coverage':len(costs),'billed_credits':sum(billed) if billed else None,'statuses':{status:sum(r.status==status for r in rows) for status in ('pending','ok','error')},'pricing_confirmed':os.getenv('MULTIAI_PRICING_CONFIRMED','false').lower()=='true'}
