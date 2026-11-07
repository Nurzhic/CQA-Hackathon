from copy import deepcopy
from decimal import Decimal
import pytest
from backend.app.checklist import OFFICIAL
from backend.app.domain import Assessment, Checklist, calculate, validate_assessments, rounded
from backend.app.exporter import ExportResult

def assessments(**changes):
    values={c.id: (True if c.type=='boolean' else max(c.scale)) for c in OFFICIAL.criteria}
    values['critical']=False; values.update(changes)
    return [Assessment(criterion_id=k,value=v,rationale='Обоснование',evidence_turns=[1],na_reason='Условие N/A выполнено' if v is None else None) for k,v in values.items()]

def test_full_score_and_critical_independent():
    assert calculate(assessments(),OFFICIAL)[0]=={'final_score':100.0,'critical_flag':False}
    assert calculate(assessments(critical=True),OFFICIAL)[0]=={'final_score':100.0,'critical_flag':True}

def test_scales_na_and_rounding():
    score,points=calculate(assessments(discovery=None,identification=None,consultation=15,dialogue=7),OFFICIAL)
    assert score['final_score']==85.38
    assert points['dialogue']['points']==10.5
    assert points['discovery']['points'] is None
    assert rounded(Decimal('50.625'))==50.63

def test_no_applicable_weight():
    checklist=Checklist(name='Custom',criteria=[{'id':'a','name':'A','description':'Rule','weight':10,'type':'boolean','allow_na':True,'na_rule':'No request'}])
    result=calculate([Assessment(criterion_id='a',value=None,rationale='No request',na_reason='No request',evidence_turns=[1])],checklist)[0]
    assert result['final_score'] is None

@pytest.mark.parametrize('key,value',[('contact',None),('contact',1),('dialogue',True),('dialogue',5),('consultation',7),('critical',None)])
def test_invalid_values(key,value):
    with pytest.raises(ValueError): validate_assessments(assessments(**{key:value}),OFFICIAL,5)

@pytest.mark.parametrize('evidence',[[0],[6],[1,1],[-1]])
def test_evidence_bounds(evidence):
    rows=assessments();rows[0].evidence_turns=evidence
    with pytest.raises(ValueError): validate_assessments(rows,OFFICIAL,5)

def test_na_requires_explanation_and_all_criteria():
    rows=assessments(discovery=None);rows[1].na_reason=None
    with pytest.raises(ValueError): validate_assessments(rows,OFFICIAL,5)
    with pytest.raises(ValueError): validate_assessments(assessments()[:-1],OFFICIAL,5)
    with pytest.raises(ValueError): validate_assessments(assessments()+[assessments()[0]],OFFICIAL,5)

def test_official_json():
    rows=assessments();score,_=calculate(rows,OFFICIAL)
    payload={'conversation_id':'x','status':'ok','criteria':[{k:v for k,v in r.model_dump().items() if k in ['criterion_id','value','rationale','evidence_turns']} for r in rows],'score':score}
    ExportResult.model_validate(payload)
    bad=deepcopy(payload);bad['score']['critical_flag']=True
    with pytest.raises(ValueError): ExportResult.model_validate(bad)
    bad=deepcopy(payload);bad['criteria'][0]['value']=1
    with pytest.raises(ValueError): ExportResult.model_validate(bad)
    ExportResult.model_validate({'conversation_id':'x','status':'error','criteria':[],'score':None,'error':'Unavailable'})
    with pytest.raises(ValueError): ExportResult.model_validate({'conversation_id':'x','status':'error','criteria':[],'score':None})
