import {useRef,useState} from 'react';
import {api,displayValue} from './api';
import type {Criterion} from './types';
import {applyEdit,editCase,flagsFor,reviewComplete} from './aiDraft';
import type {AIDraft,AIEditor} from './aiDraft';
type Validated={draft:AIDraft;checklist:{criteria:Criterion[]};context:Record<string,string>;content_sha256:string};
function download(draft:AIDraft){
 const url=URL.createObjectURL(new Blob([JSON.stringify(draft,null,2)+'\n'],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download=draft.review?.confirmed?'CQA_AI_DRAFT_18.reviewed.json':'CQA_AI_DRAFT_18.edited.json';a.click();URL.revokeObjectURL(url);
}
export function AIDraftReview({onBack}:{onBack:()=>void}){
 const input=useRef<HTMLInputElement>(null);
 const [data,setData]=useState<Validated|null>(null),[selected,setSelected]=useState<number|null>(null),[transcript,setTranscript]=useState<any>(null),[editor,setEditor]=useState<AIEditor|null>(null),[criterion,setCriterion]=useState(0),[seen,setSeen]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[dirty,setDirty]=useState(false);
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 async function load(file:File){
  if(file.size>2_000_000)throw Error('Файл должен быть не больше 2 МБ');
  const next=await api<Validated>('/benchmark/ai-drafts/validate',JSON.parse(await file.text()));
  setData(next);setSeen(next.draft.review?.reviewed_ids??[]);setSelected(null);setEditor(null);setTranscript(null);setDirty(false);setNotice('Проверены 18 диалогов и восемь критериев. Это AI-черновик, не Ground Truth.');
 }
 async function open(index:number){
  if(!data)return;
  const c=data.draft.cases[index];const next=await api<any>('/benchmark/ai-drafts/cases/'+encodeURIComponent(c.conversation_id));
  if(next.context.manifest_sha256!==data.context.manifest_sha256)throw Error('Набор изменился: загрузите черновик заново');
  setSelected(index);setEditor(editCase(c));setTranscript(next.conversation);setCriterion(0);setDirty(false);
 }
 function change(fn:(e:AIEditor)=>AIEditor){setEditor(old=>old?fn(old):old);setDirty(true)}
 async function apply(){
  if(!data||selected===null||!editor)return;
  const next=await api<Validated>('/benchmark/ai-drafts/validate',applyEdit(data.draft,selected,editor));
  setData(next);setEditor(editCase(next.draft.cases[selected]));setSeen([]);setDirty(false);setNotice('Правки проверены. Предыдущее подтверждение просмотра сброшено. Скачайте отдельный JSON.');
 }
 async function save(confirmed=data?.draft.review?.confirmed??false){
  if(!data)return;
  if(dirty)throw Error('Сначала примените правки или отмените их');
  const next=await api<Validated>('/benchmark/ai-drafts/review',{draft:data.draft,reviewed_ids:seen,confirmed});
  setData(next);download(next.draft);setNotice(confirmed?'Просмотр AI-черновика подтверждён; отдельный файл скачан. Это не независимая экспертиза.':'Отдельный AI-черновик скачан. Исходный файл не перезаписан.');
 }
 const c=selected===null?null:data?.draft.cases[selected],id=data?.draft.criteria_order[criterion];
 const rule=data?.checklist.criteria.find(r=>r.id===id);
 const evidence=new Set((editor?.evidence[criterion]??'').split(',').map(x=>Number(x.trim())));
 return <section className="panel ai-draft-review"><div className="panel-title"><h2>AI Draft Review</h2><div className="button-group"><button disabled={busy||dirty} onClick={onBack}>Вернуться к Benchmark</button><button disabled={busy||dirty} onClick={()=>input.current?.click()}>Загрузить AI-черновик</button><input ref={input} aria-label="JSON AI-черновика" type="file" accept=".json,application/json" hidden disabled={busy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void run(()=>load(file))}}/></div></div>
 <div className="settings-body"><div className="mock-note"><strong>AI-assisted draft · НЕ Ground Truth.</strong> Подтверждение просмотра не создаёт независимые A/B или consensus и не меняет accuracy. После просмотра AI-оценок нельзя считать этот просмотр независимой экспертизой. Для слепой разметки используйте других экспертов.</div>
 {error&&<div role="alert" className="alert error">{error}</div>}{notice&&<p role="status">{notice}</p>}
 {!data&&<p>Выберите CQA_AI_DRAFT_18.json. Файл проверяется по фиксированным 18 исходным диалогам и Official v1. Значения не импортируются в человеческие формы.</p>}
 {data&&<><p>Проверено 18 диалогов · отмечено просмотренными {seen.length}/18 · {data.draft.review?.confirmed?'Просмотр подтверждён (не экспертиза)':'Просмотр не подтверждён'}</p>
 <div className="button-group"><button disabled={busy||dirty} onClick={()=>void run(()=>save())}>Сохранить отдельный JSON</button><button disabled={busy||dirty||!reviewComplete(data.draft,seen)} onClick={()=>void run(()=>save(true))}>Подтвердить просмотр</button></div>
 <div className="table-wrap"><table><thead><tr><th>Диалог</th>{data.draft.criteria_order.map(key=><th key={key}>{key}</th>)}<th>Флаги / просмотр</th></tr></thead><tbody>{data.draft.cases.map((row,index)=><tr key={row.conversation_id}><td><button disabled={busy||dirty} onClick={()=>void run(()=>open(index))}>{row.language} · {row.conversation_id}</button><small>{row.split}</small></td>{row.values.map((v,i)=><td key={data.draft.criteria_order[i]} className={flagsFor(row,data.draft.criteria_order[i]).length?'ai-review-flag':''} title={flagsFor(row,data.draft.criteria_order[i]).join('\n')}>{displayValue(v)}{flagsFor(row,data.draft.criteria_order[i]).length?' ⚑':''}</td>)}<td>{row.review_flags.length} · {seen.includes(row.conversation_id)?'Просмотрен':'Не просмотрен'}</td></tr>)}</tbody></table></div>
 {c&&editor&&transcript&&rule&&<><h3>{c.conversation_id} · исходный транскрипт и AI-черновик</h3><div className="button-group">{data.draft.criteria_order.map((key,i)=><button key={key} aria-pressed={criterion===i} className={flagsFor(c,key).length?'ai-review-flag':''} onClick={()=>setCriterion(i)}>{key}{flagsFor(c,key).length?' ⚑':''}</button>)}</div>
 <div className="annotation-grid"><div className="turns">{transcript.transcript.map((t:any,i:number)=><div key={i} className={'turn '+t.speaker+(evidence.has(i+1)?' highlighted':'')}><div className="turn-meta"><strong>#{i+1} {t.speaker}</strong><span>{t.timestamp}</span></div><p>{t.text}</p></div>)}</div><div className="settings-body"><h3>{rule.name} · {rule.id}</h3><p>{rule.description}</p>{rule.allow_na&&<p>N/A: {rule.na_rule}</p>}{flagsFor(c,rule.id).map((flag,i)=><p className="ai-review-flag" key={i}>{flag}</p>)}
 <label>Предварительное значение<select disabled={busy} value={editor.values[criterion]} onChange={e=>{const v=e.target.value;change(old=>({...old,values:old.values.map((x,i)=>i===criterion?v:x)}))}}>{(rule.type==='boolean'?[true,false]:rule.scale).map(v=><option key={String(v)} value={JSON.stringify(v)}>{displayValue(v)}</option>)}{rule.allow_na&&<option value="null">N/A</option>}</select></label>
 <label>Обоснование AI-черновика<textarea disabled={busy} value={editor.rationales[criterion]} onChange={e=>{const v=e.target.value;change(old=>({...old,rationales:old.rationales.map((x,i)=>i===criterion?v:x)}))}}/></label>
 <label>Evidence: исходные номера через запятую<input disabled={busy} value={editor.evidence[criterion]} onChange={e=>{const v=e.target.value;change(old=>({...old,evidence:old.evidence.map((x,i)=>i===criterion?v:x)}))}}/></label>
 {editor.values[criterion]==='null'&&<label>Причина N/A<input disabled={busy} value={editor.naReasons[rule.id]??''} onChange={e=>{const v=e.target.value;change(old=>({...old,naReasons:{...old.naReasons,[rule.id]:v}}))}}/></label>}
 <label>review_flags всего диалога (criterion_id: пояснение, по одному в строке)<textarea disabled={busy} value={editor.flags} onChange={e=>{const v=e.target.value;change(old=>({...old,flags:v}))}}/></label>
 <p>Выделены реплики для выбранного критерия. Существование реплики не доказывает смысловую правильность evidence. Цитаты и исходный текст не переводятся.</p>
 <div className="button-group"><button disabled={busy||!dirty} onClick={()=>void run(apply)}>Применить правки</button><button disabled={busy||!dirty} onClick={()=>{setEditor(editCase(c));setDirty(false)}}>Отменить правки</button></div>
 <label><input type="checkbox" disabled={busy||dirty} checked={seen.includes(c.conversation_id)} onChange={e=>{setSeen(old=>e.target.checked?[...old,c.conversation_id]:old.filter(x=>x!==c.conversation_id));setData(old=>old?{...old,draft:{...old.draft,review:null}}:old)}}/>Этот AI-черновик диалога просмотрен (не независимая разметка)</label>
 </div></div></>}
 </>}
 </div></section>;
}
