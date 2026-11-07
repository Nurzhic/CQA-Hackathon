"""Evaluation quality and operational telemetry, with explicit missing-data denominators."""
from collections import defaultdict
from statistics import mean,median
from math import ceil
import math
from .checklist import OFFICIAL

IDS=[c.id for c in OFFICIAL.criteria]

def ratio(n,d):return n/d if d else None

def latency(values):
    values=sorted(v for v in values if type(v) in (int,float) and math.isfinite(v) and v>=0)
    return {'count':len(values),'mean_ms':mean(values) if values else None,'median_ms':median(values) if values else None,'p95_ms':values[ceil(.95*len(values))-1] if values else None,'p95_method':'nearest_rank'}

def evidence_audit(predictions,conversations):
    turns_total=turns_valid=quotes_total=quotes_valid=missing_quotes=assessments=0
    for p in predictions:
        c=conversations[p['conversation_id']]
        for a in p.get('criteria',[]):
            assessments+=1
            evidence=a.get('evidence_turns',[])
            unique=len(evidence)==len(set(evidence))
            turns_total+=len(evidence)
            turns_valid+=sum(unique and type(i) is int and 1<=i<=len(c.transcript) for i in evidence)
            quoted={}
            for q in a.get('evidence_quotes',[]):
                quotes_total+=1;i=q.get('turn');text=q.get('quote')
                valid=type(i) is int and i in evidence and 1<=i<=len(c.transcript) and isinstance(text,str) and bool(text.strip()) and text in c.transcript[i-1].text
                quotes_valid+=int(valid and i not in quoted);quoted[i]=True
            missing_quotes+=sum(i not in quoted for i in evidence)
    return {'turns_total':turns_total,'turns_valid':turns_valid,'turn_validity':ratio(turns_valid,turns_total),'quotes_total':quotes_total,'quotes_valid':quotes_valid,'quote_match':ratio(quotes_valid,quotes_total),'missing_quotes':missing_quotes,'assessments':assessments}

def telemetry(predictions,conversations):
    usage=defaultdict(float);known_costs=[];credits=[];validation_failures=0;unknown_validation=0;retries=0
    for p in predictions:
        retries+=max(0,p.get('attempts',1)-1)
        failures=p.get('validation_failures')
        if failures is None:
            if p.get('attempts',1)>1:unknown_validation+=1
        else:validation_failures+=len(failures)
        for u in p.get('usage',[]):
            for k,v in u.items():
                if type(v) in (int,float) and math.isfinite(v) and v>=0:usage[k]+=v
        if p.get('cost_usd') is not None:known_costs.append(p['cost_usd'])
        if p.get('provider_credit_delta') is not None:credits.append(p['provider_credit_delta'])
    return {'latency':latency([p.get('latency_ms') for p in predictions]),'retries':retries,'validation_failures':validation_failures,'unknown_validation_failure_evaluations':unknown_validation,'usage':dict(usage),'provider_credit_delta':sum(credits) if credits else None,'credit_delta_coverage':len(credits),'reported_cost_usd':sum(known_costs) if known_costs else None,'reported_cost_coverage':len(known_costs),'evidence':evidence_audit(predictions,conversations)}

def compare(predictions,truth,conversations,strata=None):
    # Caller supplies consensus only. final_score is deliberately not read.
    if not truth:return {'status':'unlabeled','metrics':None,'telemetry':telemetry(predictions,conversations),'labeled_conversations':0}
    pp={p['conversation_id']:p for p in predictions}
    if len(pp)!=len(predictions):raise ValueError('Duplicate prediction IDs')
    groups=defaultdict(lambda:{'correct':0,'total':0,'missing':0,'na_matrix':{'actual_na_pred_na':0,'actual_na_pred_applicable':0,'actual_applicable_pred_na':0,'actual_applicable_pred_applicable':0,'missing':0},'scale_absolute_error':0,'scale_count':0,'critical_tp':0,'critical_fp':0,'critical_fn':0,'critical_tn':0})
    for key in ['overall',*('language:'+lang for lang in ('RU','KZ','EN')),*('criterion:'+cid for cid in IDS)]:groups[key]
    if strata:
        for split in set(strata.values()):groups['split:'+split]
    dialogue_exact=0;scored=0
    for cid,label in truth.items():
        pred=pp.get(cid);values={a['criterion_id']:a for a in (pred or {}).get('criteria',[])}
        if pred and len(values)!=len(pred.get('criteria',[])):raise ValueError('Duplicate prediction criterion IDs')
        all_correct=True
        for a in label['criteria']:
            cid_key=a['criterion_id'];expected=a['value'];candidate=values.get(cid_key)
            present=bool(candidate is not None and (pred or {}).get('status','ok')=='ok')
            actual=candidate['value'] if present else '__missing__'
            correct=present and type(actual) is type(expected) and actual==expected
            all_correct=all_correct and correct
            keys=['overall','language:'+conversations[cid].language,'criterion:'+cid_key]
            if strata and cid in strata:keys.append('split:'+strata[cid])
            for key in keys:
                g=groups[key];g['total']+=1;g['correct']+=int(correct);g['missing']+=int(not present)
                if not present:g['na_matrix']['missing']+=1
                else:g['na_matrix'][('actual_na_' if expected is None else 'actual_applicable_')+('pred_na' if actual is None else 'pred_applicable')]+=1
                if type(expected) is int and type(actual) is int:g['scale_count']+=1;g['scale_absolute_error']+=abs(expected-actual)
                if cid_key=='critical':
                    g['critical_tp']+=int(expected is True and actual is True)
                    g['critical_fn']+=int(expected is True and actual is not True)
                    g['critical_fp']+=int(expected is False and actual is True)
                    g['critical_tn']+=int(expected is False and actual is False)
        scored+=1;dialogue_exact+=int(all_correct)
    result={}
    for key,g in groups.items():
        matrix=g['na_matrix'];na_correct=matrix['actual_na_pred_na']+matrix['actual_applicable_pred_applicable']
        result[key]={**g,'accuracy':ratio(g['correct'],g['total']),'na_accuracy':ratio(na_correct,g['total']),'scale_mae':ratio(g['scale_absolute_error'],g['scale_count']),'critical_precision':ratio(g['critical_tp'],g['critical_tp']+g['critical_fp']),'critical_recall':ratio(g['critical_tp'],g['critical_tp']+g['critical_fn'])}
    return {'status':'labeled','metrics':result,'labeled_conversations':len(truth),'prediction_coverage':sum(cid in pp for cid in truth),'dialogue_exact_match':ratio(dialogue_exact,scored),'telemetry':telemetry(predictions,conversations),'accuracy_definition':'micro exact match of criterion values; errors/missing count as wrong; never final_score'}
