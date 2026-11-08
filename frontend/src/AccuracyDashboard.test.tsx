import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,test} from 'vitest';
import {AccuracyDashboard,AccuracyModel,CalibrationProblems} from './AccuracyDashboard';
import {HumanFastLane} from './HumanFastLane';
const telemetry={latency:{count:0,mean_ms:null,median_ms:null,p95_ms:null,p95_method:'nearest_rank'},retries:0,validation_failures:0,evidence:{turns_valid:0,turns_total:0,quotes_valid:0,quotes_total:0,missing_quotes:0},reported_cost_usd:null,reported_cost_coverage:0};
test('independent entry point contains no AI importer or prefilled labels',()=>{const html=renderToStaticMarkup(<HumanFastLane onBack={()=>{}}/>);expect(html).toContain('Human Review Fast Lane');expect(html).toContain('Expert A');expect(html).toContain('Expert B');expect(html).toContain('Bulk Consensus');expect(html).not.toContain('Загрузить AI-черновик');expect(html).toContain('Авторизации нет')});
test('unlabeled dashboard and missing price are not displayed as zero',()=>{const h=renderToStaticMarkup(<AccuracyModel name="fixture" model={{paired:0,successful:0,success_rate:null,report:{status:'unlabeled',metrics:null,labeled_conversations:0,telemetry}}}/>);expect(h).toContain('null / unlabeled');expect(h).toContain('неизвестна');expect(h).not.toContain('0.00%')});
test('dashboard explicitly separates human accuracy, AI draft and holdout',()=>{const h=renderToStaticMarkup(<AccuracyDashboard onBack={()=>{}}/>);expect(h).toContain('восьми значениям критериев');expect(h).toContain('Holdout 9');expect(h).toContain('null/unlabeled')});
test('labeled metric denominators, MAE and N/A errors are visible',()=>{const m={accuracy:.5,correct:4,total:8,missing:1,scale_mae:5,scale_count:2,critical_precision:.5,critical_recall:1,critical_tp:1,critical_fp:1,critical_fn:0,critical_tn:0,na_matrix:{actual_na_pred_na:1,actual_na_pred_applicable:1,actual_applicable_pred_na:1,actual_applicable_pred_applicable:4,missing:1}};const h=renderToStaticMarkup(<AccuracyModel name="fixture" model={{paired:1,successful:1,success_rate:1,report:{status:'labeled',metrics:{overall:m},labeled_conversations:1,telemetry}}}/>);expect(h).toContain('4/8');expect(h).toContain('50.00%');expect(h).toContain('GT N/A');expect(h).toContain('Scale MAE / n')});

test('calibration error details never display an absent prediction as N/A',()=>{
 const analysis={paired_labeled_cases:1,pipeline_sha256:'fixture',problem_dialogues:[{conversation_id:'fixture-case',language:'EN',consensus_id:4,models:{fixture:{status:'error',evaluation_id:1,attempt_errors:[],differences:[{criterion_id:'discovery',expected:null,predicted:null,prediction_present:false,kind:'missing_result',ground_truth_turns:[1],model_turns:[]}]}}}],recommendations:[{model:null,action:'Explicit calibration recommendation'}]};
 const html=renderToStaticMarkup(<CalibrationProblems analysis={analysis}/>);
 expect(html).toContain('только calibration');expect(html).toContain('Нет принятого ответа');expect(html).toContain('missing_result');expect(html).toContain('Explicit calibration recommendation');
});
