"""Complete human-only rounds with optimistic versions and one atomic commit."""
from typing import Literal
from pydantic import StrictBool,StrictInt
from sqlalchemy import text
from .domain import StrictModel
from .checklist import OFFICIAL
from .benchmark_suite import load,digest
from . import annotations

class BulkRound(StrictModel):
    slot:Literal['A','B','consensus']
    human_confirmed:StrictBool
    context:dict[str,str]
    expected_versions:dict[str,StrictInt|None]
    annotations:list[annotations.Annotation]

def suite_context(manifest,cases):
    return {'suite_id':manifest['suite_id'],'manifest_sha256':digest(manifest),'checklist_sha256':manifest['checklist_sha256'],'transcripts_sha256':digest({cid:digest(c.model_dump()) for cid,c in cases.items()})}

def workspace(s,slot):
    if slot not in ('A','B','consensus'):raise ValueError('Choose Expert A, Expert B or consensus')
    manifest,cases=load(s);rows={cid:annotations.latest(s,cid,annotations.context(manifest,c)) for cid,c in cases.items()}
    complete=all('A' in r and 'B' in r for r in rows.values())
    if slot=='consensus' and not complete:raise ValueError('Bulk consensus requires complete A and B rounds: 18 cases each')
    result=[]
    for meta in manifest['cases']:
        cid=meta['conversation_id'];own=rows[cid].get(slot)
        st=annotations.state(s,manifest,cases[cid]) if slot=='consensus' else None
        # Never send another expert's values or any predictions in independent workspaces.
        record={'conversation':cases[cid].model_dump(),'source':meta['source'],'split':meta['split'],'context':annotations.context(manifest,cases[cid]),'annotation':st['consensus'] if st else annotations.packed(own) if own else None}
        if st:
            record.update(adjudication=st,independent_rounds={key:annotations.packed(rows[cid][key]) for key in ('A','B')})
        result.append(record)
    return {'slot':slot,'context':suite_context(manifest,cases),'checklist':OFFICIAL.model_dump(),'cases':result,'expected_versions':{cid:rows[cid][slot].id if slot in rows[cid] else None for cid in cases},'independence_guaranteed':False}

def save_round(s,body:BulkRound):
    try:
        # Serialize SQLite snapshot/version checks and writes to avoid silent lost updates.
        if s.get_bind().dialect.name=='sqlite':s.execute(text('BEGIN IMMEDIATE'))
        if not body.human_confirmed:raise ValueError('Explicit confirmation of human decisions is required')
        current=workspace(s,body.slot)
        ids=[a.conversation_id for a in body.annotations]
        if len(ids)!=18 or len(set(ids))!=18 or set(ids)!=set(current['expected_versions']):raise ValueError('Submit all 18 pinned cases exactly once')
        if body.context!=current['context']:raise ValueError('Input/checklist context changed; reload the workspace')
        if body.expected_versions!=current['expected_versions']:raise ValueError('Human annotation versions changed; reload before saving')
        if any(a.slot!=body.slot for a in body.annotations):raise ValueError('A bulk round must use one slot')
        reviewers={a.reviewer.strip().casefold() for a in body.annotations}
        if len(reviewers)!=1 or not next(iter(reviewers)):raise ValueError('Use one named human reviewer for the complete round')
        result=[annotations.save(s,a,commit=False) for a in body.annotations]
        s.commit()
        return {'saved':18,'slot':body.slot,'versions':result}
    except Exception:
        s.rollback()
        raise
