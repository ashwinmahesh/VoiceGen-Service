/* Upload preview ownership: stale requests never replace a different file or typed words. */
(function(root){
'use strict';
function create({prepare,onState}){
 let version=0,controller=null,state={file:null,reference:null,pending:false,error:null},automaticText=null,editVersion=0,activeLanguage='auto';
 function emit(){onState({...state})}
 function cancel(){version++;controller?.abort();controller=null;state={file:null,reference:null,pending:false,error:null};automaticText=null;emit()}
 function edit(){editVersion++;automaticText=null}
 async function select(file,{automatic=true,transcript='',language='auto'}={}){
  const previous=state;
  const request=++version;controller?.abort();controller=new AbortController();
  if(automaticText!==null&&transcript===automaticText&&(file!==state.file||automatic&&language!==activeLanguage))transcript='';
  activeLanguage=language;
  automaticText=null;
  state={file,reference:!automatic&&file===previous.file?previous.reference:null,pending:!!file&&automatic,error:null,transcript,applyTranscript:true};emit();
  if(!file||!automatic)return;
  const editing=editVersion;
  try{
   const reference=await prepare(file,{transcript,language,signal:controller.signal});
   if(request!==version)return;
   state={file,reference,pending:false,error:null,transcript:transcript.trim()?transcript:reference.transcript||'',applyTranscript:editing===editVersion};
   if(state.applyTranscript)automaticText=!transcript.trim()&&reference.transcription?state.transcript:null;
  }catch(exc){if(request!==version)return;state={file,reference:null,pending:false,error:exc.message,applyTranscript:false}}
  if(request===version)emit();
 }
 return {select,cancel,edit,snapshot:()=>({...state})};
}
const api={create};if(typeof module==='object'&&module.exports)module.exports=api;else root.VoiceUpload=api;
})(typeof window==='object'?window:globalThis);
