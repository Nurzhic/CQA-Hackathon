import json
import os
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import select
from .domain import StrictModel, Conversation, Checklist, Assessment, validate_assessments, calculate
from .checklist import OFFICIAL
from .storage import setup, ChecklistRow, ConversationRow, EvaluationRow, AppealRow, ExpertRow
from .providers import get_provider
from .engine import evaluate
from .exporter import validate_export

load_dotenv()
Session = setup()
@asynccontextmanager
async def lifespan(app):
    with Session() as s:
        if not s.get(ChecklistRow,1):
            s.add(ChecklistRow(id=1,payload=OFFICIAL.model_dump())); s.commit()
    yield
app = FastAPI(title='CQA API',version='0.1.0',lifespan=lifespan)

@app.exception_handler(ValueError)
async def value_error(request,exc):
    return JSONResponse(status_code=422,content={'detail':str(exc)})

def required(s,model,id):
    row = s.get(model,id)
    if not row: raise HTTPException(404,'Record not found')
    return row

def packed(row):
    return {'id':row.id,'conversation_id':row.conversation_id,'checklist_id':row.checklist_id,'created':row.created.isoformat(),**row.payload}

def latest(s,cid,checklist_id=None):
    query = select(EvaluationRow).where(EvaluationRow.conversation_id==cid)
    if checklist_id: query=query.where(EvaluationRow.checklist_id==checklist_id)
    return s.scalar(query.order_by(EvaluationRow.id.desc()).limit(1))

@app.get('/api/health')
def health(): return {'status':'ok'}

@app.get('/api/settings')
def settings():
    from .multiai import configuration
    from .gateway_budget import stats
    config=configuration()
    return {'team_name':os.getenv('TEAM_NAME','CQA'),'paid_calls_enabled':os.getenv('ALLOW_PAID_CALLS','false').lower()=='true','providers':[{'id':'mock','model':'MOCK — not AI','configured':True},{'id':'multiai','name':'MultiAI','model':config['primary_model'],'models':[m for m in (config['primary_model'],config['secondary_model']) if m],'configured':bool(os.getenv('MULTIAI_API_KEY') and config['primary_model'])}],'multiai':{**config,'key_configured':bool(os.getenv('MULTIAI_API_KEY')),'max_batch':int(os.getenv('MULTIAI_MAX_BATCH','1')),'bulk_enabled':os.getenv('MULTIAI_ALLOW_BULK','false').lower()=='true','generation_verified':False},'spending':stats(Session),'organizer_api_configured':bool(os.getenv('ORGANIZER_API_URL'))}


@app.get('/api/checklists')
def checklists():
    with Session() as s: return [{'id':r.id,**r.payload,'official':r.id==1} for r in s.scalars(select(ChecklistRow).order_by(ChecklistRow.id))]

@app.post('/api/checklists',status_code=201)
def create_checklist(body: Checklist):
    with Session() as s:
        row=ChecklistRow(payload=body.model_dump()); s.add(row); s.commit()
        return {'id':row.id,**row.payload,'official':False}

class ImportBody(StrictModel):
    dataset: Literal['DEV','PROD','DEMO']
    records: list[Conversation] = Field(min_length=1, max_length=500)

def import_records(body):
    ids=[c.conversation_id for c in body.records]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate conversation IDs')
    with Session() as s:
        for c in body.records:
            old=s.get(ConversationRow,c.conversation_id)
            if old and (old.payload!=c.model_dump() or old.dataset!=body.dataset):
                raise ValueError('Existing conversation is immutable; conflicting transcript or dataset')
        for c in body.records:
            if not s.get(ConversationRow,c.conversation_id): s.add(ConversationRow(id=c.conversation_id,dataset=body.dataset,payload=c.model_dump()))
        s.commit()
    return {'imported':len(ids),'dataset':body.dataset}

@app.post('/api/import',status_code=201)
def import_data(body:ImportBody): return import_records(body)

class RemoteImport(StrictModel):
    dataset: Literal['DEV','PROD']
    group_id: str = Field(min_length=1)

@app.post('/api/import/organizer')
async def import_organizer(body:RemoteImport):
    url=os.getenv('ORGANIZER_API_URL')
    if not url: raise ValueError('Configure ORGANIZER_API_URL in server .env')
    try:
        async with httpx.AsyncClient(timeout=60,follow_redirects=False) as client:
            # Preserve signature/query parameters, replace only group_id.
            target=httpx.URL(url).copy_merge_params({'group_id':body.group_id})
            res=await client.get(target)
        if res.status_code!=200: raise ValueError('Organizer API returned an error')
        data=res.json()
        if data.get('isSuccess') is not True or data.get('error'): raise ValueError('Organizer API reported failure')
        records=data['data']['records']
        if data['data']['total_records']!=len(records): raise ValueError('Organizer record count mismatch')
        return import_records(ImportBody(dataset=body.dataset,records=records))
    except (httpx.HTTPError,KeyError,json.JSONDecodeError):
        raise ValueError('Could not import organizer dataset; check server URL and response format') from None

@app.post('/api/demo')
def demo():
    return import_records(ImportBody(dataset='DEMO',records=[Conversation(conversation_id='demo-local-001',channel='agent chat',language='RU',transcript=[{'timestamp':'00:00','speaker':'client','text':'Здравствуйте! Где найти тарифы на обслуживание?'},{'timestamp':'00:01','speaker':'bot','text':'Подключаю специалиста.'},{'timestamp':'00:04','speaker':'operator','text':'Здравствуйте! Меня зовут Анна. Тарифы доступны на сайте компании в разделе «Тарифы». Уточните рынок, и я помогу выбрать нужный документ.'},{'timestamp':'00:25','speaker':'client','text':'Спасибо, нашёл.'},{'timestamp':'00:30','speaker':'operator','text':'Рада помочь! Если возникнут вопросы, обращайтесь. До свидания!'}])]))

@app.get('/api/conversations')
def conversations():
    with Session() as s:
        result=[]
        for c in s.scalars(select(ConversationRow).order_by(ConversationRow.id)):
            e=latest(s,c.id)
            result.append({'conversation_id':c.id,'dataset':c.dataset,'language':c.payload['language'],'channel':c.payload['channel'],'turns':len(c.payload['transcript']),'evaluation':packed(e) if e else None})
        return result

@app.get('/api/conversations/{cid}')
def conversation(cid:str):
    with Session() as s:
        c=required(s,ConversationRow,cid)
        return {**c.payload,'dataset':c.dataset,'evaluations':[packed(e) for e in s.scalars(select(EvaluationRow).where(EvaluationRow.conversation_id==cid).order_by(EvaluationRow.id.desc()))]}

class EvaluateBody(StrictModel):
    conversation_ids: list[str] = Field(min_length=1,max_length=100)
    checklist_id: int = 1
    provider: Literal['mock','multiai','openai','gemini'] = 'mock'
    allow_real_calls: bool = False
    model: str | None = None
    confirm_cost: bool = False

@app.post('/api/evaluate')
async def evaluate_batch(body:EvaluateBody):
    provider=get_provider(body.provider,body.model)
    if len(set(body.conversation_ids))!=len(body.conversation_ids): raise ValueError('Duplicate conversation IDs')
    if body.provider=='multiai':
        if not body.confirm_cost:raise ValueError('Cost confirmation required; use /api/evaluate/estimate first')
        if os.getenv('ALLOW_PAID_CALLS','false').lower()!='true':raise ValueError('Paid calls disabled pending explicit budget approval')
        if len(body.conversation_ids)>int(os.getenv('MULTIAI_MAX_BATCH','1')):raise ValueError('MultiAI batch exceeds configured small-test limit')
        if len(body.conversation_ids)>3 and os.getenv('MULTIAI_ALLOW_BULK','false').lower()!='true':raise ValueError('Bulk evaluation requires separate explicit authorization')
    with Session() as s:
        checklist=Checklist.model_validate(required(s,ChecklistRow,body.checklist_id).payload)
        records=[required(s,ConversationRow,cid) for cid in body.conversation_ids]
        if not provider.mock and any(c.payload['channel']=='voice call' for c in records) and not body.allow_real_calls:
            raise ValueError('Real call processing requires explicit allow_real_calls=true after verifying provider data policy')
        output=[]
        for c in records:
            payload=await evaluate(Conversation.model_validate(c.payload),checklist,provider)
            row=EvaluationRow(conversation_id=c.id,checklist_id=body.checklist_id,payload=payload)
            s.add(row); s.commit(); output.append(packed(row))
        return output

class AppealBody(StrictModel):
    evaluation_id: int
    criterion_id: str
    comment: str = Field(min_length=1)

@app.get('/api/appeals')
def appeals():
    with Session() as s:
        return [{'id':a.id,'evaluation_id':a.evaluation_id,'criterion_id':a.criterion_id,'comment':a.comment,'status':a.status,'decision':a.decision,'conversation_id':required(s,EvaluationRow,a.evaluation_id).conversation_id} for a in s.scalars(select(AppealRow).order_by(AppealRow.id.desc()))]

@app.post('/api/appeals',status_code=201)
def create_appeal(body:AppealBody):
    if not body.comment.strip(): raise ValueError('Comment required')
    with Session() as s:
        e=required(s,EvaluationRow,body.evaluation_id)
        if e.payload['status']!='ok' or body.criterion_id not in {c['criterion_id'] for c in e.payload['criteria']}: raise ValueError('Criterion not found in successful evaluation')
        if latest(s,e.conversation_id,e.checklist_id).id!=e.id: raise HTTPException(409,'Select latest evaluation for this checklist')
        row=AppealRow(**body.model_dump());s.add(row);s.commit();return {'id':row.id,'status':row.status}

class DecisionBody(StrictModel):
    action: Literal['confirm','change']
    reason: str = Field(min_length=1)
    assessment: Assessment | None = None

@app.post('/api/appeals/{aid}/decision')
def decide(aid:int,body:DecisionBody):
    if not body.reason.strip(): raise ValueError('Decision reason required')
    with Session() as s:
        a=required(s,AppealRow,aid)
        if a.status!='open': raise HTTPException(409,'Appeal already resolved')
        e=required(s,EvaluationRow,a.evaluation_id)
        if latest(s,e.conversation_id,e.checklist_id).id!=e.id: raise HTTPException(409,'Evaluation was superseded; create an appeal on latest revision')
        criteria=[Assessment.model_validate(c) for c in e.payload['criteria']]
        if body.action=='change':
            if body.assessment is None or body.assessment.criterion_id!=a.criterion_id: raise ValueError('Matching replacement assessment required')
            criteria=[body.assessment if c.criterion_id==a.criterion_id else c for c in criteria]
        elif body.assessment is not None: raise ValueError('Confirm cannot replace assessment')
        checklist=Checklist.model_validate(required(s,ChecklistRow,e.checklist_id).payload)
        c=Conversation.model_validate(required(s,ConversationRow,e.conversation_id).payload)
        validate_assessments(criteria,checklist,len(c.transcript))
        score,points=calculate(criteria,checklist)
        revision=EvaluationRow(conversation_id=e.conversation_id,checklist_id=e.checklist_id,payload={**e.payload,'criteria':[v.model_dump() for v in criteria],'applicability':{v.criterion_id:v.value is not None for v in criteria},'score':score,'points':points,'parent_evaluation_id':e.id,'review':{'appeal_id':a.id,'reason':body.reason,'action':body.action}})
        s.add(revision);s.flush()
        a.status='resolved';a.decision={**body.model_dump(),'revision_id':revision.id}
        s.commit();return packed(revision)

@app.get('/api/export/{dataset}')
def export(dataset:Literal['DEV','PROD']):
    with Session() as s:
        records=list(s.scalars(select(ConversationRow).where(ConversationRow.dataset==dataset).order_by(ConversationRow.id)))
        rows=[];models=set()
        for c in records:
            e=latest(s,c.id,1)
            if e and e.payload.get('mock'): raise ValueError('Official export blocked: mock is not an AI evaluation')
            if not e or e.payload['status']=='error':
                rows.append({'conversation_id':c.id,'status':'error','criteria':[],'score':None,'error':e.payload.get('error','Not evaluated') if e else 'Not evaluated'})
            else:
                rows.append({'conversation_id':c.id,'status':'ok','criteria':[{k:v for k,v in a.items() if k in ('criterion_id','value','rationale','evidence_turns')} for a in e.payload['criteria']],'score':e.payload['score']})
            if e: models.add(e.payload['model'])
        payload={'schema_version':'1.0','team_name':os.getenv('TEAM_NAME','CQA'),'model':{'mode':'external_api' if models else 'local','name':', '.join(sorted(models)) if models else 'No model used; all records unevaluated'},'results':rows}
        validate_export(payload,[Conversation.model_validate(c.payload) for c in records],dataset)
        path=Path('results')/dataset.lower()/'results.json';path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
        return JSONResponse(payload,headers={'Content-Disposition':f'attachment; filename="{dataset.lower()}-results.json"'})

@app.get('/api/multiai/connection')
async def multiai_connection():
    from .multiai import connection_status
    return await connection_status()

@app.get('/api/multiai/models')
async def multiai_models():
    from .multiai import discover
    return {'models':await discover()}

@app.get('/api/multiai/stats')
def multiai_stats():
    from .gateway_budget import stats
    return stats(Session)

@app.post('/api/evaluate/estimate')
async def estimate_evaluation(body:EvaluateBody):
    from .multiai import MultiAIProvider, request_body, FACT_SYSTEM, fact_schema, assessment_schema, SYSTEM, prompt
    from .gateway_budget import estimate
    if body.provider!='multiai':return {'provider':body.provider,'estimated_usd_upper':0,'paid':False}
    provider=MultiAIProvider(body.model)
    model=await provider.catalog_model()
    with Session() as s:
        checklist=Checklist.model_validate(required(s,ChecklistRow,body.checklist_id).payload)
        records=[required(s,ConversationRow,cid) for cid in body.conversation_ids]
        if len({r.id for r in records})!=len(records):raise ValueError('Duplicate conversation IDs')
        if any(r.payload['channel']!='agent chat' for r in records):raise ValueError('Initial MultiAI tests must use synthetic chats, not real calls')
        quotes=[]
        max_output=int(os.getenv('MULTIAI_MAX_OUTPUT_TOKENS','6000'))
        for row in records:
            c=Conversation.model_validate(row.payload)
            normalized=json.loads(prompt(c,checklist,''))
            # Include a generous allowance for extracted facts in stage 2.
            stage1=request_body(provider.name,FACT_SYSTEM,normalized,fact_schema(checklist),provider.mode)
            stage2=request_body(provider.name,SYSTEM,{**normalized,'verified_facts':'x'*(max_output*12)},assessment_schema(checklist),provider.mode)
            estimates=[estimate(len(json.dumps(b,ensure_ascii=False).encode()),max_output,model) for b in (stage1,stage2)]
            quotes.append({'conversation_id':row.id,'language':c.language,'estimated_credits_upper':sum(e['estimated_credits_upper'] for e in estimates)*2,'estimated_usd_upper':sum(e['estimated_usd_upper'] for e in estimates)*2 if all(e['estimated_usd_upper'] is not None for e in estimates) else None})
        return {'provider':'MultiAI','model':provider.name,'paid':True,'estimates':quotes,'estimated_credits_upper':sum(q['estimated_credits_upper'] for q in quotes),'estimated_usd_upper':sum(q['estimated_usd_upper'] for q in quotes) if all(q['estimated_usd_upper'] is not None for q in quotes) else None,'requests_expected':len(records)*2,'requests_max':len(records)*4,'includes_validation_retries':True,'warning':'Gateway credits are not USD. USD conversion must be independently verified. Reservations retained after errors; actual gateway charges can differ.'}

@app.get('/api/benchmark/sample')
def benchmark_sample():
    from .benchmark import select_sample
    with Session() as s:
        records=[Conversation.model_validate(r.payload) for r in s.scalars(select(ConversationRow).where(ConversationRow.dataset=='DEV'))]
        ids=select_sample(records)
        by_id={c.conversation_id:c for c in records}
        return [{'conversation_id':id,'language':by_id[id].language,'channel':by_id[id].channel} for id in ids]

class ExpertBody(StrictModel):
    conversation_id: str
    reviewer: str = Field(min_length=1)
    criteria: list[Assessment]

@app.post('/api/benchmark/labels',status_code=201)
def save_expert_label(body:ExpertBody):
    if not body.reviewer.strip():raise ValueError('Expert reviewer required')
    with Session() as s:
        c=Conversation.model_validate(required(s,ConversationRow,body.conversation_id).payload)
        if c.channel!='agent chat':raise ValueError('Initial benchmark excludes real calls')
        validate_assessments(body.criteria,OFFICIAL,len(c.transcript))
        row=ExpertRow(conversation_id=c.conversation_id,payload={**body.model_dump(),'language':c.language})
        s.add(row);s.commit();return {'id':row.id,**row.payload}

@app.get('/api/benchmark/labels')
def expert_labels():
    with Session() as s:return [{'id':r.id,'created':r.created.isoformat(),**r.payload} for r in s.scalars(select(ExpertRow).order_by(ExpertRow.id))]

@app.get('/api/benchmark/report')
def benchmark_report():
    # Legacy drafts are not independent consensus. Keep the endpoint for compatibility,
    # but never compute ground-truth accuracy from a single latest reviewer.
    from .benchmark import select_sample
    from .multiai import configuration
    with Session() as s:
        records=[Conversation.model_validate(r.payload) for r in s.scalars(select(ConversationRow).where(ConversationRow.dataset=='DEV'))]
        ids=select_sample(records)
        return {'sample_ids':ids,'expert_labeled':0,'status':'unlabeled','models':{m:{'evaluated':0,'successful':0,'predictions':[],'report':None} for m in (configuration()['primary_model'],configuration()['secondary_model']) if m},'warning':'Legacy single-reviewer labels are drafts. Use /api/benchmark/comparison and explicit A/B/consensus annotations.'}

from .benchmark_routes import create_router
app.include_router(create_router(lambda:Session))

# Serve the built UI from the same origin, after all API routes.
frontend=Path(__file__).resolve().parents[2]/'frontend'/'dist'
if frontend.exists(): app.mount('/',StaticFiles(directory=frontend,html=True),name='frontend')
