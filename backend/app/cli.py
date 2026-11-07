import argparse
import json
from pathlib import Path
from .domain import Conversation
from .exporter import validate_export
from .benchmark import metrics, select_sample

def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def records(payload):
    rows=payload if isinstance(payload,list) else payload.get('data',{}).get('records',payload.get('records',[]))
    return [Conversation.model_validate(c) for c in rows]

def main():
    parser=argparse.ArgumentParser(description='CQA offline tools; never makes paid calls')
    sub=parser.add_subparsers(dest='command',required=True)
    validate=sub.add_parser('validate-export');validate.add_argument('results');validate.add_argument('dataset_file');validate.add_argument('--dataset',choices=['DEV','PROD'],required=True)
    sample=sub.add_parser('benchmark-sample');sample.add_argument('dataset_file')
    report=sub.add_parser('benchmark-report');report.add_argument('predictions');report.add_argument('ground_truth')
    args=parser.parse_args()
    if args.command=='validate-export':
        validate_export(load(args.results),records(load(args.dataset_file)),args.dataset);print('Valid official JSON 1.0')
    elif args.command=='benchmark-sample':print(json.dumps(select_sample(records(load(args.dataset_file))),ensure_ascii=False,indent=2))
    else:raise ValueError('Stage 3 requires two independent human rounds and consensus. Use python -m backend.app.batch_cli report; a raw single Ground Truth file is not accepted as benchmark consensus.')
if __name__=='__main__':main()
