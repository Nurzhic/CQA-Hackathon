from fastapi import APIRouter,HTTPException
from sqlalchemy import select
from . import benchmark_suite,annotations,benchmark_report,batch
from .storage import BenchmarkAnnotationRow,BenchmarkRunRow
from .checklist import OFFICIAL

def create_router(session_factory):
    router=APIRouter()
    @router.post('/api/benchmark/prepare')
    def prepare():
        with session_factory()() as s:
            manifest,cases=benchmark_suite.prepare(s)
            return {'suite_id':manifest['suite_id'],'manifest_sha256':benchmark_suite.digest(manifest),'cases':len(cases),'status':'unlabeled'}
    @router.get('/api/benchmark/suite')
    def suite():
        with session_factory()() as s:
            manifest,_=benchmark_suite.load(s)
            return {**manifest,'manifest_sha256':benchmark_suite.digest(manifest)}
    @router.get('/api/benchmark/cases/{cid}')
    def case(cid:str,slot:str='A'):
        if slot not in ('A','B','consensus'):raise ValueError('Unknown annotation slot')
        with session_factory()() as s:
            manifest,cases=benchmark_suite.load(s)
            if cid not in cases:raise HTTPException(404,'Case not in pinned suite')
            c=cases[cid];ctx=annotations.context(manifest,c);rows=annotations.latest(s,cid,ctx)
            own=rows.get(slot)
            result={'conversation':c.model_dump(),'source':next(m['source'] for m in manifest['cases'] if m['conversation_id']==cid),'checklist':OFFICIAL.model_dump(),'annotation':annotations.packed(own) if own else None}
            if slot=='consensus':
                result['adjudication']=annotations.state(s,manifest,c)
                result['independent_rounds']={key:annotations.packed(rows[key]) for key in ('A','B') if key in rows}
            return result
    @router.post('/api/benchmark/annotations',status_code=201)
    def save(body:annotations.Annotation):
        with session_factory()() as s:return annotations.save(s,body)
    @router.get('/api/benchmark/annotations')
    def history(slot:str='A'):
        if slot not in ('A','B','consensus'):raise ValueError('Unknown annotation slot')
        with session_factory()() as s:return [annotations.packed(row) for row in s.scalars(select(BenchmarkAnnotationRow).where(BenchmarkAnnotationRow.slot==slot).order_by(BenchmarkAnnotationRow.id))]
    @router.get('/api/benchmark/annotation-template')
    def template(slot:str='A'):
        with session_factory()() as s:
            manifest,_=benchmark_suite.load(s)
            return annotations.template(manifest,slot)
    @router.post('/api/benchmark/annotation-import',status_code=201)
    def import_labels(body:list[annotations.Annotation]):
        if not body or len(body)>54:raise ValueError('Import 1 to 54 human annotation records')
        with session_factory()() as s:return annotations.import_annotations(s,body)
    @router.get('/api/benchmark/disagreements')
    def disagreements():
        with session_factory()() as s:
            manifest,cases=benchmark_suite.load(s)
            return [annotations.state(s,manifest,c) for c in cases.values()]
    @router.get('/api/benchmark/comparison')
    def report(pipeline_sha256:str|None=None):
        with session_factory()() as s:return benchmark_report.report(s,pipeline_sha256)
    @router.post('/api/batches/plans',status_code=201)
    def plan(body:batch.PlanOptions):
        with session_factory()() as s:return batch.create_plan(s,body)
    @router.get('/api/batches/plans/{plan_id}')
    def get_plan(plan_id:int):
        with session_factory()() as s:
            row=s.get(BenchmarkRunRow,plan_id)
            if not row:raise HTTPException(404,'Plan not found')
            return {'id':row.id,'status':row.status,**row.payload}
    @router.post('/api/batches/plans/{plan_id}/execute')
    async def execute(plan_id:int,body:batch.Approval):return await batch.execute(session_factory(),plan_id,body)
    return router
