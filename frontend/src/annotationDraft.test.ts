import {expect,test} from 'vitest';
import {draftKey,readDraft,writeDraft,evidenceNumbers} from './annotationDraft';
import type {LocalDraft} from './annotationDraft';
function memory(){const map=new Map<string,string>();return {getItem:(k:string)=>map.get(k)??null,setItem:(k:string,v:string)=>{map.set(k,v)},removeItem:(k:string)=>{map.delete(k)}}}
const context={manifest_sha256:'m',conversation_sha256:'c',checklist_sha256:'o'};
const partial:LocalDraft={version:1,reviewer:'Human A',reason:'',decisions:{},annotation_id:null,based_on:[],draft:{contact:{value:'false',rationale:'Ещё не закончено. Сәлем!',evidence:'1',na_reason:'',uncertain:false,uncertainty_reason:''},discovery:{value:'',rationale:'',evidence:'',na_reason:'',uncertain:false,uncertainty_reason:''}}};
test('partial work survives storage roundtrip without converting blank to N/A',()=>{
 const store=memory(),key=draftKey(context,'fixture','A');writeDraft(store,key,partial);
 const result=readDraft(store,key,null,[]);expect(result).toEqual(partial);expect(result?.draft.discovery.value).toBe('');expect(result?.draft.contact.value).toBe('false');
});
test('round, case, manifest, transcript and checklist isolate local drafts',()=>{
 const store=memory(),key=draftKey(context,'fixture','A');writeDraft(store,key,partial);
 for(const other of [draftKey(context,'fixture','B'),draftKey(context,'fixture','consensus'),draftKey(context,'other','A'),...Object.keys(context).map(k=>draftKey({...context,[k]:'changed'},'fixture','A'))])expect(readDraft(store,other,null,[])).toBeNull();
});
test('new server version or changed consensus references invalidate stale browser draft',()=>{
 const store=memory(),key=draftKey(context,'fixture','A');writeDraft(store,key,partial);
 expect(readDraft(store,key,3,[])).toBeNull();expect(readDraft(store,key,null,[1,2])).toBeNull();
 store.removeItem(key);expect(readDraft(store,key,null,[])).toBeNull();
});
test.each(['{broken','null','{"version":1}','{"version":1,"annotation_id":null,"based_on":[],"reviewer":"A","reason":"","draft":{"contact":{"value":null}},"decisions":{}}'])('corrupt draft is not restored: %s',raw=>{
 const store=memory();store.setItem('k',raw);expect(readDraft(store,'k',null,[])).toBeNull();
});
test.each(['','0','-1','1.5','1,1','1,,2','true','1e2'])('evidence rejects invalid manual selection %s',text=>expect(()=>evidenceNumbers(text)).toThrow());
test('evidence preserves one-based indices exactly',()=>expect(evidenceNumbers(' 1, 3, 12 ')).toEqual([1,3,12]));
