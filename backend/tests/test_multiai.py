import asyncio
import json
from copy import deepcopy
import httpx
import pytest
from backend.app import multiai
from backend.app.domain import Conversation
from backend.app.checklist import OFFICIAL
from backend.app.engine import evaluate
from backend.app.providers import MockProvider
from backend.app.gateway_budget import stats

@pytest.fixture
def env(monkeypatch,tmp_path):
    for k,v in {'DATABASE_URL':'sqlite:///'+str(tmp_path/'multi.db'),'MULTIAI_API_KEY':'test-secret-never-log','MULTIAI_BASE_URL':'https://multiai.store/v1','MULTIAI_PRIMARY_MODEL':'gpt-6.1-sol','MULTIAI_SECONDARY_MODEL':'gemini-3.8-flash','ALLOW_PAID_CALLS':'true','MULTIAI_USD_PER_MILLION_CREDITS':'1','MULTIAI_PRICING_CONFIRMED':'true','MULTIAI_MAX_REQUESTS':'12','API_BUDGET_USD':'10','MULTIAI_RESPONSE_FORMAT':'json_schema','MULTIAI_ALLOW_REAL_CALLS':'false'}.items():monkeypatch.setenv(k,v)
    return monkeypatch

def conversation(language='RU'):
    text={'RU':'Здравствуйте! Меня зовут Анна.','KZ':'Сәлеметсіз бе! Мен Аннамын. Қандай сұрағыңыз бар?','EN':'Hello! My name is Anna.'}[language]
    return Conversation(conversation_id='test-'+language,channel='agent chat',language=language,transcript=[{'timestamp':'00:00','speaker':'operator','text':text}])

def answers(c):
    facts={'facts':{k:'Test observation' for k in multiai.FACT_KEYS},'applicability':{c.id:True for c in OFFICIAL.criteria},'evidence':[{'turn':1,'observation':'Original greeting'}]}
    output=json.loads(asyncio.run(MockProvider().generate(c,OFFICIAL)).text)
    output['criteria'][0]['value']=True
    for criterion in output['criteria']:criterion['evidence_quotes']=[{'turn':1,'quote':c.transcript[0].text}]
    output={'criteria':{a['criterion_id']:{k:a.get(k) for k in ('value','rationale','evidence_turns','na_reason','uncertain','uncertainty_reason')} for a in output['criteria']}}
    return facts,output

def transport(monkeypatch,c,mutation=None,status=None,timeout=False,missing_model=False,unsupported=False,actual_model=None,billing=None):
    facts,output=answers(c);posts=[];getpaths=[];original=httpx.AsyncClient
    def handler(request):
        if request.method=='GET':
            getpaths.append(request.url.path)
            return httpx.Response(200,json={'data':[] if missing_model else [{'id':id,'availability':'available','model_kind':'text','input_multiplier':1,'output_multiplier':1} for id in ['gpt-6.1-sol','gemini-3.8-flash']]})
        body=json.loads(request.content);posts.append(body)
        assert request.url.host=='multiai.store' and request.url.path=='/v1/chat/completions'
        assert request.headers['Authorization']=='Bearer test-secret-never-log'
        if timeout:raise httpx.ReadTimeout('test-secret-never-log',request=request)
        if status:return httpx.Response(status,json={'error':{'message':'test-secret-never-log'}})
        if unsupported and 'response_format' in body:return httpx.Response(400,json={'error':{'message':'Unsupported response_format json_schema'}})
        fact='STAGE 1:' in body['messages'][0]['content']
        value=deepcopy(facts if fact else output)
        if mutation:value=mutation(value,fact)
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        return httpx.Response(200,json={'model':actual_model if actual_model is not None else body['model'],'choices':[{'finish_reason':'stop','message':{'content':text}}],'usage':{'prompt_tokens':100,'completion_tokens':80},'billing':billing if billing is not None else {'credits':180,'currency':'credits','amount':180}})
    monkeypatch.setattr(multiai.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return posts,getpaths

@pytest.mark.parametrize('language',['RU','KZ','EN'])
@pytest.mark.parametrize('model',['gpt-6.1-sol','gemini-3.8-flash'])
def test_success_pipeline_models_and_languages(env,language,model):
    c=conversation(language);posts,_=transport(env,c)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider(model)))
    assert result['status']=='ok' and result['score']['final_score']==10
    assert result['model']==model and result['provider_metadata']['request_count']==2
    assert len(posts)==2 and all(p['model']==model for p in posts)
    assert c.transcript[0].text in posts[0]['messages'][1]['content']
    assert result['cost_usd'] is None  # internal gateway units must not become USD
    assert result['usage'][0]['prompt_tokens']==100
    assert stats()['request_count']==2

@pytest.mark.parametrize('failure',['json','values','bounds','quote','duplicates','missing','empty','na'])
def test_invalid_outputs_never_accepted(env,failure):
    c=conversation()
    def mutate(value,fact):
        if fact:return value
        if failure=='json':return '{broken'
        if failure=='empty':return ''
        if failure=='values':value['criteria']['dialogue']['value']=5
        if failure=='bounds':value['criteria']['contact']['evidence_turns']=[999]
        if failure=='quote':value['criteria']['contact']['evidence_quotes']=[{'turn':1,'quote':'Imaginary sentence'}]
        if failure=='duplicates':value['criteria']['unexpected']=value['criteria']['contact']
        if failure=='missing':value['criteria'].pop('critical')
        if failure=='na':value['criteria']['discovery'].update(value=None,na_reason='Uncertain')
        return value
    posts,_=transport(env,c,mutation=mutate)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and result['score'] is None and result['criteria']==[]
    assert len(posts)<=4 and result['attempts']==2

@pytest.mark.parametrize('status,requests',[(401,1),(403,1),(402,1),(404,1),(429,2),(500,2)])
def test_safe_http_errors(env,status,requests):
    c=conversation();posts,_=transport(env,c,status=status)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and len(posts)==requests
    assert 'test-secret' not in json.dumps(result)
    assert stats()['request_count']==requests

def test_timeout_and_no_model(env):
    c=conversation();posts,_=transport(env,c,timeout=True)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and 'timeout' in result['error'] and len(posts)==2

def test_unavailable_model_no_generation(env):
    c=conversation();posts,_=transport(env,c,missing_model=True)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and not posts

def test_structured_fallback_is_bounded(env):
    c=conversation();posts,_=transport(env,c,unsupported=True)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='ok' and len(posts)==3
    assert 'response_format' in posts[0] and 'response_format' not in posts[1]
    assert result['provider_metadata']['response_format']=='prompt'

def test_budget_blocks_before_network(env):
    env.setenv('API_BUDGET_USD','0.000001')
    c=conversation();posts,_=transport(env,c)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and not posts and stats()['request_count']==0

def test_unverified_conversion_and_disabled_calls(env):
    c=conversation();posts,_=transport(env,c)
    env.setenv('MULTIAI_PRICING_CONFIRMED','false')
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and not posts
    env.setenv('ALLOW_PAID_CALLS','false')
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and not posts

def test_request_limit_and_real_call_block(env):
    env.setenv('MULTIAI_MAX_REQUESTS','1');c=conversation();posts,_=transport(env,c)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and len(posts)==1
    c.channel='voice call'
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and len(posts)==1

def test_no_silent_model_substitution(env):
    with pytest.raises(ValueError):multiai.MultiAIProvider('other-model')
    env.setenv('MULTIAI_BASE_URL','https://evil.example/v1')
    with pytest.raises(ValueError):multiai.MultiAIProvider()


def test_gateway_response_model_mismatch_rejected(env):
    c=conversation();posts,_=transport(env,c,actual_model='unapproved-model')
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and len(posts)==1
    assert result['provider_metadata']['calls'][0]['actual_model']=='unapproved-model'


def test_only_explicit_usd_billing_is_usd(env):
    c=conversation();posts,_=transport(env,c,billing={'currency':'USD','amount':0.02})
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='ok' and result['cost_usd']==0.04
    assert stats()['reported_cost_usd']==0.04 and stats()['reported_cost_coverage']==2


def test_invalid_fact_turn_rejected_before_rating(env):
    c=conversation()
    def mutate(value,fact):
        if fact:value['evidence'][0]['turn']=999
        return value
    posts,_=transport(env,c,mutation=mutate)
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider()))
    assert result['status']=='error' and len(posts)==2
    assert all('STAGE 1:' in p['messages'][0]['content'] for p in posts)
