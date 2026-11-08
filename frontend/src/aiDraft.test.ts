import {expect,test} from 'vitest';
import {applyEdit,editCase,flagsFor,reviewComplete} from './aiDraft';
import type {AIDraft} from './aiDraft';
const order=['contact','discovery','identification','consultation','empathy','dialogue','closure','critical'];
function fixture():AIDraft{return {format:'cqa-ai-review-draft-v1',origin:'AI_ASSISTED_DRAFT_NOT_HUMAN',ground_truth:false,criteria_order:order,cases:Array.from({length:18},(_,i)=>({conversation_id:'fixture-'+i,language:'KZ',split:'calibration',values:[true,null,null,20,true,10,false,false],evidence_turns:order.map(()=>[1]),rationales:order.map(()=> 'Сәлеметсіз бе! Original fixture'),na_reasons:{discovery:'Initial context',identification:'General request'},review_flags:['identification: N/A vs false','dialogue: 4 vs 7']})),review:{confirmed:true,reviewed_ids:[],content_sha256:'old',reviewed_at:'now'}}}
test('flags highlight only their criterion',()=>{const c=fixture().cases[0];expect(flagsFor(c,'identification')).toEqual(['identification: N/A vs false']);expect(flagsFor(c,'contact')).toEqual([])});
test('edits preserve AI origin, clear stale confirmation, never mutate imported draft',()=>{
 const draft=fixture(),editor=editCase(draft.cases[0]);editor.rationales[0]='Reviewed original text';editor.evidence[0]='1, 3';editor.values[2]='false';
 const next=applyEdit(draft,0,editor);expect(next.review).toBeNull();expect(next.origin).toBe('AI_ASSISTED_DRAFT_NOT_HUMAN');expect(next.ground_truth).toBe(false);
 expect(next.cases[0].evidence_turns[0]).toEqual([1,3]);expect(next.cases[0].na_reasons).toEqual({discovery:'Initial context'});expect(draft.cases[0].rationales[0]).toContain('Сәлеметсіз');expect(draft.review?.confirmed).toBe(true);
});
test('bad manual indices cannot be applied',()=>{const d=fixture(),e=editCase(d.cases[0]);e.evidence[0]='0';expect(()=>applyEdit(d,0,e)).toThrow()});
test('confirmation requires every distinct case',()=>{const d=fixture(),ids=d.cases.map(c=>c.conversation_id);expect(reviewComplete(d,ids)).toBe(true);expect(reviewComplete(d,ids.slice(1))).toBe(false);expect(reviewComplete(d,[...ids.slice(1),ids[1]])).toBe(false)});
