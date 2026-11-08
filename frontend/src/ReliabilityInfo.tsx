import type {Evaluation} from './types';

export function ReliabilityInfo({evaluation}:{evaluation:Evaluation}) {
 const failures=evaluation.attempt_errors?.flatMap(a=>(a.diagnostics??[]).map(d=>({...d,attempt:a.attempt})))??[];
 return <>
  {evaluation.provider_metadata?.evidence_source==='model_selected_turns/backend_original_full_turn'&&<p className="muted">Реплики выбрала модель. Цитаты скопированы из исходного текста. Соответствие текста проверено; смысловую достаточность evidence должен проверить человек.</p>}
  {failures.length>0&&<details><summary>Диагностика попыток ({failures.length})</summary><ul>{failures.map((d,i)=><li key={i}>Попытка {d.attempt} · {d.stage} · {d.category} · {d.path.join('.')||'root'}: {d.message}</li>)}</ul></details>}
 </>;
}
