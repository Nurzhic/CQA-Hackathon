import pytest
from backend.app.benchmark import metrics

def test_metrics_respect_value_types_and_na():
    gt=[{'conversation_id':'x','language':'KZ','criteria':[{'criterion_id':'contact','value':True,'evidence_turns':[2]},{'criterion_id':'discovery','value':None,'evidence_turns':[1]},{'criterion_id':'dialogue','value':7,'evidence_turns':[2]},{'criterion_id':'critical','value':True,'evidence_turns':[2]}]}]
    pred=[{'conversation_id':'x','criteria':[{'criterion_id':'contact','value':1,'evidence_turns':[2]},{'criterion_id':'discovery','value':False,'evidence_turns':[2]},{'criterion_id':'dialogue','value':4,'evidence_turns':[2]},{'criterion_id':'critical','value':False,'evidence_turns':[2]}]}]
    result=metrics(pred,gt)['metrics']
    assert result['overall']['accuracy']==0
    assert result['overall']['na_errors']==1
    assert result['overall']['scale_mae']==3
    assert result['criterion:critical']['critical_recall']==0
    assert result['language:KZ']['evidence_precision']==.75

def test_no_ground_truth_no_accuracy_claim():
    with pytest.raises(ValueError):metrics([],[])
