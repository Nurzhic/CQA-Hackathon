import type {Assessment,Criterion} from './types';
import type {Draft} from './annotationDraft';
import {evidenceNumbers} from './annotationDraft';
export type HumanForm={criteria:Record<string,Draft>;reason:string;decisions:Record<string,string>};
export type Workspace={slot:'A'|'B'|'consensus';context:Record<string,string>;expected_versions:Record<string,number|null>;checklist:{criteria:Criterion[]};cases:any[]};
export function initialForms(w:Workspace):Record<string,HumanForm>{return Object.fromEntries(w.cases.map(c=>[c.conversation.conversation_id,{reason:c.annotation?.decision_reason??'',decisions:{...c.annotation?.criterion_decisions},criteria:Object.fromEntries(w.checklist.criteria.map(rule=>{
 const a:Assessment|undefined=c.annotation?.criteria.find((x:Assessment)=>x.criterion_id===rule.id);
 return [rule.id,{value:a?JSON.stringify(a.value):'',rationale:a?.rationale??'',evidence:a?.evidence_turns.join(', ')??'',na_reason:a?.na_reason??'',uncertain:a?.uncertain??false,uncertainty_reason:a?.uncertainty_reason??''}];
}))}]))}
function references(w:Workspace){return Object.fromEntries(w.cases.map(c=>[c.conversation.conversation_id,c.adjudication?.based_on??[]]))}
export function workspaceKey(w:Workspace,reviewer:string){return 'cqa-human-fast-v1:'+JSON.stringify([w.context, w.slot,reviewer.trim().toLowerCase()])}
export function packDraft(w:Workspace,reviewer:string,forms:Record<string,HumanForm>){return {format:'cqa-human-fast-local-v1',reviewer:reviewer.trim(),context:w.context,slot:w.slot,versions:w.expected_versions,references:references(w),forms}}
export function restoreDraft(raw:string|null,w:Workspace,reviewer:string):Record<string,HumanForm>|null{
 try{
  const d=JSON.parse(raw??'null');if(!d||d.format!=='cqa-human-fast-local-v1'||d.slot!==w.slot||d.reviewer!==reviewer.trim()||JSON.stringify(d.context)!==JSON.stringify(w.context)||JSON.stringify(d.versions)!==JSON.stringify(w.expected_versions)||JSON.stringify(d.references)!==JSON.stringify(references(w)))return null;
  if(Object.keys(d.forms??{}).length!==18)return null;
  for(const c of w.cases){const f=d.forms[c.conversation.conversation_id];if(!f||typeof f.reason!=='string'||!f.decisions||!Object.values(f.decisions).every(v=>typeof v==='string'))return null;
   for(const rule of w.checklist.criteria){const a=f.criteria?.[rule.id];if(!a||!['value','rationale','evidence','na_reason','uncertainty_reason'].every(k=>typeof a[k]==='string')||typeof a.uncertain!=='boolean')return null;}
  }return d.forms;
 }catch{return null}
}
export function annotationFor(w:Workspace,c:any,f:HumanForm,reviewer:string){
 const criteria=w.checklist.criteria.map(rule=>{
  const a=f.criteria[rule.id];if(a.value==='')throw Error(rule.id+': выберите значение');
  const value=JSON.parse(a.value);const allowed=rule.type==='boolean'?[true,false]:rule.scale;
  if(value===null?!rule.allow_na:!allowed.some(v=>typeof v===typeof value&&v===value))throw Error(rule.id+': недопустимое значение');
  const evidence=evidenceNumbers(a.evidence);if(evidence.some(i=>i>c.conversation.transcript.length))throw Error(rule.id+': реплика отсутствует');
  if(!a.rationale.trim()||(value===null&&!a.na_reason.trim())||(a.uncertain&&!a.uncertainty_reason.trim()))throw Error(rule.id+': заполните объяснения');
  return {criterion_id:rule.id,value,rationale:a.rationale,evidence_turns:evidence,na_reason:value===null?a.na_reason:null,uncertain:a.uncertain,uncertainty_reason:a.uncertain?a.uncertainty_reason:null};
 });
 if(w.slot==='consensus'){
  if(!f.reason.trim())throw Error('Нужна общая причина согласования');
  for(const d of c.adjudication.disagreements)if(!f.decisions[d.criterion_id]?.trim())throw Error(d.criterion_id+': нужна причина решения');
 }
 return {conversation_id:c.conversation.conversation_id,slot:w.slot,reviewer,origin:'human',criteria,based_on:w.slot==='consensus'?c.adjudication.based_on:[],decision_reason:w.slot==='consensus'?f.reason:'',criterion_decisions:w.slot==='consensus'?f.decisions:{}};
}
export function readyCase(w:Workspace,c:any,f:HumanForm){try{annotationFor(w,c,f,'human');return true}catch{return false}}
export function bulkPayload(w:Workspace,forms:Record<string,HumanForm>,reviewer:string,confirmed:boolean){
 if(!confirmed)throw Error('Подтвердите, что решения приняты человеком');
 return {slot:w.slot,human_confirmed:confirmed,context:w.context,expected_versions:w.expected_versions,annotations:w.cases.map(c=>annotationFor(w,c,forms[c.conversation.conversation_id],reviewer.trim()))};
}

export function chooseHumanVariant(form:HumanForm,a:Assessment):HumanForm{
 return {...form,criteria:{...form.criteria,[a.criterion_id]:{value:JSON.stringify(a.value),rationale:a.rationale,evidence:a.evidence_turns.join(', '),na_reason:a.na_reason??'',uncertain:a.uncertain??false,uncertainty_reason:a.uncertainty_reason??''}}};
}
