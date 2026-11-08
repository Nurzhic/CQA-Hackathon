"""AI-assisted drafts only: validate/export review metadata, never human annotations or predictions."""
from datetime import datetime,timezone
from typing import Literal
from pydantic import Field,StrictBool,StrictInt,StrictStr
from .domain import StrictModel,Assessment,validate_assessments
from .checklist import OFFICIAL
from .benchmark_suite import load,digest

class Review(StrictModel):
    declaration: Literal['AI_ASSISTED_REVIEW_NOT_INDEPENDENT']='AI_ASSISTED_REVIEW_NOT_INDEPENDENT'
    reviewed_ids:list[StrictStr]=[]
    confirmed:StrictBool=False
    content_sha256:StrictStr
    reviewed_at:StrictStr|None=None

class DraftCase(StrictModel):
    conversation_id:StrictStr
    language:Literal['RU','KZ','EN']
    split:Literal['calibration','holdout']
    values:list[StrictBool|StrictInt|None]
    evidence_turns:list[list[StrictInt]]
    rationales:list[StrictStr]
    na_reasons:dict[StrictStr,StrictStr]
    review_flags:list[StrictStr]

class Draft(StrictModel):
    format:Literal['cqa-ai-review-draft-v1']
    source:StrictStr=''
    origin:Literal['AI_ASSISTED_DRAFT_NOT_HUMAN']='AI_ASSISTED_DRAFT_NOT_HUMAN'
    ground_truth:Literal[False]=False
    warning:StrictStr='AI-assisted draft, not independent human Ground Truth or consensus.'
    turn_numbering:StrictStr='1-based over the complete original transcript'
    criteria_order:list[StrictStr]
    case_field_guide:dict[StrictStr,StrictStr]={}
    cases:list[DraftCase]=Field(min_length=18,max_length=18)
    review:Review|None=None
    context:dict[StrictStr,StrictStr]|None=None

class ReviewRequest(StrictModel):
    draft:Draft
    reviewed_ids:list[StrictStr]
    confirmed:StrictBool=False

WARNING='AI-assisted draft and review only. NOT independent human A/B, NOT consensus, NOT Ground Truth.'

def validate(s,draft:Draft):
    manifest,cases=load(s)
    ids=[c.conversation_id for c in draft.cases];expected={c.id for c in OFFICIAL.criteria}
    if len(ids)!=len(set(ids)) or set(ids)!=set(cases):raise ValueError('AI draft must contain exactly the 18 pinned conversation IDs')
    if len(draft.criteria_order)!=8 or set(draft.criteria_order)!=expected:raise ValueError('criteria_order must contain each of the eight Official v1 IDs exactly once')
    meta={c['conversation_id']:c for c in manifest['cases']}
    for case in draft.cases:
        c=cases[case.conversation_id]
        if case.language!=c.language or case.split!=meta[case.conversation_id]['split']:raise ValueError('AI draft language/split differs from the pinned suite: '+case.conversation_id)
        if any(len(v)!=8 for v in (case.values,case.evidence_turns,case.rationales)):raise ValueError('Each case needs eight values, evidence arrays and rationales: '+case.conversation_id)
        nulls={cid for cid,v in zip(draft.criteria_order,case.values) if v is None}
        if set(case.na_reasons)!=nulls:raise ValueError('na_reasons must match exactly the N/A criterion IDs: '+case.conversation_id)
        if any(not f.strip() for f in case.review_flags):raise ValueError('Empty review flag')
        for flag in case.review_flags:
            if ':' not in flag or flag.split(':',1)[0].strip() not in expected:raise ValueError('review_flags must start with an Official criterion_id followed by a colon')
        rows=[Assessment(criterion_id=cid,value=value,rationale=rationale,evidence_turns=turns,na_reason=case.na_reasons.get(cid)) for cid,value,rationale,turns in zip(draft.criteria_order,case.values,case.rationales,case.evidence_turns)]
        validate_assessments(rows,OFFICIAL,len(c.transcript))
    context={'suite_id':manifest['suite_id'],'manifest_sha256':digest(manifest),'checklist_sha256':manifest['checklist_sha256'],'transcripts_sha256':digest({cid:digest(c.model_dump()) for cid,c in cases.items()})}
    if draft.context is not None and draft.context!=context:raise ValueError('AI draft belongs to a different manifest/checklist/transcript context')
    data=draft.model_dump(exclude={'review'});data.update(origin='AI_ASSISTED_DRAFT_NOT_HUMAN',ground_truth=False,warning=WARNING,context=context)
    sha=digest(data)
    if draft.review:
        review=draft.review
        if review.content_sha256!=sha:raise ValueError('Draft was changed after review; clear review metadata and review again')
        if len(review.reviewed_ids)!=len(set(review.reviewed_ids)) or not set(review.reviewed_ids)<=set(cases):raise ValueError('Invalid reviewed conversation IDs')
        if review.confirmed and (set(review.reviewed_ids)!=set(cases) or not review.reviewed_at):raise ValueError('Confirmation requires all 18 reviewed cases and a timestamp')
    data['review']=draft.review.model_dump() if draft.review else None
    return {'draft':data,'context':context,'content_sha256':sha,'checklist':OFFICIAL.model_dump()}

def record_review(s,body:ReviewRequest):
    result=validate(s,body.draft);ids={c['conversation_id'] for c in result['draft']['cases']}
    if len(body.reviewed_ids)!=len(set(body.reviewed_ids)) or not set(body.reviewed_ids)<=ids:raise ValueError('Invalid reviewed conversation IDs')
    if body.confirmed and set(body.reviewed_ids)!=ids:raise ValueError('Review all 18 cases before confirming the AI-assisted review')
    result['draft']['review']=Review(reviewed_ids=body.reviewed_ids,confirmed=body.confirmed,content_sha256=result['content_sha256'],reviewed_at=datetime.now(timezone.utc).isoformat() if body.confirmed else None).model_dump()
    return result
