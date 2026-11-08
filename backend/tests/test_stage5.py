"""Full human rounds/calibration planning on temporary SQLite, with mock-only providers."""
from copy import deepcopy
import asyncio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.app import main,fast_review,annotations,batch,benchmark_report,benchmark_suite
from backend.app.storage import BenchmarkAnnotationRow,EvaluationRow,BenchmarkRunRow
from test_stage3 import lab,labels,approve

def body(w,name):
 records=[]
 for c in w['cases']:
  a=labels(c['conversation']['conversation_id'],w['slot'],name)
  if w['slot']=='consensus':a.based_on=c['adjudication']['based_on'];a.decision_reason='Human fixture adjudication'
  records.append(a.model_dump())
 return {'slot':w['slot'],'human_confirmed':True,'context':deepcopy(w['context']),'expected_versions':deepcopy(w['expected_versions']),'annotations':records}

def ws(client,slot):
 r=client.get('/api/benchmark/fast-review',params={'slot':slot});assert r.status_code==200,r.text;return r.json()

def count(lab,slot=None):
 with lab['factory']() as s:
  q=select(func.count()).select_from(BenchmarkAnnotationRow)
  if slot:q=q.where(BenchmarkAnnotationRow.slot==slot)
  return s.scalar(q)

def save_round(client,slot,name):
 data=body(ws(client,slot),name);r=client.post('/api/benchmark/fast-review/save',json=data);assert r.status_code==201,r.text;return data


def test_complete_round_and_blind_workspaces(lab):
 with TestClient(main.app) as client:
  a=save_round(client,'A','Alice')
  b=ws(client,'B');assert all(c['annotation'] is None and 'independent_rounds' not in c for c in b['cases'])
  assert 'EXPERT_LABEL_SECRET' not in str(b)
  assert client.get('/api/benchmark/fast-review?slot=consensus').status_code==422
  assert client.post('/api/benchmark/fast-review/save',json=body(b,'Alice')).status_code==422
  assert count(lab)==18
  save_round(client,'B','Bob')
  assert len(ws(client,'consensus')['cases'])==18
  assert client.post('/api/benchmark/fast-review/save',json=a).status_code==422 # stale snapshot
 assert count(lab)==36 and not lab['tracker']['posts'] and not lab['tracker']['gets']

@pytest.mark.parametrize('failure',['last_invalid_evidence','incomplete','duplicate','mixed_slots','reviewers','not_human_confirmed','wrong_context','wrong_versions','origin_ai','invalid_na'])
def test_atomic_bulk_rejects_entire_round(lab,failure):
 with TestClient(main.app) as client:
  w=ws(client,'A');b=body(w,'Alice')
  if failure=='last_invalid_evidence':b['annotations'][-1]['criteria'][0]['evidence_turns']=[0]
  if failure=='incomplete':b['annotations'].pop()
  if failure=='duplicate':b['annotations'][-1]=deepcopy(b['annotations'][0])
  if failure=='mixed_slots':b['annotations'][-1]['slot']='B'
  if failure=='reviewers':b['annotations'][-1]['reviewer']='Someone else'
  if failure=='not_human_confirmed':b['human_confirmed']=False
  if failure=='wrong_context':b['context']['manifest_sha256']='changed'
  if failure=='wrong_versions':b['expected_versions'][w['cases'][0]['conversation']['conversation_id']]=123
  if failure=='origin_ai':b['annotations'][-1]['origin']='AI_ASSISTED_DRAFT_NOT_HUMAN'
  if failure=='invalid_na':b['annotations'][-1]['criteria'][0]['value']=None
  assert client.post('/api/benchmark/fast-review/save',json=b).status_code==422
  assert count(lab)==0
  # Retry of a valid packet succeeds; failed transaction did not leave a partial prefix.
  assert client.post('/api/benchmark/fast-review/save',json=body(w,'Alice')).status_code==201
 assert count(lab)==18 and not lab['tracker']['posts']


def test_bulk_consensus_versions_staleness_and_rollback(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');w=ws(client,'consensus');final=body(w,'Adjudicator')
  # Change final A after workspace loaded, forcing a late based_on mismatch and rollback.
  last=w['cases'][-1]['conversation']['conversation_id']
  changed=labels(last,'A','Alice',critical=True)
  assert client.post('/api/benchmark/annotations',json=changed.model_dump()).status_code==201
  assert client.post('/api/benchmark/fast-review/save',json=final).status_code==422
  assert count(lab,'consensus')==0
  fresh=ws(client,'consensus');good=body(fresh,'Adjudicator')
  good['annotations'][-1]['criterion_decisions']={}
  assert client.post('/api/benchmark/fast-review/save',json=good).status_code==422
  assert count(lab,'consensus')==0
  good=body(fresh,'Adjudicator')
  assert client.post('/api/benchmark/fast-review/save',json=good).status_code==201
  assert count(lab,'consensus')==18
  report=client.get('/api/benchmark/comparison').json();assert report['expert_labeled']==18 and report['status']=='labeled'
  assert all(m['report']['metrics'] is None for m in report['models'].values()) # not yet evaluated, no false 0%
  changed.criteria[0].rationale='Independent human revision'
  client.post('/api/benchmark/annotations',json=changed.model_dump())
  assert client.get('/api/benchmark/comparison').json()['expert_labeled']==17
  assert count(lab,'consensus')==18 # history preserved
 assert not lab['tracker']['posts']


def test_calibration_plan_excludes_holdout_requires_consensus_and_approval(lab):
 with lab['factory']() as s:
  p=batch.create_plan(s,batch.PlanOptions(mode='calibration',concurrency=3,limit_usd='1'),lab['catalog'])
 expected={c['conversation_id'] for c in lab['manifest']['cases'] if c['split']=='calibration'}
 assert p['evaluation_count']==18 and p['dialogue_count']==9
 assert {j['conversation_id'] for j in p['jobs']}==expected
 assert len(p['jobs'])==18 and p['provider_requests_expected']==36 and p['provider_requests_max']==72
 assert p['deadline_bound_seconds']==2400 and p['fits_deadline']
 assert not any(p['human_consensus_ids'].values())
 assert 'EXPERT_LABEL_SECRET' not in str(p)
 with pytest.raises(ValueError,match='Paid execution disabled'):asyncio.run(batch.execute(lab['factory'],p['id'],batch.Approval(approved=True,plan_sha256=p['plan_sha256'],evaluation_count=18,limit_usd='1')))
 with pytest.raises(ValueError,match='human consensus'):asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
 assert not lab['tracker']['posts'] and not lab['tracker']['gets']


def test_calibration_execution_checks_key_reserve_before_any_generation(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');save_round(client,'consensus','Adjudicator')
 with lab['factory']() as s:p=batch.create_plan(s,batch.PlanOptions(mode='calibration',concurrency=3,limit_usd='1'),lab['catalog'])
 # lab gateway limits omit a finite key remaining value; that must fail closed.
 with pytest.raises(ValueError,match='wallet and key allowance'):asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
 assert not lab['tracker']['posts']
 with lab['factory']() as s:
  assert s.get(BenchmarkRunRow,p['id']).status=='planned'
  assert s.scalar(select(func.count()).select_from(EvaluationRow))==0


def test_stale_consensus_snapshot_requires_fresh_calibration_plan(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');save_round(client,'consensus','Adjudicator')
  with lab['factory']() as s:p=batch.create_plan(s,batch.PlanOptions(mode='calibration',concurrency=3,limit_usd='1'),lab['catalog'])
  cid=p['jobs'][0]['conversation_id'];client.post('/api/benchmark/annotations',json=labels(cid,'A','Alice').model_dump())
 with pytest.raises(ValueError,match='human consensus'):asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
 assert not lab['tracker']['posts'] and not lab['tracker']['gets']


def test_approved_calibration_with_mock_quota_executes_only_nine_inputs(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');save_round(client,'consensus','Adjudicator')
 with lab['factory']() as s:p=batch.create_plan(s,batch.PlanOptions(mode='calibration',concurrency=3,limit_usd='1'),lab['catalog'])
 original=batch.connection_status
 async def mock_limits():
  result=await original();result['account_balance']['tokens']=100_000_000;result['key_limits']['limit_remaining']=100_000_000;return result
 lab['monkeypatch'].setattr(batch,'connection_status',mock_limits)
 out=asyncio.run(batch.execute(lab['factory'],p['id'],approve(lab,p)))
 assert len(out['results'])==18 and all(r['status']=='ok' for r in out['results'])
 expected={c['conversation_id'] for c in lab['manifest']['cases'] if c['split']=='calibration'}
 assert {r['conversation_id'] for r in out['results']}==expected
 assert len(lab['tracker']['posts'])==36
 # HTTP fixture asserts human EXPERT_LABEL_SECRET never appears in model requests.


def test_legacy_single_consensus_cannot_bypass_complete_round_gate(lab):
 cid=lab['manifest']['cases'][0]['conversation_id']
 with TestClient(main.app) as client:
  a=client.post('/api/benchmark/annotations',json=labels(cid,'A','Alice').model_dump()).json()
  b=client.post('/api/benchmark/annotations',json=labels(cid,'B','Bob').model_dump()).json()
  final=labels(cid,'consensus','Human');final.based_on=[a['id'],b['id']];final.decision_reason='Reviewed'
  assert client.post('/api/benchmark/annotations',json=final.model_dump()).status_code==422
  assert client.get('/api/benchmark/cases/'+cid+'?slot=consensus').json()['independent_rounds']=={}
 assert count(lab,'consensus')==0
