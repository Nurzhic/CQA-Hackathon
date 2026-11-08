"""Read-only calibration error analysis. Never uses AI Draft or holdout to recommend changes."""
from collections import Counter
from sqlalchemy import select
from . import benchmark_report,benchmark_suite,annotations
from .storage import EvaluationRow

JSON_ERRORS={'malformed_json'}
SCHEMA_ERRORS={'missing_field','invalid_type','invalid_enum','extra_field','invalid_value','invalid_na'}

def analyze(s,pipeline_sha256=None):
    report=benchmark_report.report(s,pipeline_sha256)
    manifest,cases=benchmark_suite.load(s);pipeline=report['comparison_pipeline_sha256'];models=list(report['models'])
    calibration={c['conversation_id'] for c in manifest['cases'] if c['split']=='calibration'}
    predictions={}
    for row in s.scalars(select(EvaluationRow).where(EvaluationRow.checklist_id==1).order_by(EvaluationRow.id)):
        p=row.payload;ctx=p.get('benchmark_context',{})
        if row.conversation_id not in calibration or p.get('model') not in models or p.get('mock') or p.get('review') or ctx.get('pipeline_sha256')!=pipeline:continue
        expected=annotations.context(manifest,cases[row.conversation_id])
        if any(ctx.get(k)!=v for k,v in expected.items()):continue
        predictions.setdefault(row.conversation_id,{})[p['model']]={'id':row.id,**p}
    counts={m:Counter() for m in models};diagnostics={m:Counter() for m in models};problems=[];compared=0
    for meta in manifest['cases']:
        cid=meta['conversation_id']
        if cid not in calibration or set(predictions.get(cid,{}))!=set(models):continue
        truth=annotations.state(s,manifest,cases[cid])['consensus']
        if not truth:continue
        compared+=1;gt={a['criterion_id']:a for a in truth['criteria']};item={'conversation_id':cid,'language':meta['language'],'split':'calibration','consensus_id':truth['id'],'models':{}}
        for m in models:
            p=predictions[cid][m];values={a['criterion_id']:a for a in p.get('criteria',[])};diffs=[]
            for key,a in gt.items():
                candidate=values.get(key);present=p['status']=='ok' and candidate is not None
                value=candidate['value'] if present else None
                if not present or type(value) is not type(a['value']) or value!=a['value']:
                    kind='missing_result' if not present else 'na_applicability' if (value is None)!=(a['value'] is None) else 'scale' if type(value) is int else 'boolean_value'
                    diffs.append({'criterion_id':key,'expected':a['value'],'predicted':value,'prediction_present':present,'kind':kind,'ground_truth_turns':a['evidence_turns'],'model_turns':candidate.get('evidence_turns',[]) if present else []})
                    counts[m][key]+=1
            for failure in p.get('validation_failures',[]):
                ds=failure.get('diagnostics',[])
                if not ds:diagnostics[m]['historical_validation_unspecified']+=1
                for d in ds:diagnostics[m][d['category']]+=1
            item['models'][m]={'evaluation_id':p['id'],'status':p['status'],'differences':diffs,'latency_ms':p.get('latency_ms'),'attempts':p.get('attempts'),'attempt_errors':p.get('attempt_errors',[])}
        if any(v['differences'] or v['attempt_errors'] for v in item['models'].values()):problems.append(item)
    summary={m:{'criterion_errors':dict(counts[m]),'validation_categories':dict(diagnostics[m]),'json_errors':sum(diagnostics[m][k] for k in JSON_ERRORS),'schema_value_errors':sum(diagnostics[m][k] for k in SCHEMA_ERRORS),'other_validation_errors':sum(v for k,v in diagnostics[m].items() if k not in JSON_ERRORS|SCHEMA_ERRORS)} for m in models}
    recommendations=[]
    for m,stats in summary.items():
        if stats['other_validation_errors']:recommendations.append({'model':m,'scope':'calibration','action':'Разобрать validation/evidence failures на локальных фикстурах; не угадывать причины старых ошибок без raw output.'})
        if stats['criterion_errors'].get('discovery') or stats['criterion_errors'].get('identification'):recommendations.append({'model':m,'scope':'calibration','action':'Проверить N/A и изменение объёма запроса по consensus; новые общие правила сначала проверить на calibration, без holdout.'})
        if stats['criterion_errors'].get('consultation') or stats['criterion_errors'].get('dialogue'):recommendations.append({'model':m,'scope':'calibration','action':'Сверить ступени consultation/dialogue и достаточность следующего шага с человеческим регламентом; не подгонять баллы под отдельные примеры.'})
    recommendations.append({'model':None,'scope':'calibration','action':'До окончательного выбора модели провести согласованный парный benchmark всех 9 calibration cases. Эти результаты не подтверждают обобщение на 18/holdout.'})
    return {'scope':'calibration_only','pipeline_sha256':pipeline,'calibration_cases':9,'paired_labeled_cases':compared,'consensus_ids':{p['conversation_id']:p['consensus_id'] for p in problems},'models':summary,'problem_dialogues':problems,'recommendations':recommendations,'warning':'Missing result is not N/A; no holdout-based tuning or automatic changes to Ground Truth/prompts.'}
