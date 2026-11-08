export type Value=boolean|number|null;
export type AICase={conversation_id:string;language:string;split:string;values:Value[];evidence_turns:number[][];rationales:string[];na_reasons:Record<string,string>;review_flags:string[]};
export type AIDraft={format:'cqa-ai-review-draft-v1';criteria_order:string[];cases:AICase[];origin:string;ground_truth:false;context?:Record<string,string>;review?:{confirmed:boolean;reviewed_ids:string[];content_sha256:string;reviewed_at:string|null}|null};
export type AIEditor={values:string[];evidence:string[];rationales:string[];naReasons:Record<string,string>;flags:string};
import {evidenceNumbers} from './annotationDraft';
export function flagsFor(c:AICase,id:string){return c.review_flags.filter(f=>f.split(':',1)[0].trim()===id)}
export function editCase(c:AICase):AIEditor{return {values:c.values.map(v=>JSON.stringify(v)),evidence:c.evidence_turns.map(t=>t.join(', ')),rationales:[...c.rationales],naReasons:{...c.na_reasons},flags:c.review_flags.join('\n')}}
export function applyEdit(draft:AIDraft,index:number,editor:AIEditor):AIDraft{
 const values=editor.values.map(v=>JSON.parse(v) as Value);
 const c={...draft.cases[index],values,evidence_turns:editor.evidence.map(evidenceNumbers),rationales:[...editor.rationales],na_reasons:Object.fromEntries(draft.criteria_order.flatMap((id,i)=>values[i]===null?[[id,editor.naReasons[id]??'']]:[])),review_flags:editor.flags.split('\n').map(s=>s.trim()).filter(Boolean)};
 // Review confirmation belongs to exact contents; any edit clears it and all reviewed checkmarks.
 return {...draft,cases:draft.cases.map((old,i)=>i===index?c:old),review:null};
}
export function reviewComplete(draft:AIDraft,ids:string[]){return ids.length===18&&new Set(ids).size===18&&draft.cases.every(c=>ids.includes(c.conversation_id))}
