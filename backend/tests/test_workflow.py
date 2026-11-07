import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.domain import Conversation
from backend.app.engine import evaluate
from backend.app.checklist import OFFICIAL
from backend.app.providers import ProviderReply, MockProvider
from backend.app.storage import setup

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'Session',setup('sqlite:///'+str(tmp_path/'test.db')))
    with TestClient(main.app) as c: yield c

def test_full_workflow(client):
    assert client.get('/api/health').json()['status']=='ok'
    assert client.post('/api/demo').status_code==200
    cid='demo-local-001'
    assert client.get('/api/conversations').json()[0]['turns']==5
    body={'conversation_ids':[cid],'provider':'mock','checklist_id':1}
    evaluation=client.post('/api/evaluate',json=body).json()[0]
    assert evaluation['mock'] and evaluation['status']=='ok'
    original=client.get('/api/checklists').json()[0]
    custom={k:original[k] for k in ['name','criteria']};custom['name']='Custom v2'
    custom['criteria'][0]['weight']=25
    custom['criteria'].append({'id':'new_rule','name':'New criterion','description':'Operator must provide a next step','weight':5,'type':'boolean'})
    version=client.post('/api/checklists',json=custom).json()
    assert version['id']==2
    revised=client.post('/api/evaluate',json={**body,'checklist_id':2}).json()[0]
    assert len(revised['criteria'])==9
    assert client.get('/api/checklists').json()[0]['criteria'][0]['weight']==10
    appeal=client.post('/api/appeals',json={'evaluation_id':evaluation['id'],'criterion_id':'contact','comment':'Приветствие присутствует'}).json()
    assessment={**evaluation['criteria'][0],'value':True,'rationale':'Поздоровалась и представилась','uncertain':False,'uncertainty_reason':None,'evidence_turns':[3]}
    decision=client.post(f"/api/appeals/{appeal['id']}/decision",json={'action':'change','reason':'Подтверждено репликой 3','assessment':assessment})
    assert decision.status_code==200,decision.text
    assert decision.json()['score']['final_score']==10
    detail=client.get('/api/conversations/'+cid).json()
    assert len(detail['evaluations'])==3
    assert detail['evaluations'][-1]['score']['final_score']==0
    assert client.post(f"/api/appeals/{appeal['id']}/decision",json={'action':'confirm','reason':'Again'}).status_code==409
    assert client.get('/api/export/PROD').status_code==422
    assert client.post('/api/evaluate',json={**body,'provider':'openai'}).status_code==422

def test_import_immutable_duplicate(client):
    data={'dataset':'DEV','records':[{'conversation_id':'test','channel':'agent chat','language':'KZ','transcript':[{'timestamp':'00:00','speaker':'bot','text':'Сәлем'}]}]}
    assert client.post('/api/import',json=data).status_code==201
    assert client.post('/api/import',json={**data,'records':data['records']*2}).status_code==422
    data['records'][0]['transcript'][0]['text']='Changed'
    assert client.post('/api/import',json=data).status_code==422

def test_retry_invalid_evidence():
    class BadProvider(MockProvider):
        async def generate(self,c,ch,feedback=''):
            reply=await super().generate(c,ch,feedback)
            data=json.loads(reply.text);data['criteria'][0]['evidence_turns']=[999]
            return ProviderReply(json.dumps(data),{})
    c=Conversation(conversation_id='x',channel='agent chat',language='EN',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Hello'}])
    output=asyncio.run(evaluate(c,OFFICIAL,BadProvider()))
    assert output['status']=='error' and output['attempts']==2

def test_mock_cannot_be_officially_exported(client):
    client.post('/api/import',json={'dataset':'DEV','records':[{'conversation_id':'dev-test','channel':'agent chat','language':'RU','transcript':[{'timestamp':'00:00','speaker':'operator','text':'Hello'}]}]})
    client.post('/api/evaluate',json={'conversation_ids':['dev-test']})
    response=client.get('/api/export/DEV')
    assert response.status_code==422 and 'mock' in response.json()['detail']

def test_na_cannot_contradict_extracted_facts():
    class ContradictingProvider(MockProvider):
        async def generate(self,c,ch,feedback=''):
            reply=await super().generate(c,ch,feedback)
            data=json.loads(reply.text)
            data['criteria'][1]['value']=None;data['criteria'][1]['na_reason']='Request is complete'
            return ProviderReply(json.dumps(data),{})
    c=Conversation(conversation_id='x',channel='agent chat',language='EN',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Hello'}])
    output=asyncio.run(evaluate(c,OFFICIAL,ContradictingProvider()))
    assert output['status']=='error'
