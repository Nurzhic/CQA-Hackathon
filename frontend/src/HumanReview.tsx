import {displayValue} from './api';
import type {Assessment,Criterion} from './types';
export function HumanProgress({annotations=[]}:{annotations?:{conversation_id:string;status:string;rounds:Record<string,number>}[]}){
 return <div><p>Готовность: A {annotations.filter(a=>a.rounds.A).length}/18 · B {annotations.filter(a=>a.rounds.B).length}/18 · consensus {annotations.filter(a=>a.status==='consensus').length}/18.</p><p>Незавершённая разметка и черновик не считаются Ground Truth. Без consensus accuracy: — / unlabeled.</p></div>;
}
export function HumanDisagreement({criterion,rounds,disagreement}:{criterion:Criterion;rounds:any;disagreement:any}){
 if(!rounds?.A||!rounds?.B)return null;
 return <details open={!!disagreement}><summary>{disagreement?'Расхождение — требуется причина решения':'Сравнить человеческие A/B'}</summary>{(['A','B'] as const).map(slot=>{
 const a:Assessment|undefined=rounds[slot]?.criteria.find((x:Assessment)=>x.criterion_id===criterion.id);
 return a&&<div key={slot}><strong>{slot}: {displayValue(a.value)}</strong><p>{a.rationale}</p><p>Evidence: {a.evidence_turns.map(n=>'#'+n).join(', ')}{a.na_reason?' · N/A: '+a.na_reason:''}</p>{a.uncertain&&<p>Неопределённость: {a.uncertainty_reason}</p>}</div>;
 })}</details>;
}
