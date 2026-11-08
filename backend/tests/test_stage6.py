"""Calibration-only retrospective error analysis, no provider calls or working labels."""
from copy import deepcopy
from fastapi.testclient import TestClient
from backend.app import main,annotations,benchmark_suite,accuracy_analysis
from backend.app.storage import EvaluationRow
from test_stage3 import lab,labels
from test_stage5 import save_round

MODELS=['gpt-6.1-sol','gemini-3.8-flash']
def add_predictions(lab,pipeline='stage6-test'):
 with lab['factory']() as s:
  manifest,cases=benchmark_suite.load(s)
  for split in ('calibration','holdout'):
   cid=next(c['conversation_id'] for c in manifest['cases'] if c['split']==split)
   criteria=[a.model_dump() for a in labels(cid,'A','Human fixture').criteria]
   for model in MODELS:
    items=deepcopy(criteria);items[0]['value']=False;items[3]['value']=15
    failure={'attempt':1,'kind':'validation','error':'Invalid JSON','diagnostics':[{'category':'malformed_json','stage':'fact_extraction','path':[],'message':'Output is not a complete valid JSON document'}]}
    p={'model':model,'mock':False,'status':'ok' if model==MODELS[0] else 'error','criteria':items if model==MODELS[0] else [],'score':{'final_score':100} if model==MODELS[0] else None,'benchmark_context':{**annotations.context(manifest,cases[cid]),'pipeline_sha256':pipeline},'attempts':1,'latency_ms':10,'usage':[],'validation_failures':[] if model==MODELS[0] else [failure],'attempt_errors':[] if model==MODELS[0] else [failure]}
    s.add(EvaluationRow(conversation_id=cid,checklist_id=1,payload=p))
  s.commit()


def test_analysis_excludes_holdout_and_distinguishes_missing_from_na(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');save_round(client,'consensus','Human')
  add_predictions(lab)
  r=client.get('/api/benchmark/error-analysis?pipeline_sha256=stage6-test');assert r.status_code==200,r.text
  a=r.json();assert a['scope']=='calibration_only' and a['paired_labeled_cases']==1
  assert all(p['split']=='calibration' for p in a['problem_dialogues'])
  assert len(a['problem_dialogues'])==1
  assert a['models'][MODELS[0]]['criterion_errors']=={'contact':1,'consultation':1}
  assert a['models'][MODELS[1]]['json_errors']==1
  missing=a['problem_dialogues'][0]['models'][MODELS[1]]['differences']
  assert len(missing)==8 and all(d['kind']=='missing_result' and d['prediction_present'] is False for d in missing)
  assert all(r['scope']=='calibration' for r in a['recommendations'])
 assert not lab['tracker']['posts'] and not lab['tracker']['gets']


def test_no_consensus_means_no_invented_problem_labels(lab):
 add_predictions(lab)
 with lab['factory']() as s:a=accuracy_analysis.analyze(s,'stage6-test')
 assert a['paired_labeled_cases']==0 and a['problem_dialogues']==[]
 assert all(not m['criterion_errors'] for m in a['models'].values())


def test_stale_consensus_and_mismatched_pipeline_are_excluded(lab):
 with TestClient(main.app) as client:
  save_round(client,'A','Alice');save_round(client,'B','Bob');save_round(client,'consensus','Human')
  add_predictions(lab)
  with lab['factory']() as s:
   m,_=benchmark_suite.load(s);cid=next(c['conversation_id'] for c in m['cases'] if c['split']=='calibration')
   annotations.save(s,labels(cid,'A','Alice'))
   assert accuracy_analysis.analyze(s,'stage6-test')['paired_labeled_cases']==0
   assert accuracy_analysis.analyze(s,'not-evaluated')['problem_dialogues']==[]
 assert not lab['tracker']['posts']
