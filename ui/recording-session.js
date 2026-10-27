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
function createStore(storage,project){
 const key='voice-studio-session-v1:'+project;
 function read(){try{return JSON.parse(storage.getItem(key)||'{}')||{}}catch{return {}}}
 function update(values){try{storage.setItem(key,JSON.stringify({...read(),...values}))}catch{}}
 return {selection:()=>{const value=read().selection;return validSelection(value)?value:null},save:selection=>update({selection}),clear:()=>update({selection:null}),model:()=>read().model,setModel:model=>update({model})};
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
const api={validId,fromURL,resolveSelection,url,createStore,readJSON,createPoller};
if(typeof module==='object'&&module.exports)module.exports=api;else root.VoiceRecording=api;
})(typeof window==='object'?window:globalThis);
