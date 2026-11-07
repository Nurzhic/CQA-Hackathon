import json
import os
import pytest
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.storage import setup
from backend.app.checklist import OFFICIAL
from backend.tests.test_multiai import env,transport,conversation,answers

@pytest.fixture
def app_client(env):
    env.setattr(main,'Session',setup(os.environ['DATABASE_URL']))
    with TestClient(main.app) as client:yield client

def import_sample(client):
    records=[]
    for lang in ('RU','KZ','EN'):
        for i in range(6):
            c=conversation(lang).model_dump();c['conversation_id']=lang+str(i);records.append(c)
    assert client.post('/api/import',json={'dataset':'DEV','records':records}).status_code==201

def test_estimate_approval_persist_expert_and_report(app_client,env):
    import_sample(app_client);posts,_=transport(env,conversation())
    body={'conversation_ids':['RU0'],'provider':'multiai','model':'gpt-6.1-sol'}
    quote=app_client.post('/api/evaluate/estimate',json=body)
    assert quote.status_code==200,quote.text
    assert quote.json()['requests_expected']==2 and quote.json()['requests_max']==4
    assert not posts
    assert app_client.post('/api/evaluate',json=body).status_code==422
    output=app_client.post('/api/evaluate',json={**body,'confirm_cost':True})
    assert output.status_code==200,output.text
    assert output.json()[0]['status']=='ok' and len(posts)==2
    assert app_client.get('/api/multiai/stats').json()['request_count']==2
    sample=app_client.get('/api/benchmark/sample').json()
    assert len(sample)==18 and all(r['channel']=='agent chat' for r in sample)
    assert app_client.get('/api/benchmark/report').json()['models']['gpt-6.1-sol']['report'] is None
    labels=answers(conversation())[1]['criteria']
    label=app_client.post('/api/benchmark/labels',json={'conversation_id':'RU0','reviewer':'Test expert','criteria':labels})
    assert label.status_code==201,label.text
    report=app_client.get('/api/benchmark/report').json()
    assert report['expert_labeled']==0 and report['status']=='unlabeled' and report['models']['gpt-6.1-sol']['report'] is None
    assert app_client.post('/api/benchmark/labels',json={'conversation_id':'RU0','reviewer':'Test expert','criteria':labels}).status_code==201
    assert len(app_client.get('/api/benchmark/labels').json())==2
    settings=app_client.get('/api/settings').json()
    assert 'test-secret-never-log' not in json.dumps(settings)
    assert [p['id'] for p in settings['providers']]==['mock','multiai']

def test_bulk_and_call_guards(app_client,env):
    import_sample(app_client);posts,_=transport(env,conversation())
    body={'conversation_ids':['RU0','RU1'],'provider':'multiai','confirm_cost':True}
    assert app_client.post('/api/evaluate',json=body).status_code==422 and not posts
    env.setenv('MULTIAI_MAX_BATCH','100')
    body['conversation_ids']=['RU0','RU1','RU2','RU3']
    assert app_client.post('/api/evaluate',json=body).status_code==422 and not posts
    c=conversation().model_dump();c.update(conversation_id='real-test',channel='voice call')
    app_client.post('/api/import',json={'dataset':'DEV','records':[c]})
    assert app_client.post('/api/evaluate/estimate',json={**body,'conversation_ids':['real-test']}).status_code==422
    assert not posts
