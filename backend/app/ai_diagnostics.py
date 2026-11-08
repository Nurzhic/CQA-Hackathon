"""Bounded diagnostics: fixed categories/paths only; never exception input/message/context."""
from pydantic import ValidationError

MESSAGES={
 'missing_field':'Required field is missing', 'invalid_type':'Field has an invalid type',
 'invalid_enum':'Value is outside the allowed set', 'invalid_na':'N/A violates applicability rules',
 'invalid_evidence_index':'Evidence must select existing unique transcript turns',
 'evidence_empty':'Evidence selection is empty',
 'evidence_duplicate_index':'Evidence indices must be unique',
 'evidence_index_type':'Evidence index must be an integer',
 'evidence_zero_index':'Evidence is one-based; index zero is forbidden',
 'evidence_negative_index':'Evidence index must be positive',
 'evidence_index_out_of_range':'Evidence index exceeds the transcript',
 'missing_evidence_quote':'Every evidence turn requires one exact quote',
 'quote_mismatch':'Quote does not match its original turn', 'truncated_output':'Provider output was truncated',
 'malformed_json':'Output is not a complete valid JSON document', 'provider_error':'Provider request or response failed',
 'extra_field':'Unexpected field is forbidden', 'invalid_value':'Output violates a validation constraint',
}
FIELDS=set('facts applicability evidence criteria turn quote observation value criterion_id rationale evidence_turns evidence_quotes na_reason uncertain uncertainty_reason request greeting identification protected_action completeness empathy closure critical language contact discovery consultation dialogue choices message content model finish_reason bool int str none'.split())

def safe_path(path):
 return [v if type(v) is int and 0<=v<100000 else v if type(v) is str and v in FIELDS else '<field>' for v in path[:12]]

class OutputError(ValueError):
 def __init__(self,category,path=(),stage='validation'):
  self.category=category;self.path=safe_path(path);self.stage=stage
  super().__init__(MESSAGES[category])

def diagnostics(exc,stage='validation'):
 if isinstance(exc,OutputError):return [{'category':exc.category,'path':exc.path,'stage':stage if exc.stage=='validation' else exc.stage,'message':MESSAGES[exc.category]}]
 if isinstance(exc,ValidationError):
  result=[]
  for e in exc.errors(include_input=False,include_context=False,include_url=False)[:20]:
   kind=e['type']
   category='malformed_json' if kind=='json_invalid' else 'missing_field' if kind=='missing' else 'extra_field' if kind=='extra_forbidden' else 'invalid_enum' if kind in ('literal_error','enum') else 'invalid_type' if kind.endswith(('_type','_parsing')) else 'invalid_value'
   if kind.endswith(('_type','_parsing')) and ('evidence_turns' in e['loc'] or ('evidence' in e['loc'] and 'turn' in e['loc'])):category='evidence_index_type'
   result.append({'category':category,'path':safe_path(e['loc']),'stage':stage,'message':MESSAGES[category]})
  return result
 category=getattr(exc,'category','provider_error' if getattr(exc,'is_provider_error',False) else 'invalid_value')
 return [{'category':category,'path':safe_path(getattr(exc,'path',())),'stage':stage,'message':MESSAGES[category]}]
