"""AI review fixtures are separate from human labels and never invoke a provider."""
from copy import deepcopy
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.app import main,ai_draft_review,benchmark_report
from backend.app.checklist import OFFICIAL
from backend.app.storage import BenchmarkAnnotationRow,EvaluationRow
from test_stage3 import lab

@pytest.fixture
def draft(lab):
 return {'format':'cqa-ai-review-draft-v1','origin':'AI_ASSISTED_DRAFT_NOT_HUMAN','ground_truth':False,'criteria_order':[c.id for c in OFFICIAL.criteria],'cases':[{'conversation_id':c['conversation_id'],'language':c['language'],'split':c['split'],'values':[False if x.id=='critical' else True if x.type=='boolean' else max(x.scale) for x in OFFICIAL.criteria],'evidence_turns':[[1] for _ in OFFICIAL.criteria],'rationales':['Explicit synthetic offline fixture' for _ in OFFICIAL.criteria],'na_reasons':{},'review_flags':['identification: needs review']} for c in lab['manifest']['cases']]}

def test_validate_confirm_roundtrip_no_ground_truth_writes(lab,draft):
 with TestClient(main.app) as client:
  r=client.post('/api/benchmark/ai-drafts/validate',json=draft);assert r.status_code==200,r.text
  validated=r.json();assert len(validated['draft']['cases'])==18
  ids=[c['conversation_id'] for c in draft['cases']]
  r=client.post('/api/benchmark/ai-drafts/review',json={'draft':validated['draft'],'reviewed_ids':ids,'confirmed':True})
  assert r.status_code==200,r.text
  saved=r.json()['draft'];assert saved['review']['confirmed'] and saved['review']['reviewed_at']
  assert saved['ground_truth'] is False and saved['origin']=='AI_ASSISTED_DRAFT_NOT_HUMAN'
  assert client.post('/api/benchmark/ai-drafts/validate',json=saved).status_code==200
  assert client.post('/api/benchmark/annotations',json=saved).status_code==422
  assert client.post('/api/benchmark/annotation-import',json=[saved]).status_code==422
  report=client.get('/api/benchmark/comparison').json();assert report['expert_labeled']==0 and report['status']=='unlabeled'
 with lab['factory']() as s:
  assert s.scalar(select(func.count()).select_from(BenchmarkAnnotationRow))==0
  assert s.scalar(select(func.count()).select_from(EvaluationRow))==0
 assert not lab['tracker']['posts'] and not lab['tracker']['gets']

@pytest.mark.parametrize('failure',['missing_case','duplicate_id','unknown_id','missing_criterion','duplicate_criterion','wrong_boolean','wrong_scale','float_scale','short_values','short_evidence','short_rationales','empty_rationale','empty_evidence','zero','out_of_range','duplicates','string_index','bool_index','forbidden_na','missing_na_reason','extra_na_reason','unknown_flag','human_origin','ground_truth','wrong_language','wrong_split','unknown_field'])
def test_reject_invalid_draft(lab,draft,failure):
 c=draft['cases'][0]
 if failure=='missing_case':draft['cases'].pop()
 if failure=='duplicate_id':draft['cases'][1]=deepcopy(c)
 if failure=='unknown_id':c['conversation_id']='NOT_IN_SUITE'
 if failure=='missing_criterion':draft['criteria_order'].pop()
 if failure=='duplicate_criterion':draft['criteria_order'][-1]='contact'
 if failure=='wrong_boolean':c['values'][0]=1
 if failure=='wrong_scale':c['values'][5]=5
 if failure=='float_scale':c['values'][5]=7.0
 if failure=='short_values':c['values'].pop()
 if failure=='short_evidence':c['evidence_turns'].pop()
 if failure=='short_rationales':c['rationales'].pop()
 if failure=='empty_rationale':c['rationales'][0]=' '
 if failure=='empty_evidence':c['evidence_turns'][0]=[]
 if failure=='zero':c['evidence_turns'][0]=[0]
 if failure=='out_of_range':c['evidence_turns'][0]=[2]
 if failure=='duplicates':c['evidence_turns'][0]=[1,1]
 if failure=='string_index':c['evidence_turns'][0]=['1']
 if failure=='bool_index':c['evidence_turns'][0]=[True]
 if failure=='forbidden_na':c['values'][0]=None;c['na_reasons']['contact']='Fixture'
 if failure=='missing_na_reason':c['values'][1]=None
 if failure=='extra_na_reason':c['na_reasons']['discovery']='Not actually N/A'
 if failure=='unknown_flag':c['review_flags']=['invented: flag']
 if failure=='human_origin':draft['origin']='human'
 if failure=='ground_truth':draft['ground_truth']=True
 if failure=='wrong_language':c['language']='EN' if c['language']!='EN' else 'RU'
 if failure=='wrong_split':c['split']='holdout' if c['split']=='calibration' else 'calibration'
 if failure=='unknown_field':c['final_score']=100
 with TestClient(main.app) as client:assert client.post('/api/benchmark/ai-drafts/validate',json=draft).status_code==422
 assert not lab['tracker']['posts']

def test_valid_na_and_reordered_vectors(lab,draft):
 for c in draft['cases']:
  c['values'][1]=None;c['na_reasons']={'discovery':'Request was already complete'}
  for field in ('values','evidence_turns','rationales'):c[field].reverse()
 draft['criteria_order'].reverse()
 with lab['factory']() as s:
  r=ai_draft_review.validate(s,ai_draft_review.Draft.model_validate(draft))
 assert r['draft']['criteria_order'][0]=='critical'
 assert r['draft']['cases'][0]['values'][6] is None


def test_review_requires_every_id_and_matching_content(lab,draft):
 with TestClient(main.app) as client:
  validated=client.post('/api/benchmark/ai-drafts/validate',json=draft).json()['draft']
  ids=[c['conversation_id'] for c in draft['cases']]
  for invalid in (ids[:-1],ids[:-1]+[ids[0]],ids[:-1]+['unknown']):
   assert client.post('/api/benchmark/ai-drafts/review',json={'draft':validated,'reviewed_ids':invalid,'confirmed':True}).status_code==422
  partial=client.post('/api/benchmark/ai-drafts/review',json={'draft':validated,'reviewed_ids':ids[:1],'confirmed':False}).json()['draft']
  assert partial['review']['confirmed'] is False
  reviewed=client.post('/api/benchmark/ai-drafts/review',json={'draft':validated,'reviewed_ids':ids,'confirmed':True}).json()['draft']
  reviewed['cases'][0]['rationales'][0]='Edited after review'
  assert client.post('/api/benchmark/ai-drafts/validate',json=reviewed).status_code==422
  reviewed['review']=None
  assert client.post('/api/benchmark/ai-drafts/validate',json=reviewed).status_code==200


def test_transcript_endpoint_contains_no_expert_labels(lab,draft):
 cid=draft['cases'][0]['conversation_id']
 with TestClient(main.app) as client:
  result=client.get('/api/benchmark/ai-drafts/cases/'+cid).json()
  assert set(result)=={'conversation','context','checklist'}
  assert result['conversation']['transcript'][0]['text']=='Здравствуйте! Я Анна.'
  assert client.get('/api/benchmark/ai-drafts/cases/unknown').status_code==404
  changed=client.post('/api/benchmark/ai-drafts/validate',json=draft).json()['draft']
  changed['context']['manifest_sha256']='different'
  assert client.post('/api/benchmark/ai-drafts/validate',json=changed).status_code==422
