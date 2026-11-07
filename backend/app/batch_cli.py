"""Dry-run by default. run requires a fresh explicit count/budget/digest approval."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from . import main,batch,benchmark_suite

def main_cli():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    catalog_cmd=sub.add_parser('catalog');catalog_cmd.add_argument('--out',default='data/multiai-models.json')
    sub.add_parser('prepare')
    templates=sub.add_parser('label-templates');templates.add_argument('--out-dir',required=True)
    labels=sub.add_parser('labels-import');labels.add_argument('file')
    sub.add_parser('report')
    for mode in ('preflight','benchmark','prod'):
        c=sub.add_parser(mode);c.add_argument('--out',required=True);c.add_argument('--model');c.add_argument('--concurrency',type=int,default=2 if mode=='prod' else 1);c.add_argument('--timeout',type=int,default=90);c.add_argument('--attempts',type=int,default=2);c.add_argument('--deadline',type=int,default=3300);c.add_argument('--max-backoff',type=int,default=30);c.add_argument('--limit-usd',default='0.50');c.add_argument('--catalog',help='Previously verified local catalog snapshot; never fetched by planning')
    run=sub.add_parser('run');run.add_argument('plan_file');run.add_argument('--approved',action='store_true');run.add_argument('--evaluations',type=int,required=True);run.add_argument('--limit-usd',required=True)
    args=p.parse_args()
    if args.command=='catalog':
        from .multiai import discover
        from datetime import datetime,timezone
        models=asyncio.run(discover())
        dest=Path(args.out);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps({'data':models,'fetched_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'read_only':True,'models':len(models),'path':str(dest)}));return
    if args.command=='prepare':
        with main.Session() as s:manifest,cases=benchmark_suite.prepare(s)
        print(json.dumps({'suite_id':manifest['suite_id'],'cases':len(cases),'status':'unlabeled'},ensure_ascii=False));return
    if args.command=='label-templates':
        from .annotations import template
        with main.Session() as s:manifest,_=benchmark_suite.load(s)
        dest=Path(args.out_dir);dest.mkdir(parents=True,exist_ok=True)
        for slot in ('A','B','consensus'):
            path=dest/(slot+'.json')
            if path.exists():raise ValueError('Refusing to overwrite human labels: '+str(path))
            path.write_text(json.dumps(template(manifest,slot),ensure_ascii=False,indent=2)+'\n')
        print('Created UNLABELED human-only templates A/B/consensus');return
    if args.command=='labels-import':
        from .annotations import Annotation,import_annotations
        raw=json.loads(Path(args.file).read_text());records=[Annotation.model_validate(r) for r in (raw['annotations'] if isinstance(raw,dict) else raw)]
        with main.Session() as s:result=import_annotations(s,records)
        print(json.dumps({'imported':len(result)},ensure_ascii=False));return
    if args.command=='report':
        from .benchmark_report import report
        with main.Session() as s:result=report(s)
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if args.command=='run':
        saved=json.loads(Path(args.plan_file).read_text())
        if benchmark_suite.digest({k:v for k,v in saved.items() if k not in ('id','plan_sha256')})!=saved['plan_sha256']:raise ValueError('Plan file was modified; create and approve a fresh plan')
        if not args.approved:raise ValueError('Stop: explicit user approval is required before --approved')
        if os.getenv('ALLOW_PAID_CALLS','false').lower()!='true':raise ValueError('Stop: paid calls disabled; do not enable until the user approves this plan/count/USD budget')
        # CLI approval applies only to the exact plan, never to later plans.
        os.environ['MULTIAI_BATCH_APPROVED_PLAN']=saved['plan_sha256']
        approval=batch.Approval(approved=True,plan_sha256=saved['plan_sha256'],evaluation_count=args.evaluations,limit_usd=args.limit_usd)
        try:result=asyncio.run(batch.execute(main.Session,saved['id'],approval))
        finally:os.environ.pop('MULTIAI_BATCH_APPROVED_PLAN',None)
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    options=batch.PlanOptions(mode=args.command,model=args.model,concurrency=args.concurrency,timeout_seconds=args.timeout,max_attempts=args.attempts,max_backoff_seconds=args.max_backoff,deadline_seconds=args.deadline,limit_usd=args.limit_usd)
    catalog=None
    if args.catalog:
        raw=json.loads(Path(args.catalog).read_text());catalog=raw.get('data',[]) if isinstance(raw,dict) else raw
    with main.Session() as s:result=batch.create_plan(s,options,catalog)
    dest=Path(args.out)
    if dest.exists():raise ValueError('Plan output already exists; use a new filename so approval cannot silently change')
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('id','mode','evaluation_count','dialogue_count','provider_requests_expected','provider_requests_max','estimated_credits_upper','estimated_usd_upper','fits_deadline','required_total_request_limit','paid_calls_enabled')},ensure_ascii=False,indent=2))
if __name__=='__main__':main_cli()
