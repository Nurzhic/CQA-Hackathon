"""Offline comparison only: this module never invokes a model."""
from collections import defaultdict

def select_sample(conversations):
    result=[]
    for language in ('RU','KZ','EN'):
        candidates=sorted((c for c in conversations if c.language==language and c.channel=='agent chat'),key=lambda c:c.conversation_id)
        if len(candidates)<6: raise ValueError(f'Need at least 6 {language} conversations')
        result.extend(c.conversation_id for c in candidates[:6])
    return result

def metrics(predictions, ground_truth):
    """Inputs: [{conversation_id, language, criteria, latency_ms?, cost_usd?}]."""
    truth={r['conversation_id']:r for r in ground_truth}
    if len(truth)!=len(ground_truth): raise ValueError('Duplicate ground truth IDs')
    if len({p['conversation_id'] for p in predictions})!=len(predictions): raise ValueError('Duplicate prediction IDs')
    groups=defaultdict(lambda:{'correct':0,'total':0,'na_errors':0,'na_correct':0,'scale_absolute_error':0,'scale_count':0,'evidence_correct':0,'evidence_total':0,'critical_tp':0,'critical_fp':0,'critical_fn':0})
    times=[];costs=[];matched=0
    for p in predictions:
        if p['conversation_id'] not in truth: continue
        gt=truth[p['conversation_id']];matched+=1
        predicted={a['criterion_id']:a for a in p['criteria']}
        if len(predicted)!=len(p['criteria']): raise ValueError('Duplicate prediction criteria')
        if p.get('latency_ms') is not None: times.append(p['latency_ms'])
        if p.get('cost_usd') is not None: costs.append(p['cost_usd'])
        for a in gt['criteria']:
            b=predicted.get(a['criterion_id']);expected=a['value'];actual=b['value'] if b else '__missing__'
            for key in ('criterion:'+a['criterion_id'],'language:'+gt['language'],'overall'):
                g=groups[key];g['total']+=1;g['correct']+=int(type(actual) is type(expected) and actual==expected)
                g['na_errors']+=int(b is None or (expected is None)!=(actual is None))
                g['na_correct']+=int(b is not None and (expected is None)==(actual is None))
                if type(expected) is int and type(actual) is int:
                    g['scale_count']+=1;g['scale_absolute_error']+=abs(expected-actual)
                if b and 'evidence_turns' in a:
                    evidence=set(b['evidence_turns']);reference=set(a['evidence_turns'])
                    g['evidence_correct']+=len(evidence&reference);g['evidence_total']+=len(evidence)
                if a['criterion_id']=='critical':
                    g['critical_tp']+=int(actual is True and expected is True)
                    g['critical_fp']+=int(actual is True and expected is False)
                    g['critical_fn']+=int(actual is not True and expected is True)
    if not matched: raise ValueError('No overlapping labeled conversations; accuracy cannot be computed')
    report={}
    for key,g in groups.items():
        report[key]={**g,'accuracy':g['correct']/g['total'],'na_accuracy':g['na_correct']/g['total'],'scale_mae':g['scale_absolute_error']/g['scale_count'] if g['scale_count'] else None,'evidence_precision':g['evidence_correct']/g['evidence_total'] if g['evidence_total'] else None,'critical_precision':g['critical_tp']/(g['critical_tp']+g['critical_fp']) if g['critical_tp']+g['critical_fp'] else None,'critical_recall':g['critical_tp']/(g['critical_tp']+g['critical_fn']) if g['critical_tp']+g['critical_fn'] else None}
    return {'matched_conversations':matched,'ground_truth_conversations':len(truth),'mean_latency_ms':sum(times)/len(times) if times else None,'known_cost_usd':sum(costs) if costs else None,'cost_coverage':len(costs),'usage':[p.get('usage',[]) for p in predictions],'evidence_validity':sum(type(i) is int and 1<=i<=p['turn_count'] for p in predictions if 'turn_count' in p for a in p['criteria'] for i in a.get('evidence_turns',[]))/max(1,sum(len(a.get('evidence_turns',[])) for p in predictions if 'turn_count' in p for a in p['criteria'])) if any('turn_count' in p for p in predictions) else None,'metrics':report}
