"""Conservative durable budget reservations; errors retain reservations."""
import os
from decimal import Decimal, ROUND_CEILING
from sqlalchemy import update
from .storage import setup, BudgetRow

def reserve(prompt_bytes: int, output_tokens: int):
    input_rate=os.getenv('INPUT_USD_PER_MILLION')
    output_rate=os.getenv('OUTPUT_USD_PER_MILLION')
    if not input_rate or not output_rate:
        raise ValueError('Set verified INPUT_USD_PER_MILLION and OUTPUT_USD_PER_MILLION before external calls')
    rates=[Decimal(input_rate),Decimal(output_rate)]
    if any(not r.is_finite() or r<=0 for r in rates): raise ValueError('Token prices must be positive finite USD rates')
    # UTF-8 byte count bounds the content token count conservatively. Add an overhead margin.
    amount=int(((prompt_bytes+4096)*rates[0]+output_tokens*rates[1]).to_integral_value(rounding=ROUND_CEILING))
    limit=Decimal(os.getenv('API_BUDGET_USD','10'))
    if not limit.is_finite() or limit<=0 or limit>10: raise ValueError('Budget must be > 0 and <= $10')
    factory=setup()
    with factory() as s:
        if not s.get(BudgetRow,1): s.add(BudgetRow(id=1,reserved_microdollars=0));s.commit()
        result=s.execute(update(BudgetRow).where(BudgetRow.id==1,BudgetRow.reserved_microdollars+amount<=int(limit*1_000_000)).values(reserved_microdollars=BudgetRow.reserved_microdollars+amount))
        if result.rowcount!=1: raise ValueError('Budget reservation would exceed the configured cap (maximum $10)')
        s.commit()
    return amount/1_000_000

def usage_cost(usage,kind):
    input_tokens=usage.get('prompt_tokens',0) if kind=='openai' else usage.get('promptTokenCount',0)
    output_tokens=usage.get('completion_tokens',0) if kind=='openai' else usage.get('candidatesTokenCount',0)+usage.get('thoughtsTokenCount',0)
    return float((Decimal(input_tokens)*Decimal(os.environ['INPUT_USD_PER_MILLION'])+Decimal(output_tokens)*Decimal(os.environ['OUTPUT_USD_PER_MILLION']))/1_000_000)
