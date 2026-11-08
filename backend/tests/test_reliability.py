"""Synthetic offline regressions. Every HTTP request is intercepted; no historical bodies used."""
import asyncio
import json
from pathlib import Path
from copy import deepcopy
import httpx
import pytest
from backend.app import multiai,ai_output,benchmark_report,batch
from backend.app.ai_diagnostics import diagnostics,OutputError
from backend.app.engine import evaluate
from backend.app.checklist import OFFICIAL
from backend.app.domain import Conversation,EvidenceQuote,EvaluationOutput
from backend.app.providers import ProviderReply
from test_multiai import env,conversation,answers

CASES=json.loads(Path('backend/tests/fixtures/reliability/cases.json').read_text())
MODELS=['gpt-6.1-sol','gemini-3.8-flash']

def install_http(monkeypatch,c,model,case):
 facts,criteria=answers(c);posts=[];original=httpx.AsyncClient
 def handler(request):
  if request.method=='GET':return httpx.Response(200,json={'data':[{'id':m,'availability':'available','model_kind':'text','input_multiplier':1,'output_multiplier':1} for m in MODELS]})
  body=json.loads(request.content);posts.append(body)
  assert body['model']==model and request.url.host=='multiai.store'
  assert body['response_format']['type']=='json_schema' and body['response_format']['json_schema']['strict'] is True
  fact='STAGE 1:' in body['messages'][0]['content'];obj=deepcopy(facts if fact else criteria)
  target=case['target']==('facts' if fact else 'criteria')
  op=case['op'] if target else 'none'
  if op in ('set','delete'):
   node=obj
   for k in case['path'][:-1]:node=node[k]
   if op=='delete':del node[case['path'][-1]]
   else:node[case['path'][-1]]=case['value']
  if op in ('na','na_reason'):
   obj['criteria']['discovery'].update(value=None,na_reason='No missing request details' if op=='na' else None)
  if op=='uncertain':obj['criteria']['contact'].update(uncertain=True,uncertainty_reason=None)
  if op=='envelope':return httpx.Response(200,json=case['value'])
  if op=='envelope_raw':return httpx.Response(200,text=case['value'])
  content=case['value'] if op=='raw' else json.dumps(obj,ensure_ascii=False)
  envelope={'model':model,'choices':[{'finish_reason':'length' if op=='truncate' else 'stop','message':{'content':content}}],'usage':{'prompt_tokens':123,'completion_tokens':45,'total_tokens':168}}
  if op=='choices':envelope['choices']=case['value']
  return httpx.Response(200,json=envelope)
 monkeypatch.setattr(multiai.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
 return posts

@pytest.mark.parametrize('model',MODELS)
@pytest.mark.parametrize('case',CASES,ids=lambda c:c['name'])
def test_offline_contract_categories_paths_and_redaction(env,model,case):
 c=conversation('KZ');posts=install_http(env,c,model,case)
 result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider(model),attempts=1))
 assert 'PRIVATE_TOKEN_AND_CLIENT_TEXT' not in json.dumps(result,ensure_ascii=False)
 if case['category'] is None:
  assert result['status']=='ok' and len(posts)==2
  assert all(a['evidence_quotes']==[{'turn':1,'quote':c.transcript[0].text}] for a in result['criteria'])
  assert result['provider_metadata']['evidence_semantics_verified'] is False
 else:
  assert result['status']=='error' and result['criteria']==[] and result['score'] is None
  d=result['attempt_errors'][0]['diagnostics']
  assert case['category'] in {a['category'] for a in d},d
  assert all(set(a)=={'category','path','stage','message'} for a in d)
  if case['name']=='missing_field':assert d[0]['path']==['criteria','contact','rationale']
  if case['name']=='extra_field':assert d[0]['path']==['criteria','contact','<field>']
  assert d[0]['stage']==('fact_extraction' if case['target']=='facts' else 'criteria_evaluation')
 assert result['usage'] and all(u['prompt_tokens']==123 for u in result['usage'] if u)
 assert len(result['provider_metadata']['calls'])==len(posts)

@pytest.mark.parametrize('text,language',[("Здравствуйте! Ёжик — помощь.",'RU'),('Сәлеметсіз бе! Әә Ғғ Ққ Ңң Өө Ұұ Үү Һһ Іі','KZ'),('Hello! Original quotation.','EN'),('Тарифті таптым, спасибо! Ещё помощь?','KZ')])
@pytest.mark.parametrize('model',MODELS)
def test_unicode_original_quotes_and_compact_schema(env,text,language,model):
 c=conversation(language);c.transcript[0].text=text
 posts=install_http(env,c,model,CASES[0]);result=asyncio.run(evaluate(c,OFFICIAL,multiai.MultiAIProvider(model)))
 assert result['status']=='ok'
 assert all(a['evidence_quotes'][0]['quote'].encode()==text.encode() for a in result['criteria'])
 schema=posts[1]['response_format']['json_schema']['schema']
 assert set(schema['properties'])=={'criteria'}
 assert set(schema['properties']['criteria']['properties'])=={c.id for c in OFFICIAL.criteria}
 def strict_objects(node):
  if isinstance(node,dict):
   if node.get('type')=='object':assert node['additionalProperties'] is False and set(node['required'])==set(node['properties'])
   for v in node.values():strict_objects(v)
  elif isinstance(node,list):
   for v in node:strict_objects(v)
 strict_objects(schema);strict_objects(posts[0]['response_format']['json_schema']['schema'])
 assert 'evidence_quotes' not in json.dumps(schema)
 assert 'anyOf' not in schema['properties']['criteria']

@pytest.mark.parametrize('turns',[[2],[1,2]])
def test_quotes_use_only_model_selected_turns(turns):
 c=conversation();c.transcript.append(c.transcript[0].model_copy(update={'text':'Клиент: где тариф?'}))
 facts,wire=answers(c)
 for a in wire['criteria'].values():a['evidence_turns']=turns
 out=ai_output.parse_criteria(json.dumps(wire),OFFICIAL,c,facts['facts'],facts['applicability'])
 assert all(a.evidence_turns==turns and [q.quote for q in a.evidence_quotes]==[c.transcript[i-1].text for i in turns] for a in out.criteria)
 # This fixture can be semantically irrelevant. Extraction guarantees provenance, never semantics.

@pytest.mark.parametrize('missing',[True,False])
def test_legacy_quotes_still_strictly_validated(missing):
 c=conversation();facts,wire=answers(c)
 out=ai_output.parse_criteria(json.dumps(wire),OFFICIAL,c,facts['facts'],facts['applicability'])
 if missing:out.criteria[0].evidence_quotes=[]
 else:out.criteria[0].evidence_quotes=[EvidenceQuote(turn=1,quote='Invented translation')]
 class LocalFixture:
  name='local-fixture';mock=False;verify_evidence_quotes=True
  async def generate(self,*args):return ProviderReply(out.model_dump_json(),{},metadata={'fact_applicability':facts['applicability']})
 result=asyncio.run(evaluate(c,OFFICIAL,LocalFixture(),attempts=1))
 assert result['status']=='error'
 assert result['validation_failures'][0]['diagnostics'][0]['category']==('missing_evidence_quote' if missing else 'quote_mismatch')

def test_valid_na_and_incomplete_transcript_uncertainty():
 c=conversation();facts,wire=answers(c)
 facts['applicability']['discovery']=False
 wire['criteria']['discovery'].update(value=None,na_reason='Initial request sufficient')
 wire['criteria']['contact'].update(uncertain=True,uncertainty_reason='Beginning omitted; human review needed')
 out=ai_output.parse_criteria(json.dumps(wire),OFFICIAL,c,facts['facts'],facts['applicability'])
 assert out.criteria[1].value is None and out.criteria[0].uncertain

def test_diagnostics_do_not_persist_arbitrary_exception_or_loc():
 assert 'PRIVATE' not in json.dumps(diagnostics(ValueError('PRIVATE API KEY')))
 assert diagnostics(OutputError('invalid_enum',('criteria','PRIVATE',3)))[0]['path']==['criteria','<field>',3]

def test_completed_historical_plan_not_reused(tmp_path):
 from backend.app.storage import BenchmarkRunRow
 row=BenchmarkRunRow(status='complete',payload={'plan':{'options':batch.PlanOptions().model_dump(mode='json')}})
 with pytest.raises(ValueError,match='already consumed'):batch.validate_run(None,row,None)

def test_hardening_implementation_is_in_pipeline_hash(monkeypatch):
 old=benchmark_report.pipeline_digest()
 original=Path.read_text
 def changed(path,*a,**kw):return original(path,*a,**kw)+('\n# regression' if path.name=='ai_output.py' else '')
 monkeypatch.setattr(Path,'read_text',changed)
 assert benchmark_report.pipeline_digest()!=old


def test_compact_synthetic_wire_is_smaller_without_discarding_rationale():
 c=conversation('KZ');facts,wire=answers(c)
 old={'facts':facts['facts'],'applicability':facts['applicability'],'criteria':[{'criterion_id':cid,**a,'evidence_quotes':[{'turn':1,'quote':c.transcript[0].text}]} for cid,a in wire['criteria'].items()]}
 assert len(json.dumps(wire,ensure_ascii=False).encode())<len(json.dumps(old,ensure_ascii=False).encode())
 out=ai_output.parse_criteria(json.dumps(wire),OFFICIAL,c,facts['facts'],facts['applicability'])
 assert [a.rationale for a in out.criteria]==[a['rationale'] for a in wire['criteria'].values()]


def test_exception_input_and_feedback_are_redacted():
 c=conversation();received=[]
 class LocalFixture:
  name='local-fixture';mock=False
  async def generate(self,c,checklist,feedback=''):
   received.append(feedback)
   raise ValueError('PRIVATE_TOKEN_AND_CLIENT_TEXT')
 result=asyncio.run(evaluate(c,OFFICIAL,LocalFixture(),max_backoff_seconds=0))
 assert result['status']=='error' and len(received)==2
 assert 'PRIVATE_TOKEN_AND_CLIENT_TEXT' not in json.dumps([result,received])


@pytest.mark.parametrize('cid',['model_config','model_dump'])
def test_custom_checklist_ids_do_not_become_python_fields(cid):
 from backend.app.domain import Criterion
 c=conversation();facts,wire=answers(c)
 checklist=OFFICIAL.model_copy(update={'criteria':[*OFFICIAL.criteria,Criterion(id=cid,name='Custom',description='Fixture',type='boolean',weight=1)]})
 wire['criteria'][cid]=deepcopy(wire['criteria']['contact']);facts['applicability'][cid]=True
 out=ai_output.parse_criteria(json.dumps(wire),checklist,c,facts['facts'],facts['applicability'])
 assert out.criteria[-1].criterion_id==cid and out.criteria[-1].value is True
