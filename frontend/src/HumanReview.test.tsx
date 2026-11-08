import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,test} from 'vitest';
import {HumanProgress,HumanDisagreement} from './HumanReview';
import {BenchmarkPanel} from './Benchmark';
import type {Criterion} from './types';
const criterion:Criterion={id:'critical',name:'Грубые ошибки',description:'Official fixture',weight:0,type:'boolean',scale:[],allow_na:false,na_rule:''};
test('empty real annotation state is displayed as unlabeled, not zero accuracy',()=>{
 const page=renderToStaticMarkup(<BenchmarkPanel/>);expect(page).toContain('не загружен');expect(page).toContain('Авторизации нет');expect(page).not.toContain('Accuracy: 0');
 const progress=renderToStaticMarkup(<HumanProgress/>);expect(progress).toContain('A 0/18');expect(progress).toContain('B 0/18');expect(progress).toContain('consensus 0/18');expect(progress).toContain('не считаются Ground Truth');
});
test('only valid consensus counts in progress; stale consensus does not',()=>{
 const html=renderToStaticMarkup(<HumanProgress annotations={[{conversation_id:'1',status:'needs_adjudication',rounds:{A:1,B:2,consensus:3}},{conversation_id:'2',status:'consensus',rounds:{A:4,B:5,consensus:6}}]}/>);
 expect(html).toContain('A 2/18');expect(html).toContain('B 2/18');expect(html).toContain('consensus 1/18');
});
test('adjudication shows both values, human rationale and evidence',()=>{
 const a={criterion_id:'critical',value:false,rationale:'Human A explanation',evidence_turns:[1]};
 const b={...a,value:true,rationale:'Human B explanation',evidence_turns:[2]};
 const html=renderToStaticMarkup(<HumanDisagreement criterion={criterion} rounds={{A:{criteria:[a]},B:{criteria:[b]}}} disagreement={{criterion_id:'critical'}}/>);
 for(const text of ['A: Нет','B: Да','Human A explanation','Human B explanation','#1','#2','требуется причина'])expect(html).toContain(text);
 expect(html).not.toContain('GPT');
});
test('adjudication does not expose one existing round before both are present',()=>expect(renderToStaticMarkup(<HumanDisagreement criterion={criterion} rounds={{A:{criteria:[]}}} disagreement={null}/>)).toBe(''));
