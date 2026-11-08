"""MultiAI gateway. Discovery is free/read-only; generation requires explicit gates."""
import json
import asyncio
import os
import time
import math
from urllib.parse import urlsplit
from email.utils import parsedate_to_datetime
from datetime import datetime,timezone
import httpx
from pydantic import Field, StrictBool, StrictInt
from .domain import StrictModel, Checklist, Conversation
from .providers import ProviderReply, SYSTEM, prompt
from . import gateway_budget
from .ai_diagnostics import OutputError,diagnostics
from . import ai_output


def parse_retry_after(value):
    if not value:return None
    try:
        seconds=float(value)
        if not math.isfinite(seconds):return None
        return max(0,seconds)
    except ValueError:
        try:return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
        except (TypeError,ValueError,OverflowError):return None

class ProviderError(ValueError):
    is_provider_error=True
    def __init__(self,message,retryable=False,retry_after=None,category="provider_error",path=()):
        super().__init__(message);self.retryable=retryable;self.retry_after=retry_after;self.category=category;self.path=path

class FactQuote(StrictModel):
    turn: StrictInt
    observation: str = Field(min_length=1)

class FactReport(StrictModel):
    facts: dict[str,str]
    applicability: dict[str,StrictBool]
    evidence: list[FactQuote]

def configuration():
    return {'base_url':os.getenv('MULTIAI_BASE_URL','https://multiai.store/v1').rstrip('/'),'primary_model':os.getenv('MULTIAI_PRIMARY_MODEL') or os.getenv('MULTIAI_MODEL_1',''),'secondary_model':os.getenv('MULTIAI_SECONDARY_MODEL') or os.getenv('MULTIAI_MODEL_2','')}

def base_url():
    value=configuration()['base_url'];u=urlsplit(value)
    if u.scheme!='https' or u.hostname not in ('multiai.store','www.multiai.store') or u.username or u.password or u.query or u.fragment or u.path.rstrip('/')!='/v1' or u.port not in (None,443):
        raise ValueError('MULTIAI_BASE_URL must be the verified https://multiai.store/v1 gateway')
    return value

def headers():
    key=os.getenv('MULTIAI_API_KEY','')
    if not key:raise ProviderError('MULTIAI_API_KEY is not configured')
    return {'Authorization':'Bearer '+key,'Content-Type':'application/json'}

def safe_usage(value):
    if not isinstance(value,dict):return {}
    return {k:v if type(v) in (int,float) and math.isfinite(v) and v>=0 else safe_usage(v) for k,v in value.items() if (type(v) in (int,float) and math.isfinite(v) and v>=0) or isinstance(v,dict)}

def http_error(status,body=None):
    labels={401:'authentication failed',403:'key or plan access denied',402:'insufficient balance or quota',404:'model or endpoint unavailable',429:'rate limit reached'}
    return ProviderError('MultiAI: '+labels.get(status,f'HTTP {status}'),retryable=status==429 or status>=500)

async def discover():
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            r=await client.get(base_url()+'/models',headers=headers())
        if r.status_code!=200:raise http_error(r.status_code)
        body=r.json()
        if not isinstance(body,dict) or not isinstance(body.get('data'),list):raise ValueError()
        allowed={'id','availability','model_kind','input_multiplier','output_multiplier','cached_input_multiplier','tariff_revision','catalog_revision','context_window','max_output_tokens'}
        return [{k:v for k,v in m.items() if k in allowed} for m in body['data'] if isinstance(m,dict) and isinstance(m.get('id'),str)]
    except httpx.TimeoutException:raise ProviderError('MultiAI discovery timeout',True) from None
    except httpx.HTTPError:raise ProviderError('MultiAI discovery connection failed',True) from None
    except (json.JSONDecodeError,TypeError):raise ProviderError('MultiAI returned an invalid model catalog') from None

async def connection_status():
    models=await discover()
    try:
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:r=await client.get(base_url()+'/subscription/limits',headers=headers())
        if r.status_code!=200:raise http_error(r.status_code)
        b=r.json()
        return {'connected':True,'models':models,'configured':configuration(),'billing_mode':b.get('billing_mode'),'account_balance':b.get('account_balance',{}),'key_limits':{k:v for k,v in b.get('api_key',{}).items() if k in ('tokens_spent','token_limit','limit_remaining','unlimited')},'generation_verified':False,'structured_output_verified':False}
    except httpx.HTTPError:raise ProviderError('MultiAI limits connection failed') from None

FACT_KEYS=['request','greeting','identification','protected_action','completeness','empathy','closure','critical','language']
FACT_SYSTEM=SYSTEM+'''\nSTAGE 1: extract observations before rating. Return concise facts, applicability for every supplied criterion (true=applicable), and evidence {turn,observation}. Select one-based original turn numbers yourself. Do not copy quotations: the backend extracts the exact original text. Use only existing turns; selected turns must actually support your observations. No ratings or total score at this stage. Inspect messages to the bot for initially sufficient request details; a system identity event may establish verification. Record the order of verification vs protected action. Record ALL client questions and which are answered, and the operator's concrete response to dissatisfaction. Identify farewell separately from a help offer or summary. RU/KZ/EN and mixed Russian/Kazakh are supported: recognize Сәлеметсіз бе, Қайырлы күн, Сау болыңыз, Өкінішке орай, түсінемін as context, not automatic proof of every criterion. Language matching, names, verification and empathy must be judged by context. Do not translate evidence. If transcript is incomplete, record that fact and uncertainty; do not invent missing events.'''

def fact_schema(checklist):
    return {'type':'object','additionalProperties':False,'properties':{'facts':{'type':'object','additionalProperties':False,'properties':{k:{'type':'string'} for k in FACT_KEYS},'required':FACT_KEYS},'applicability':{'type':'object','additionalProperties':False,'properties':{c.id:{'type':'boolean'} for c in checklist.criteria},'required':[c.id for c in checklist.criteria]},'evidence':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,'properties':{'turn':{'type':'integer','minimum':1},'observation':{'type':'string','minLength':1}},'required':['turn','observation']}}},'required':['facts','applicability','evidence']}

def assessment_schema(checklist):
    return ai_output.schema(checklist)


def verify_quotes(quotes,conversation):
    for q in quotes:
        if q.turn<1 or q.turn>len(conversation.transcript) or not q.quote.strip() or q.quote not in conversation.transcript[q.turn-1].text:
            raise OutputError('quote_mismatch',('evidence_quotes',))

def request_body(model,system,content,schema,mode,max_output_tokens=None):
    # The schema is always included in prompt mode as well.
    body={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({'input':content,'output_schema':schema},ensure_ascii=False)}],'max_tokens':max_output_tokens if max_output_tokens is not None else int(os.getenv('MULTIAI_MAX_OUTPUT_TOKENS','6000')),'stream':False}
    if mode=='json_schema':body['response_format']={'type':'json_schema','json_schema':{'name':'cqa_evaluation','strict':True,'schema':schema}}
    return body

class MultiAIProvider:
    mock=False
    verify_evidence_quotes=True
    def __init__(self,model=None,*,timeout_seconds=None,max_output_tokens=None,response_format=None,run_id=None,max_calls=4,max_backoff_seconds=None):
        config=configuration();self.name=model or config['primary_model']
        if not self.name or self.name not in {config['primary_model'],config['secondary_model']}:raise ProviderError('Select an explicitly configured MultiAI model; automatic substitution is disabled')
        headers();base_url()
        self.mode=response_format or os.getenv('MULTIAI_RESPONSE_FORMAT','json_schema')
        self.timeout_seconds=timeout_seconds or float(os.getenv('MULTIAI_TIMEOUT_SECONDS','90'))
        self.max_output_tokens=max_output_tokens or int(os.getenv('MULTIAI_MAX_OUTPUT_TOKENS','6000'))
        self.run_id=run_id;self.max_calls=max_calls
        self.max_backoff_seconds=float(os.getenv('MULTIAI_MAX_BACKOFF_SECONDS','30')) if max_backoff_seconds is None else max_backoff_seconds
        if self.mode not in ('json_schema','prompt'):raise ValueError('MULTIAI_RESPONSE_FORMAT must be json_schema or prompt')
        self._catalog=None;self._fact_key=None;self._facts=None;self._fact_evidence=[];self.calls=[]
    def begin(self):
        self.calls=[];self._fact_key=None;self._facts=None;self._fact_evidence=[];self.current_stage='validation'
    async def catalog_model(self):
        if self._catalog is None:self._catalog=await discover()
        model=next((m for m in self._catalog if m['id']==self.name),None)
        if not model or model.get('availability')!='available' or model.get('model_kind','text')!='text':raise ProviderError('Configured MultiAI model is unavailable; no substitute selected')
        return model
    async def call(self,stage,system,content,schema):
        self.current_stage=stage
        if os.getenv('ALLOW_PAID_CALLS','false').lower()!='true':raise ProviderError('Paid calls disabled; explicit test/budget approval is required')
        model=await self.catalog_model()
        body=request_body(self.name,system,content,schema,self.mode,self.max_output_tokens)
        if not 512<=body['max_tokens']<=12000:raise ProviderError('MULTIAI_MAX_OUTPUT_TOKENS must be between 512 and 12000')
        try:quote=gateway_budget.estimate(len(json.dumps(body,ensure_ascii=False).encode()),body['max_tokens'],model)
        except ValueError as exc:raise ProviderError(str(exc)) from None
        if len(self.calls)>=self.max_calls:raise ProviderError('Per-evaluation HTTP request bound reached')
        try:request_id=gateway_budget.reserve(self.name,stage,quote,run_id=self.run_id) if self.run_id is not None else gateway_budget.reserve(self.name,stage,quote)
        except ValueError as exc:raise ProviderError(str(exc)) from None
        started=time.monotonic();audit={'request_id':request_id,'stage':stage,'requested_model':self.name,'response_format':self.mode,'usage':{},'cost_usd':None}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds,follow_redirects=False) as client:
                r=await asyncio.wait_for(client.post(base_url()+'/chat/completions',headers=headers(),json=body),timeout=self.timeout_seconds)
            audit['http_status']=r.status_code
            if r.status_code!=200:
                # Read only to classify unsupported feature; never log/persist the response body.
                unsupported=r.status_code in (400,422) and any(s in r.text.lower() for s in ('response_format','json_schema','structured output'))
                if unsupported and self.mode=='json_schema':
                    self.mode='prompt';raise ProviderError('MultiAI rejected structured output; retrying with schema in instructions',True)
                if r.status_code==429:
                    retry_after=parse_retry_after(r.headers.get('Retry-After'))
                    bound=self.max_backoff_seconds
                    raise ProviderError('MultiAI: rate limit reached',retryable=retry_after is None or retry_after<=bound,retry_after=retry_after)
                raise http_error(r.status_code)
            b=r.json()
            if not isinstance(b,dict) or b.get('error') is not None:raise ProviderError('MultiAI returned an invalid response envelope',True)
            actual=b.get('model')
            audit['actual_model']=actual
            audit['usage']=safe_usage(b.get('usage'))
            billing=b.get('billing',{})
            if isinstance(billing,dict):
                audit['gateway_billing']=safe_usage(billing)
                currency=billing.get('currency')
                if currency in ('USD','RUB','credits'):audit['gateway_billing']['currency']=currency
                if currency=='USD' and type(billing.get('amount')) in (int,float) and math.isfinite(billing['amount']) and billing['amount']>=0:audit['cost_usd']=billing['amount']
                if type(billing.get('credits')) in (int,float) and math.isfinite(billing['credits']) and billing['credits']>=0:audit['billed_credits']=billing['credits']
            if not isinstance(actual,str) or not actual:raise ProviderError('MultiAI response omits actual model_id')
            if actual!=self.name:raise ProviderError('MultiAI returned a different model_id; result rejected, no substitution accepted')
            choices=b.get('choices')
            if not choices or not isinstance(choices,list):raise ProviderError('MultiAI returned no choices',True)
            choice=choices[0]
            if not isinstance(choice,dict) or not isinstance(choice.get('message'),dict):raise ProviderError('MultiAI returned an invalid response envelope',True,path=('choices',0,'message'))
            if choice.get('finish_reason') in ('length','max_tokens'):raise ProviderError('MultiAI output was truncated',True,category='truncated_output',path=('choices',0,'finish_reason'))
            if choice.get('finish_reason')!='stop' or choice['message'].get('refusal'):raise ProviderError('MultiAI output did not complete normally',True,path=('choices',0,'finish_reason'))
            text=choice['message'].get('content')
            if not isinstance(text,str) or not text.strip():raise ProviderError('MultiAI returned empty content',True)
            audit['latency_ms']=round((time.monotonic()-started)*1000)
            gateway_budget.finish(request_id,'ok',audit);self.calls.append(audit)
            return text
        except Exception as exc:
            audit['latency_ms']=round((time.monotonic()-started)*1000)
            if isinstance(exc,(httpx.TimeoutException,asyncio.TimeoutError)):error=ProviderError('MultiAI timeout; reservation retained',True)
            elif isinstance(exc,httpx.HTTPError):error=ProviderError('MultiAI connection failed; reservation retained',True)
            elif isinstance(exc,(json.JSONDecodeError,KeyError,TypeError,AttributeError)):error=ProviderError('MultiAI returned an invalid response envelope',True)
            else:error=exc
            audit['error']=str(error) if isinstance(error,ProviderError) else 'Invalid provider response'
            audit['diagnostics']=diagnostics(error,stage)
            gateway_budget.finish(request_id,'error',audit);self.calls.append(audit)
            raise error from None
    def telemetry(self):
        return {'provider':'MultiAI','requested_model':self.name,'calls':self.calls,'request_count':len(self.calls),'usage':[c['usage'] for c in self.calls],'cost_usd':sum(c['cost_usd'] for c in self.calls if c['cost_usd'] is not None) if any(c['cost_usd'] is not None for c in self.calls) else None,'cost_coverage':sum(c['cost_usd'] is not None for c in self.calls),'response_format':self.mode,'fact_evidence':self._fact_evidence,'output_contract':ai_output.VERSION,'evidence_source':'model_selected_turns/backend_original_full_turn','evidence_semantics_verified':False}
    async def generate(self,conversation:Conversation,checklist:Checklist,feedback=''):
        if conversation.channel=='voice call' and os.getenv('MULTIAI_ALLOW_REAL_CALLS','false').lower()!='true':raise ProviderError('Real calls require separate explicit permission; MultiAI blocked')
        # Canonical validation preserves every original timestamp, text and turn order.
        conversation=Conversation.model_validate(conversation.model_dump())
        normalized=json.loads(prompt(conversation,checklist,''))
        key=json.dumps(normalized,ensure_ascii=False)
        if self._fact_key!=key:
            self.calls=[];self._facts=None;self._fact_evidence=[];self._fact_key=key
        if self._facts is None:
            text=await self.call('fact_extraction',FACT_SYSTEM,{**normalized,'validation_feedback':feedback},fact_schema(checklist))
            report=FactReport.model_validate(ai_output.decode_json(text))
            if set(report.facts)!=set(FACT_KEYS):
                extra=set(report.facts)-set(FACT_KEYS);key=next(iter(extra or set(FACT_KEYS)-set(report.facts)))
                raise OutputError('extra_field' if extra else 'missing_field',('facts',key),self.current_stage)
            if set(report.applicability)!={c.id for c in checklist.criteria}:
                expected={c.id for c in checklist.criteria};extra=set(report.applicability)-expected;key=next(iter(extra or expected-set(report.applicability)))
                raise OutputError('extra_field' if extra else 'missing_field',('applicability',key),self.current_stage)
            quotes=ai_output.quotes_for([e.turn for e in report.evidence],conversation,('evidence',))
            for c in checklist.criteria:
                if not c.allow_na and report.applicability[c.id] is False:raise OutputError('invalid_na',('applicability',c.id),self.current_stage)
            self._facts=report.model_dump()
            self._fact_evidence=[{**q,'observation':e.observation} for q,e in zip(quotes,report.evidence)]
        content={**normalized,'verified_facts':self._facts,'validation_feedback':feedback}
        text=await self.call('criteria_evaluation',SYSTEM+'\nSTAGE 2: apply the supplied checklist to the extracted facts. Return only the criteria object keyed by each supplied criterion_id. Do not repeat facts or applicability. Do not change applicability established in facts. Select evidence_turns yourself from the original one-based transcript indices, only where they support your rationale. For omissions select where the action was expected. The backend copies those original turns; do not return quotes. Use concise rationale without repeating transcript text; preserve na_reason and explicit uncertainty. No score or weights in output.',content,assessment_schema(checklist))
        output=ai_output.parse_criteria(text,checklist,conversation,self._facts['facts'],self._facts['applicability'])
        meta=self.telemetry();meta['fact_applicability']=self._facts['applicability'];meta['extracted_facts']=self._facts['facts']
        return ProviderReply(output.model_dump_json(),{},meta['cost_usd'],self.name,meta)
