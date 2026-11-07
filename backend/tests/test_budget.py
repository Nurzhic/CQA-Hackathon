import pytest
from backend.app.budget import reserve

def test_durable_budget_cap(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/'budget.db'))
    monkeypatch.setenv('INPUT_USD_PER_MILLION','10')
    monkeypatch.setenv('OUTPUT_USD_PER_MILLION','10')
    monkeypatch.setenv('API_BUDGET_USD','0.05')
    assert reserve(100,100)==.04296
    with pytest.raises(ValueError,match='exceed'): reserve(100,100)
    monkeypatch.setenv('API_BUDGET_USD','11')
    with pytest.raises(ValueError,match='<='):reserve(1,1)
