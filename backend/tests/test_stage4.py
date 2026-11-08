"""Human-only workflow and precise evidence diagnostics. All writes use isolated lab SQLite."""
import asyncio,json
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from backend.app import annotations,benchmark_report,benchmark_suite,main,multiai,ai_output
from backend.app.ai_diagnostics import diagnostics,OutputError
from backend.app.domain import Conversation
from backend.app.checklist import OFFICIAL
from backend.app.benchmark_metrics import compare
from backend.app.engine import evaluate
from test_stage3 import lab,labels,complete_other_cases
from test_multiai import env,conversation,answers
from test_reliability import install_http

@pytest.mark.parametrize('turns,category',[
 ([], 'evidence_empty'),([1,1],'evidence_duplicate_index'),([999],'evidence_index_out_of_range'),
 ([0],'evidence_zero_index'),([-1],'evidence_negative_index'),(['1'],'evidence_index_type'),
 ([True],'evidence_index_type'),([1.0],'evidence_index_type'),([{}],'evidence_index_type'),([[]],'evidence_index_type'),
])
def test_detailed_index_errors_before_hashing(turns,category):
 with pytest.raises(OutputError) as caught:ai_output.quotes_for(turns,conversation(),('evidence',))
 d=diagnostics(caught.value,'fact_extraction')[0]
 assert d['category']==category and d['stage']=='fact_extraction'
 assert 'input' not in d and 'quote' not in d

@pytest.mark.parametrize('turns,category',[
 ([], 'evidence_empty'),([1,1],'evidence_duplicate_index'),([999],'evidence_index_out_of_range'),
 ([0],'evidence_zero_index'),([-1],'evidence_negative_index'),(['1'],'evidence_index_type'),([True],'evidence_index_type'),
])
@pytest.mark.parametrize('model',['gpt-6.1-sol','gemini-3.8-flash'])
def test_fact_stage_index_diagnostics_via_mock_http(env,turns,category,model):
 # A fixture mutation at the actual facts response boundary; no values or numbering repaired.
 c=conversation('KZ');case={'name':'fact-indices','target':'facts','path':['evidence'],'op':'set','value':[{'turn':t,'observation':'explicit local fixture'} for t in turns],'category':category}
 posts=install_http(env,c,model,case);result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider(model),attempts=1))
 assert result['status']=='error' and result['criteria']==[] and result['score'] is None
 assert result['validation_failures'][0]['diagnostics'][0]['category']==category
 assert len(posts)==1 and 'STAGE 1:' in posts[0]['messages'][0]['content']


def test_one_based_selection_never_shifted():
 c=conversation();c.transcript.extend([c.transcript[0].model_copy(update={'text':'SECOND'}),c.transcript[0].model_copy(update={'text':'THIRD'})])
 assert ai_output.quotes_for([1,3],c,('evidence',))==[{'turn':1,'quote':c.transcript[0].text},{'turn':3,'quote':'THIRD'}]
 # [1,2] could have been intended as zero-based, but intent cannot be inferred. No auto correction.
 assert ai_output.quotes_for([1,2],c,('evidence',))[1]['quote']=='SECOND'


def test_report_does_not_disclose_rounds_or_consensus_answers(lab):
 cid=lab['manifest']['cases'][0]['conversation_id']
 with TestClient(main.app) as client:
  a=client.post('/api/benchmark/annotations',json=labels(cid,'A','Alice').model_dump()).json()
  early=client.get('/api/benchmark/cases/'+cid+'?slot=consensus').json()
  assert early['independent_rounds']=={} and early['adjudication']['based_on']==[]
  b=client.post('/api/benchmark/annotations',json=labels(cid,'B','Bob',critical=True).model_dump()).json()
  for slot in ('A','B'):
   data=client.get('/api/benchmark/cases/'+cid+'?slot='+slot).json()
   assert data['annotation']['slot']==slot and 'independent_rounds' not in data
   assert 'context' in data and 'evaluations' not in data
  report=client.get('/api/benchmark/comparison').json()
  assert report['status']=='unlabeled' and report['expert_labeled']==0
  assert 'EXPERT_LABEL_SECRET' not in json.dumps(report)
  assert all(set(s)=={'conversation_id','status','rounds','disagreement_count','based_on'} for s in report['annotations'])
  with lab['factory']() as s:complete_other_cases(s,lab['manifest'],cid)
  final=labels(cid,'consensus','Human adjudicator',critical=True);final.based_on=[a['id'],b['id']];final.decision_reason='Compared original transcript'
  final.criterion_decisions={}
  assert client.post('/api/benchmark/annotations',json=final.model_dump()).status_code==422
  final.criterion_decisions={'critical':'Human fixture resolution based on original turn'}
  assert client.post('/api/benchmark/annotations',json=final.model_dump()).status_code==201
  report=client.get('/api/benchmark/comparison').json();assert report['expert_labeled']==1
  assert 'EXPERT_LABEL_SECRET' not in json.dumps(report)
 assert not lab['tracker']['posts']


def test_rationale_only_disagreement_requires_explicit_reason(lab):
 cid=lab['manifest']['cases'][0]['conversation_id']
 with lab['factory']() as s:
  a=annotations.save(s,labels(cid,'A','Alice'))
  b=labels(cid,'B','Bob');b.criteria[0].rationale='Different independent human reasoning'
  bb=annotations.save(s,b);m,cs=benchmark_suite.load(s);st=annotations.state(s,m,cs[cid])
  assert st['disagreements'][0]['rationale_disagreement'] and not st['disagreements'][0]['value_disagreement']
  complete_other_cases(s,m,cid)
  final=labels(cid,'consensus','Human');final.based_on=[a['id'],bb['id']];final.decision_reason='Reviewed'
  final.criterion_decisions={}
  with pytest.raises(ValueError,match='Every disagreement'):annotations.save(s,final)
  final.criterion_decisions={'contact':'The two explanations refer to the same greeting'}
  annotations.save(s,final);assert annotations.state(s,m,cs[cid])['status']=='consensus'
  # New human version invalidates previous consensus without rewriting it.
  annotations.save(s,b);assert annotations.state(s,m,cs[cid])['status']=='needs_adjudication'


def test_no_round_or_ai_label_can_become_consensus_automatically(lab):
 cid=lab['manifest']['cases'][0]['conversation_id']
 with lab['factory']() as s:
  a=annotations.save(s,labels(cid,'A','Alice'))
  c=labels(cid,'consensus','Human');c.based_on=[a['id']];c.decision_reason='not enough'
  with pytest.raises(ValueError):annotations.save(s,c)
  with pytest.raises(ValueError):annotations.Annotation.model_validate({**labels(cid,'B','Bob').model_dump(),'origin':'ai'})
  assert benchmark_report.report(s)['status']=='unlabeled'


def test_accuracy_uses_criteria_with_na_scales_languages_and_missing():
 conversations={lang:Conversation(conversation_id=lang,channel='agent chat',language=lang,transcript=[{'timestamp':'0','speaker':'operator','text':'Local fixture'}]) for lang in ('RU','KZ','EN')}
 def criteria():return [{'criterion_id':c.id,'value':False if c.id=='critical' else True if c.type=='boolean' else max(c.scale)} for c in OFFICIAL.criteria]
 truth={cid:{'criteria':criteria()} for cid in conversations}
 truth['RU']['criteria'][1]['value']=None
 truth['KZ']['criteria'][-1]['value']=True;truth['EN']['criteria'][-1]['value']=True
 ru=deepcopy(truth['RU']['criteria']);ru[1]['value']=True;ru[2]['value']=None;ru[3]['value']=15;ru[5]['value']=7;ru[-1]['value']=True
 preds=[{'conversation_id':'RU','status':'ok','criteria':ru,'score':{'final_score':0}}, {'conversation_id':'KZ','status':'ok','criteria':deepcopy(truth['KZ']['criteria']),'score':{'final_score':0}}, {'conversation_id':'EN','status':'error','criteria':[],'score':None}]
 r=compare(preds,truth,conversations);metrics=r['metrics'];o=metrics['overall']
 assert o['correct']==11 and o['total']==24 and o['accuracy']==11/24
 assert metrics['language:RU']['accuracy']==3/8 and metrics['language:KZ']['accuracy']==1 and metrics['language:EN']['accuracy']==0
 assert metrics['criterion:consultation']['scale_mae']==2.5 and metrics['criterion:dialogue']['scale_mae']==1.5
 assert o['na_matrix']=={'actual_na_pred_na':0,'actual_na_pred_applicable':1,'actual_applicable_pred_na':1,'actual_applicable_pred_applicable':14,'missing':8}
 assert o['critical_precision']==.5 and o['critical_recall']==.5
 assert compare(preds,{},conversations)['metrics'] is None
 for p in preds:p['score']={'final_score':100}
 assert compare(preds,truth,conversations)['metrics']==metrics
