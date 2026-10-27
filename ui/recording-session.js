/* Browser-only recording recovery. Form drafts remain separate and model-specific. */
(function(root){
'use strict';
const validId=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
function validSelection(value){return value&&['candidates','job','artifact'].includes(value.kind)&&validId(value.id)}
function fromURL(search){
 const params=new URLSearchParams(search);
 for(const kind of ['candidates','job','artifact'])if(validId(params.get(kind)))return {kind,id:params.get(kind),selected:validId(params.get('option'))?params.get('option'):null};
 return null;
}
function resolveSelection(explicit,saved){return explicit?{...(saved?.kind===explicit.kind&&saved?.id===explicit.id?saved:{}),...explicit}:saved}
function url(selection){const params=new URLSearchParams({[selection.kind]:selection.id});if(selection.kind==='candidates'&&validId(selection.selected))params.set('option',selection.selected);return '/?'+params}
function summary(selection,data){
 const batch=selection.kind==='candidates',jobs=batch?data.jobs:[data],active=['queued','running'].includes(data.status);
 const running=jobs.find(job=>job.status==='running');
 return {status:data.status,backend:data.backend||jobs[0]?.backend||selection.model,count:batch?data.candidate_count:1,
  ready:batch?data.done_count:data.result?1:0,failed:jobs.filter(job=>job.status==='error').length,
  stage:running?.stage||(active?'Waiting in queue':''),created:data.created_at,
  completed:active?null:jobs.map(job=>job.completed_at).filter(Boolean).sort().at(-1)||data.completed_at||null};
}
function presentation(summary,connection='connected'){
 const active=['queued','running'].includes(summary?.status);
 if(connection!=='connected')return {title:connection==='reconnecting'?'Reconnecting to your recording…':'Opening your recording…',active:false,action:'Return to recording'};
 const count=summary.count||1,ready=summary.ready||0;
 const title=active?'Recording in progress':summary.status==='done'?'Your recording is ready':summary.status==='partial'?`${ready} of ${count} options ready`:summary.status==='deleted'?'Recording removed from queue':'Recording needs attention';
 return {title,active,action:active?'Return to recording':summary.status==='done'?'Listen to recording':'Review recording'};
}
function createStore(storage,project){
 const key='voice-studio-session-v1:'+project;
 function read(){try{return JSON.parse(storage.getItem(key)||'{}')||{}}catch{return {}}}
 function update(values){try{storage.setItem(key,JSON.stringify({...read(),...values}))}catch{}}
 return {key,selection:()=>{const value=read().selection;return validSelection(value)?value:null},save:selection=>update({selection}),clear:()=>update({selection:null}),model:()=>read().model,setModel:model=>update({model})};
}
async function readJSON(path,{signal,timeout=10000,...options}={}){
 const controller=new AbortController(),abort=()=>controller.abort();
 if(signal?.aborted)controller.abort();else signal?.addEventListener('abort',abort,{once:true});
 const timer=timeout>0?setTimeout(abort,timeout):null;
 try{
  const response=await fetch(path,{...options,signal:controller.signal});
  if(!response.ok){let body;try{body=await response.json()}catch{}throw Object.assign(Error(typeof body?.detail==='string'?body.detail:body?.detail?.[0]?.msg||`Request failed (${response.status})`),{status:response.status})}
  return await response.json();
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
}
function createPoller({read,onData,onConnection=()=>{},onMissing=()=>{},onError=()=>{},interval=1500,maxDelay=10000}){
 let stopped=false,controller=null,timer=null,wake=null,failures=0;
 function release(){clearTimeout(timer);if(wake){const resolve=wake;wake=null;resolve()}}
 function stop(){stopped=true;controller?.abort();release()}
 function refresh(){controller?.abort();release()}
 async function run(){
  while(!stopped){
   controller=new AbortController();const request=controller;
   try{
    const value=await read(request.signal);
    if(stopped||request.signal.aborted)continue;
    failures=0;onConnection(false);
    let finished;
    try{finished=onData(value)}catch(exc){onError(exc);stop();break}
    if(finished){stop();break}
   }catch(exc){
    if(stopped)break;
    if(request.signal.aborted)continue;
    if(exc.status===404){onMissing();stop();break}
    if(exc.status&&exc.status<500&&![408,425,429].includes(exc.status)){onError(exc);stop();break}
    failures++;onConnection(true);
   }finally{if(controller===request)controller=null}
   if(!stopped)await new Promise(resolve=>{wake=resolve;timer=setTimeout(()=>{wake=null;resolve()},Math.min(maxDelay,interval*2**Math.min(failures,3)))})
  }
 }
 // Start after callers have stored the handle, including with synchronous test readers.
 Promise.resolve().then(run);
 return {stop,refresh};
}
const api={validId,fromURL,resolveSelection,url,summary,presentation,createStore,readJSON,createPoller};
if(typeof module==='object'&&module.exports)module.exports=api;else root.VoiceRecording=api;
})(typeof window==='object'?window:globalThis);
