const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createStore,fromURL,resolveSelection,url,createPoller,readJSON,summary,presentation}=require('../ui/recording-session.js');
const id='a'.repeat(32),second='b'.repeat(32);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check){for(let n=0;n<200;n++){if(check())return;await pause(2)}assert.fail('polling condition timed out')}
test('recording selection is project-wide, model-independent, and preserves chosen option',()=>{
 const data=new Map(),storage={getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};
 const one=createStore(storage,'project-one');one.save({kind:'candidates',id,selected:second,model:'voxcpm2'});one.setModel('higgs');
 const returned=createStore(storage,'project-one');assert.equal(returned.model(),'higgs');assert.equal(returned.selection().selected,second);assert.equal(returned.selection().model,'voxcpm2');
 assert.equal(createStore(storage,'project-two').selection(),null);
 returned.clear();assert.equal(one.selection(),null);assert.equal(one.model(),'higgs');
});
test('invalid storage and denied storage never break navigation',()=>{
 const store=createStore({getItem:()=>'{',setItem:()=>{throw Error('denied')}},'project');
 assert.equal(store.selection(),null);store.save({kind:'job',id});store.setModel('higgs');store.clear();
});
test('deep links validate IDs, preserve the chosen option, and replace stale selection parameters',()=>{
 const value={kind:'candidates',id,selected:second};assert.deepEqual(fromURL(url(value).slice(2)),value);
 assert.equal(fromURL('?candidates=../../secret'),null);assert.equal(fromURL('?job='+id).kind,'job');
 assert.equal(fromURL('?artifact='+id).kind,'artifact');assert.equal(fromURL('?voice='+id),null);
 assert.equal(url({kind:'job',id,selected:second}),'/?job='+id);
});
test('transient errors reconnect and do not finish or submit work',async()=>{
 let reads=0,finished=false;const states=[],values=[];
 const poll=createPoller({interval:2,maxDelay:4,read:async()=>{reads++;if(reads<3)throw Object.assign(Error('unavailable'),{status:503});return {done:reads===4}},onConnection:value=>states.push(value),onData:value=>{values.push(value);finished=value.done;return value.done}});
 await until(()=>finished);const finalReads=reads;await pause(10);assert.equal(reads,finalReads);assert.deepEqual(states,[true,true,false,false]);assert.equal(values.length,2);poll.stop();
});
test('a removed recording stops retries and reports missing once',async()=>{
 let reads=0,missing=0;const poll=createPoller({interval:2,read:async()=>{reads++;throw Object.assign(Error('gone'),{status:404})},onData:()=>assert.fail(),onMissing:()=>missing++});
 await until(()=>missing);await pause(10);assert.equal(reads,1);assert.equal(missing,1);poll.stop();
});
test('actionable client errors stop polling instead of reconnecting forever',async()=>{
 let failed=0;const poll=createPoller({read:async()=>{throw Object.assign(Error('invalid'),{status:422})},onData:()=>assert.fail(),onError:exc=>{assert.equal(exc.status,422);failed++}});await until(()=>failed);assert.equal(failed,1);poll.stop();
});
test('revalidation aborts a suspended read and immediately obtains fresh results',async()=>{
 let reads=0,done=false,aborted=false;
 const poll=createPoller({interval:10000,read:signal=>{reads++;if(reads===1)return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>{aborted=true;reject(Error('aborted'))}));return Promise.resolve('ready')},onData:value=>{assert.equal(value,'ready');done=true;return true}});
 await until(()=>reads===1);poll.refresh();await until(()=>done);assert.equal(aborted,true);assert.equal(reads,2);poll.stop();
});
test('revalidation wakes backoff, and replaced monitors ignore late data',async()=>{
 let reads=0,resolveOld,seen=0;
 const old=createPoller({read:()=>new Promise(resolve=>{resolveOld=resolve}),onData:()=>{seen++;return true}});await until(()=>resolveOld);old.stop();resolveOld('stale');await pause(5);assert.equal(seen,0);
 const poll=createPoller({interval:10000,read:async()=>++reads,onData:()=>{seen++;return reads===2}});await until(()=>reads===1);poll.refresh();await until(()=>reads===2);assert.equal(seen,2);poll.stop();
});
test('read-only request deadlines recover a stalled connection and retain HTTP status',async()=>{
 const original=global.fetch;
 try{
  global.fetch=(path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('deadline'))));
  await assert.rejects(readJSON('/api/jobs/'+id,{timeout:3}),/deadline/);
  global.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'Database temporarily unavailable'})});
  await assert.rejects(readJSON('/health'),exc=>exc.status===503&&exc.message==='Database temporarily unavailable');
 }finally{global.fetch=original}
});

test('render failures are reported once rather than misreported as a connection outage',async()=>{
 let failed=0,reads=0;const poll=createPoller({read:async()=>++reads,onData:()=>{throw Error('render failure')},onConnection:offline=>assert.equal(offline,false),onError:exc=>{assert.equal(exc.message,'render failure');failed++}});await until(()=>failed);await pause(5);assert.equal(reads,1);poll.stop();
});

test('explicit links win while retaining saved timing for the same recording',()=>{
 const saved={kind:'candidates',id,selected:second,model:'voxcpm2',timing:{start:1234,label:'Loading'}};
 const explicit=fromURL('?candidates='+id+'&option='+second);
 assert.deepEqual(resolveSelection(explicit,saved),saved);
 assert.equal(resolveSelection({kind:'job',id:second},saved).timing,undefined);
 assert.deepEqual(resolveSelection(null,saved),saved);
});

test('standalone takes restore without a batch and duplicate refreshes preserve playback',()=>{
 const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
 const source=fs.readFileSync(path.join(__dirname,'../ui/app.js'),'utf8');
 const show=source.slice(source.indexOf('function markSelectedOption(identity)'),source.indexOf('\nlet pauseSource='));
 const elements=new Map();let audioAssignments=0,stops=0;const selections=[];
 const element=id=>{if(!elements.has(id))elements.set(id,{hidden:true,attributes:{},set src(value){this.attributes.src=value;audioAssignments++},getAttribute(name){return this.attributes[name]||null}});return elements.get(id)};
 const row={id,backend:'higgs',audio_url:'/api/audio/outputs/'+id,duration:1,sample_rate:16000,generation_seconds:.1,seed:42};
 const context=vm.createContext({$:element,browsePage:false,latest:null,currentCandidates:null,candidateCards:new Map(),recordingSelection:null,recordingPoller:{stop:()=>stops++},watching:null,monitoredImmediate:false,controls:()=>{},rememberRecording:value=>selections.push(value),renderExpansion:()=>{},candidateExpansion:()=>{},waveform:()=>{},encodeURIComponent,row});
 vm.runInContext(show+';showResult(row);showResult(row);',context);
 assert.equal(selections[0].kind,'artifact');assert.equal(audioAssignments,1);assert.equal(element('result').hidden,false);assert.equal(element('result-resources').href,'/observability?job='+id);
 context.currentCandidates={id:second};context.watching='candidates:'+second;context.monitoredImmediate=true;
 vm.runInContext('showResult(row)',context);assert.equal(stops,1);assert.equal(context.watching,null);assert.equal(context.currentCandidates,null);
 context.currentCandidates={id:second};context.row={...row,candidate_batch_id:second};
 vm.runInContext('showResult(row)',context);assert.equal(selections.at(-1).kind,'candidates');assert.equal(selections.at(-1).selected,id);
});

test('return summary distinguishes live, completed, partial, removed and disconnected recordings',()=>{
 const selection={kind:'candidates',id,model:'higgs'};
 const jobs=[{status:'running',stage:'Generating speech',backend:'higgs'},{status:'queued'}];
 const active=summary(selection,{status:'running',candidate_count:2,done_count:0,created_at:'2026-10-27T12:00:00Z',jobs});
 assert.equal(active.stage,'Generating speech');assert.equal(active.completed,null);assert.equal(presentation(active).active,true);
 assert.equal(presentation(active,'checking').title,'Opening your recording…');
 assert.equal(presentation(active,'reconnecting').active,false);
 const partial=summary(selection,{status:'partial',candidate_count:2,done_count:1,jobs:[{status:'done',completed_at:'2026-10-27T12:00:02Z'},{status:'error',completed_at:'2026-10-27T12:00:03Z'}]});
 assert.equal(partial.failed,1);assert.equal(partial.completed,'2026-10-27T12:00:03Z');assert.equal(presentation(partial).title,'1 of 2 options ready');assert.equal(presentation(partial).action,'Review recording');
 assert.equal(presentation({...partial,status:'done',ready:2}).action,'Listen to recording');
 assert.equal(presentation({...partial,status:'deleted'}).title,'Recording removed from queue');
});
test('return summaries store progress without copying dialogue or media into browser state',()=>{
 const data=summary({kind:'job',id},{status:'done',backend:'voxcpm2',result:{audio_url:'/private-audio'},text:'private dialogue',requested:{description:'private prompt'},completed_at:'2026-10-27T12:00:00Z'});
 assert.equal(data.count,1);assert.equal(data.ready,1);assert.equal(data.status,'done');
 assert.equal(JSON.stringify(data).includes('private'),false);
});
