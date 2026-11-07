import asyncio
import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from collections import Counter
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app import main,batch,benchmark_suite,annotations,benchmark_report,multiai
from backend.app.domain import Conversation,Assessment,calculate
from backend.app.checklist import OFFICIAL
from backend.app.benchmark_metrics import compare,latency,evidence_audit
from backend.app.storage import setup,ConversationRow,EvaluationRow,BenchmarkRunRow,RunBudgetRow,GatewayBudgetRow
from backend.app.gateway_budget import stats,reserve

@pytest.fixture
def lab(tmp_path,monkeypatch):
    for key,value in {'DATABASE_URL':'sqlite:///'+str(tmp_path/'lab.db'),'MULTIAI_API_KEY':'unit-key-not-real','MULTIAI_PRIMARY_MODEL':'gpt-6.1-sol','MULTIAI_SECONDARY_MODEL':'gemini-3.8-flash','MULTIAI_BASE_URL':'https://multiai.store/v1','ALLOW_PAID_CALLS':'false','MULTIAI_MAX_REQUESTS':'200','API_BUDGET_USD':'10','MULTIAI_PRICING_CONFIRMED':'true','MULTIAI_USD_PER_MILLION_CREDITS':'0.1','MULTIAI_MAX_BACKOFF_SECONDS':'30'}.items():monkeypatch.setenv(key,value)
    monkeypatch.delenv('MULTIAI_BATCH_APPROVED_PLAN',raising=False)
    factory=setup();monkeypatch.setattr(main,'Session',factory);monkeypatch.setattr(batch,'ROOT',tmp_path/'artifacts')
    manifest=json.loads(Path('benchmarks/multilingual-v1.json').read_text());records=[];own=[]
    for meta in manifest['cases']:
        c=Conversation(conversation_id=meta['conversation_id'],language=meta['language'],channel='agent chat',transcript=[{'timestamp':'00:00','speaker':'operator','text':{'RU':'Здравствуйте! Я Анна.','KZ':'Сәлеметсіз бе! Мен Аннамын. Можно по-русски?','EN':'Hello! My name is Anna.'}[meta['language']]}])
        meta['sha256']=benchmark_suite.digest(c.model_dump());records.append((meta,c))
        if meta['source']=='team_synthetic':own.append(c.model_dump())
    mp=tmp_path/'manifest.json';mp.write_text(json.dumps(manifest));sp=tmp_path/'synthetic.json';sp.write_text(json.dumps({'records':own}))
    monkeypatch.setattr(benchmark_suite,'MANIFEST',mp);monkeypatch.setattr(benchmark_suite,'SYNTHETIC',sp)
    with factory() as s:
        for meta,c in records:
            if meta['source']!='team_synthetic':s.add(ConversationRow(id=c.conversation_id,dataset='DEV',payload=c.model_dump()))
        s.commit();benchmark_suite.prepare(s)
    catalog=[{'id':m,'availability':'available','model_kind':'text','input_multiplier':1,'output_multiplier':1,'tariff_revision':1,'catalog_revision':'test-v1'} for m in ('gpt-6.1-sol','gemini-3.8-flash')]
    monkeypatch.setattr(batch,'cached_catalog',lambda:catalog)
    tracker={'posts':[],'gets':[],'active':0,'max_active':0,'spent':0,'rate_limit':False,'retry_after':'0'}
    original=httpx.AsyncClient
    async def handler(request):
        if request.method=='GET':
            tracker['gets'].append(request.url.path)
            if request.url.path.endswith('/models'):return httpx.Response(200,json={'data':catalog})
            if request.url.path.endswith('/subscription/limits'):return httpx.Response(200,json={'billing_mode':'tokens','account_balance':{'tokens':1000000},'api_key':{'tokens_spent':tracker['spent']}})
            raise AssertionError('Unexpected test GET')
        body=json.loads(request.content);tracker['posts'].append(body)
        assert 'EXPERT_LABEL_SECRET' not in request.content.decode()
        tracker['active']+=1;tracker['max_active']=max(tracker['max_active'],tracker['active'])
        await asyncio.sleep(.002);tracker['active']-=1
        if tracker['rate_limit']:
            tracker['rate_limit']=False;return httpx.Response(429,json={'error':{'message':'rate limit'}},headers={'Retry-After':tracker['retry_after']})
        tracker['spent']+=100
        original_input=json.loads(body['messages'][1]['content'])['input'];turns=original_input['conversation']['transcript'];text=turns[0]['text']
        facts={k:'test observation' for k in multiai.FACT_KEYS};applicable={c.id:True for c in OFFICIAL.criteria}
        if 'STAGE 1:' in body['messages'][0]['content']:
            output={'facts':facts,'applicability':applicable,'evidence':[{'turn':1,'quote':text,'observation':'test quote'}]}
        else:
            output={'facts':facts,'applicability':applicable,'criteria':[{'criterion_id':c.id,'value':False if c.type=='boolean' else min(c.scale),'rationale':'HTTP mock fixture, not real AI','evidence_turns':[1],'evidence_quotes':[{'turn':1,'quote':text}],'na_reason':None,'uncertain':False,'uncertainty_reason':None} for c in OFFICIAL.criteria]}
        return httpx.Response(200,json={'model':body['model'],'choices':[{'finish_reason':'stop','message':{'content':json.dumps(output,ensure_ascii=False)}}],'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
    monkeypatch.setattr(multiai.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    return {'factory':factory,'monkeypatch':monkeypatch,'manifest':manifest,'catalog':catalog,'tracker':tracker,'tmp':tmp_path}

def labels(cid,slot,reviewer,critical=False):
    return annotations.Annotation(conversation_id=cid,slot=slot,reviewer=reviewer,origin='human',criteria=[Assessment(criterion_id=c.id,value=critical if c.id=='critical' else True if c.type=='boolean' else max(c.scale),rationale='EXPERT_LABEL_SECRET independent human test fixture',evidence_turns=[1]) for c in OFFICIAL.criteria])

def plan(lab,**kwargs):
    with lab['factory']() as s:return batch.create_plan(s,batch.PlanOptions(**kwargs),lab['catalog'])

def approve(lab,p):
    lab['monkeypatch'].setenv('ALLOW_PAID_CALLS','true');lab['monkeypatch'].setenv('MULTIAI_BATCH_APPROVED_PLAN',p['plan_sha256'])
    return batch.Approval(approved=True,plan_sha256=p['plan_sha256'],evaluation_count=p['evaluation_count'],limit_usd=p['options']['limit_usd'])

def test_manifest_pins_provenance_balance_and_original_text(lab):
    with lab['factory']() as s:
        m,cases=benchmark_suite.load(s)
        assert Counter(c.language for c in cases.values())=={'RU':6,'KZ':6,'EN':6}
        assert len(cases)==18 and sum(x['source']=='organizer_dev_synthetic' for x in m['cases'])==17
        assert set(m['required_coverage'])<=set(t for c in m['cases'] for t in c['tags'])
        row=s.get(ConversationRow,m['cases'][0]['conversation_id']);payload=deepcopy(row.payload);payload['transcript'][0]['text']='Tampered';row.payload=payload;s.commit()
        with pytest.raises(ValueError,match='hash mismatch'):benchmark_suite.load(s)

def test_two_blind_reviewers_disagreements_and_stale_consensus(lab):
    cid=lab['manifest']['cases'][0]['conversation_id']
    with lab['factory']() as s:
        m,cases=benchmark_suite.load(s)
        assert annotations.state(s,m,cases[cid])['status']=='unlabeled'
        a=annotations.save(s,labels(cid,'A','Alice'))
        with pytest.raises(ValueError,match='different'):annotations.save(s,labels(cid,'B',' alice '))
        b=annotations.save(s,labels(cid,'B','Bob',critical=True))
        state=annotations.state(s,m,cases[cid]);assert state['status']=='needs_adjudication'
        assert state['disagreements'][0]['criterion_id']=='critical'
        final=labels(cid,'consensus','Adjudicator',critical=True);final.based_on=[a['id'],b['id']];final.decision_reason='Reviewed original turn and both independent annotations'
        annotations.save(s,final);assert annotations.state(s,m,cases[cid])['status']=='consensus'
        annotations.save(s,labels(cid,'A','Alice'))
        assert annotations.state(s,m,cases[cid])['consensus'] is None
        with pytest.raises(ValueError,match='latest'):annotations.save(s,final)

def test_templates_and_atomic_human_import(lab):
    with lab['factory']() as s:
        m,cases=benchmark_suite.load(s);draft=annotations.template(m,'A')
        assert draft['status']=='unlabeled' and len(draft['annotations'])==18
        assert all(c['value']=='UNLABELED' for r in draft['annotations'] for c in r['criteria'])
        with pytest.raises(ValueError):annotations.Annotation.model_validate(draft['annotations'][0])
        first=labels(next(iter(cases)),'A','Alice');bad=labels('outside-case','A','Alice')
        with pytest.raises(ValueError):annotations.import_annotations(s,[first,bad])
        s.rollback();assert not annotations.latest(s,first.conversation_id,annotations.context(m,cases[first.conversation_id]))

def test_unlabeled_and_denominator_correctness():
    c=Conversation(conversation_id='x',language='KZ',channel='agent chat',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Сәлем!'}]);cases={'x':c}
    assert compare([],{},cases)['status']=='unlabeled' and compare([],{},cases)['metrics'] is None
    truth={'x':{'criteria':[{'criterion_id':'contact','value':True},{'criterion_id':'discovery','value':None},{'criterion_id':'dialogue','value':7},{'criterion_id':'critical','value':True}]}}
    pred=[{'conversation_id':'x','status':'ok','final_score':100,'criteria':[{'criterion_id':'contact','value':1},{'criterion_id':'discovery','value':False},{'criterion_id':'dialogue','value':4},{'criterion_id':'critical','value':False}]}]
    r=compare(pred,truth,cases);g=r['metrics']['overall']
    assert g['accuracy']==0 and g['scale_mae']==3 and g['na_matrix']['actual_na_pred_applicable']==1
    assert r['metrics']['criterion:critical']['critical_recall']==0
    missing=compare([],truth,cases)['metrics']['overall'];assert missing['total']==4 and missing['missing']==4 and missing['accuracy']==0

def test_latency_percentiles_and_quote_correspondence():
    assert latency(list(range(1,21)))=={'count':20,'mean_ms':10.5,'median_ms':10.5,'p95_ms':19,'p95_method':'nearest_rank'}
    assert latency([])['p95_ms'] is None
    c=Conversation(conversation_id='x',language='EN',channel='agent chat',transcript=[{'timestamp':'00:00','speaker':'client','text':'Hello'},{'timestamp':'00:01','speaker':'operator','text':'Good day'}])
    audit=evidence_audit([{'conversation_id':'x','criteria':[{'evidence_turns':[2,999],'evidence_quotes':[{'turn':2,'quote':'Invented'},{'turn':999,'quote':'Good day'}]}]}],{'x':c})
    assert audit['turn_validity']==.5 and audit['quote_match']==0

def test_scoring_real_official_half_up_case():
    from backend.tests.test_core import assessments
    a=assessments(identification=None,consultation=0,dialogue=7,discovery=False)
    assert calculate(a,OFFICIAL)[0]['final_score']==50.63
    assert calculate(assessments(critical=True),OFFICIAL)[0]['final_score']==100


def test_dry_run_is_six_evaluations_twelve_http_and_never_spends(lab):
    p=plan(lab)
    assert p['evaluation_count']==6 and p['dialogue_count']==3
    assert p['provider_requests_expected']==12 and p['provider_requests_max']==24
    assert Counter(j['language'] for j in p['jobs'])=={'RU':2,'KZ':2,'EN':2}
    assert p['estimated_usd_upper']>0 and not p['paid_calls_enabled']
    assert not lab['tracker']['posts'] and not lab['tracker']['gets'] and stats()['request_count']==0
    no=batch.Approval(approved=False,plan_sha256=p['plan_sha256'],evaluation_count=6,limit_usd='0.50')
    with pytest.raises(ValueError,match='approval'):asyncio.run(batch.execute(lab['factory'],p['id'],no))
    yes=approve(lab,p);yes.evaluation_count=5
    with pytest.raises(ValueError,match='count/budget'):asyncio.run(batch.execute(lab['factory'],p['id'],yes))
    assert not lab['tracker']['posts']


def test_bounded_run_concurrency_rate_limit_and_label_leakage(lab):
    p=plan(lab,concurrency=2);cid=p['jobs'][0]['conversation_id']
    with lab['factory']() as s:annotations.save(s,labels(cid,'A','Human A'))
    lab['tracker']['rate_limit']=True
    out=asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
    assert out['status']=='complete' and len(out['results'])==6 and all(r['status']=='ok' for r in out['results'])
    assert 1<lab['tracker']['max_active']<=2 and len(lab['tracker']['posts'])==13
    assert out['provider_credit_delta']==1200 and out['converted_cost_usd']==.00012
    with lab['factory']() as s:
        rr=s.get(RunBudgetRow,p['id']);assert rr.requests==13 and rr.requests<=rr.request_limit
        evaluations=list(s.scalars(select(EvaluationRow)));assert sum(e.payload['attempts']-1 for e in evaluations)==1
        assert all('validation_failures' in e.payload for e in evaluations)
        r=benchmark_report.report(s);assert r['status']=='unlabeled' and r['paired_cases']==3
        assert all(v['report']['metrics'] is None for v in r['models'].values())
    with pytest.raises(ValueError,match='consumed'):asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))


def test_catalog_change_and_global_request_limit_block_without_posts(lab):
    p=plan(lab);approval=approve(lab,p)
    lab['monkeypatch'].setenv('MULTIAI_MAX_REQUESTS','1')
    with pytest.raises(ValueError,match='Global request'):asyncio.run(batch.execute(lab['factory'],p['id'],approval))
    lab['monkeypatch'].setenv('MULTIAI_MAX_REQUESTS','200');lab['catalog'][0]['input_multiplier']=2
    with pytest.raises(ValueError,match='catalog/tariff changed'):asyncio.run(batch.execute(lab['factory'],p['id'],approval))
    assert not lab['tracker']['posts']


def test_atomic_scoped_budget_guard(lab):
    with lab['factory']() as s:s.add(RunBudgetRow(id=999,requests=0,request_limit=1,reserved_microusd=0,limit_microusd=1));s.commit()
    q={'usd_conversion_verified':True,'estimated_usd_upper':.01,'estimated_credits_upper':100}
    with pytest.raises(ValueError,match='Approved run'):reserve('gpt-6.1-sol','test',q,run_id=999)
    assert stats()['request_count']==0  # global update rolled back atomically too


def test_prod_plan_requires_ten_real_inputs_and_export_only_after_run(lab):
    with lab['factory']() as s:
        with pytest.raises(ValueError,match='10 genuine'):batch.create_plan(s,batch.PlanOptions(mode='prod',model='gpt-6.1-sol',concurrency=2),lab['catalog'])
        for i in range(10):
            c=Conversation(conversation_id='prod-unit-'+str(i),channel='agent chat',language='EN',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Hello from explicit unit fixture'}])
            s.add(ConversationRow(id=c.conversation_id,dataset='PROD',payload=c.model_dump()))
        s.commit()
    p=plan(lab,mode='prod',model='gpt-6.1-sol',concurrency=2)
    assert p['evaluation_count']==10 and p['fits_deadline'] and p['deadline_bound_seconds']<3600
    path=lab['tmp']/'artifacts/results/prod/results.json';assert not path.exists()
    out=asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
    assert out['status']=='complete' and path.exists()
    data=json.loads(path.read_text());assert data['schema_version']=='1.0' and len(data['results'])==10
    from backend.app.exporter import validate_export
    with lab['factory']() as s:
        rows=list(s.scalars(select(ConversationRow).where(ConversationRow.dataset=='PROD')))
        validate_export(data,[Conversation.model_validate(r.payload) for r in rows],'PROD')
    assert all(set(a)=={'criterion_id','value','rationale','evidence_turns'} for r in data['results'] for a in r['criteria'])


def test_annotation_api_blindness_and_unlabeled_report(lab):
    cid=lab['manifest']['cases'][0]['conversation_id']
    with TestClient(main.app) as client:
        assert client.get('/api/benchmark/comparison').json()['status']=='unlabeled'
        a=labels(cid,'A','Alice');assert client.post('/api/benchmark/annotations',json=a.model_dump()).status_code==201
        b=client.get('/api/benchmark/cases/'+cid+'?slot=B').json()
        assert b['annotation'] is None and 'independent_rounds' not in b
        assert 'EXPERT_LABEL_SECRET' not in json.dumps(b)
        assert client.get('/api/benchmark/annotation-template?slot=B').json()['status']=='unlabeled'
        assert client.post('/api/batches/plans',json={'mode':'preflight'}).status_code==201
        assert not lab['tracker']['posts']


def test_paired_same_pipeline_consensus_metrics_and_holdout(lab):
    m=lab['manifest'];cid=m['preflight_ids'][0]
    with lab['factory']() as s:
        manifest,cases=benchmark_suite.load(s)
        a=annotations.save(s,labels(cid,'A','Alice'));b=annotations.save(s,labels(cid,'B','Bob'))
        final=labels(cid,'consensus','Reviewer');final.based_on=[a['id'],b['id']];final.decision_reason='Independent labels reviewed';annotations.save(s,final)
        ctx={**annotations.context(manifest,cases[cid]),'pipeline_sha256':'frozen-experiment-v1'}
        criteria=[c.model_dump() for c in final.criteria]
        models=['gpt-6.1-sol','gemini-3.8-flash']
        for model in models:
            items=deepcopy(criteria)
            if model==models[1]:items[-1]['value']=True
            # Deliberately identical total scores: accuracy must still differ by criterion.
            s.add(EvaluationRow(conversation_id=cid,checklist_id=1,payload={'model':model,'mock':False,'status':'ok','criteria':items,'score':{'final_score':100,'critical_flag':False},'benchmark_context':ctx,'latency_ms':100,'attempts':1,'usage':[],'validation_failures':[]}))
        s.commit();r=benchmark_report.report(s)
        assert r['paired_cases']==1 and r['paired_labeled_cases']==1
        assert r['models'][models[0]]['report']['metrics']['overall']['accuracy']==1
        assert r['models'][models[1]]['report']['metrics']['overall']['accuracy']==7/8
        assert 'split:calibration' in r['models'][models[0]]['report']['metrics']
        assert r['models'][models[0]]['report']['metrics']['language:KZ']['accuracy'] is None
        assert r['models'][models[0]]['report']['metrics']['split:holdout']['accuracy'] is None
        # A newer unilateral experiment cannot overwrite the matched earlier pair.
        s.add(EvaluationRow(conversation_id=cid,checklist_id=1,payload={'model':models[0],'status':'ok','mock':False,'criteria':criteria,'benchmark_context':{**ctx,'pipeline_sha256':'unpaired-other-pipeline'}}));s.commit()
        r=benchmark_report.report(s);assert r['comparison_pipeline_sha256']=='frozen-experiment-v1'
        assert r['paired_cases']==1


def test_approval_invalidates_after_pipeline_or_input_change(lab):
    p=plan(lab);approval=approve(lab,p)
    with lab['factory']() as s:
        row=s.get(ConversationRow,p['jobs'][0]['conversation_id']);payload=deepcopy(row.payload);payload['transcript'][0]['text']='Changed after approval';row.payload=payload;s.commit()
    with pytest.raises(ValueError,match='transcript changed'):asyncio.run(batch.execute(lab['factory'],p['id'],approval))
    assert not lab['tracker']['posts']


def test_deadline_errors_are_preserved_without_fake_scores(lab):
    p=plan(lab)
    async def timed_out(*args,**kwargs):raise asyncio.TimeoutError()
    lab['monkeypatch'].setattr(batch,'evaluate',timed_out)
    result=asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
    assert len(result['results'])==6 and all(x['status']=='error' for x in result['results'])
    with lab['factory']() as s:
        assert all(e.payload['score'] is None and e.payload['criteria']==[] for e in s.scalars(select(EvaluationRow)))
    assert not lab['tracker']['posts']


def test_retry_after_bound_and_validation_failure_audit(lab):
    assert multiai.parse_retry_after('5')==5
    assert multiai.parse_retry_after('not a date') is None
    from backend.app.engine import evaluate
    from backend.app.providers import MockProvider,ProviderReply
    class Broken(MockProvider):
        async def generate(self,*args,**kwargs):return ProviderReply('{broken',{})
    c=Conversation(conversation_id='test',language='RU',channel='agent chat',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Здравствуйте'}])
    r=asyncio.run(evaluate(c,OFFICIAL,Broken(),max_backoff_seconds=0))
    assert r['status']=='error' and len(r['validation_failures'])==2 and r['attempts']==2
    assert all('input' not in item for item in r['validation_failures'])


def test_long_retry_after_fails_closed_without_early_retry(lab):
    from backend.app.engine import evaluate
    lab['monkeypatch'].setenv('ALLOW_PAID_CALLS','true')
    lab['tracker']['rate_limit']=True;lab['tracker']['retry_after']='120'
    with lab['factory']() as s:_,cases=benchmark_suite.load(s)
    c=next(iter(cases.values()))
    result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider(max_backoff_seconds=30)))
    assert result['status']=='error' and result['attempts']==1
    assert len(lab['tracker']['posts'])==1 and not result['validation_failures']


def test_retry_after_is_honored_within_bound(lab):
    from backend.app.engine import evaluate
    from backend.app.providers import MockProvider
    waits=[]
    async def sleep(seconds):waits.append(seconds)
    class Limited(MockProvider):
        calls=0
        async def generate(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise multiai.ProviderError('Rate limited',True,retry_after=2)
            return await super().generate(*args,**kwargs)
    from backend.app import engine
    lab['monkeypatch'].setattr(engine.asyncio,'sleep',sleep)
    with lab['factory']() as s:_,cases=benchmark_suite.load(s)
    result=asyncio.run(evaluate(next(iter(cases.values())),OFFICIAL,Limited(),max_backoff_seconds=30))
    assert result['status']=='ok' and waits==[2] and result['retry_wait_ms']==2000
    assert not result['validation_failures']


def test_total_provider_timeout_is_bounded_and_reservation_retained(lab):
    from backend.app.engine import evaluate
    lab['monkeypatch'].setenv('ALLOW_PAID_CALLS','true')
    with lab['factory']() as s:_,cases=benchmark_suite.load(s)
    result=asyncio.run(evaluate(next(iter(cases.values())),OFFICIAL,multiai.MultiAIProvider(timeout_seconds=.001),max_backoff_seconds=0))
    assert result['status']=='error' and 'timeout' in result['error']
    assert stats()['request_count']==2 and stats()['reserved_usd']>0
    assert not result['validation_failures']
