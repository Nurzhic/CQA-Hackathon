"""Stage 3.5 compact wire contract; deterministic quotes, never invented evidence indices."""
import json
from typing import Any
from pydantic import create_model,Field,StrictBool,StrictInt,StrictStr,ValidationError
from .domain import StrictModel,Assessment,EvaluationOutput,validate_assessments
from .ai_diagnostics import OutputError,diagnostics

VERSION='stage3.5-index-evidence-v1'

def decode_json(text):
 def pairs(values):
  result={}
  for key,value in values:
   if key in result:raise OutputError('invalid_value',(key,))
   result[key]=value
  return result
 def constant(value):raise OutputError('malformed_json')
 try:return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)
 except json.JSONDecodeError:raise OutputError('malformed_json') from None

def criterion_schema(c):
 value={'type':'boolean'} if c.type=='boolean' else {'type':'integer','enum':c.scale}
 if c.allow_na:value={'anyOf':[value,{'type':'null'}]}
 props={'value':value,'rationale':{'type':'string','minLength':1},'evidence_turns':{'type':'array','minItems':1,'items':{'type':'integer','minimum':1}},'na_reason':{'type':['string','null']},'uncertain':{'type':'boolean'},'uncertainty_reason':{'type':['string','null']}}
 return {'type':'object','additionalProperties':False,'properties':props,'required':list(props)}

def schema(checklist):
 props={c.id:criterion_schema(c) for c in checklist.criteria}
 return {'type':'object','additionalProperties':False,'properties':{'criteria':{'type':'object','additionalProperties':False,'properties':props,'required':list(props)}},'required':['criteria']}

def quotes_for(turns,conversation,path):
 if not turns:raise OutputError('evidence_empty',path)
 # Check types before hashing: unhashable invalid input must not become an opaque TypeError.
 for n,i in enumerate(turns):
  loc=(*path,n,'turn') if path==('evidence',) else (*path,n)
  if type(i) is not int:raise OutputError('evidence_index_type',loc)
  if i==0:raise OutputError('evidence_zero_index',loc)
  if i<0:raise OutputError('evidence_negative_index',loc)
  if i>len(conversation.transcript):raise OutputError('evidence_index_out_of_range',loc)
 if len(turns)!=len(set(turns)):raise OutputError('evidence_duplicate_index',path)
 # Full original turn, including punctuation/Unicode. No translation, trimming or guessed indices.
 if any(not conversation.transcript[i-1].text.strip() for i in turns):raise OutputError('quote_mismatch',path)
 return [{'turn':i,'quote':conversation.transcript[i-1].text} for i in turns]

def validate_output(output,checklist,conversation):
 ids=[a.criterion_id for a in output.criteria]
 if len(ids)!=len(set(ids)) or set(ids)!={c.id for c in checklist.criteria}:raise OutputError('invalid_enum',('criteria',))
 if set(output.applicability)!={c.id for c in checklist.criteria}:raise OutputError('missing_field',('applicability',))
 by={a.criterion_id:a for a in output.criteria}
 for c in checklist.criteria:
  a=by[c.id];path=('criteria',c.id)
  if a.value is None:
   if not c.allow_na or not a.na_reason or not a.na_reason.strip():raise OutputError('invalid_na',(*path,'value'))
  elif c.type=='boolean' and type(a.value) is not bool:raise OutputError('invalid_type',(*path,'value'))
  elif c.type=='scale':
   if type(a.value) is not int:raise OutputError('invalid_type',(*path,'value'))
   if a.value not in c.scale:raise OutputError('invalid_enum',(*path,'value'))
  if output.applicability[c.id]!=(a.value is not None):raise OutputError('invalid_na',(*path,'value'))
  quotes_for(a.evidence_turns,conversation,(*path,'evidence_turns'))
 validate_assessments(output.criteria,checklist,len(conversation.transcript))

def parse_criteria(text,checklist,conversation,facts,applicability):
 fields={}
 for c in checklist.criteria:
  typ=StrictBool if c.type=='boolean' else StrictInt
  # Null is parsed, then strictly rejected with an actionable invalid_na diagnostic if forbidden.
  item=create_model('Wire_'+c.id,__base__=StrictModel,value=(typ|None,...),rationale=(StrictStr,Field(min_length=1)),evidence_turns=(list[StrictInt],...),na_reason=(StrictStr|None,...),uncertain=(StrictBool,...),uncertainty_reason=(StrictStr|None,...))
  fields[c.id]=item
 # Criterion IDs are data, not Python field names (custom checklists may use model_config).
 model=create_model('CriteriaWire',__base__=StrictModel,criteria=(dict[StrictStr,Any],...))
 wire=model.model_validate(decode_json(text))
 expected=set(fields);actual=set(wire.criteria)
 if expected!=actual:
  extra=actual-expected;key=sorted(extra or expected-actual)[0]
  raise OutputError('extra_field' if extra else 'missing_field',('criteria',key))
 items=[]
 for cid,item in fields.items():
  try:a=item.model_validate(wire.criteria[cid]).model_dump()
  except ValidationError as exc:
   d=diagnostics(exc)[0]
   raise OutputError(d['category'],('criteria',cid,*d['path'])) from None
  if a['uncertain'] and (not a['uncertainty_reason'] or not a['uncertainty_reason'].strip()):raise OutputError('missing_field',('criteria',cid,'uncertainty_reason'))
  q=quotes_for(a['evidence_turns'],conversation,('criteria',cid,'evidence_turns'))
  items.append(Assessment(criterion_id=cid,**a,evidence_quotes=q))
 output=EvaluationOutput(facts=facts,applicability=applicability,criteria=items)
 validate_output(output,checklist,conversation)
 return output
