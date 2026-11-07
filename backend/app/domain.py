from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, StrictBool, StrictInt

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Turn(StrictModel):
    timestamp: str
    speaker: Literal['client', 'operator', 'bot', 'system']
    text: str

class Conversation(StrictModel):
    conversation_id: str = Field(min_length=1)
    channel: Literal['agent chat', 'voice call']
    language: Literal['RU', 'KZ', 'EN']
    transcript: list[Turn] = Field(min_length=1)
    audio_url: str | None = None

class Criterion(StrictModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: StrictInt = Field(ge=0, le=100)
    type: Literal['boolean', 'scale']
    scale: list[StrictInt] = []
    allow_na: bool = False
    na_rule: str = ''
    @model_validator(mode='after')
    def valid(self):
        if self.type == 'scale' and (not self.scale or max(self.scale) <= 0 or min(self.scale) < 0 or len(set(self.scale)) != len(self.scale)):
            raise ValueError('Scale must contain unique nonnegative steps and a positive maximum')
        if self.type == 'boolean' and self.scale:
            raise ValueError('Boolean criterion cannot have a scale')
        if self.allow_na and not self.na_rule.strip():
            raise ValueError('N/A requires an applicability rule')
        if self.id == 'critical' and (self.type != 'boolean' or self.weight != 0 or self.allow_na):
            raise ValueError('critical must be boolean, weight 0 and not nullable')
        return self

class Checklist(StrictModel):
    name: str = Field(min_length=1)
    criteria: list[Criterion] = Field(min_length=1)
    @model_validator(mode='after')
    def valid(self):
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError('Duplicate criterion IDs')
        if not any(c.weight > 0 for c in self.criteria):
            raise ValueError('At least one positive weight is required')
        return self

class EvidenceQuote(StrictModel):
    turn: StrictInt
    quote: str = Field(min_length=1)

class Assessment(StrictModel):
    criterion_id: str
    value: StrictBool | StrictInt | None
    rationale: str = Field(min_length=1)
    evidence_turns: list[StrictInt] = Field(min_length=1)
    # Internal audit fields, intentionally excluded from official JSON.
    evidence_quotes: list[EvidenceQuote] = []
    na_reason: str | None = None
    uncertain: bool = False
    uncertainty_reason: str | None = None
    @model_validator(mode='after')
    def valid(self):
        if not self.rationale.strip():
            raise ValueError('Empty rationale')
        if self.uncertain and not self.uncertainty_reason:
            raise ValueError('Uncertainty requires an explanation')
        return self

class EvaluationOutput(StrictModel):
    facts: dict[str, str]
    applicability: dict[str, StrictBool]
    criteria: list[Assessment]

def validate_assessments(criteria: list[Assessment], checklist: Checklist, turns: int):
    by_id = {a.criterion_id: a for a in criteria}
    if len(by_id) != len(criteria) or set(by_id) != {c.id for c in checklist.criteria}:
        raise ValueError('Missing, duplicate or unexpected criteria')
    for c in checklist.criteria:
        a = by_id[c.id]
        if any(type(i) is not int or i < 1 or i > turns for i in a.evidence_turns) or len(set(a.evidence_turns)) != len(a.evidence_turns):
            raise ValueError(f'{c.id}: invalid evidence_turns')
        if a.value is None:
            if not c.allow_na or not a.na_reason or not a.na_reason.strip():
                raise ValueError(f'{c.id}: N/A is forbidden or applicability explanation is absent')
        elif c.type == 'boolean' and type(a.value) is not bool:
            raise ValueError(f'{c.id}: expected boolean')
        elif c.type == 'scale' and (type(a.value) is not int or a.value not in c.scale):
            raise ValueError(f'{c.id}: invalid scale step')
    return by_id

def rounded(value: Decimal) -> float:
    return float(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def calculate(criteria: list[Assessment], checklist: Checklist):
    by_id = {a.criterion_id: a for a in criteria}
    applicable = [c for c in checklist.criteria if by_id[c.id].value is not None and c.weight > 0]
    denominator = sum(c.weight for c in applicable)
    points = {}
    total = Decimal(0)
    for c in checklist.criteria:
        value = by_id[c.id].value
        raw = Decimal(0) if value is None else Decimal(int(value)) / (Decimal(max(c.scale)) if c.type == 'scale' else Decimal(1)) * c.weight
        normalized = raw * 100 / denominator if denominator and value is not None else None
        points[c.id] = {'points': rounded(raw) if value is not None else None, 'normalized_points': rounded(normalized) if normalized is not None else None, 'weight': c.weight}
        total += raw
    return {'final_score': rounded(total * 100 / denominator) if denominator else None, 'critical_flag': bool(by_id['critical'].value) if 'critical' in by_id else False}, points
