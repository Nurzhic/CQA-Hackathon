export type Draft={value:string;rationale:string;evidence:string;na_reason:string;uncertain:boolean;uncertainty_reason:string};
export type LocalDraft={version:1;reviewer:string;reason:string;decisions:Record<string,string>;draft:Record<string,Draft>;annotation_id:number|null;based_on:number[]};
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export function draftKey(context:Record<string,string>,cid:string,slot:string){
 return 'cqa-human-draft-v1:'+JSON.stringify([context.manifest_sha256,context.conversation_sha256,context.checklist_sha256,cid,slot]);
}
export function readDraft(store:StorageLike,key:string,annotationId:number|null,basedOn:number[]):LocalDraft|null {
 try {
  const d=JSON.parse(store.getItem(key)??'null');
  if(!d||d.version!==1||d.annotation_id!==annotationId||JSON.stringify(d.based_on)!==JSON.stringify(basedOn)||typeof d.reviewer!=='string'||typeof d.reason!=='string'||!d.draft||!d.decisions)return null;
  if(!Object.values(d.decisions).every(v=>typeof v==='string'))return null;
  if(!Object.values(d.draft).every((x:any)=>x&&['value','rationale','evidence','na_reason','uncertainty_reason'].every(k=>typeof x[k]==='string')&&typeof x.uncertain==='boolean'))return null;
  return d;
 }catch{return null}
}
export function writeDraft(store:StorageLike,key:string,draft:LocalDraft){store.setItem(key,JSON.stringify(draft))}
export function evidenceNumbers(value:string){
 if(!/^\s*[1-9]\d*(\s*,\s*[1-9]\d*)*\s*$/.test(value))throw Error('Укажите целые исходные номера от 1 через запятую');
 const turns=value.split(',').map(Number);
 if(turns.some(n=>!Number.isSafeInteger(n))||new Set(turns).size!==turns.length)throw Error('Номера реплик должны быть целыми и уникальными');
 return turns;
}
