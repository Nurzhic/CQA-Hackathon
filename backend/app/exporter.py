from typing import Literal
from pydantic import Field, model_validator, StrictBool, StrictInt, StrictFloat
from .domain import StrictModel, Assessment, Conversation, validate_assessments, calculate
from .checklist import OFFICIAL

class ExportCriterion(StrictModel):
    criterion_id: str
    value: StrictBool | StrictInt | None
    rationale: str = Field(min_length=1)
    evidence_turns: list[StrictInt] = Field(min_length=1)
class Score(StrictModel):
    final_score: StrictFloat | None = Field(ge=0, le=100)
    critical_flag: StrictBool
class ExportResult(StrictModel):
    conversation_id: str
    status: Literal['ok','error']
    criteria: list[ExportCriterion]
    score: Score | None
    error: str | None = None
    @model_validator(mode='after')
    def valid(self):
        if self.status == 'error':
            if self.criteria or self.score is not None or not self.error:
                raise ValueError('Error requires empty criteria, null score and a reason')
        else:
            if self.score is None or self.error is not None:
                raise ValueError('Successful result requires score and no error')
            items = [Assessment(**a.model_dump(), na_reason=a.rationale if a.value is None else None) for a in self.criteria]
            validate_assessments(items, OFFICIAL, max([i for a in items for i in a.evidence_turns], default=0))
            expected, _ = calculate(items, OFFICIAL)
            if self.score.model_dump() != expected:
                raise ValueError('Score or critical flag does not match criteria')
        return self
class ModelInfo(StrictModel):
    mode: Literal['local','external_api','hybrid']
    name: str = Field(min_length=1)
class OfficialExport(StrictModel):
    schema_version: Literal['1.0']
    team_name: str = Field(min_length=1)
    model: ModelInfo
    results: list[ExportResult]
    @model_validator(mode='after')
    def unique(self):
        if len({r.conversation_id for r in self.results}) != len(self.results):
            raise ValueError('Duplicate conversation IDs')
        return self

def validate_export(payload: dict, conversations: list[Conversation], dataset: str):
    doc = OfficialExport.model_validate(payload)
    count = 65 if dataset == 'DEV' else 10 if dataset == 'PROD' else None
    expected = {c.conversation_id:c for c in conversations}
    if count is None or len(conversations) != count or len(expected) != count or len(doc.results) != count:
        raise ValueError(f'{dataset}: expected exactly {count} unique conversations')
    if {r.conversation_id for r in doc.results} != set(expected):
        raise ValueError('Export IDs differ from imported dataset')
    if dataset == 'PROD' and any(c.channel != 'agent chat' for c in conversations):
        raise ValueError('PROD must contain 10 chats')
    for row in doc.results:
        if row.status == 'ok':
            assessments = [Assessment(**a.model_dump(), na_reason=a.rationale if a.value is None else None) for a in row.criteria]
            validate_assessments(assessments, OFFICIAL, len(expected[row.conversation_id].transcript))
    return doc.model_dump(exclude_none=False)
