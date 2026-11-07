import asyncio
import json
import httpx
import pytest
from backend.app import providers
from backend.app.checklist import OFFICIAL
from backend.app.domain import Conversation

@pytest.mark.parametrize('kind',['openai','gemini'])
def test_structured_adapter_without_network(monkeypatch,kind):
    monkeypatch.setenv('ALLOW_PAID_CALLS','true')
    monkeypatch.setenv(kind.upper()+'_API_KEY','test-key-not-real')
    monkeypatch.setenv(kind.upper()+'_MODEL','test-model')
    monkeypatch.setenv('INPUT_USD_PER_MILLION','1');monkeypatch.setenv('OUTPUT_USD_PER_MILLION','2')
    monkeypatch.setattr(providers,'reserve',lambda *args:.1)
    original=httpx.AsyncClient
    def handler(request):
        body=json.loads(request.content)
        if kind=='openai':
            assert body['response_format']['json_schema']['strict'] is True
            assert request.headers['Authorization']=='Bearer test-key-not-real'
            return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}],'usage':{'prompt_tokens':100,'completion_tokens':50}})
        assert body['generationConfig']['responseMimeType']=='application/json'
        assert request.headers['x-goog-api-key']=='test-key-not-real'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'{}'}]}}],'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':50}})
    monkeypatch.setattr(providers.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    c=Conversation(conversation_id='test',language='EN',channel='agent chat',transcript=[{'timestamp':'00:00','speaker':'operator','text':'Hello'}])
    reply=asyncio.run(providers.ExternalProvider(kind).generate(c,OFFICIAL))
    assert reply.text=='{}' and reply.cost_usd==.0002
