import asyncio
import time
import os
from .ai_diagnostics import diagnostics,OutputError
from .ai_output import validate_output
from .domain import EvaluationOutput, calculate

async def evaluate(conversation, checklist, provider, attempts=2,max_backoff_seconds=None):
    if type(attempts) is not int or not 1<=attempts<=3:raise ValueError('Evaluation attempts must be between 1 and 3')
    started = time.monotonic()
    if hasattr(provider,'begin'): provider.begin()
    feedback = ''
    usages = []
    costs = []
    failures=[];attempt_errors=[];wait_ms=0
    for attempt in range(attempts):
        try:
            reply = await provider.generate(conversation,checklist,feedback)
            usages.append(reply.usage)
            if reply.cost_usd is not None: costs.append(reply.cost_usd)
            output = EvaluationOutput.model_validate_json(reply.text)
            validate_output(output,checklist,conversation)
            metadata=reply.metadata or {}
            if getattr(provider,'verify_evidence_quotes',False):
                from .multiai import verify_quotes
                for assessment in output.criteria:
                    if len(assessment.evidence_quotes)!=len(assessment.evidence_turns) or {q.turn for q in assessment.evidence_quotes}!=set(assessment.evidence_turns):
                        raise OutputError('missing_evidence_quote',('criteria',assessment.criterion_id,'evidence_quotes'))
                    verify_quotes(assessment.evidence_quotes,conversation)
                if output.applicability!=metadata.get('fact_applicability'):
                    raise OutputError('invalid_na',('applicability',))
            score, points = calculate(output.criteria,checklist)
            return {'status':'ok','criteria':[c.model_dump() for c in output.criteria],'facts':metadata.get('extracted_facts',output.facts),'applicability':output.applicability,'score':score,'points':points,'model':reply.model_id or provider.name,'mock':provider.mock,'attempts':attempt+1,'latency_ms':round((time.monotonic()-started)*1000),'usage':metadata.get('usage',usages),'cost_usd':metadata.get('cost_usd',sum(costs) if costs else None),'provider_metadata':metadata,'validation_failures':failures,'attempt_errors':attempt_errors,'retry_wait_ms':wait_ms}
        except Exception as exc:
            # Never interpolate exception text, input values, unknown keys or Pydantic context.
            from .multiai import ProviderError
            details=diagnostics(exc,getattr(provider,'current_stage','validation'))
            error='MultiAI timeout; reservation retained' if isinstance(exc,ProviderError) and str(exc)=='MultiAI timeout; reservation retained' else details[0]['message']
            feedback='Return a complete valid result. '+ '; '.join(d['category']+':'+'.'.join(map(str,d['path'])) for d in details)[:400]
            item={'attempt':attempt+1,'error':error,'kind':'provider' if isinstance(exc,ProviderError) else 'validation','diagnostics':details}
            attempt_errors.append(item)
            if not isinstance(exc,ProviderError):failures.append(item)
            if getattr(exc,'retryable',True) is False:
                break
            if attempt + 1 < attempts:
                delay=getattr(exc,'retry_after',None)
                delay=delay if delay is not None else min(float(os.getenv('MULTIAI_MAX_BACKOFF_SECONDS','30')) if max_backoff_seconds is None else max_backoff_seconds,0.15*(2**attempt))
                bound=float(os.getenv('MULTIAI_MAX_BACKOFF_SECONDS','30')) if max_backoff_seconds is None else max_backoff_seconds
                if delay>bound:break
                wait_ms+=round(delay*1000)
                await asyncio.sleep(delay)
    metadata=provider.telemetry() if hasattr(provider,'telemetry') else {}
    return {'status':'error','criteria':[],'score':None,'error':error,'model':provider.name,'mock':provider.mock,'attempts':attempt+1,'latency_ms':round((time.monotonic()-started)*1000),'usage':metadata.get('usage',usages),'cost_usd':metadata.get('cost_usd',sum(costs) if costs else None),'provider_metadata':metadata,'validation_failures':failures,'attempt_errors':attempt_errors,'retry_wait_ms':wait_ms}
