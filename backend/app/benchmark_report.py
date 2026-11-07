from sqlalchemy import select
from pathlib import Path
from .storage import EvaluationRow
from .benchmark_suite import load,digest
from .annotations import state,context
from .benchmark_metrics import compare,telemetry
from .multiai import configuration,FACT_SYSTEM
from .providers import SYSTEM

def pipeline_digest(options=None):
    from .multiai import fact_schema,assessment_schema
    from .checklist import OFFICIAL
    return digest({'version':'stage3-v1','system':SYSTEM,'fact_system':FACT_SYSTEM,'implementation_sha256':digest({name:Path(__file__).with_name(name).read_text() for name in ('engine.py','multiai.py','providers.py')}),'fact_schema':fact_schema(OFFICIAL),'assessment_schema':assessment_schema(OFFICIAL),'options':options or {'max_output_tokens':6000,'response_format':'json_schema','max_attempts':2}})

def report(s,pipeline_sha256=None):
    manifest,cases=load(s)
    models=[configuration()['primary_model'],configuration()['secondary_model']]
    if len(set(models))!=2 or not all(models):raise ValueError('Configure two distinct comparison model IDs')
    labels={cid:state(s,manifest,c) for cid,c in cases.items()}
    consensus={cid:st['consensus'] for cid,st in labels.items() if st['consensus']}
    candidates={model:{} for model in models};excluded=0
    # Never combine model revisions from different pipelines/options. Latest common pipeline wins per case.
    versions={}
    for e in s.scalars(select(EvaluationRow).where(EvaluationRow.checklist_id==1).order_by(EvaluationRow.id)):
        if e.conversation_id not in cases or e.payload.get('model') not in models or e.payload.get('mock') or e.payload.get('review'):continue
        ctx=e.payload.get('benchmark_context',{})
        if {k:ctx.get(k) for k in context(manifest,cases[e.conversation_id])}!=context(manifest,cases[e.conversation_id]) or not ctx.get('pipeline_sha256'):
            excluded+=1;continue
        key=(e.conversation_id,ctx['pipeline_sha256'])
        versions.setdefault(key,{})[e.payload['model']]={'id':e.id,'conversation_id':e.conversation_id,**e.payload}
        candidates[e.payload['model']][e.conversation_id]={'id':e.id,'conversation_id':e.conversation_id,**e.payload}
    eligible=[(min(p['id'] for p in preds.values()),pipeline) for (cid,pipeline),preds in versions.items() if set(preds)==set(models)]
    selected_pipeline=pipeline_sha256 or (max(eligible)[1] if eligible else pipeline_digest())
    paired={};pair_ids={}
    for (cid,pipeline),preds in versions.items():
        if pipeline!=selected_pipeline:continue
        if set(preds)==set(models):
            rank=min(p['id'] for p in preds.values())
            if cid not in paired or rank>paired[cid][0]:paired[cid]=(rank,preds);pair_ids[cid]=pipeline
    matched_truth={cid:v for cid,v in consensus.items() if cid in paired}
    reports={}
    for model in models:
        preds=[paired[cid][1][model] for cid in cases if cid in paired]
        reports[model]={'evaluated':len(candidates[model]),'paired':len(preds),'successful':sum(p['status']=='ok' for p in preds),'report':compare(preds,matched_truth,cases,{c['conversation_id']:c['split'] for c in manifest['cases']}),'all_available_telemetry':telemetry(list(candidates[model].values()),cases)}
    agreements=[]
    for cid,(_,preds) in paired.items():
        if all(p['status']=='ok' for p in preds.values()):
            a={x['criterion_id']:x['value'] for x in preds[models[0]]['criteria']};b={x['criterion_id']:x['value'] for x in preds[models[1]]['criteria']}
            agreements.extend(type(a.get(k)) is type(b.get(k)) and a.get(k)==b.get(k) for k in a)
    from .storage import BenchmarkRunRow
    run_spending=[{'id':r.id,**{k:r.payload['outcome'].get(k) for k in ('provider_credit_delta','converted_cost_usd','credit_delta_scope','pricing')}} for r in s.scalars(select(BenchmarkRunRow).where(BenchmarkRunRow.status=='complete')) if r.payload['plan']['mode']!='prod' and 'outcome' in r.payload]
    return {'run_spending':run_spending,'suite_id':manifest['suite_id'],'manifest_sha256':digest(manifest),'status':'unlabeled' if not consensus else 'labeled' if len(consensus)==len(cases) else 'partially_labeled','cases':len(cases),'expert_labeled':len(consensus),'paired_cases':len(paired),'paired_labeled_cases':len(matched_truth),'comparison_pipeline_sha256':selected_pipeline,'pair_pipeline_hashes':pair_ids,'models':reports,'annotations':[{'conversation_id':cid,**st} for cid,st in labels.items()],'legacy_or_context_mismatch_excluded':excluded,'model_value_agreement':sum(agreements)/len(agreements) if agreements else None,'warning':'Model agreement and final_score are not accuracy. Accuracy uses human consensus on identical paired inputs/pipeline only.'}
