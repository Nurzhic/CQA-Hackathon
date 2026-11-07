"""Independent human rounds and explicit, version-pinned adjudication. Never model input."""
from typing import Literal
from pydantic import Field,StrictInt
from sqlalchemy import select
from .domain import StrictModel,Assessment,validate_assessments
from .checklist import OFFICIAL
from .storage import BenchmarkAnnotationRow
from .benchmark_suite import load,digest

class Annotation(StrictModel):
    conversation_id: str
    slot: Literal['A','B','consensus']
    reviewer: str = Field(min_length=1)
    origin: Literal['human']
    criteria: list[Assessment]
    based_on: list[StrictInt] = []
    decision_reason: str = ''

def context(manifest,conversation):
    return {'suite_id':manifest['suite_id'],'manifest_sha256':digest(manifest),'conversation_sha256':digest(conversation.model_dump()),'checklist_sha256':manifest['checklist_sha256']}

def latest(s,cid,ctx):
    found={}
    for row in s.scalars(select(BenchmarkAnnotationRow).where(BenchmarkAnnotationRow.conversation_id==cid).order_by(BenchmarkAnnotationRow.id)):
        if row.payload.get('context')==ctx:found[row.slot]=row
    return found

def packed(row):return {'id':row.id,'created':row.created.isoformat(),**row.payload}

def state(s,manifest,conversation):
    rows=latest(s,conversation.conversation_id,context(manifest,conversation))
    a,b=rows.get('A'),rows.get('B');final=rows.get('consensus')
    valid=bool(a and b and final and final.payload['based_on']==[a.id,b.id])
    disagreements=[]
    if a and b:
        aa={x['criterion_id']:x for x in a.payload['criteria']};bb={x['criterion_id']:x for x in b.payload['criteria']}
        for c in OFFICIAL.criteria:
            x,y=aa[c.id],bb[c.id]
            if type(x['value']) is not type(y['value']) or x['value']!=y['value'] or x['evidence_turns']!=y['evidence_turns']:
                disagreements.append({'criterion_id':c.id,'value_disagreement':type(x['value']) is not type(y['value']) or x['value']!=y['value'],'evidence_disagreement':x['evidence_turns']!=y['evidence_turns'],'A':x,'B':y})
    return {'conversation_id':conversation.conversation_id,'status':'consensus' if valid else 'needs_adjudication' if a and b else 'unlabeled','rounds':{slot:row.id for slot,row in rows.items()},'disagreements':disagreements,'consensus':packed(final) if valid else None,'based_on':[a.id,b.id] if a and b else []}

def save(s,body:Annotation,commit=True):
    manifest,cases=load(s)
    if body.conversation_id not in cases:raise ValueError('Annotation is outside the pinned benchmark')
    conversation=cases[body.conversation_id];ctx=context(manifest,conversation)
    reviewer=body.reviewer.strip()
    if not reviewer:raise ValueError('Human reviewer identity is required')
    validate_assessments(body.criteria,OFFICIAL,len(conversation.transcript))
    from .multiai import verify_quotes
    for a in body.criteria:
        if a.evidence_quotes:
            if {q.turn for q in a.evidence_quotes}!=set(a.evidence_turns) or len(a.evidence_quotes)!=len(a.evidence_turns):raise ValueError('Quote/turn mismatch in expert evidence')
            verify_quotes(a.evidence_quotes,conversation)
    rows=latest(s,body.conversation_id,ctx)
    if body.slot in ('A','B'):
        other=rows.get('B' if body.slot=='A' else 'A')
        if other and other.reviewer.casefold()==reviewer.casefold():raise ValueError('Rounds A and B require different independent human reviewers')
        if body.based_on or body.decision_reason:raise ValueError('Independent rounds cannot reference another round')
    else:
        a,b=rows.get('A'),rows.get('B')
        if not a or not b or body.based_on!=[a.id,b.id]:raise ValueError('Consensus must pin the latest A and B annotation IDs')
        if not body.decision_reason.strip():raise ValueError('Explicit human adjudication reason is required')
    row=BenchmarkAnnotationRow(conversation_id=body.conversation_id,slot=body.slot,reviewer=reviewer,payload={**body.model_dump(),'reviewer':reviewer,'context':ctx})
    s.add(row);s.flush()
    if commit:s.commit()
    return packed(row)


def template(manifest,slot):
    if slot not in ('A','B','consensus'):raise ValueError('Unknown slot')
    return {'status':'unlabeled','suite_id':manifest['suite_id'],'instructions':'Human-only. UNLABELED is deliberately not an admissible criterion value; choose every value explicitly. N/A is null, not missing. Consensus requires latest A/B IDs and a decision reason.','annotations':[{'conversation_id':c['conversation_id'],'slot':slot,'reviewer':'','origin':'human','criteria':[{'criterion_id':criterion.id,'value':'UNLABELED','rationale':'','evidence_turns':[],'na_reason':None} for criterion in OFFICIAL.criteria],'based_on':[],'decision_reason':''} for c in manifest['cases']]}


def import_annotations(s,records):
    keys=[(r.conversation_id,r.slot) for r in records]
    if len(set(keys))!=len(keys):raise ValueError('Duplicate case/slot in annotation import')
    result=[save(s,r,commit=False) for r in records]
    s.commit();return result
