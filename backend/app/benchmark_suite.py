"""Pinned inputs only. No expert labels, predictions or coverage tags cross into AI prompts."""
import hashlib
import json
from pathlib import Path
from collections import Counter
from .domain import Conversation
from .checklist import OFFICIAL
from .storage import ConversationRow

ROOT=Path(__file__).resolve().parents[2]
MANIFEST=ROOT/'benchmarks/multilingual-v1.json'
SYNTHETIC=ROOT/'benchmarks/team_synthetic.json'

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def manifest():
    value=json.loads(MANIFEST.read_text())
    ids=[c['conversation_id'] for c in value['cases']]
    if len(ids)!=len(set(ids)) or Counter(c['language'] for c in value['cases'])!={'RU':6,'KZ':6,'EN':6}:
        raise ValueError('Suite must pin 18 unique cases: 6 RU / 6 KZ / 6 EN')
    if not set(value['required_coverage'])<=set(t for c in value['cases'] for t in c['tags']):raise ValueError('Missing required scenario coverage')
    if value['checklist_sha256']!=digest(OFFICIAL.model_dump()):raise ValueError('Official checklist changed; create a new suite version')
    for split in ('calibration','holdout'):
        if Counter(c['language'] for c in value['cases'] if c.get('split')==split)!={'RU':3,'KZ':3,'EN':3}:raise ValueError('Each frozen split needs 3 RU / 3 KZ / 3 EN')
    pre=value['preflight_ids']
    if len(set(pre))!=3 or not set(pre)<=set(ids) or {c['language'] for c in value['cases'] if c['conversation_id'] in pre}!={'RU','KZ','EN'}:raise ValueError('Preflight must pin one RU, one KZ and one EN')
    if any(c['split']!='calibration' for c in value['cases'] if c['conversation_id'] in pre):raise ValueError('Preflight must not consume holdout cases')
    return value

def prepare(s):
    own=json.loads(SYNTHETIC.read_text())['records']
    for data in own:
        c=Conversation.model_validate(data)
        if not c.conversation_id.startswith('cqa-synthetic-') or c.channel!='agent chat' or c.audio_url is not None:raise ValueError('Invalid own synthetic provenance')
        row=s.get(ConversationRow,c.conversation_id)
        if row and (row.dataset!='BENCHMARK' or row.payload!=c.model_dump()):raise ValueError('Synthetic ID collision; no transcript overwritten')
        if not row:s.add(ConversationRow(id=c.conversation_id,dataset='BENCHMARK',payload=c.model_dump()))
    s.commit()
    return load(s)

def load(s):
    value=manifest();cases={}
    for case in value['cases']:
        row=s.get(ConversationRow,case['conversation_id'])
        if not row:raise ValueError('Missing suite input: '+case['conversation_id']+'; import DEV and prepare benchmark')
        expected='DEV' if case['source']=='organizer_dev_synthetic' else 'BENCHMARK'
        c=Conversation.model_validate(row.payload)
        if row.dataset!=expected or c.channel!='agent chat' or c.language!=case['language'] or digest(c.model_dump())!=case['sha256']:raise ValueError('Suite transcript/provenance hash mismatch: '+c.conversation_id)
        cases[c.conversation_id]=c
    return value,cases

def model_input(conversation):
    # Reconstruct only the strict Conversation schema, never a joined annotation/report object.
    return Conversation.model_validate(conversation.model_dump())
