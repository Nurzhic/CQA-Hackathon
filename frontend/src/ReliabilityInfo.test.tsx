import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,test} from 'vitest';
import {ReliabilityInfo} from './ReliabilityInfo';
import type {Evaluation} from './types';
const base:Evaluation={id:1,checklist_id:1,conversation_id:'fixture',created:'',status:'error',criteria:[],score:null,mock:false,model:'fixture'};
test('historical results need no new fields',()=>expect(renderToStaticMarkup(<ReliabilityInfo evaluation={base}/>)).toBe(''));
test('provenance does not claim semantic validation',()=>{
 const html=renderToStaticMarkup(<ReliabilityInfo evaluation={{...base,provider_metadata:{evidence_source:'model_selected_turns/backend_original_full_turn',evidence_semantics_verified:false}}}/>);
 expect(html).toContain('Реплики выбрала модель');expect(html).toContain('должен проверить человек');
});
test('safe structured diagnostics render category and field path',()=>{
 const html=renderToStaticMarkup(<ReliabilityInfo evaluation={{...base,attempt_errors:[{attempt:1,kind:'validation',error:'Invalid type',diagnostics:[{category:'invalid_type',stage:'criteria_evaluation',path:['criteria','contact','value'],message:'Field has an invalid type'}]}]}}/>);
 expect(html).toContain('invalid_type');expect(html).toContain('criteria.contact.value');
});
