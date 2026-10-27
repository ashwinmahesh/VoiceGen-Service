const {test}=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../ui/voice-upload.js');
const file={name:'one.wav'},other={name:'two.wav'};
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
test('file selection prepares an editable transcript before the voice is saved',async()=>{
 const states=[];let options;
 const controller=create({prepare:async(value,request)=>{assert.equal(value,file);options=request;return {id:'ref',transcript:'Welcome.',transcription:{model:'Whisper'}}},onState:state=>states.push(state)});
 await controller.select(file,{language:'en'});
 assert.equal(states[0].pending,true);assert.equal(states.at(-1).pending,false);assert.equal(states.at(-1).applyTranscript,true);assert.equal(controller.snapshot().reference.id,'ref');assert.equal(options.language,'en');
});
test('words typed while recognition is running are never overwritten',async()=>{
 const pending=deferred(),states=[];const controller=create({prepare:()=>pending.promise,onState:state=>states.push(state)});
 const ready=controller.select(file);controller.edit();pending.resolve({id:'ref',transcript:'Detected words.'});await ready;
 assert.equal(states.at(-1).applyTranscript,false);assert.equal(controller.snapshot().reference.id,'ref');
});
test('a new file aborts the old request and ignores its late transcript',async()=>{
 const requests=[],states=[];const controller=create({prepare:(value,options)=>{const result=deferred();requests.push({value,options,result});return result.promise},onState:state=>states.push(state)});
 const first=controller.select(file),second=controller.select(other);assert.equal(requests[0].options.signal.aborted,true);
 requests[1].result.resolve({id:'two',transcript:'Second voice.'});await second;
 requests[0].result.resolve({id:'one',transcript:'Wrong voice.'});await first;
 assert.equal(controller.snapshot().reference.id,'two');assert.equal(states.at(-1).transcript,'Second voice.');
});
test('canceling the modal prevents late results from changing the next voice',async()=>{
 const pending=deferred();let signal;const controller=create({prepare:(value,options)=>{signal=options.signal;return pending.promise},onState:()=>{}});
 const first=controller.select(file);controller.cancel();pending.resolve({id:'late',transcript:'Late words.'});await first;
 assert.equal(signal.aborted,true);assert.equal(controller.snapshot().reference,null);assert.equal(controller.snapshot().pending,false);
});
test('opt out preserves supplied or detected words without starting another transcription',async()=>{
 let calls=0;const controller=create({prepare:async()=>{calls++;return {id:'ref',transcript:'Keep these words.',transcription:{model:'Whisper'}}},onState:()=>{}});
 await controller.select(file);await controller.select(file,{automatic:false,transcript:'Keep these words.'});assert.equal(controller.snapshot().transcript,'Keep these words.');assert.equal(calls,1);
 await controller.select(other,{automatic:false,transcript:'My typed words.'});assert.equal(controller.snapshot().transcript,'My typed words.');assert.equal(calls,1);
});
test('switching recordings clears the prior automatic transcript and failures can be retried',async()=>{
 let calls=0;const controller=create({prepare:async(value,options)=>{calls++;if(calls===2){assert.equal(options.transcript,'');throw Error('Unavailable')}return {id:String(calls),transcript:'Detected.',transcription:{model:'Whisper'}}},onState:()=>{}});
 await controller.select(file);await controller.select(other,{transcript:'Detected.'});assert.equal(controller.snapshot().error,'Unavailable');assert.equal(controller.snapshot().pending,false);
 await controller.select(other);assert.equal(controller.snapshot().reference.id,'3');assert.equal(controller.snapshot().error,null);
});

test('changing the recognition language re-transcribes generated words but keeps manual ones',async()=>{
 const seen=[];const controller=create({prepare:async(value,options)=>{seen.push(options);return {id:'ref',transcript:'Detected.',transcription:{model:'Whisper'}}},onState:()=>{}});
 await controller.select(file);await controller.select(file,{transcript:'Detected.',language:'en'});assert.equal(seen.at(-1).transcript,'');
 controller.edit();await controller.select(file,{transcript:'My corrected words.',language:'ro'});assert.equal(seen.at(-1).transcript,'My corrected words.');assert.equal(controller.snapshot().transcript,'My corrected words.');
});
