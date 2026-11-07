import pytest
from backend.app.domain import Conversation
from backend.app.exporter import validate_export

def fixture():
    records=[Conversation(conversation_id=str(i),channel='agent chat',language='RU',transcript=[{'timestamp':'00:00','speaker':'client','text':'Question'}]) for i in range(10)]
    payload={'schema_version':'1.0','team_name':'CQA','model':{'mode':'local','name':'No model used'},'results':[{'conversation_id':c.conversation_id,'status':'error','criteria':[],'score':None,'error':'Not evaluated'} for c in records]}
    return records,payload

def test_exact_dataset_count_and_ids():
    records,payload=fixture()
    validate_export(payload,records,'PROD')
    with pytest.raises(ValueError):validate_export(payload,records,'DEV')
    payload['results'][0]['conversation_id']='missing'
    with pytest.raises(ValueError):validate_export(payload,records,'PROD')

def test_duplicates_and_unknown_fields():
    records,payload=fixture()
    payload['results'][0]['extra']='invalid'
    with pytest.raises(ValueError):validate_export(payload,records,'PROD')
    del payload['results'][0]['extra']
    payload['results'][0]['conversation_id']='1'
    with pytest.raises(ValueError):validate_export(payload,records,'PROD')

def test_successful_export_checks_evidence_and_score():
    from backend.app.checklist import OFFICIAL
    records,payload=fixture()
    criteria=[{'criterion_id':c.id,'value':False if c.id=='critical' else True if c.type=='boolean' else max(c.scale),'rationale':'Verified against transcript','evidence_turns':[1]} for c in OFFICIAL.criteria]
    payload['results']=[{'conversation_id':c.conversation_id,'status':'ok','criteria':[dict(a) for a in criteria],'score':{'final_score':100.0,'critical_flag':False}} for c in records]
    validate_export(payload,records,'PROD')
    payload['results'][0]['criteria'][0]['evidence_turns']=[2]
    with pytest.raises(ValueError):validate_export(payload,records,'PROD')
    payload['results'][0]['criteria'][0]['evidence_turns']=[1]
    payload['results'][0]['score']['final_score']=99.99
    with pytest.raises(ValueError):validate_export(payload,records,'PROD')
