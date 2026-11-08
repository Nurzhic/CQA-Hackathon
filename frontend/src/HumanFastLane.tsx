import {useState,useEffect} from 'react';
import {api,displayValue} from './api';
import {HumanDisagreement} from './HumanReview';
import {evidenceNumbers} from './annotationDraft';
import type {Draft} from './annotationDraft';
import {initialForms,workspaceKey,packDraft,restoreDraft,readyCase,bulkPayload,chooseHumanVariant} from './fastReview';
import type {Workspace,HumanForm} from './fastReview';
export function HumanFastLane({onBack}:{onBack:()=>void}){
 const [name,setName]=useState(''),[w,setW]=useState<Workspace|null>(null),[forms,setForms]=useState<Record<string,HumanForm>>({}),[index,setIndex]=useState(0),[criterion,setCriterion]=useState(0),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[storage,setStorage]=useState('');
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');setNotice('');try{await fn()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 useEffect(()=>{
  if(!w)return;
  try{localStorage.setItem(workspaceKey(w,name),JSON.stringify(packDraft(w,name,forms)));setStorage('Незавершённая работа сохранена в этом браузере; это ещё не серверные метки.')}catch{setStorage('Локальный черновик не сохранён. Не закрывайте вкладку: хранилище недоступно.')}
 },[w,name,forms]);
 async function open(slot:Workspace['slot']){
  const next=await api<Workspace>('/benchmark/fast-review?slot='+slot);let restored=null;
  try{restored=restoreDraft(localStorage.getItem(workspaceKey(next,name)),next,name)}catch{/* browser storage unavailable */}
  setForms(restored??initialForms(next));setW(next);setIndex(0);setCriterion(0);setConfirmed(false);setNotice(restored?'Восстановлен ваш локальный черновик.':'Загружены только данные выбранного человеческого раунда.');
 }
 async function save(){
  if(!w)return;
  await api('/benchmark/fast-review/save',bulkPayload(w,forms,name,confirmed));
  try{localStorage.removeItem(workspaceKey(w,name))}catch{/* storage unavailable */}
  const fresh=await api<Workspace>('/benchmark/fast-review?slot='+w.slot);setForms(initialForms(fresh));setW(fresh);setConfirmed(false);setNotice('Все 18 человеческих версий сохранены атомарно.');
 }
 const c=w?.cases[index],cid=c?.conversation.conversation_id,f=cid?forms[cid]:null,rule=w?.checklist.criteria[criterion],d=f&&rule?f.criteria[rule.id]:null;
 function update(key:keyof Draft,value:string|boolean){if(!cid||!rule)return;setConfirmed(false);setForms(old=>({...old,[cid]:{...old[cid],criteria:{...old[cid].criteria,[rule.id]:{...old[cid].criteria[rule.id],[key]:value}}}}))}
 function pickHuman(slot:'A'|'B'){
  if(!w||w.slot!=='consensus'||!c||!rule)return;
  const a=c.independent_rounds[slot].criteria.find((a:any)=>a.criterion_id===rule.id);
  setConfirmed(false);setForms(old=>({...old,[cid]:chooseHumanVariant(old[cid],a)}));
 }
 function decision(value:string,perCriterion=false){if(!cid||!rule)return;setConfirmed(false);setForms(old=>({...old,[cid]:{...old[cid],...(perCriterion?{decisions:{...old[cid].decisions,[rule.id]:value}}:{reason:value})}}))}
 let selected:number[]=[];try{selected=d?.evidence?evidenceNumbers(d.evidence):[]}catch{/* unfinished manual input */}
 function chooseTurn(turn:number){
  if(!d)return;
  try{const old=d.evidence?evidenceNumbers(d.evidence):[];update('evidence',(old.includes(turn)?old.filter(t=>t!==turn):[...old,turn]).sort((a,b)=>a-b).join(', '))}catch{setError('Сначала исправьте ручной список evidence')}
 }
 const complete=w?w.cases.filter(c=>readyCase(w,c,forms[c.conversation.conversation_id])).length:0;
 return <section className="panel human-fast-lane"><div className="panel-title"><h2>Human Review Fast Lane</h2><button disabled={busy} onClick={onBack}>Вернуться к Benchmark</button></div><div className="settings-body"><div className="mock-note">Только человеческие решения. Здесь нет AI Draft и ответов другого эксперта в режимах A/B. Авторизации нет: выбор режима не гарантирует независимость. Используйте разные имена и профили; не смотрите чужие/AI-ответы до завершения A/B.</div>
 {error&&<p role="alert" className="alert error">{error}</p>}{notice&&<p role="status">{notice}</p>}
 <label>Имя эксперта<input disabled={!!w||busy} value={name} onChange={e=>setName(e.target.value)}/></label>
 {!w&&<div className="button-group">{(['A','B','consensus'] as const).map(slot=><button key={slot} disabled={busy||!name.trim()} onClick={()=>void run(()=>open(slot))}>{slot==='consensus'?'Bulk Consensus':'Expert '+slot}</button>)}</div>}
 {w&&<><p>{w.slot==='consensus'?'Bulk Consensus':'Expert '+w.slot} · готово к проверке {complete}/18 · серверных версий {Object.values(w.expected_versions).filter(Boolean).length}/18</p><p role="status">{storage}</p>
 <div className="benchmark-sample">{w.cases.map((row,i)=><button key={row.conversation.conversation_id} disabled={busy} aria-current={index===i?'step':undefined} className={'badge '+(readyCase(w,row,forms[row.conversation.conversation_id])?'green':'neutral')} onClick={()=>{setIndex(i);setCriterion(0)}}>{row.conversation.language} · {row.conversation.conversation_id} · {readyCase(w,row,forms[row.conversation.conversation_id])?'готово':'черновик'}</button>)}</div>
 {c&&f&&rule&&d&&<><h3>{index+1}/18 · {cid} · {c.split} · {c.source}</h3><div className="button-group"><button disabled={busy||index===0} onClick={()=>{setIndex(index-1);setCriterion(0)}}>Предыдущий диалог</button><button disabled={busy||index===17} onClick={()=>{setIndex(index+1);setCriterion(0)}}>Следующий диалог</button></div>
 <div className="button-group">{w.checklist.criteria.map((r,i)=><button key={r.id} aria-pressed={i===criterion} disabled={busy} onClick={()=>setCriterion(i)}>{i+1}. {r.id}{f.criteria[r.id].value!==''?' ✓':''}</button>)}</div>
 <div className="annotation-grid"><div className="turns">{c.conversation.transcript.map((t:any,i:number)=><div key={i} className={'turn '+t.speaker+(selected.includes(i+1)?' highlighted':'')}><div className="turn-meta"><strong>#{i+1} {t.speaker}</strong><span>{t.timestamp}</span></div><p>{t.text}</p><label><input type="checkbox" aria-label={'Evidence #'+(i+1)} disabled={busy} checked={selected.includes(i+1)} onChange={()=>chooseTurn(i+1)}/>Выбрать как evidence для {rule.id}</label></div>)}</div>
 <div className="settings-body"><h3>{rule.name} · {rule.id}</h3><p>{rule.description}</p><p>Вес {rule.weight}{rule.allow_na?' · N/A: '+rule.na_rule:''}</p>
 {w.slot==='consensus'&&<><HumanDisagreement criterion={rule} rounds={c.independent_rounds} disagreement={c.adjudication.disagreements.find((a:any)=>a.criterion_id===rule.id)}/><div className="button-group"><button disabled={busy} onClick={()=>pickHuman('A')}>Выбрать человеческий вариант A</button><button disabled={busy} onClick={()=>pickHuman('B')}>Выбрать человеческий вариант B</button></div><p>Согласование A/B IDs: {c.adjudication.based_on.join(', ')}. Итог выбирается человеком, не автоматически.</p></>}
 <label>Значение<select disabled={busy} value={d.value} onChange={e=>update('value',e.target.value)}><option value="">Не размечено</option>{(rule.type==='boolean'?[true,false]:rule.scale).map(v=><option key={String(v)} value={JSON.stringify(v)}>{displayValue(v)}</option>)}{rule.allow_na&&<option value="null">N/A</option>}</select></label>
 <label>Человеческое rationale<textarea disabled={busy} value={d.rationale} onChange={e=>update('rationale',e.target.value)}/></label>
 <label>Evidence: исходные номера<input disabled={busy} value={d.evidence} onChange={e=>update('evidence',e.target.value)}/></label>
 {d.value==='null'&&<label>Причина N/A<input disabled={busy} value={d.na_reason} onChange={e=>update('na_reason',e.target.value)}/></label>}
 <label><input type="checkbox" disabled={busy} checked={d.uncertain} onChange={e=>update('uncertain',e.target.checked)}/>Есть неопределённость</label>{d.uncertain&&<label>Причина неопределённости<textarea disabled={busy} value={d.uncertainty_reason} onChange={e=>update('uncertainty_reason',e.target.value)}/></label>}
 {w.slot==='consensus'&&<><label>Причина решения по {rule.id}{c.adjudication.disagreements.some((a:any)=>a.criterion_id===rule.id)?' (обязательно)':''}<textarea disabled={busy} value={f.decisions[rule.id]??''} onChange={e=>decision(e.target.value,true)}/></label><label>Общая причина согласования диалога<textarea disabled={busy} value={f.reason} onChange={e=>decision(e.target.value)}/></label></>}
 <div className="button-group"><button disabled={busy||criterion===0} onClick={()=>setCriterion(criterion-1)}>Предыдущий критерий</button><button disabled={busy||criterion===7} onClick={()=>setCriterion(criterion+1)}>Следующий критерий</button></div>
 </div></div></>}
 <label><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>Подтверждаю, что все решения приняты человеком; AI Draft не использовался как независимый эталон</label>
 <button className="primary" disabled={busy||complete!==18||!confirmed} onClick={()=>void run(save)}>{w.slot==='consensus'?'Сохранить согласованный Ground Truth':'Проверить и сохранить все оценки'}</button>
 <p>Сервер проверит все 18 случаев, контекст и версии. Любая ошибка отменяет весь пакет. Изменившиеся версии требуют повторной загрузки; старые записи не перезаписываются.</p>
 </>}
 </div></section>;
}
