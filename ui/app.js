
const $=id=>document.getElementById(id);let mode='design',collection='voices',reference=null,latest=null,voiceRows=[],historyRows=[],busy=false,online=false,uploading=false,watching=null,submitting=false,queueWritable=true;
const collectionPage=location.pathname.startsWith('/collection'),observabilityPage=location.pathname.startsWith('/observability');
const browsePage=collectionPage||observabilityPage;
document.title=(observabilityPage?'Observability':collectionPage?'Collection':'Studio')+' · Voice Studios';
if(collectionPage){const params=new URLSearchParams(location.search);collection=params.get('tab')==='history'?'history':'voices';$('show-deleted').checked=params.get('deleted')==='1';$('page-title').textContent='Your collection';$('page-description').textContent='Find saved voices, listen to takes, and reuse a performance in the studio.';}
$(observabilityPage?'observability-link':collectionPage?'catalog-link':'studio-link').setAttribute('aria-current','page');
let availableModels=[],modelChoiceReady=false,changingModel=false,queuePaused=false,awaitingSourceModel=false,lastModelList=null,pendingModelRestore=null;
let recordSource='saved',uploadTranscriptDraft=null;
const recordSources=[['saved','Saved voice'],['upload','Upload reference audio'],['preset','Built-in speaker']];
function sourceMode(source=mode){return ['saved','upload'].includes(source)?'reference':source}
function acceptsTextDirections(){return ['saved','upload'].includes(mode)?!!caps?.modes.reference.emotion&&!['continuation','combined'].includes($('clone-mode').value):mode==='preset'||!!caps?.modes.design.emotion}
function readySourceModels(source=mode){return availableModels.filter(row=>row.modes?.includes(sourceMode(source)))}
function readyDesignModels(){return readySourceModels('design')}
function resolvedReferenceMode(){
 const selected=$('clone-mode').value;if(selected!=='auto')return selected;
 const transcript=$('ref-text').value||(mode==='saved'?voiceRows.find(row=>row.id===$('voice').value)?.transcript:reference?.transcript)||'';
 return caps?.backend==='voxcpm2'&&transcript.trim()&&!$('emotion').value.trim()&&!$('accent').value.trim()?'combined':'reference';
}
let pageSize = 24, caps=null, storageKey='voice-studio', historyPage={data:[],total:0,has_more:false}, pendingKey=null;
let voicePage = {data: [], total: 0, has_more: false};
let currentCandidates=null, candidateCards=new Map();
let appliedExpansion=null,pendingExpansion=null,enhancing=false,expansionChoice=null;
let catalogOffset = 0, searchTimer = null, catalogRequest = 0, refreshRequest=0, voiceEditor = null, voiceSaving = false;
let collectionDeleteTarget=null,collectionChanging=false;
let selectedTags=new Set(),availableTags=[];
let collectionCleanup=null;
let monitoredImmediate=false,higgsSelectedControls={},suspendedHiggsDirections={};
let recordingStore=null,recordingSelection=null,recordingPoller=null,healthSnapshot=null,studioInitialized=false,studioStarting=false;
let returnPoller=null,returnSelection=null,returnSummary=null;
const enabledPlaceholders=new WeakMap();
function disabledPlaceholder(input,reason){
 if(input.disabled){
  if(!enabledPlaceholders.has(input))enabledPlaceholders.set(input,input.getAttribute('placeholder'));
  input.placeholder=reason;
 }else if(enabledPlaceholders.has(input)){
  const original=enabledPlaceholders.get(input);
  if(original===null)input.removeAttribute('placeholder');else input.placeholder=original;
  enabledPlaceholders.delete(input);
 }
}
const presets={natural:'Natural, conversational delivery with relaxed pacing.',joy:'Excited and joyful, with an audible smile and lively pacing.',sadness:'Quiet sadness and disappointment, speaking slowly with a trembling voice.',anger:'Angry and frustrated, with firm emphasis and a tense, forceful delivery.',fear:'Frightened and anxious, with a trembling voice and urgent, uneven pacing.',relief:'Deeply relieved, softening into warmth with a gentle exhale.',sarcasm:'Dry, understated sarcasm with deliberate pauses and restrained emphasis.',whisper:'A soft, intimate whisper, speaking gently and slowly.',custom:''};
async function api(path,options={}){return VoiceRecording.readJSON(path,{timeout:options.method?0:10000,...options})}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function error(message){const target=$($('queue-dialog').open?'queue-error':browsePage?'page-error':'error');target.textContent=message;target.hidden=false}function announce(text){$('announcement').textContent=text}
function immediateInProgress(){return !!watching&&monitoredImmediate&&$('generate-immediately').checked}
function controls(){
 $('form').inert=!studioInitialized||changingModel;$('form').setAttribute('aria-busy',String(!studioInitialized||changingModel));
 updateSeedMode();
 const loading=immediateInProgress();$('generate').disabled=!studioInitialized||!online||!queueWritable||submitting||uploading||enhancing||changingModel||transcriptSaving||!modelChoiceReady||unresolvedHiggsControls().length>0||loading;
 $('generate').setAttribute('aria-busy',String(!studioInitialized||submitting||loading));$('generate-label').textContent=!studioInitialized?'Opening studio…':submitting?'Submitting…':loading?'Generating speech…':'Generate speech';
 $('generate-icon').className=submitting||loading?'button-spinner':'';$('generate-icon').textContent=submitting||loading?'':'↗';
 $('unload').disabled=!online||busy||enhancing;$('new-seed').disabled=busy;updatePromptExpansion();
}
function clearExpansion(){appliedExpansion=null;pendingExpansion=null;updatePromptExpansion();}
let generationClock=null;
function elapsedTime(milliseconds){const seconds=Math.max(0,Math.floor(milliseconds/1000)),minutes=Math.floor(seconds/60);return minutes>=60?`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`:`${minutes}:${String(seconds%60).padStart(2,'0')}`}
function updateElapsed(){
 updateReturnElapsed();
 for(const target of document.querySelectorAll('[data-generation-elapsed]')){
  target.hidden=!generationClock;if(!generationClock)continue;
  target.textContent=`Elapsed ${elapsedTime((generationClock.end??Date.now())-generationClock.start)} · ${generationClock.label}`;
  target.title='Time since this request was accepted, including time waiting in the queue.';
 }
}
function generationTiming(identity,created,label,completed=null){
 const timestamp=Date.parse(created),end=Date.parse(completed);
 generationClock={identity,start:Number.isFinite(timestamp)?timestamp:generationClock?.identity===identity?generationClock.start:Date.now(),end:Number.isFinite(end)?end:null,label};
 if(recordingSelection?.id===identity){recordingSelection.timing=generationClock;recordingStore?.save(recordingSelection)}updateElapsed();
}
function batchTiming(batch){
 const running=batch.jobs.find(job=>job.status==='running'),finished=!['queued','running'].includes(batch.status);
 const completed=batch.jobs.map(job=>job.completed_at).filter(Boolean).sort().at(-1);
 generationTiming(batch.id,batch.created_at,running?`${running.stage} · option ${running.candidate_index} of ${batch.candidate_count}`:finished?`${batch.done_count} of ${batch.candidate_count} ready`:'Waiting in queue',finished?(completed||new Date().toISOString()):null);
}
setInterval(updateElapsed,1000);
function setProgress(track,progress,stage=''){
 const percent=['generating','waiting','done'].includes(progress?.phase)?progress.percent:0;
 track.firstElementChild.style.width=percent+'%';track.setAttribute('aria-valuenow',String(percent));track.setAttribute('aria-valuetext',VoiceRecording.progressSpeech(progress,stage));
}
function progressBar(progress,stage,cls){
 const track=el('div',null,'progress-track '+cls);track.append(el('span',null,'progress-fill'));
 track.setAttribute('role','progressbar');track.setAttribute('aria-label','Estimated generation progress');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');
 setProgress(track,progress,stage);return track;
}
function showGenerationProgress(progress,stage=''){
 const box=$('generation-progress');if(!box)return;
 box.hidden=!progress||progress.phase==='done';if(box.hidden)return;
 setProgress($('generation-progress-bar'),progress,stage);$('generation-progress-label').textContent=VoiceRecording.progressText(progress);
}
function resumeRecording(){updateElapsed();if(document.hidden)return;recordingPoller?.refresh();if(browsePage&&studioInitialized)watchReturnRecording();if(studioInitialized)health();else startStudio()}
document.addEventListener('visibilitychange',resumeRecording);window.addEventListener('focus',resumeRecording);window.addEventListener('pageshow',resumeRecording);window.addEventListener('online',resumeRecording);

function updatePromptExpansion(){
 if(!caps)return;
 const available=modelChoiceReady&&!!caps.limits.prompt_expansion?.enabled,enabled=available&&$('prompt-expansion-enabled').checked,backend=caps.backend;
 $('prompt-expansion-enabled').disabled=!available||enhancing;
 updateCandidateVariation();
 const profile=caps.models[backend]?.prompt_expansion,profileMode=['saved','upload'].includes(mode)?'reference':mode;
 const spec=profile?.modes[profileMode];
 const clone=resolvedReferenceMode(),supported=!!spec?.available&&(!spec.clone_modes||spec.clone_modes.includes(clone));
 const catalogDirection=['fish','higgs'].includes(backend);
 const emptyOriginal=spec?.kind==='prose'?!$(spec.target).value.trim():catalogDirection&&!$('expansion-guidance').value.trim();
 const emptyReason=catalogDirection?(backend==='higgs'?'Enter a Delivery direction below to preview a suggestion. Selected controls are kept unless the suggestion changes that category.':'Enter a Delivery direction below for Fish tag suggestions. Clear existing tags first.'):profileMode==='design'?'Enter a voice description above before previewing its expansion.':'Enter an Emotion & delivery direction above to expand it. The accent direction and spoken words stay unchanged.';
 const unsupportedReason=spec?.clone_modes&&$('clone-mode').value==='auto'?'Auto is following the transcribed recording because no new delivery or accent direction is entered. Enter an Emotion & delivery direction above, or choose Voice reference, to preview new delivery guidance.':'This reference mode follows recorded performance. Choose Auto with a delivery direction, or Voice reference, to use delivery instructions.';
 $('expansion-profile').hidden=!profile||!modelChoiceReady;
 $('expansion-profile-info').textContent=profile?`${profile.name} · ${spec?.label||profileMode} · Profile v${profile.version}`:'';
 $('expansion-profile-rules').textContent=spec?.available?(supported?spec.instructions:unsupportedReason):spec?.reason||'No expansion specification for this mode.';
 $('expansion-guidance-field').hidden=!enabled||!supported;
 $('expand-guidance').disabled=!online||!enabled||!supported||emptyOriginal||busy||enhancing||uploading||submitting||!!appliedExpansion;
 if(unresolvedHiggsControls().length)$('expand-guidance').disabled=true;
 updateHiggsSuggestions();
 const catalog=backend==='higgs';
 $('expand-guidance').textContent=enhancing?'Preparing suggestion…':catalog?'Suggest delivery control':'Preview expanded guidance';
 $('expansion-guidance-label').textContent=['fish','higgs'].includes(backend)?'Delivery direction':'Additional delivery direction (optional)';
 $('expansion-guidance').placeholder=catalog?'For example: Keep the reference voice and accent, but use more expressive intonation.':'Describe the delivery you want to explore. Required for Fish tag suggestions.';
 $('prompt-expansion-hint').textContent=!modelChoiceReady?'Choose a model to see prompt-expansion support.':!available?'LLM disabled at startup. Restart with make serve without NO_LLM=1 to enable.':!enabled?(catalog?'Off. Selected Higgs controls still apply; no new LLM suggestions.':'Off. No LLM previews or per-option planning; distinct designs use built-in directions.'):!supported?(spec?.reason||unsupportedReason):emptyOriginal?emptyReason:catalog?'On. Describe the acting below; the LLM suggests one supported Emotion, Prosody, or Style control and keeps your other selected controls. Accent comes from your reference.':'On. Design batches use LLM contrast planning. For a single take or reference delivery, preview and apply guidance below. Dialogue and transcripts stay unchanged.';
 $('expansion-applied').hidden=!appliedExpansion;$('restore-guidance').hidden=!appliedExpansion;
 renderExpansion(appliedExpansion,'applied-expansion-details','Applied to your next generation');
 if(appliedExpansion)$('expansion-applied').textContent='Expanded guidance applied · '+appliedExpansion.llm.model+' · '+appliedExpansion.llm.seconds+'s. Original retained.';
}
function applyGuidanceValues(values,higgsControl=null){
 for(const [key,value] of Object.entries(values)){
  if(key==='fish')$('fish-delivery-tags').value=value.delivery_tags.join('\n');
  else if(key==='higgs')for(const name of higgsControl?[higgsControl]:['emotion','prosody','style'])$('higgs-'+name).value=value[name]||'';
  else if(['description','emotion'].includes(key))$(key).value=value;
 }
 updatePerformancePreview();
}
function guidancePreview(values){
 return Object.entries(values).map(([key,value])=>{
  if(key==='fish')return 'Delivery tags: '+(value.delivery_tags.join(', ')||'None');
  if(key==='higgs')return Object.entries(value).filter(([,v])=>v).map(([name,v])=>name+': '+v).join('\n')||'No delivery control';
  return (key==='description'?'Voice description':'Delivery direction')+'\n'+value;
 }).join('\n\n');
}
function expansionChanged(record){return record.changed??Object.keys(record.updates).some(key=>JSON.stringify(record.updates[key])!==JSON.stringify(record.original[key]))}
function originalGuidancePreview(record){
 const catalog=['fish','higgs'].includes(record.context.backend);
 return catalog&&record.request_guidance?record.request_guidance:guidancePreview(record.original)+(record.request_guidance?'\n\nAdditional delivery direction\n'+record.request_guidance:'');
}
function renderExpansion(record,id,caption){
 const panel=$(id);panel.hidden=!record;
 if(!record){delete panel.dataset.receipt;panel.querySelector('.expansion-content').replaceChildren();return}
 if(panel.dataset.receipt===record.id)return;
 panel.dataset.receipt=record.id;panel.open=true;
 const content=panel.querySelector('.expansion-content'),original=el('details'),originalText=el('p',originalGuidancePreview(record),'expanded-guidance');
 original.append(el('summary',['fish','higgs'].includes(record.context.backend)&&record.request_guidance?'Your delivery direction':'Original guidance'),originalText);
 content.replaceChildren(el('p',caption,'hint'),el('p',expansionChanged(record)?guidancePreview(record.updates):'No change suggested. Original settings kept.','expanded-guidance'),original,
  el('p',`${record.llm.model} · ${record.llm.seconds}s · ${record.llm.policy_version}`+
   (record.llm[record.context.backend]?` · ${record.llm[record.context.backend].label} · Profile v${record.llm[record.context.backend].version}`:''),'hint'));
 if(record.higgs?.message)content.append(el('p',record.higgs.message,'hint'));
}
async function previewGuidance(higgsControl=null){
 if(enhancing)return;enhancing=true;controls();$('error').hidden=true;
 try{
  const {candidate_count,candidate_variation,candidate_use_llm,...generation}=settings(!!higgsControl);generation.prompt_expansion_id=null;
  pendingExpansion=await post('/api/prompts/expand',{generation,guidance:higgsControl?$('higgs-'+higgsControl).value.trim():$('expansion-guidance').value,
   ...(higgsControl?{higgs:{control:higgsControl}}:{})});
  const catalog=['fish','higgs'].includes(pendingExpansion.context.backend),changed=expansionChanged(pendingExpansion);
  $('expansion-title').textContent=caps.backend==='higgs'?'Suggested Higgs delivery control':'Expanded voice guidance';
  $('expansion-original-title').textContent=catalog?'Your delivery direction':'Original guidance';
  $('expansion-original').textContent=originalGuidancePreview(pendingExpansion);
  $('expansion-updates').textContent=changed?guidancePreview(pendingExpansion.updates):'No change suggested. Original settings kept.';
  $('expansion-notice').textContent=pendingExpansion.higgs?.message||(!changed?'The LLM did not suggest a change. Revise your direction to try again, or keep the original settings.':'');
  $('expansion-notice').hidden=!$('expansion-notice').textContent;
  $('apply-expansion').disabled=!changed;
  $('apply-expansion').textContent=caps.backend==='higgs'?'Apply control':'Apply guidance';
  $('cancel-expansion').textContent=changed?'Keep original':'Close';
  const selected=pendingExpansion.llm[caps.backend];
  $('expansion-model').textContent=`${pendingExpansion.llm.model} · ${pendingExpansion.llm.seconds}s · ${pendingExpansion.llm.policy_version}`+(selected?` · ${selected.label} · Profile v${selected.version}`:'');
  $('expansion-preview').showModal();
 }catch(exc){error(exc.message)}finally{enhancing=false;controls()}
}
$('expand-guidance').onclick=()=>previewGuidance();
$('cancel-expansion').onclick=()=>{$('expansion-preview').close();pendingExpansion=null};
$('apply-expansion').onclick=()=>{
 if(!pendingExpansion||!expansionChanged(pendingExpansion))return;
 applyGuidanceValues(pendingExpansion.updates,pendingExpansion.higgs?.control);appliedExpansion=pendingExpansion;pendingExpansion=null;
 $('expansion-preview').close();pendingKey=null;controls();persist();announce('Expanded guidance applied. Dialogue and reference transcript kept.');
};
function restoreAppliedGuidance(){const target=appliedExpansion?.higgs?.control;if(target){$('higgs-'+target).value=appliedExpansion.request_guidance;updatePerformancePreview()}else if(appliedExpansion)applyGuidanceValues(appliedExpansion.original);}
$('prompt-expansion-enabled').onchange=()=>{
 expansionChoice=$('prompt-expansion-enabled').checked;
 if(caps?.backend==='higgs'){
  if(!expansionChoice){suspendHiggsDirections();clearExpansion()}
  else{for(const [key,direction] of Object.entries(suspendedHiggsDirections))$('higgs-'+key).value=direction;suspendedHiggsDirections={};clearExpansion()}
  updatePerformancePreview();
 }else if(!expansionChoice&&appliedExpansion){restoreAppliedGuidance();clearExpansion()}
 pendingKey=null;controls();persist();
};
$('restore-guidance').onclick=()=>{restoreAppliedGuidance();clearExpansion();pendingKey=null;controls();persist();};
function setMode(next){
 if(next==='record')next=readySourceModels(recordSource).length?recordSource:recordSources.find(([source])=>readySourceModels(source).length)?.[0];
 if(!next)return;
 if(caps){
  if(!readySourceModels(next).length)return;
  awaitingSourceModel=caps.modes[sourceMode(next)]?.available===false;
 }
 if(mode!==next)rememberTranscriptDraft();
 pendingKey=null;clearExpansion();const previous=mode;mode=next;
 if(mode!=='design')recordSource=mode;
 if(['saved','upload'].includes(mode)&&$('emotion-preset').value==='natural'&&$('emotion').value===presets.natural){$('emotion').value='';$('emotion-preset').value='custom'}
 for(const button of document.querySelectorAll('[data-mode]'))button.setAttribute('aria-selected',button.dataset.mode===(mode==='design'?'design':'record'));
 $('record-panel').hidden=mode==='design';
 for(const name of ['design','saved','upload','preset'])$(name+'-panel').hidden=name!==mode;
 if(mode==='saved')voicePreview();else{transcriptVoice=null;if(mode==='upload'&&previous!==mode)$('ref-text').value=uploadTranscriptDraft??reference?.transcript??'';}
 renderModelChoices();updateCapabilities();updatePromptExpansion();controls();persist();
}

document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{if(button.getAttribute('aria-disabled')==='true'){showControlHelp(button);return}setMode(button.dataset.mode)});
$('record-source').onchange=()=>setMode($('record-source').value);
$('emotion-preset').onchange=()=>{$('emotion').value=presets[$('emotion-preset').value];clearExpansion();persist()};$('emotion').oninput=()=>{$('emotion-preset').value='custom';persist()};
function count(){$('count').textContent=`${$('text').value.length} / ${caps?.limits.text_characters||3000}`}count();
function updateCandidateVariation(){
 if(!caps)return;
 const design=mode==='design',multiple=Number($('candidate-count').value)>1;
 $('candidate-variation-field').hidden=!design||!multiple;
 $('candidate-count-hint').textContent=design?'Keep the original design, then explore contrasting resonance and articulation. Specified voice traits and accent remain constraints.':'Saved/reference/preset options are alternative takes of the same selected speaker.';
 $('candidate-variation-hint').textContent=$('candidate-variation').value==='seed_only'?'Keeps identical instructions and changes only the seed.':caps.limits.prompt_expansion?.enabled&&$('prompt-expansion-enabled').checked?'One local LLM plan creates different directions for the batch before speech. The original is option 1.':'Uses built-in contrast directions. Turn on Prompt expansion with the LLM available to tailor them to your description. The original is option 1.';
}
$('candidate-count').onchange=$('candidate-variation').onchange=updateCandidateVariation;
function candidateExpansion(row){
 const variant=row.candidate_variant;if(!variant)return row.prompt_expansion;
 return {id:row.id+'-candidate',context:{backend:row.backend},original:row.prompt_expansion?.original||{description:variant.base_description},updates:{description:variant.description},
  llm:{...(variant.llm||{model:'Built-in contrast directions',seconds:0}),policy_version:variant.version,[row.backend]:variant.profile_spec}};
}
function settings(allowCustomHiggs=false){
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=!awaitingSourceModel&&acceptsTextDirections();
 const result={model:caps?.backend,prompt_expansion_id:appliedExpansion?.id||null,candidate_count:Number($('candidate-count').value),candidate_use_llm:!!caps?.limits.prompt_expansion?.enabled&&$('prompt-expansion-enabled').checked,candidate_variation:mode==='design'?$('candidate-variation').value:'auto',generate_immediately:$('generate-immediately').checked,text:$('text').value,description:mode==='design'?$('description').value:'',
   emotion:direction?$('emotion').value:'',accent:direction?$('accent').value:'',
   seed:Number($('seed').value),max_tokens:caps?.limits.max_tokens_fixed?1000:Number($('max-tokens').value),
   preset_voice:mode==='preset'?$('preset-voice').value||null:null,
   voice_id:mode==='saved'?$('voice').value||null:null,
   reference_id:mode==='upload'?reference?.id||null:null,
   clone_mode:referenceMode&&caps?.modes.reference.continuation?$('clone-mode').value:'reference',
   language:$('language').value||'auto',output_dir:$('output-dir').value.trim()||null};
 for(const key of ['steps','guidance','temperature'])if(caps?.controls[key])result[key]=Number($(key).value);
 if(referenceMode&&(caps?.modes.reference.transcript&&caps?.backend!=='voxcpm2'||['auto','continuation','combined'].includes(result.clone_mode)))result.ref_text=$('ref-text').value||null;
 if(mode==='preset'&&$('steering-emotion').value){result.steering_emotion=$('steering-emotion').value;result.steering_strength=Number($('steering-strength').value)}
 const modelControls=caps?.controls[caps?.backend]||{},options={};
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence'])if(modelControls[key])options[key]=Number($(key).value);
 if(modelControls.emotion_vector){
   const source=$('index-emotion-mode').value;
   if(source==='audio'&&$('emotion-reference-id').value)options.emotion_reference_id=$('emotion-reference-id').value;
   if(source==='vector'){options.emotion_vector=readIndexVector();if(options.emotion_vector.reduce((a,b)=>a+b,0)>1.000000001)throw Error('Emotion mixture total must not exceed 1.');}
   if(options.emotion_reference_id||options.emotion_vector)options.emotion_alpha=Number($('emotion_alpha').value);
 }
 if(caps?.backend==='fish'){options.delivery_tags=$('fish-delivery-tags').value.split('\n').map(x=>x.trim()).filter(Boolean);}
 if(caps?.backend==='higgs'){
  for(const key of ['emotion','prosody','style']){const value=higgsControlValue(key);if(value===undefined&&!allowCustomHiggs)throw Error('Choose a suggested '+key+' or use Suggest control to preview your custom direction.');if(value)options[key]=value;}
  options.text_chunk_size=Number($('higgs-text-chunk-size').value);
 }
 if(modelControls.top_p){options.top_p=Number($('model-top-p').value);options.top_k=Number($('model-top-k').value);}
 if(modelControls.punctuation_pauses){options.punctuation_pauses=$('punctuation-pauses').checked&&pacingAvailable();for(const key of ['sentence_pause_ms','comma_pause_ms'])options[key]=Number($(key).value);}
 if(Object.keys(options).length)result[caps.backend]=options;
 return result;
}
function persist(){if(caps&&!awaitingSourceModel)try{localStorage.setItem(storageKey+'-form',JSON.stringify({...settings(true),randomize_seed:$('random-seed').checked,...(caps.backend==='higgs'?{higgs_direction_drafts:Object.fromEntries(['emotion','prosody','style'].map(key=>[key,$('higgs-'+key).value])),higgs_selected_controls:higgsSelectedControls,higgs_suspended_directions:suspendedHiggsDirections}:{}),ref_text:$('ref-text').value,expansion_guidance:$('expansion-guidance').value,prompt_expansion_choice:expansionChoice,prompt_expansion:appliedExpansion,description:$('description').value,accent:$('accent').value,emotion:$('emotion').value,mode,preset:$('emotion-preset').value}))}catch{}}
function updateCapabilities(){
 if(!caps)return;
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=!awaitingSourceModel&&acceptsTextDirections();
 $('emotion').disabled=$('emotion-preset').disabled=$('accent').disabled=!direction;
 $('emotion-hint').textContent=referenceMode&&caps.backend==='voxcpm2'&&$('clone-mode').value==='auto'?'Leave directions empty to follow the transcribed recording. Enter an emotion or accent to use flexible voice conditioning and preview expanded delivery guidance.':referenceMode&&caps.backend==='voxcpm2'&&['continuation','combined'].includes($('clone-mode').value)?'Disabled because Combined and Continue performance follow the recorded delivery. Choose Auto or Voice reference to enter new delivery directions.':direction?'Direct the performance in words. Voice identity and expression are approximate.':`Disabled because ${caps.name} reference cloning does not accept written delivery instructions. ${caps.backend==='qwen3'?'Choose a built-in speaker or Design a voice to use written directions.':'Use the model-specific performance controls instead.'}`;
 $('accent-hint').textContent=direction?'A prompt direction, not a trained accent pack. A recording with the desired accent is usually the stronger cue.':referenceMode&&caps.backend==='voxcpm2'?'Disabled because Combined and Continue performance follow the recording. Choose Auto or Voice reference to enter an accent direction.':`Disabled because ${caps.name} reference cloning does not accept written accent directions. The recording supplies the accent.`;
 const structured=referenceMode&&['higgs','fish'].includes(caps.backend);
 $('accent-direction-field').hidden=structured;
 $('emotion-direction-field').hidden=structured;
 $('recorded-accent-hint').hidden=!structured;
 $('recorded-accent-hint').textContent=structured?`${caps.name} uses the recording as its accent cue; the accent can still drift. ${caps.backend==='higgs'?'Use Emotion, Prosody and Style above for delivery. Compare Follow recording before adding acting tags; free-text accent instructions are unsupported.':'Use delivery tags above for expression. Choose VoxCPM2 for editable accent directions.'}`:'';
 for(const key of ['steps','guidance','temperature'])$(key+'-field').hidden=!caps.controls[key];
 const sources=recordSources.filter(([source])=>readySourceModels(source).length);
 $('record-source').replaceChildren(...sources.map(([source,label])=>new Option(label,source)));
 $('record-source').value=mode==='design'?recordSource:mode;
 const designTab=document.querySelector('[data-mode="design"]'),designDisabled=!readyDesignModels().length;
 designTab.hidden=false;designTab.setAttribute('aria-disabled',String(designDisabled));
 designTab.dataset.helpFor='voice-design-tab';
 if(designDisabled&&mode==='design')modelChoiceReady=false;
 const recordTab=document.querySelector('[data-mode="record"]');recordTab.setAttribute('aria-disabled',String(!sources.length));recordTab.dataset.helpFor='record-lines-tab';
 $('structured-delivery-heading').hidden=!structured;
 $('structured-delivery-subtitle').textContent=caps.backend==='higgs'?'Keep Follow recording for the original delivery, or choose an emotion, prosody, or vocal style for this take.':'Leave delivery tags empty to follow the recording, or add short acting directions for this take.';
 $('language-field').hidden=caps.languages.length<2;
 $('text').maxLength=caps.limits.text_characters;
 $('max-tokens-field').hidden=!!caps.limits.max_tokens_fixed;
 $('higgs-chunk-field').hidden=caps.backend!=='higgs';
 $('index-emotion').hidden=!caps.controls[caps.backend]?.emotion_vector;
 $('inline-hint').hidden=!caps.limits.inline_controls;
 $('inline-hint').textContent=caps.backend==='fish'?'Manual local tags such as [whisper] and [pause] remain available. Delivery tags guide performance; audition accent retention.':caps.backend==='higgs'?'Manual local tokens such as <|prosody:pause|> and <|prosody:long_pause|> remain available. They do not specify exact milliseconds.':'';
 $('reference-options').hidden=!referenceMode;
 for(const option of $('clone-mode').options)option.hidden=!(caps.modes.reference.clone_modes||['auto','reference']).includes(option.value);
 if(!(caps.modes.reference.clone_modes||['auto','reference']).includes($('clone-mode').value))$('clone-mode').value='auto';
 $('clone-mode-field').hidden=!caps.modes.reference.continuation;
 const transcript=referenceMode&&caps.modes.reference.transcript;
 $('transcript-field').hidden=!transcript;
 $('ref-text-label').textContent=caps.backend==='qwen3'?'Exact reference transcript (optional)':'Exact reference transcript (or use saved transcript)';
 $('transcript-hint').textContent=caps.backend==='voxcpm2'&&$('clone-mode').value==='reference'?'Saved with this voice for later reuse; flexible Voice reference does not send the transcript as performance context.':caps.modes.reference.transcript_required?'This model requires the exact words in the reference recording. Supply them here or in the saved voice.':caps.backend==='qwen3'?'An exact transcript improves cloning. Leave blank to use the saved transcript, or speaker embedding mode when none is saved.':'Auto follows recorded performance when an exact transcript is available; otherwise it uses voice reference. Explicit combined/continuation require a transcript.';
 count();
 $('edit-transcript-field').hidden=false;
 updatePerformancePreview();
 updatePacingControls();
 $('steering-field').hidden=!caps.extensions?.emotion_steering;
 $('steering-strength-field').hidden=!$('steering-emotion').value;
 $('performance-guidance').hidden=awaitingSourceModel;$('inline-hint').hidden=awaitingSourceModel||!$('inline-hint').textContent;
 $('performance-controls').hidden=awaitingSourceModel;$('backend-settings').hidden=awaitingSourceModel;
 $('apply-recommended').closest('details').hidden=awaitingSourceModel;
 if(awaitingSourceModel){$('emotion-hint').textContent=$('accent-hint').textContent='Disabled until you choose a model that supports this voice source.';$('performance-guidance').hidden=true;$('inline-hint').hidden=true;$('expansion-profile').hidden=true;$('performance-controls').hidden=true;$('backend-settings').hidden=true;$('rendered-preview-field').hidden=true;$('license-notice').hidden=true;$('model-badge').textContent='Choose a model for this voice source';}
 const followsPerformance=referenceMode&&caps.backend==='voxcpm2'&&['continuation','combined'].includes($('clone-mode').value);
 const placeholders={emotion:awaitingSourceModel?'Choose a model first':followsPerformance?'Follows recorded delivery':structured?'Use model delivery controls':'Text directions unsupported',
                     accent:awaitingSourceModel?'Choose a model first':followsPerformance?'Follows recorded accent':'Accent comes from recording'};
 for(const [id,hint] of [['emotion','emotion-hint'],['accent','accent-hint']]){
  const input=$(id);disabledPlaceholder(input,placeholders[id]);
  input.title=input.disabled?$(hint).textContent:'';
 }
 $('emotion-preset').title=$('emotion-preset').disabled?$('emotion-hint').textContent:'';
 installControlHelp();
 updateTranscriptActions();
}
$('clone-mode').onchange=()=>{updateCapabilities();persist()};
$('steering-emotion').onchange=()=>{if($('steering-emotion').value){if($('emotion-preset').value==='natural'){$('emotion').value='';$('emotion-preset').value='custom'}if(Number($('temperature').value)===.9)$('temperature').value='1.1'}updateCapabilities();persist()};
function restore(row){
 const requestedModel=row.backend||row.requested?.model||row.model;
 if(requestedModel&&availableModels.some(model=>model.id===requestedModel)&&requestedModel!==caps?.backend){if(changingModel){pendingModelRestore=row;return}selectVoiceModel(requestedModel).then(()=>restore(row)).catch(exc=>error(exc.message));return}

 $('candidate-count').value=String(row.candidate_count??2);
 expansionChoice=row.candidate_variant?false:row.prompt_expansion_choice??null;
 $('prompt-expansion-enabled').checked=!!caps.limits.prompt_expansion?.enabled&&(expansionChoice??true);
 $('candidate-variation').value=row.candidate_variant?'seed_only':row.candidate_variation||'auto';
 $('expansion-guidance').value=row.expansion_guidance||'';
 $('generate-immediately').checked=row.generate_immediately??true;
 $('random-seed').checked=row.randomize_seed??$('random-seed').checked;
 $('text').value=row.text||'';$('description').value=row.description||'';$('emotion').value=row.preset_voice&&!row.mode?[row.description,row.emotion].filter(Boolean).map(x=>x.trim().replace(/\.+$/,'')).join('. '):row.emotion||'';
 $('accent').value=row.accent||'';$('emotion-preset').value='custom';
 $('clone-mode').value=row.clone_mode||'reference';$('steering-emotion').value=row.steering_emotion||'';
 $('steering-strength').value=row.steering_strength??8;
 for(const [key,id]of [['seed','seed'],['steps','steps'],['guidance','guidance'],['temperature','temperature'],['language','language'],['output_dir','output-dir'],['max_tokens','max-tokens']])if(row[key]!=null)$(id).value=row[key];
 if(row.preset_voice&&caps.modes.preset?.available){$('preset-voice').value=row.preset_voice;setMode('preset')}
 else if(row.voice_id&&voiceRows.some(v=>v.id===row.voice_id)){$('voice').value=row.voice_id;setMode('saved');voicePreview()}
 else if(row.reference_id){reference={id:row.reference_id};$('reference-info').textContent='Using the reference saved with this take.';$('reference-preview').src=`/api/audio/references/${row.reference_id}`;$('reference-preview').hidden=false;setMode('upload')}
 else if(['design','saved','upload','preset'].includes(row.mode))setMode(readySourceModels(row.mode).length?row.mode:readySourceModels('upload').length?'upload':'design');
 else setMode('design');
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha'])if($(key))$(key).value=row[caps.backend]?.[key]??row[key]??caps.controls[caps.backend]?.[key]?.default??1;
 $('emotion-reference-id').value=row[caps.backend]?.emotion_reference_id||row.emotion_reference_id||'';$('emotion-vector').value=(row[caps.backend]?.emotion_vector||row.emotion_vector)?.join(',')||'';
 $('emotion-reference-info').textContent=(row[caps.backend]?.emotion_reference_id||row.emotion_reference_id)?'Using the saved emotion recording.':'';
 restorePerformance(row);$('ref-text').value=row.ref_text??(mode==='saved'?voiceRows.find(v=>v.id===$('voice').value)?.transcript:reference?.transcript)??'';rememberTranscriptDraft();appliedExpansion=row.candidate_variant||caps.backend==='higgs'&&!$('prompt-expansion-enabled').checked?null:row.prompt_expansion||null;updateCapabilities();updatePromptExpansion();count();persist();
}
$('form').addEventListener('input',event=>{if(['description','emotion','accent','voice','reference','ref-text','clone-mode','preset-voice','fish-delivery-tags','higgs-emotion','higgs-prosody','higgs-style','expansion-guidance'].includes(event.target.id))clearExpansion();pendingKey=null;count();updatePerformancePreview();controls();persist()});$('form').addEventListener('change',event=>{if(['voice','reference','clone-mode','preset-voice','higgs-emotion','higgs-prosody','higgs-style'].includes(event.target.id))clearExpansion();pendingKey=null;persist()});
function randomSeed(){let value;do{value=crypto.getRandomValues(new Uint32Array(1))[0]}while(value===Number($('seed').value));return value;}
function updateSeedMode(){
 const random=$('random-seed').checked;
 $('seed').disabled=random;$('seed-label').textContent=random?'Last generated seed':'Seed';
 disabledPlaceholder($('seed'),'Automatic seed');
 $('seed').title=random?'Chosen automatically for each new generation. Turn off Random seed each generation to enter a fixed seed.':'';
 $('seed-mode-hint').textContent=random?'Each Generate click chooses a fresh random seed. Each option shows its actual seed; retrying the same submission keeps it.':'This seed stays fixed for comparisons or repeatable requests. Batch options use consecutive seeds.';
 $('new-seed').hidden=random;
 $('seed-label').closest('.help-label-row')?.querySelector('.help-trigger')?.setAttribute('aria-label','Help: '+$('seed-label').textContent);
}
$('random-seed').onchange=()=>{pendingKey=null;controls();persist()};
$('new-seed').onclick=()=>{pendingKey=null;$('seed').value=randomSeed();persist();announce('New random seed selected')};
const transcriptDrafts=new Map();let transcriptVoice=null,transcriptSaving=false;
function rememberTranscriptDraft(){if(mode==='saved'&&transcriptVoice)transcriptDrafts.set(transcriptVoice,$('ref-text').value);if(mode==='upload')uploadTranscriptDraft=$('ref-text').value;updateTranscriptActions()}
function updateTranscriptActions(){
 const row=mode==='saved'?voiceRows.find(v=>v.id===$('voice').value):null;
 $('transcript-actions').hidden=!row;
 const changed=!!row&&$('ref-text').value.trim()!==(row.transcript||'').trim();
 $('save-transcript').disabled=!changed||transcriptSaving;
 $('save-transcript').textContent=transcriptSaving?'Saving transcript…':'Save transcript to voice';
 $('transcript-status').textContent=transcriptSaving?'Saving…':changed?'Unsaved · also saved when you generate':row?.transcript?'Saved with this voice':'No transcript saved';
}
async function saveSelectedTranscript(){
 const row=mode==='saved'?voiceRows.find(v=>v.id===$('voice').value):null;if(!row)return;
 const transcript=$('ref-text').value.trim();if(transcript===(row.transcript||'').trim())return;
 const identity=row.id;transcriptSaving=true;updateTranscriptActions();controls();
 try{const saved=await api('/api/voices/'+identity,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({transcript})});Object.assign(row,saved);announce('Exact transcript saved with “'+saved.name+'”.')}
 finally{transcriptSaving=false;updateTranscriptActions();controls()}
}
$('ref-text').addEventListener('input',rememberTranscriptDraft);
$('save-transcript').onclick=async()=>{try{await saveSelectedTranscript();persist()}catch(exc){error('Could not save the transcript: '+exc.message)}};
function voicePreview(){const row=voiceRows.find(v=>v.id===$('voice').value);$('voice-preview').hidden=!row;if(row){if(mode==='saved'&&transcriptVoice!==row.id){transcriptVoice=row.id;$('ref-text').value=transcriptDrafts.get(row.id)??row.transcript??''}if($('voice-preview').getAttribute('src')!==row.audio_url)$('voice-preview').src=row.audio_url;$('voice-hint').textContent=row.description||'Reference identity is reused; use the supported delivery controls below.'}else transcriptVoice=null;updateTranscriptActions();updatePromptExpansion()}
$('voice').onchange=()=>{voicePreview();persist()};
async function refresh() {
  const token=++refreshRequest;
  const [picker, tags, cleanup] = await Promise.all([
    api('/api/voices?limit=100'), api('/api/voice-tags?'+new URLSearchParams({deleted:$('show-deleted').checked})),
    collectionPage?api('/api/collection/cleanup').catch(()=>({last_error:'Cleanup status is temporarily unavailable.'})):null
  ]);
  if(token!==refreshRequest)return;
  let selected = $('voice').value;
  if (!selected) {
    try { selected = JSON.parse(localStorage.getItem(storageKey+'-form') || 'null')?.voice_id || ''; } catch {}
  }
  const rows=picker.data;
  if (selected && !rows.some(row => row.id === selected)) {
    try { rows.push(await api('/api/voices/' + selected)); } catch {}
  }
  if(token!==refreshRequest)return;
  voiceRows=rows;
  collectionCleanup=cleanup;
  $('voice').replaceChildren(new Option('Choose a saved voice…', ''));
  for (const row of voiceRows) $('voice').append(new Option(row.name, row.id));
  $('voice').value = selected;
  voicePreview();
  availableTags=tags.data;
  selectedTags=new Set([...selectedTags].filter(name=>availableTags.some(tag=>tag.name===name)));
  renderTagChoices();
  await loadCollection();
}
function el(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node}
function updateTagSummary(){
 const names=[...selectedTags];
 $('tag-filter-summary').textContent=names.length>2?`${names.length} selected`:names.join(', ')||'All tags';
 $('tag-filter').setAttribute('aria-label','Tags: '+$('tag-filter-summary').textContent);
 $('tag-filter').title=names.length?names.join(', '):'All tags';
 $('tag-clear').disabled=!names.length;
}
function renderTagChoices(){
 const list=$('tag-choices');list.replaceChildren();
 const query=$('tag-search').value.trim().toLocaleLowerCase();
 const matches=availableTags.filter(tag=>tag.name.toLocaleLowerCase().includes(query));
 for(const tag of matches){
  const label=el('label'),input=el('input');input.type='checkbox';input.value=tag.name;input.checked=selectedTags.has(tag.name);
  input.onchange=()=>{if(input.checked)selectedTags.add(tag.name);else selectedTags.delete(tag.name);updateTagSummary();catalogOffset=0;loadVoicePage().catch(exc=>error(exc.message))};
  label.append(input,el('span',tag.name),el('small',tag.count));list.append(label);
 }
 if(!matches.length)list.append(el('p',availableTags.length?'No tags match your search.':'No tags in this collection yet.','hint'));
 updateTagSummary();
}
function closeTagChoices(restoreFocus=false){$('tag-options').hidden=true;$('tag-filter').setAttribute('aria-expanded','false');if($('tag-search').value){$('tag-search').value='';renderTagChoices()}if(restoreFocus)$('tag-filter').focus()}
function openTagChoices(){$('tag-options').hidden=false;$('tag-filter').setAttribute('aria-expanded','true');$('tag-search').focus()}
$('tag-filter').onclick=()=>{if($('tag-options').hidden)openTagChoices();else closeTagChoices()};
$('tag-filter').onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();openTagChoices()}};
$('tag-search').oninput=renderTagChoices;
$('tag-search').onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();$('tag-choices').querySelector('input')?.focus()}};
$('tag-clear').onclick=()=>{selectedTags.clear();renderTagChoices();closeTagChoices(true);catalogOffset=0;loadVoicePage().catch(exc=>error(exc.message))};
document.addEventListener('pointerdown',event=>{if(!$('collection-tags').contains(event.target))closeTagChoices()});
$('collection-tags').addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('tag-options').hidden){event.preventDefault();closeTagChoices(true)}});
$('collection-tags').addEventListener('focusout',event=>{if(!$('collection-tags').contains(event.relatedTarget))closeTagChoices()});
const queueIconPaths={
 queue:'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
 monitor:'M4 4h16v12H4zM8 21h8M12 16v5',
 play:'m8 5 11 7-11 7Z',
 pause:'M8 5v14M16 5v14',
 remove:'M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6',
 clear:'M3 6h18M3 12h9M3 18h9m5-6 5 6m0-6-5 6',
 close:'m6 6 12 12M18 6 6 18',
 previous:'m14 6-6 6 6 6',next:'m10 6 6 6-6 6',
 options:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
 resources:'M4 3v18h17M8 16v-5M13 16V7M18 16v-8',
 retry:'M3 10a9 9 0 1 1 1 8M3 4v6h6',
 grip:'M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01'
};
function queueIcon(name){
 const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');
 for(const[key,value]of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':name==='grip'?'3':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'queue-icon'}))icon.setAttribute(key,value);
 const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d',queueIconPaths[name]);icon.append(path);return icon;
}
function queueLabel(control,icon,label){control.replaceChildren(queueIcon(icon),el('span',label));return control}
function queueControl(tag,label,icon,cls='secondary'){return queueLabel(el(tag,null,cls),icon,label)}
for(const[id,icon,label]of [['queue-clear','clear','Clear pending'],['queue-prev','previous','Previous'],['queue-next','next','Next']])queueLabel($(id),icon,label);
$('close-queue').replaceChildren(queueIcon('close'));
$('open-queue').prepend(queueIcon('queue'));
function renderCards() {
  const isVoices = collection === 'voices',deleted=$('show-deleted').checked;
  $('search').placeholder = isVoices ? 'Search voice names, descriptions, or tags…' : 'Search text, voice descriptions, or emotions…';
  const query = $('search').value.toLowerCase();
  const page=isVoices?voicePage:historyPage;const rows=page.data;
  $('cards').replaceChildren();
  $('voice-filters').hidden = !isVoices;
  $('collection-filters').dataset.history=String(!isVoices);
  $('catalog-count').hidden = false;
  $('voice-pagination').hidden = page.total <= pageSize;
  $('voice-prev').disabled = catalogOffset === 0;
  $('voice-next').disabled = !page.has_more;
  $('catalog-count').textContent = `${page.total} ${deleted?'deleted ':''}${isVoices?'voice':'take'}${page.total===1?'':'s'}`;
  $('deleted-collection-hint').hidden=!deleted;
  $('deleted-collection-hint').textContent='Restore deleted recordings within 24 hours. After that, their files are removed automatically while the generator runs; unfinished jobs can delay cleanup.'+(collectionCleanup?.last_error?' Cleanup is delayed: '+collectionCleanup.last_error:'');
  $('voice-page-label').textContent = page.total ?
    `${catalogOffset + 1}–${catalogOffset + rows.length} of ${page.total}` : 'No entries';
  $('collection-empty').hidden = rows.length > 0;
  $('collection-empty').textContent = (query || (isVoices && selectedTags.size)) ?
    'No matching takes or voices.' : deleted?`No deleted ${isVoices?'voices':'takes'}.`:isVoices ?
    'Save a generated take or upload a reference to create your voice library.' :
    'Your generated takes will appear here.';
  for (const row of rows) {
    // Each entry is a row: details on the left, the player and actions on the right.
    const card = el('article', null, 'card collection-row'),main=el('div',null,'collection-main'),side=el('div',null,'collection-side');
    card.dataset.collectionId=row.id;card.dataset.collectionKind=isVoices?'voices':'outputs';
    card.dataset.deleted=String(deleted);
    if(deleted){const badge=el('span',null,'deleted-badge');badge.append(queueIcon('remove'),el('span','Deleted'));main.append(badge)}
    const head = el('div', null, 'card-title');
    head.append(el('strong', isVoices ? row.name : new Date(row.created_at).toLocaleString()),
      el('span', `${row.duration.toFixed(1)}s`, 'tiny'));
    main.append(head, el('p', isVoices ? row.description || 'No description yet.' : row.text, 'library-description'));
    if(deleted&&row.deletion){
      const info=row.deletion;
      const message=info.cleanup_started?'Removing files…':info.cleanup_deferred_reason||'Files will be removed after '+new Date(info.purge_after).toLocaleString()+'.';
      main.append(el('p',message,'notice'));
    }
    if (isVoices && row.tags.length) {
      const tags = el('div', null, 'tag-list');
      for (const tag of row.tags) {
        const chip = el('button', tag, 'tag-chip');
        chip.type = 'button'; chip.setAttribute('aria-label', 'Filter by tag ' + tag);
        chip.onclick = () => { selectedTags=new Set([tag]);renderTagChoices();catalogOffset = 0; loadVoicePage().catch(exc => error(exc.message)); };
        tags.append(chip);
      }
      main.append(tags);
    }
    if (!isVoices) main.append(el('p','Voice: '+(voiceLabel(row)||'Unknown'),'collection-voice'));
    if (!isVoices && row.emotion) main.append(el('p', row.emotion));
    if (!isVoices && (row.steering_emotion || row.accent)) main.append(el('p',[row.steering_emotion?'Experimental '+row.steering_emotion+' steering':'',row.accent].filter(Boolean).join(' · ')));
    if(!row.deletion?.cleanup_started){const player = el('audio'); player.controls = true; player.preload = 'none'; player.src = row.audio_url;side.append(player)}
    const actions = el('div', null, 'card-actions');
    if(!deleted){
    const use = el('button', isVoices ? 'Use this voice' : 'Open take', 'secondary');
    use.type = 'button';
    use.onclick = () => { if (isVoices) chooseVoice(row); else showResult(row); };
    actions.append(use);
    if (isVoices) {
      const edit = el('button', 'Edit details', 'secondary'); edit.type = 'button';
      edit.onclick = () => openVoiceEditor({edit_id: row.id, ...row}); actions.append(edit);
    } else {
      if(row.candidate_batch_id){const options=el('button','View all options','secondary');options.type='button';options.onclick=()=>watchCandidates(row.candidate_batch_id);actions.append(options);}
      const reuse = el('button', 'Reuse settings', 'secondary'); reuse.type = 'button';
      reuse.onclick = () => { if(browsePage){location.href='/?reuse='+encodeURIComponent(row.id);return}restore(row); $('text').focus(); }; actions.append(reuse);
      const pauses=el('button','Adjust pauses','secondary');pauses.type='button';pauses.onclick=()=>openPauseEditor(row);actions.append(pauses);
    }
    }
    // Download and Delete stay together on one line; Delete is an icon-only trash button.
    const fileActions=el('span',null,'collection-file-actions');
    if(!row.deletion?.cleanup_started){const download = el('a', 'Download WAV', 'secondary');
    download.href = row.audio_url + '?download=true'; download.download = '';
    fileActions.append(download)}
    const change=el('button',deleted?'Restore':null,deleted?'secondary':'secondary danger icon-button');change.type='button';
    if(!deleted){change.append(queueIcon('remove'));change.setAttribute('aria-label',isVoices?'Delete voice':'Delete take');change.title='Delete'}
    if(deleted&&(row.deletion?.cleanup_started||Date.parse(row.deletion?.purge_after)<=Date.now())){change.disabled=true;change.title='The 24-hour restore window has expired.'}
    change.onclick=()=>deleted?restoreCollectionEntry(row,isVoices,change):openCollectionDelete(row,isVoices);
    fileActions.append(change);actions.append(fileActions);side.append(actions);card.append(main,side); $('cards').append(card);
  }
}
function collectionPath(target){return '/api/'+(target.kind==='voices'?'voices':'artifacts')+'/'+target.id}
function collectionTarget(row,isVoice){return {id:row.id,kind:isVoice?'voices':'outputs',label:isVoice?row.name:(row.text||'Recording').slice(0,160)}}
function collectionNotice(message){$('voice-notice').textContent=message;$('voice-notice').hidden=false;announce(message)}
function openCollectionDelete(row,isVoice){
 collectionDeleteTarget=collectionTarget(row,isVoice);
 $('collection-delete-title').textContent=isVoice?'Delete voice?':'Delete take?';
 $('collection-delete-target').textContent=collectionDeleteTarget.label;
 $('collection-delete-error').hidden=true;$('collection-delete-confirm').disabled=false;
 $('collection-delete-dialog').showModal();
}
$('collection-delete-cancel').onclick=()=>{if(!collectionChanging)$('collection-delete-dialog').close()};
$('collection-delete-dialog').addEventListener('cancel',event=>{if(collectionChanging)event.preventDefault()});
$('collection-delete-dialog').addEventListener('close',()=>{if(!collectionChanging)collectionDeleteTarget=null});
$('collection-delete-confirm').onclick=async()=>{
 if(collectionChanging||!collectionDeleteTarget)return;
 const target=collectionDeleteTarget;let completed=false;collectionChanging=true;
 $('collection-delete-error').hidden=true;
 $('collection-delete-confirm').disabled=$('collection-delete-cancel').disabled=true;$('collection-delete-confirm').textContent='Deleting…';
 try{
  await api(collectionPath(target),{method:'DELETE'});completed=true;
  if(target.kind==='voices'&&$('voice').value===target.id){
   $('voice').value='';transcriptDrafts.delete(target.id);
   if(mode==='saved'){transcriptVoice=null;$('ref-text').value='';clearExpansion();persist()}
  }
  $('collection-delete-dialog').close();
  collectionNotice(`Deleted “${target.label}”. Restore it within 24 hours using Show deleted; its files are then removed automatically.`);
  await refresh();
 }catch(exc){
  if(completed)error('Deleted successfully, but Collection could not refresh. Reload the page. '+exc.message);
  else{$('collection-delete-error').textContent=exc.message;$('collection-delete-error').hidden=false;}
 }finally{
  collectionChanging=false;collectionDeleteTarget=null;
  $('collection-delete-confirm').disabled=$('collection-delete-cancel').disabled=false;$('collection-delete-confirm').textContent='Delete';
  if(!completed&&$('collection-delete-dialog').open)collectionDeleteTarget=target;
 }
};
async function restoreCollectionEntry(row,isVoice,button){
 const target=collectionTarget(row,isVoice);button.disabled=true;button.textContent='Restoring…';let completed=false;
 try{await post(collectionPath(target)+'/restore',{});completed=true;collectionNotice(`Restored “${target.label}”. Uncheck Show deleted to see it in your collection.`);await refresh()}
 catch(exc){error((completed?'Restored successfully. Reload Collection. ':'')+exc.message)}
 finally{button.disabled=false;button.textContent='Restore'}
}
document.querySelectorAll('[data-collection]').forEach(button => button.onclick = () => setCollection(button.dataset.collection));
$('search').oninput = () => {
  catalogOffset = 0;
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>loadCollection().catch(exc=>error(exc.message)),250);
};
function waveform(peaks){const canvas=$('waveform'),ratio=devicePixelRatio||1;canvas.width=canvas.clientWidth*ratio;canvas.height=110*ratio;const c=canvas.getContext('2d');c.clearRect(0,0,canvas.width,canvas.height);c.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--accent');if(!peaks?.length)return;const step=canvas.width/peaks.length;peaks.forEach((peak,i)=>{const h=Math.max(2*ratio,peak*canvas.height*.9);c.fillRect(i*step,(canvas.height-h)/2,Math.max(1,step*.55),h)})}
function markSelectedOption(identity){
 for(const [id,card] of candidateCards){
  const selected=id===identity;card.element.dataset.selected=String(selected);
  const button=card.element.querySelector('[data-select-option]');
  if(button){button.textContent=selected?'Selected':'Select option';button.setAttribute('aria-pressed',String(selected))}
 }
}
function showResult(row){if(browsePage){location.href='/?artifact='+encodeURIComponent(row.id);return}const sameAudio=latest?.id===row.id&&$('player').getAttribute('src')===row.audio_url;
 if(currentCandidates&&row.candidate_batch_id!==currentCandidates.id){recordingPoller?.stop();watching=null;monitoredImmediate=false;currentCandidates=null;candidateCards.clear();$('candidate-results').hidden=true;controls()}
 latest=row;
 if(currentCandidates&&currentCandidates.id===row.candidate_batch_id)rememberRecording({kind:'candidates',id:currentCandidates.id,selected:row.id,model:row.backend});
 else rememberRecording({kind:recordingSelection?.kind==='job'&&recordingSelection.id===row.id?'job':'artifact',id:row.id,model:row.backend});
 $('result-resources').href='/observability?job='+encodeURIComponent(row.id);
 renderExpansion(candidateExpansion(row),'result-expansion-details','Saved with this take');markSelectedOption(row.id);$('result-title').textContent=row.candidate_index?'Option '+row.candidate_index+' · selected take':'Your latest take';const details=[row.preset_voice?'Preset '+row.preset_voice:'',row.clone_mode==='combined'?'Combined identity and recorded performance':row.clone_mode==='continuation'?'Continued reference performance':'',row.accent?'Accent direction: '+row.accent:'',row.steering_emotion?`Experimental ${row.steering_emotion} steering · ${row.steering_strength}`:''].filter(Boolean);$('direction-summary').textContent=details.join(' · ');$('direction-summary').hidden=!details.length;$('empty').hidden=true;$('working').hidden=true;$('result').hidden=false;if(!sameAudio)$('player').src=row.audio_url;$('download').href=row.audio_url+'?download=true';$('metrics').textContent=`${row.duration.toFixed(1)} seconds · ${row.sample_rate/1000} kHz · ${row.transformation?`Pause edit ${row.transformation.processing_seconds}s · Original ${row.timing_scope?'process total':'synthesis'}`:row.timing_scope?'Total process':'Synthesis'} ${row.generation_seconds}s${row.cold_load&&Number.isFinite(row.load_seconds)?` · Load ${row.load_seconds}s`:``}`;$('result-seed').textContent=`Seed ${row.seed}`;$('path').textContent=row.path;$('truncated').hidden=!row.possibly_truncated;$('save-hint').textContent='Use a clean, natural take as your character reference. Up to the first 30 seconds are saved.';waveform(row.waveform)}
let pauseSource=null,pauseSpans=[],pausePreviewEnd=null,pausePreviewTimer=null,pausePendingKey=null,pauseSaving=false;
function pauseError(message){$('pause-error').textContent=message;$('pause-error').hidden=false}
function renderPauseSpans(){
 $('pause-spans').replaceChildren();
 if(!pauseSpans.length)$('pause-spans').append(el('p','No reviewed gaps selected.','hint'));
 pauseSpans.forEach((span,index)=>{const row=el('div',null,'dialog-actions');row.append(el('span',`${span.start_seconds.toFixed(3)}–${span.end_seconds.toFixed(3)} s`));
  const remove=el('button','Remove','secondary');remove.type='button';remove.disabled=pauseSaving;remove.onclick=()=>{pauseSpans.splice(index,1);$('pause-reviewed').checked=false;pausePendingKey=null;renderPauseSpans()};row.append(remove);$('pause-spans').append(row)});
}
function openPauseEditor(row){
 pauseSource=row;pauseSpans=[];pausePendingKey=null;pausePreviewEnd=null;clearTimeout(pausePreviewTimer);$('pause-player').src=row.audio_url;
 $('pause-source-text').textContent=row.text;$('pause-start').value=$('pause-end').value='0';
 $('pause-start').max=$('pause-end').max=row.duration;$('pause-minimum').value='750';$('pause-output-dir').value='';
 $('pause-reviewed').checked=false;$('pause-error').hidden=true;renderPauseSpans();$('pause-editor').showModal();
}
$('adjust-pauses').onclick=()=>{if(latest)openPauseEditor(latest)};
$('pause-player').ontimeupdate=()=>{const player=$('pause-player');$('pause-current-time').textContent=`Playback: ${player.currentTime.toFixed(3)} s`;if(pausePreviewEnd!==null&&player.currentTime>=pausePreviewEnd){player.pause();pausePreviewEnd=null}};
$('pause-player').onpause=()=>{clearTimeout(pausePreviewTimer);pausePreviewEnd=null};
$('pause-mark-start').onclick=()=>{$('pause-start').value=$('pause-player').currentTime.toFixed(3)};
$('pause-mark-end').onclick=()=>{$('pause-end').value=$('pause-player').currentTime.toFixed(3)};
function selectedPause(){const start=Number($('pause-start').value),end=Number($('pause-end').value);if(!Number.isFinite(start)||!Number.isFinite(end)||start<=0||end<=start||end>=pauseSource.duration||end-start<.02)throw Error('Select an interior quiet gap at least 0.02 seconds long.');return {start_seconds:start,end_seconds:end}}
$('pause-listen').onclick=async()=>{try{const span=selectedPause();$('pause-player').currentTime=span.start_seconds;pausePreviewEnd=span.end_seconds;clearTimeout(pausePreviewTimer);await $('pause-player').play();pausePreviewTimer=setTimeout(()=>$('pause-player').pause(),Math.max(0,span.end_seconds-$('pause-player').currentTime)*1000)}catch(exc){pauseError(exc.message)}};
$('pause-add').onclick=()=>{try{const span=selectedPause();if(pauseSpans.length>=30)throw Error('Use up to 30 reviewed gaps per version.');if(pauseSpans.some(s=>span.start_seconds<s.end_seconds&&s.start_seconds<span.end_seconds))throw Error('Selected gaps must not overlap.');pauseSpans.push(span);pauseSpans.sort((a,b)=>a.start_seconds-b.start_seconds);$('pause-reviewed').checked=false;pausePendingKey=null;$('pause-error').hidden=true;renderPauseSpans()}catch(exc){pauseError(exc.message)}};
$('pause-cancel').onclick=()=>{$('pause-player').pause();$('pause-editor').close()};
$('pause-editor').addEventListener('close',()=>{$('pause-player').pause();pausePreviewEnd=null;clearTimeout(pausePreviewTimer)});
$('pause-editor').addEventListener('cancel',event=>{if(pauseSaving)event.preventDefault()});
$('pause-editor-form').addEventListener('input',()=>{pausePendingKey=null});
$('pause-editor-form').onsubmit=async event=>{
 event.preventDefault();if(pauseSaving||!pauseSource)return;
 if(!pauseSpans.length){pauseError('Add at least one gap after reviewing the playback.');return}
 if(!$('pause-reviewed').checked){pauseError('Review every selected gap before creating a version.');return}
 const minimum=Number($('pause-minimum').value);if(!Number.isInteger(minimum)||minimum<100||minimum>5000){pauseError('Choose a total gap from 100 to 5000 milliseconds.');return}
 pauseSaving=true;for(const id of ['pause-create','pause-cancel','pause-add','pause-minimum','pause-output-dir','pause-reviewed'])$(id).disabled=true;
 for(const id of ['pause-minimum','pause-output-dir'])disabledPlaceholder($(id),'Saving audio…');renderPauseSpans();
 try{pausePendingKey=pausePendingKey||crypto.randomUUID();const row=await post('/api/artifacts/'+pauseSource.id+'/pauses',{spans:pauseSpans,reviewed:true,minimum_gap_ms:minimum,output_dir:$('pause-output-dir').value.trim()||null,idempotency_key:pausePendingKey});pausePendingKey=null;$('pause-editor').close();showResult(row);await refresh();announce('Separate pause-adjusted version ready. Original recording preserved.')}
 catch(exc){pauseError(exc.message)}finally{pauseSaving=false;for(const id of ['pause-create','pause-cancel','pause-add','pause-minimum','pause-output-dir','pause-reviewed'])$(id).disabled=false;for(const id of ['pause-minimum','pause-output-dir'])disabledPlaceholder($(id),'');renderPauseSpans()}
};
$('reuse').onclick=()=>{if(latest){restore(latest);$('text').focus()}};
function renderCandidates(batch){
 if(currentCandidates?.id!==batch.id){candidateCards.clear();$('candidate-cards').replaceChildren();latest=null;$('result').hidden=true;}
 currentCandidates=batch;$('candidate-results').hidden=false;
 batchTiming(batch);
 $('candidate-summary').textContent=`${batch.done_count} of ${batch.candidate_count} ready · ${batch.status==='done'?'Choose a favorite to save':batch.status==='partial'?'Some options failed or were removed':batch.status==='error'?'Generation failed':batch.status==='deleted'?'Pending options removed':'Options generate one at a time'}`;
 for(const job of batch.jobs){
  const signature=JSON.stringify([job.status,job.stage,job.error,job.result?.id,job.candidate_variant]);
  const previous=candidateCards.get(job.id);
  if(previous?.signature===signature)continue;
  const card=el('div',null,'card');card.dataset.option=job.candidate_index;card.dataset.selected=String(latest?.id===job.id);
  const title=el('div',null,'card-title');title.append(el('strong','Option '+job.candidate_index),el('span','Seed '+job.requested.seed,'tiny'));card.append(title);
  card.append(el('p',job.status==='done'?`${job.result.duration.toFixed(1)} seconds · Ready`:job.error||job.stage));
  if(job.candidate_variant){const variant=job.candidate_variant,details=el('details');details.append(el('summary',variant.label),el('p',variant.description,'expanded-guidance'));card.append(details);}
  if(job.result){
   const audio=el('audio');audio.controls=true;audio.preload='none';audio.src=job.result.audio_url;audio.setAttribute('aria-label','Listen to option '+job.candidate_index);card.append(audio);
   const actions=el('div',null,'card-actions'),select=el('button','Select option','secondary'),save=el('button','Save voice','secondary'),download=el('a','Download WAV','secondary');
   select.type=save.type='button';select.dataset.selectOption=job.id;select.setAttribute('aria-pressed',String(latest?.id===job.id));select.textContent=latest?.id===job.id?'Selected':'Select option';select.onclick=()=>showResult(job.result);
   save.onclick=()=>openVoiceEditor({output_id:job.result.id,name:'',description:job.result.description||'',transcript:savedTranscript(job.result),tags:[]});
   download.href=job.result.audio_url+'?download=true';download.download='';actions.append(select,save,download);card.append(actions);
  }else if(job.status==='error'&&!job.paused&&job.backend===caps?.backend){
   const retry=el('button','Retry this option','secondary');retry.type='button';retry.onclick=async()=>{try{await post('/api/jobs/'+job.id+'/retry',{});watchCandidates(batch.id)}catch(exc){error(exc.message)}};card.append(retry);
   if(job.backend==='higgs'&&/reached max_tokens.*before EOC/.test(job.error||'')){
    card.append(el('p','The take stopped before its end marker. Retry keeps the same limit. Use a higher Audio limit or reduce Higgs text chunk size in Advanced settings. Completed options remain available.','hint'));
    if(job.requested.max_tokens<caps.controls.max_tokens.max){
     const higher=el('button','Use higher audio limit','secondary');higher.type='button';higher.onclick=()=>{restore({...job.requested,backend:job.backend,max_tokens:caps.controls.max_tokens.max,candidate_count:1});clearExpansion();persist();$('max-tokens').closest('details').open=true;$('max-tokens').focus();announce('Failed option loaded with a higher audio limit. Review the settings and generate one replacement take.');};card.append(higher);
    }
   }
  }
  const resources=el('a','Resources & logs ↗','plain');resources.href='/observability?job='+encodeURIComponent(job.id);resources.setAttribute('aria-label','Resources & logs for option '+job.candidate_index);card.append(resources);
  if(previous)previous.element.replaceWith(card);else $('candidate-cards').append(card);
  candidateCards.set(job.id,{signature,element:card});
 }
}
function updateReturnElapsed(){
 const target=$('recording-return-elapsed'),summary=returnSummary;
 if(!summary?.created){target.textContent='';return}
 const start=Date.parse(summary.created),end=Date.parse(summary.completed);
 if(!Number.isFinite(start)){target.textContent='';return}
 const active=['queued','running'].includes(summary.status);
 target.textContent=active?` · Elapsed ${elapsedTime(Date.now()-start)}`:Number.isFinite(end)?` · Elapsed ${elapsedTime(end-start)}`:'';
}
function showReturnRecording(selection,summary,connection='connected'){
 returnSelection=selection;returnSummary=summary;
 const bar=$('recording-return');bar.hidden=!selection;if(!selection)return;
 const view=VoiceRecording.presentation(summary,connection);
 bar.dataset.active=String(view.active);bar.dataset.connection=connection;
 $('recording-return-heading').textContent=view.title;
 const name=availableModels.find(row=>row.id===summary?.backend)?.name||summary?.backend||'';
 const detail=summary?[name,summary.count>1?`${summary.ready} of ${summary.count} options ready`:summary.ready?'Audio ready':'One take',summary.failed?`${summary.failed} failed`:summary.stage,summary.percent!=null&&['queued','running'].includes(summary.status)?`~${summary.percent}%`:null].filter(Boolean).join(' · '):'Checking progress. Your request is saved.';
 $('recording-return-detail').textContent=detail;
 $('recording-return-link').href=browsePage?VoiceRecording.url(selection)+'#listening-room':'#listening-room';
 $('recording-return-link').textContent=(browsePage?view.action:view.active?'View progress':'View recording')+' ↗';
 updateReturnElapsed();
}
function saveRecordingSummary(data){
 if(!recordingSelection)return;
 recordingSelection.summary=VoiceRecording.summary(recordingSelection,data);recordingStore?.save(recordingSelection);
 showReturnRecording(recordingSelection,recordingSelection.summary);
 $('recording-away-hint').hidden=!['queued','running'].includes(data.status);
}
function watchReturnRecording(){
 if(!browsePage||!recordingStore)return;
 const selection=recordingStore.selection();returnPoller?.stop();
 if(!selection){showReturnRecording(null);return}
 showReturnRecording(selection,selection.summary,'checking');
 const path=selection.kind==='candidates'?'/api/candidates/':selection.kind==='job'?'/api/jobs/':'/api/artifacts/';
 returnPoller=VoiceRecording.createPoller({interval:5000,read:signal=>api(path+selection.id,{signal}),
  onData:data=>{const value=selection.kind==='artifact'?{status:'done',backend:data.backend,count:1,ready:1,created:null}:VoiceRecording.summary(selection,data);showReturnRecording(selection,value);return !['queued','running'].includes(value.status)},
  onConnection:offline=>{if(offline)showReturnRecording(selection,returnSummary,'reconnecting')},
  onMissing:()=>{if(recordingStore.selection()?.id===selection.id)recordingStore.clear();showReturnRecording(null)},
  onError:()=>showReturnRecording(selection,returnSummary,'reconnecting')
 });
}
window.addEventListener('storage',event=>{
 if(!browsePage||event.key!==recordingStore?.key)return;
 const saved=recordingStore.selection();
 if(saved?.id!==returnSelection?.id||saved?.selected!==returnSelection?.selected||saved?.summary?.status!==returnSummary?.status)watchReturnRecording();
});
$('recording-reconnect').onclick=()=>{recordingPoller?.refresh();health()};
function showEmptyRoom(){if(browsePage)return;$('working').hidden=true;$('empty').hidden=false}
function rememberRecording(selection){
 if(browsePage)return;
 const same=recordingSelection?.kind===selection.kind&&recordingSelection?.id===selection.id;
 recordingSelection={...(same?recordingSelection:{}),...selection,model:selection.model||(same?recordingSelection.model:null)||caps?.backend};
 if(!same){const clock=selection.timing;generationClock=clock&&clock.identity===selection.id&&Number.isFinite(clock.start)?clock:null;updateElapsed()}
 recordingStore?.save(recordingSelection);
 showReturnRecording(recordingSelection,recordingSelection.summary,recordingSelection.summary?'connected':'checking');
 history.replaceState(null,'',VoiceRecording.url(recordingSelection)+location.hash);
}
function recordingConnection(reconnecting){
 $('recording-connection').hidden=!reconnecting;
 $('recording-connection-message').textContent='Your request is saved. We’ll update your recording when the connection returns.';
 if(recordingSelection)showReturnRecording(recordingSelection,recordingSelection.summary,reconnecting?'reconnecting':recordingSelection.summary?'connected':'checking');
}
function finishMonitoring(){watching=null;monitoredImmediate=false;showGenerationProgress(null);controls();refreshQueue().catch(()=>{})}
function monitorRecording(path,onData){
 recordingPoller?.stop();
 recordingPoller=VoiceRecording.createPoller({
  read:signal=>api(path,{signal}),onConnection:recordingConnection,
  onData:value=>{const finished=onData(value);if(finished)finishMonitoring();return finished},
  onMissing:()=>{recordingConnection(false);recordingStore?.clear();recordingSelection=null;generationClock=null;showReturnRecording(null);updateElapsed();history.replaceState(null,'','/');$('working').hidden=true;$('candidate-results').hidden=true;$('empty').hidden=!!latest;error('This recording is no longer available. Choose another take from Collection or Queue.');finishMonitoring()},
  onError:exc=>{recordingConnection(false);error(exc.message);finishMonitoring()}
 });
}
function watchCandidates(identity,immediate=null,selected=null){
 if(browsePage){location.href=VoiceRecording.url({kind:'candidates',id:identity,selected});return}
 const token='candidates:'+identity;if(watching===token){recordingPoller?.refresh();return}
 if(currentCandidates?.id!==identity){currentCandidates=null;candidateCards.clear();$('candidate-cards').replaceChildren();$('candidate-results').hidden=true;latest=null;$('result').hidden=true;}
 selected=selected||(recordingSelection?.kind==='candidates'&&recordingSelection.id===identity?recordingSelection.selected:null);
 rememberRecording({kind:'candidates',id:identity,selected});
 watching=token;monitoredImmediate=immediate??true;showGenerationProgress(null);controls();$('error').hidden=true;$('empty').hidden=true;$('working').hidden=false;$('stage').textContent='Opening your recording…';$('working-description').textContent='Fetching progress and audio. Your accepted request continues on the server.';
 $('working-job-resources').hidden=true;
 monitorRecording('/api/candidates/'+identity,batch=>{
  saveRecordingSummary(batch);
  monitoredImmediate=['queued','running'].includes(batch.status)&&batch.jobs.some(job=>job.generate_immediately);controls();$('working').hidden=true;
  renderCandidates(batch);
  showGenerationProgress(['queued','running'].includes(batch.status)?batch.progress:null,batch.jobs.find(job=>job.status==='running')?.stage);
  const preferred=batch.jobs.find(job=>job.result?.id===recordingSelection?.selected);
  if(preferred&&latest?.id!==preferred.result.id)showResult(preferred.result);
  else if(!latest){const first=batch.jobs.find(job=>job.result);if(first)showResult(first.result)}
  if(!['queued','running'].includes(batch.status)){refresh().catch(exc=>error(exc.message));announce(`${batch.done_count} of ${batch.candidate_count} voice options ready.`);return true}
  return false;
 });
}
function watch(identity){
 if(browsePage){location.href='/?job='+encodeURIComponent(identity);return}
 if(watching===identity){recordingPoller?.refresh();return}
 currentCandidates=null;latest=null;$('candidate-results').hidden=true;watching=identity;monitoredImmediate=true;showGenerationProgress(null);rememberRecording({kind:'job',id:identity});controls();$('error').hidden=true;$('empty').hidden=true;$('result').hidden=true;$('working').hidden=false;
 $('stage').textContent='Opening your recording…';$('working-description').textContent='Fetching progress and audio. Your accepted request continues on the server.';$('working-job-resources').hidden=false;$('working-job-resources').href='/observability?job='+encodeURIComponent(identity);
 monitorRecording('/api/jobs/'+identity,job=>{
  // Queue/Observability links often point at one child. Recover its entire listening room.
  if(job.candidate_batch_id){watchCandidates(job.candidate_batch_id,job.generate_immediately,job.result?.id);return false}
  saveRecordingSummary(job);
  monitoredImmediate=['queued','running'].includes(job.status)&&job.generate_immediately;controls();$('stage').textContent=job.stage;
  showGenerationProgress(['queued','running'].includes(job.status)?job.progress:null,job.stage);
  generationTiming(identity,job.created_at,job.stage,!['queued','running'].includes(job.status)?job.completed_at||new Date().toISOString():null);
  if(job.status==='done'){showResult(job.result);refresh().catch(exc=>error(exc.message));announce('Speech ready');return true}
  if(job.status==='error'||job.status==='deleted'){$('working').hidden=true;error(job.error||'This pending take was removed.');return true}
  return false;
 });
}
$('form').onsubmit=async event=>{
 event.preventDefault();if(!modelChoiceReady){error('Choose a set-up model that supports this voice source.');return}if(submitting||uploading||immediateInProgress())return;let request;try{request=settings()}catch(exc){error(exc.message);return}
 if(mode==='preset'&&!request.preset_voice){error('Choose an expressive preset voice first.');return}
 if(mode==='saved'&&!request.voice_id){error('Choose a saved voice first.');return}
 if(mode==='upload'&&!request.reference_id){error('Choose an audio reference first.');return}
 submitting=true;controls();
 generationTiming('submitting',new Date().toISOString(),'Submitting request');
 try{
  await saveSelectedTranscript();
  if(!pendingKey){
   if($('random-seed').checked){request.seed=randomSeed();$('seed').value=request.seed;persist()}
   pendingKey=crypto.randomUUID();
  }
  const batch=await post('/api/candidates',{...request,idempotency_key:pendingKey});pendingKey=null;watchCandidates(batch.id,request.generate_immediately);await refreshQueue();
 }
 catch(exc){if(generationClock?.identity==='submitting'){generationClock=null;updateElapsed()}error(exc.message)}finally{submitting=false;controls()}
};
document.addEventListener('keydown',event=>{if(!browsePage&&!document.querySelector('dialog[open]')&&(event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();if(!$('generate').disabled)$('form').requestSubmit()}});
$('save-voice').onclick = () => {
  if (latest) openVoiceEditor({output_id: latest.id, name: $('voice-name').value,
    description: latest.description || '', transcript:savedTranscript(latest),tags: []});
};
$('reference').onchange=async()=>{const file=$('reference').files[0];reference=null;$('reference-preview').hidden=true;$('save-reference').hidden=true;if(!file)return;uploading=true;controls();$('reference-info').textContent='Preparing reference…';try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);body.append('transcript',$('ref-text').value);reference=await api('/api/references',{method:'POST',body});$('reference-info').textContent=`${reference.name} · ${reference.duration}s`;$('reference-preview').src=reference.audio_url;$('reference-preview').hidden=false;$('save-reference').hidden=false}catch(exc){$('reference-info').textContent='';error(exc.message)}finally{uploading=false;controls()}};
$('save-reference').onclick = () => {
  if (reference) openVoiceEditor({reference_id: reference.id,
    name: (reference.name || 'Uploaded voice').replace(/\.[^.]+$/, '').slice(0, 100), description: '', transcript:$('ref-text').value,tags: []});
};
$('unload').onclick=async()=>{try{await post('/api/model/unload',{});announce('Model memory released. It reloads on the next take.');await health()}catch(exc){error(exc.message)}};
function queueNotices(state){
 const storageMessage=state?.queue_storage?.status==='unavailable'?state.queue_storage.message:null;
 const serviceMessage=state?.queue_service&&state.queue_service.status!=='ready'?state.queue_service.message:null;
 for(const id of ['queue-service-notice','queue-service-dialog-notice']){
  $(id).textContent=storageMessage||serviceMessage||'';
  $(id).hidden=!(storageMessage||serviceMessage);
 }
}
// The header switch and the queue dialog button both mirror the persisted pause flag read by health polling.
// Each pause/resume bumps the revision so a health reading requested before it cannot undo the change.
let queueSwitchBusy=false,queuePauseRevision=0;
// The header and Queue dialog switches show the same shared pause flag.
const queueSwitches=()=>[$('queue-processing'),$('queue-processing-dialog')];
function showQueuePaused(paused,available=true,revision=queuePauseRevision){
 if(revision!==queuePauseRevision)return;
 queuePaused=paused;
 if(queueSwitchBusy)return;
 for(const toggle of queueSwitches()){toggle.checked=!paused;toggle.disabled=!available}
}
async function setQueuePaused(paused){
 if(queueSwitchBusy){for(const toggle of queueSwitches())toggle.checked=!queuePaused;return}
 queueSwitchBusy=true;for(const toggle of queueSwitches())toggle.disabled=true;queuePauseRevision++;
 try{
  const state=await post('/api/queue/'+(paused?'pause':'resume'),{});queueSwitchBusy=false;showQueuePaused(state.paused);
  announce(state.paused?'Queue paused. Active work finishes first; Generate immediately and Run Now jobs still run.':'Queue resumed.');
 }catch(exc){queueSwitchBusy=false;showQueuePaused(queuePaused,queueWritable);error(`Could not ${paused?'pause':'resume'} the queue: ${exc.message}`)}
 finally{queueSwitchBusy=false;queuePauseRevision++;await health()}
}
let healthInFlight=false;
async function health(){
 if(healthInFlight)return;healthInFlight=true;
 try{
  const pauseRevision=queuePauseRevision,state=await api('/health');healthSnapshot=state;
  if(!recordingStore)recordingStore=VoiceRecording.createStore(localStorage,state.project||location.origin);
  if(!changingModel)await loadModelChoices();
  online=state.downloaded&&!state.runtime_errors?.length;
  queueWritable=state.queue_storage?.status!=='unavailable';queueNotices(state);
  // Without readable storage, queue_paused is only a default: keep the last known position and disable the switch.
  if(queueWritable)showQueuePaused(!!state.queue_paused,true,pauseRevision);else if(!queueSwitchBusy)for(const toggle of queueSwitches())toggle.disabled=true;
  const pausedIdle=queueWritable&&queuePaused&&!state.busy&&state.status!=='error';
  $('status').dataset.offline=!online;$('status').dataset.paused=pausedIdle;
  $('status').textContent=state.status==='error'?(state.last_error||'Setup incomplete'):state.busy?state.status:pausedIdle?'Queue paused':state.model_loaded?'Model loaded · Ready':caps?.limits.model_residency==='per job subprocess'?'Ready · Loads per take':'Ready · Loads on first take';
  busy=state.busy;
  if(queueWritable){
   try{await refreshQueue()}catch(exc){queueWritable=false;queueNotices({queue_storage:{status:'unavailable',message:exc.message}})}
  }else{
   $('queue-count').textContent='Queue storage unavailable. Displayed entries may be out of date.';
   for(const id of ['queue-clear','queue-prev','queue-next'])$(id).disabled=true;
  }
 }catch(exc){
  online=false;$('status').textContent=exc.message==='Failed to fetch'?'Server offline':exc.message;
  $('status').dataset.offline=true;$('status').dataset.paused=false;for(const toggle of queueSwitches())toggle.disabled=true;queueNotices(null);
 }finally{healthInFlight=false;controls()}
}
let queueOffset=0,queuePage=null,queueRequest=0,queueDrag=null,queueMutating=false;
async function refreshQueue(){
 if(queueDrag||queueMutating)return;
 const token=++queueRequest;
 const page=await api('/api/queue?'+new URLSearchParams({status:$('queue-status').value,limit:$('queue-size').value,offset:queueOffset}));
 if(token!==queueRequest||queueDrag||queueMutating)return;
 if(queueOffset&&queueOffset>=page.total){queueOffset=0;return refreshQueue()}
 const queueScroll=$('queue-entries').scrollTop,focused=document.activeElement,focusRow=focused?.closest('.queue-entry'),queueFocus=focusRow?{id:focusRow.dataset.jobId,text:focused.textContent}:null;
 queuePage=page;$('queue-entries').replaceChildren();$('queue-badge').textContent=(page.counts.queued||0)+(page.counts.running||0)||'';
 $('queue-count').textContent=`${page.counts.queued||0} pending · ${page.counts.running||0} running · ${page.counts.error||0} failed`;
 $('queue-clear').disabled=!(page.counts.queued>0);$('queue-prev').disabled=queueOffset===0;$('queue-next').disabled=!page.has_more;
 $('queue-page').textContent=page.total?`${queueOffset+1}–${queueOffset+page.data.length} of ${page.total}`:'No matching entries';
 for(const row of page.data){
  const card=el('article',null,'queue-entry'),content=el('div',null,'queue-entry-content');
  card.dataset.jobId=row.id;card.dataset.queueStatus=row.status;
  if(row.status==='queued'){
   const handle=el('button',null,'queue-drag-handle');handle.append(queueIcon('grip'));handle.type='button';handle.dataset.jobId=row.id;
   handle.setAttribute('aria-label','Reorder job: '+(row.requested?.text||row.id).slice(0,90));handle.setAttribute('aria-describedby','queue-reorder-hint');
   handle.title='Drag to reorder, adopting the target’s priority, or use the up and down arrow keys';handle.addEventListener('pointerdown',startQueueDrag);
   handle.addEventListener('lostpointercapture',()=>{if(queueDrag?.handle===handle)finishQueueDrag()});
   handle.onkeydown=event=>{if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();changeQueueOrder(row.id,{direction:event.key==='ArrowUp'?'up':'down'},false,true)}};
   card.append(handle);
  }else{const spacer=el('span',null,'queue-drag-spacer');spacer.setAttribute('aria-hidden','true');card.append(spacer)}
  content.append(el('strong',[row.status,row.backend||'legacy',voiceLabel(row.requested),row.generate_immediately?'Immediate':'Normal'].filter(Boolean).join(' · ')));
  const progress=row.status==='running'?row.progress:null,progressLabel=VoiceRecording.progressText(progress,{compact:true});
  content.append(el('p',row.requested?.text||row.id),el('p',progressLabel?`${row.stage} · ${progressLabel}`:row.stage,'hint'));
  if(progress)content.append(progressBar(progress,row.stage,'queue-progress'));
  if(row.error)content.append(el('p',row.error,'error'));
  const actions=el('div',null,'card-actions queue-actions');
  if(row.candidate_batch_id){const options=queueControl('button','View all options','options');options.type='button';options.onclick=()=>{$('queue-dialog').close();watchCandidates(row.candidate_batch_id)};actions.append(options);}
  const inspect=queueControl('button',row.status==='done'?'Open take':'Monitor',row.status==='done'?'play':'monitor','secondary queue-monitor');inspect.type='button';inspect.onclick=()=>{$('queue-dialog').close();row.result?showResult(row.result):watch(row.id)};actions.append(inspect);
  if(row.status!=='queued'){const resourceLink=queueControl('a','Resources & logs','resources');resourceLink.href='/observability?job='+encodeURIComponent(row.id);actions.append(resourceLink)}
  if(row.status==='queued'){
   const run=queueControl('button','Run Now','play','secondary queue-run-now');run.type='button';run.disabled=!!row.paused;
   run.title=row.paused?row.stage:'Run next, ahead of other waiting jobs. The current batch finishes first.';
   run.onclick=()=>changeQueueOrder(row.id,null,true);actions.append(run);
   const remove=queueControl('button','Remove','remove','secondary queue-remove');remove.type='button';remove.onclick=async()=>{try{await api('/api/queue/'+row.id,{method:'DELETE'});await refreshQueue()}catch(exc){error(exc.message)}};actions.append(remove);
  }
  if(row.status==='error'&&!row.paused){const retry=queueControl('button','Retry same entry','retry');retry.type='button';retry.onclick=async()=>{try{const job=await post('/api/jobs/'+row.id+'/retry',{});$('queue-dialog').close();watch(job.id);await refreshQueue()}catch(exc){error(exc.message)}};actions.append(retry)}
  card.append(content,actions);$('queue-entries').append(card);
 }
 $('queue-entries').scrollTop=queueScroll;
 if(queueFocus){const card=$('queue-entries').querySelector(`[data-job-id="${queueFocus.id}"]`);[...card?.querySelectorAll('button')||[]].find(button=>button.textContent===queueFocus.text&&!button.disabled)?.focus({preventScroll:true})}
}
// Name the voice a line is recorded with: saved voices by name, uploads and built-in speakers by kind.
const savedVoiceNames=new Map();
function voiceLabel(request){
 if(!request)return '';
 if(request.voice_id){
  const name=voiceRows.find(voice=>voice.id===request.voice_id)?.name||savedVoiceNames.get(request.voice_id);
  if(name)return name;
  if(!savedVoiceNames.has(request.voice_id)){
   savedVoiceNames.set(request.voice_id,null);
   api('/api/voices/'+request.voice_id).then(voice=>{savedVoiceNames.set(request.voice_id,voice.name||'Saved voice');voiceNamesChanged()}).catch(()=>savedVoiceNames.set(request.voice_id,'Saved voice'));
  }
  return 'Saved voice';
 }
 if(request.reference_id)return 'Uploaded voice';
 if(request.preset_voice)return 'Built-in voice: '+request.preset_voice;
 return request.description?'Designed voice':'';
}
function voiceNamesChanged(){
 if($('queue-dialog')?.open)refreshQueue().catch(()=>{});
 if($('cards')&&collection==='history'&&historyPage.data.length)renderCards();
}
async function changeQueueOrder(identity,target,runNow=false,refocus=false){
 if(queueMutating||queueDrag)return;
 queueMutating=true;queueRequest++;$('queue-error').hidden=true;$('queue-entries').setAttribute('aria-busy','true');
 for(const button of $('queue-entries').querySelectorAll('button'))button.disabled=true;
 try{
  await post('/api/queue/'+identity+(runNow?'/run-now':'/move'),target||{});
  announce(runNow?'Job will run next after the current generation finishes.':'Queue order saved.');
 }catch(exc){error(exc.message)}
 finally{
  queueMutating=false;$('queue-entries').removeAttribute('aria-busy');
  try{await refreshQueue();if(refocus||runNow){const card=$('queue-entries').querySelector(`[data-job-id="${identity}"]`);(card?.querySelector(runNow?'.queue-run-now':'.queue-drag-handle')||card?.querySelector('button'))?.focus()}}catch(exc){error(exc.message)}
 }
}
function queuedEntries(){return [...$('queue-entries').querySelectorAll('[data-queue-status="queued"]')]}
function liftQueueEntry(card,y){
 const box=card.getBoundingClientRect(),ghost=card.cloneNode(true);
 ghost.className='queue-entry queue-ghost';delete ghost.dataset.jobId;delete ghost.dataset.queueStatus;ghost.setAttribute('aria-hidden','true');ghost.inert=true;
 Object.assign(ghost.style,{left:box.left+'px',top:box.top+'px',width:box.width+'px',height:box.height+'px'});
 $('queue-dialog').append(ghost);card.classList.add('queue-moving');
 return {ghost,offset:y-box.top};
}
function moveQueueGap(list,card,before){
 // Animate rows that shift around the gap from where they were (FLIP).
 const rows=[...list.children].filter(row=>row!==card),first=new Map(rows.map(row=>[row,row.getBoundingClientRect().top]));
 list.insertBefore(card,before);
 if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 for(const row of rows){const shift=first.get(row)-row.getBoundingClientRect().top;if(shift)row.animate([{transform:`translateY(${shift}px)`},{transform:'none'}],{duration:150,easing:'ease-out'})}
}
function queueDropTarget(y){
 const list=$('queue-entries'),card=queueDrag.card,rows=queuedEntries().filter(row=>row!==card);
 if(!rows.length){queueDrag.target=null;return}
 // Place the gap by the lifted row's centre, so releasing where the drag began restores the original order.
 const center=queueDrag.ghost?y-queueDrag.offset+card.offsetHeight/2:y;
 const next=rows.find(row=>{const box=row.getBoundingClientRect();return center<=box.top+box.height/2});
 const before=next||rows.at(-1).nextSibling;
 if(before!==card&&card.nextSibling!==before)moveQueueGap(list,card,before);
 const queued=queuedEntries(),index=queued.indexOf(card);
 queueDrag.target=index===queueDrag.startIndex?null:queued[index+1]?{before_id:queued[index+1].dataset.jobId}:{after_id:queued[index-1].dataset.jobId};
}
function queueDragFrame(){
 if(!queueDrag?.moving)return;
 const box=$('queue-entries').getBoundingClientRect(),y=queueDrag.y;
 if(y<box.top+40&&queueDrag.direction<0)$('queue-entries').scrollTop-=12;
 else if(y>box.bottom-40&&queueDrag.direction>0)$('queue-entries').scrollTop+=12;
 queueDropTarget(y);queueDrag.frame=requestAnimationFrame(queueDragFrame);
}
function startQueueDrag(event){
 if(event.button!==0||queueMutating||queueDrag)return;
 const handle=event.currentTarget;event.preventDefault();handle.focus({preventScroll:true});
 queueRequest++;handle.setPointerCapture(event.pointerId);
 const card=handle.closest('.queue-entry');
 queueDrag={identity:handle.dataset.jobId,handle,card,startIndex:queuedEntries().indexOf(card),pointerId:event.pointerId,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,direction:0,moving:false,target:null};
}
function finishQueueDrag(commit=false){
 if(!queueDrag)return;
 const drag=queueDrag;queueDrag=null;cancelAnimationFrame(drag.frame);
 if(drag.handle.hasPointerCapture(drag.pointerId))drag.handle.releasePointerCapture(drag.pointerId);
 $('queue-entries').classList.remove('queue-dragging');drag.ghost?.remove();
 for(const card of $('queue-entries').children)card.classList.remove('queue-moving');
 if(commit&&drag.moving&&drag.target)changeQueueOrder(drag.identity,drag.target,false,true);
 else refreshQueue().catch(exc=>error(exc.message));
}
document.addEventListener('pointermove',event=>{
 if(!queueDrag||event.pointerId!==queueDrag.pointerId)return;
 queueDrag.direction=Math.sign(event.clientY-queueDrag.y)||queueDrag.direction;
 queueDrag.x=event.clientX;queueDrag.y=event.clientY;
 if(!queueDrag.moving&&Math.hypot(event.clientX-queueDrag.startX,event.clientY-queueDrag.startY)>6){
  queueDrag.moving=true;$('queue-entries').classList.add('queue-dragging');Object.assign(queueDrag,liftQueueEntry(queueDrag.card,event.clientY));
  announce('Moving job. Release at the outlined position, or press Escape to cancel.');queueDragFrame();
 }
 if(queueDrag.ghost)queueDrag.ghost.style.top=(event.clientY-queueDrag.offset)+'px';
 if(queueDrag.moving)event.preventDefault();
},{passive:false});
document.addEventListener('pointerup',event=>{
 if(!queueDrag||event.pointerId!==queueDrag.pointerId)return;
 const box=$('queue-entries').getBoundingClientRect();
 if(queueDrag.moving)queueDropTarget(event.clientY);
 finishQueueDrag(event.clientX>=box.left-24&&event.clientX<=box.right+24&&event.clientY>=box.top-48&&event.clientY<=box.bottom+48);
});
document.addEventListener('pointercancel',event=>{if(queueDrag?.pointerId===event.pointerId)finishQueueDrag()});
window.addEventListener('blur',()=>finishQueueDrag());
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&queueDrag){event.preventDefault();event.stopPropagation();finishQueueDrag()}},true);
$('queue-dialog').addEventListener('close',()=>finishQueueDrag());
$('queue-clear').onclick=async()=>{try{const result=await api('/api/queue',{method:'DELETE'});announce(`Removed ${result.deleted} pending takes. Running speech continues.`);await refreshQueue()}catch(exc){error(exc.message)}};
$('queue-status').onchange=$('queue-size').onchange=()=>{queueOffset=0;refreshQueue().catch(exc=>error(exc.message))};
$('queue-prev').onclick=()=>{queueOffset=Math.max(0,queueOffset-Number($('queue-size').value));refreshQueue().catch(exc=>error(exc.message))};
$('queue-next').onclick=()=>{if(queuePage?.has_more){queueOffset+=Number($('queue-size').value);refreshQueue().catch(exc=>error(exc.message))}};
async function loadCollection(){
 if(collection==='voices')return loadVoicePage();
 const token=++catalogRequest,page=await api('/api/history?'+new URLSearchParams({q:$('search').value,limit:pageSize,offset:catalogOffset,deleted:$('show-deleted').checked}));
 if(token!==catalogRequest)return;
 if(catalogOffset&&catalogOffset>=page.total){catalogOffset=Math.max(0,Math.floor((page.total-1)/pageSize)*pageSize);return loadCollection()}
 historyPage=page;historyRows=page.data;renderCards();
}
async function loadVoicePage() {
  const token = ++catalogRequest;
  const params = new URLSearchParams({q: collection === 'voices' ? $('search').value : '',
    sort: $('voice-sort').value, limit: pageSize, offset: catalogOffset,deleted:$('show-deleted').checked});
  for(const tag of selectedTags)params.append('tags',tag);
  const page = await api('/api/voices?' + params);
  if (token !== catalogRequest) return;
  if (catalogOffset && catalogOffset >= page.total) { catalogOffset=Math.max(0,Math.floor((page.total-1)/pageSize)*pageSize);return loadVoicePage(); }
  voicePage = page;
  renderCards();
}
function setCollection(next) {
  clearTimeout(searchTimer); collection = next; catalogOffset = 0;
  closeTagChoices();$('voice-filters').hidden=next!=='voices';$('collection-filters').dataset.history=String(next!=='voices');
  document.querySelectorAll('[data-collection]').forEach(button => button.setAttribute('aria-selected', button.dataset.collection === collection));
  $('search').placeholder = next === 'voices' ? 'Search voice names, descriptions, or tags…' : 'Search text, voice descriptions, or emotions…';
  syncCollectionLocation();loadCollection().catch(exc=>error(exc.message));
}
function syncCollectionLocation(){if(collectionPage){const url=new URL(location.href);url.searchParams.set('tab',collection);if($('show-deleted').checked)url.searchParams.set('deleted','1');else url.searchParams.delete('deleted');history.replaceState(null,'',url)}}
$('show-deleted').onchange=()=>{catalogOffset=0;catalogRequest++;selectedTags.clear();renderTagChoices();syncCollectionLocation();refresh().catch(exc=>error(exc.message))};
setInterval(()=>{if(collectionPage&&document.visibilityState==='visible'&&$('show-deleted').checked&&!$('collection-delete-dialog').open&&$('tag-options').hidden)refresh().catch(exc=>error(exc.message))},60000);
function chooseVoice(row, focus = true) {
  if(browsePage&&focus){location.href='/?voice='+encodeURIComponent(row.id);return;}
  if (!voiceRows.some(item => item.id === row.id)) voiceRows.push(row);
  if (![...$('voice').options].some(option => option.value === row.id)) $('voice').append(new Option(row.name, row.id));
  $('voice').value = row.id; setMode('saved'); voicePreview();persist();
  if (focus) { $('text').focus(); $('form').scrollIntoView({block: 'start'}); }
}
let voiceUploadAudioURL=null;
function clearVoiceUploadAudio(){
 const player=$('voice-upload-preview');player.pause();player.removeAttribute('src');player.load();player.hidden=true;
 if(voiceUploadAudioURL){URL.revokeObjectURL(voiceUploadAudioURL);voiceUploadAudioURL=null}
}
function voiceUploadControls(){
 const state=uploadPreview.snapshot();
 $('voice-transcription-language').disabled=!$('voice-auto-transcribe').checked;
 $('voice-transcription-language').title=$('voice-auto-transcribe').checked?'':'Turn on automatic transcription to choose its language.';
 $('voice-editor-save').disabled=voiceSaving||state.pending||!!(voiceEditor?.upload&&$('voice-auto-transcribe').checked&&state.error&&!$('edit-transcript').value.trim());
 if(!voiceSaving)$('voice-editor-save').textContent=state.pending?'Transcribing…':voiceEditor?.upload?'Upload to Collection':voiceEditor?.edit_id?'Save changes':'Save voice';
}
const uploadPreview=VoiceUpload.create({
 prepare:async(file,{transcript,language,signal})=>{const body=new FormData();body.append('file',file);body.append('transcript',transcript);body.append('transcribe','true');body.append('transcription_language',language);return api('/api/references',{method:'POST',body,signal})},
 onState:state=>{
  if(!voiceEditor?.upload)return;
  if(state.applyTranscript)$('edit-transcript').value=state.transcript||'';
  $('voice-transcription-status').textContent=state.pending?'Transcribing on this Mac… You can keep editing the voice details.':state.error?state.error+' Enter the words manually, retry, or turn automatic transcription off.':state.reference?.transcription?'Transcript ready. Review the detected words below before saving.':state.file?$('voice-auto-transcribe').checked?'Your supplied transcript was kept.':'Automatic transcription is off. You can enter the words manually.':'';
  $('voice-transcription-retry').hidden=!state.error||!$('voice-auto-transcribe').checked;voiceUploadControls();
 }
});
function prepareVoiceUpload(){
 const file=$('voice-upload-file').files[0];
 if(file&&file.size>25*1024*1024){uploadPreview.cancel();$('voice-transcription-status').textContent='Choose a file under 25 MB.';return}
 uploadPreview.select(file,{automatic:$('voice-auto-transcribe').checked,transcript:$('edit-transcript').value,language:$('voice-transcription-language').value});
}
function openVoiceEditor(context) {
  uploadPreview.cancel();clearVoiceUploadAudio();voiceEditor = context;
  if(context.upload)$('voice-upload-field').after($('edit-transcript-field'));else document.querySelector('label[for="edit-voice-tags"]').before($('edit-transcript-field'));
  $('voice-editor-title').textContent = context.upload ? 'Upload a voice' : context.edit_id ? 'Edit voice details' : 'Save a voice';
  $('voice-editor-save').textContent = context.upload ? 'Upload to Collection' : context.edit_id ? 'Save changes' : 'Save voice';
  $('voice-upload-field').hidden=!context.upload;$('voice-upload-file').required=!!context.upload;$('voice-upload-file').value='';
  $('voice-auto-transcribe').checked=true;$('voice-transcription-language').value='auto';$('voice-transcription-status').textContent='';$('voice-transcription-retry').hidden=true;
  $('voice-editor-hint').textContent = context.upload ? 'Add a recording, review its automatic transcript, then save the voice with a name and tags.' : context.edit_id ?
    'Update the catalog details. The saved recording stays the same.' :
    'Save named performance variants of the same speaker with mood tags and the exact transcript. Choose the desired recorded delivery before combined cloning.';
  $('edit-voice-name').value = context.name || '';
  $('edit-voice-description').value = context.description || '';
  $('edit-voice-tags').value = (context.tags || []).join(', ');
  $('edit-transcript').value=context.transcript||'';
  $('voice-editor-error').hidden = true;
  $('voice-editor').showModal(); $('edit-voice-name').focus();voiceUploadControls();
}
$('upload-voice').onclick=$('upload-saved-voice').onclick=()=>openVoiceEditor({upload:true});
$('voice-upload-file').onchange=()=>{
 clearVoiceUploadAudio();const file=$('voice-upload-file').files[0];
 if(file&&file.size<=25*1024*1024){voiceUploadAudioURL=URL.createObjectURL(file);$('voice-upload-preview').src=voiceUploadAudioURL;$('voice-upload-preview').hidden=false}
 if(file&&!$('edit-voice-name').value.trim())$('edit-voice-name').value=file.name.replace(/\.[^.]+$/,'').slice(0,100);
 prepareVoiceUpload();
};
$('voice-auto-transcribe').onchange=$('voice-transcription-language').onchange=$('voice-transcription-retry').onclick=prepareVoiceUpload;
$('edit-transcript').addEventListener('input',()=>{uploadPreview.edit();voiceUploadControls()});
$('voice-editor').addEventListener('close',()=>{uploadPreview.cancel();clearVoiceUploadAudio();$('voice-upload-file').value=''})
$('voice-editor-cancel').onclick = () => $('voice-editor').close();
$('voice-editor').addEventListener('cancel',event=>{if(voiceSaving)event.preventDefault()});
$('voice-editor-form').onsubmit = async event => {
  event.preventDefault(); if (!voiceEditor||voiceSaving||uploadPreview.snapshot().pending||$('voice-editor-save').disabled) return;
  const context = voiceEditor;
  const details = {name: $('edit-voice-name').value.trim(), description: $('edit-voice-description').value.trim(),
    tags: $('edit-voice-tags').value.split(',').map(tag => tag.trim()).filter(Boolean),transcript:$('edit-transcript').value.trim()};
  if (details.tags.length > 20 || details.tags.some(tag => tag.length > 40)) {
    $('voice-editor-error').textContent = 'Use up to 20 tags, each no more than 40 characters.';
    $('voice-editor-error').hidden = false; return;
  }
  voiceSaving=true;const saveLabel=$('voice-editor-save').textContent;
  $('voice-editor-save').disabled = true; $('voice-editor-cancel').disabled = true;
  if(context.upload)$('voice-editor-save').textContent='Uploading…';
  try {
    let row;
    if(context.upload){
      const file=$('voice-upload-file').files[0];if(!file)throw Error('Choose a voice recording first.');
      if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');
      const prepared=uploadPreview.snapshot();
      if(prepared.file===file&&prepared.reference)row=await post('/api/voices',{...details,reference_id:prepared.reference.id});
      else{
       const form=new FormData();form.append('file',file);form.append('name',details.name);form.append('description',details.description);form.append('transcript',details.transcript);form.append('transcribe',String($('voice-auto-transcribe').checked));form.append('transcription_language',$('voice-transcription-language').value);
       for(const tag of details.tags)form.append('tags',tag);
       row=await api('/api/voices/upload',{method:'POST',body:form});
      }
    }
    else if (context.edit_id) row = await api('/api/voices/' + context.edit_id,
      {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(details)});
    else row = await post('/api/voices', {...details, output_id: context.output_id || null, reference_id: context.reference_id || null});
    if (!context.edit_id) {
      $('search').value = ''; selectedTags.clear();renderTagChoices(); $('voice-sort').value = 'updated';
      $('show-deleted').checked=false;
      collection = 'voices'; catalogOffset = 0;
      syncCollectionLocation();
      document.querySelectorAll('[data-collection]').forEach(button => button.setAttribute('aria-selected', button.dataset.collection === collection));
    }
    transcriptDrafts.set(row.id,row.transcript||'');
    if(transcriptVoice===row.id)$('ref-text').value=row.transcript||'';
    await refresh();
    if (!context.edit_id) chooseVoice(row, false);
    $('voice-editor').close();
    $('voice-notice').textContent = context.edit_id ? `Updated “${row.name}”.` : `Saved “${row.name}”. Select Use this voice for more lines.`;
    $('voice-notice').hidden = false;
    $('save-hint').textContent = `Saved “${row.name}” in your voice library.`;
    announce($('voice-notice').textContent);
  } catch (exc) {
    $('voice-editor-error').textContent = exc.message; $('voice-editor-error').hidden = false;
  } finally {
    voiceSaving=false;$('voice-editor-save').textContent=saveLabel;
    voiceUploadControls(); $('voice-editor-cancel').disabled = false;
  }
};
$('voice-sort').onchange = () => {catalogOffset = 0; loadVoicePage().catch(exc => error(exc.message));};
$('voice-prev').onclick = () => {catalogOffset = Math.max(0, catalogOffset - pageSize); loadCollection().catch(exc => error(exc.message));};
$('voice-next').onclick = () => {catalogOffset += pageSize; loadCollection().catch(exc => error(exc.message));};
$('page-size').onchange=()=>{pageSize=Number($('page-size').value);catalogOffset=0;loadCollection().catch(exc=>error(exc.message))};
function renderModelChoices(){
 if(!lastModelList)return;
 const rows=caps?readySourceModels():availableModels;
 const selected=awaitingSourceModel?'':caps?.backend||lastModelList.default_model;
 $('voice-model').replaceChildren(...rows.map(row=>new Option(row.name+(row.id===lastModelList.default_model?' (default)':''),row.id)));
 modelChoiceReady=rows.some(row=>row.id===selected);
 if(modelChoiceReady)$('voice-model').value=selected;
 else{$('voice-model').prepend(new Option(mode==='design'?'Choose a voice-design model…':'Choose a capable model…',''));$('voice-model').value=''}
 if(browsePage){$('model-badge').textContent='All voice models';$('license-notice').hidden=true;}
 else if(caps){$('model-footer').textContent=awaitingSourceModel?'Choose a voice model · On your Mac':`${caps.name} · ${caps.quantization} · ${caps.limits.runtime||'MLX'} on Apple Silicon`;if(!awaitingSourceModel){$('model-badge').textContent=`${caps.sample_rate/1000} kHz · ${caps.quantization} · On your Mac`;$('license-notice').hidden=!caps.models[caps.backend]?.notice;}}
 $('voice-model-hint').textContent=(awaitingSourceModel?'Choose a model for this voice source. ':'')+'One shared queue · Default: '+lastModelList.default_model+(lastModelList.active_model?' · Active: '+lastModelList.active_model:'');
}
async function loadModelChoices(){
 lastModelList=await api('/v1/models');availableModels=lastModelList.data;renderModelChoices();
 if(caps)updateCapabilities();
 return modelChoiceReady?$('voice-model').value:awaitingSourceModel?null:availableModels[0]?.id;
}
async function selectVoiceModel(backend){
 if(changingModel)return;
 const draftMode=awaitingSourceModel?mode:null;
 changingModel=true;controls();
 try{
  caps=await api('/api/capabilities?'+new URLSearchParams({model:backend}));
  awaitingSourceModel=false;modelChoiceReady=true;
  $('prompt-expansion-enabled').checked=!!caps.limits.prompt_expansion?.enabled;
  $('random-seed').checked=true;
  storageKey=caps.service;
  $('model-badge').textContent=`${caps.sample_rate/1000} kHz · ${caps.quantization} · On your Mac`;
  $('model-footer').textContent=`${caps.name} · ${caps.quantization} · ${caps.limits.runtime||'MLX'} on Apple Silicon`;
  $('storage-footer').textContent=caps.storage;$('output-dir').placeholder=caps.output_dir;
  $('language').replaceChildren(...caps.languages.map(x=>new Option(x,x)));
  $('preset-voice').replaceChildren(...caps.presets.map(x=>new Option(`${x.name} · ${x.language}`,x.id)));
  $('steering-emotion').replaceChildren(new Option('None',''));
  for(const emotion of caps.extensions?.emotion_steering?.emotions||[])$('steering-emotion').append(new Option(emotion[0].toUpperCase()+emotion.slice(1),emotion));
  $('settings-hint').textContent=(caps.limits.max_tokens_fixed?'This upstream Chatterbox API uses a fixed 1000-token ceiling. ':caps.backend==='higgs'?'Higgs recommends 2,000 audio tokens per text chunk. If a take cannot finish before this cap, it fails without publishing incomplete audio. Increase the limit or reduce text chunk size. ':caps.backend==='moss'?'The audio-frame ceiling can be raised to its text-derived minimum. ':'The audio token limit is a safety cap. Reaching it can cut a take short. ')+(caps.limits.memory_note||'A matching seed alone does not lock a voice.');
  const notice=caps.models[caps.backend];$('license-notice').hidden=!notice?.notice;
  if(notice?.notice){$('license-text').textContent=notice.notice;$('license-link').href=notice.license_url;}
  buildBackendSettings();buildPerformanceControls();updateCapabilities();await refresh();
  const saved=JSON.parse(localStorage.getItem(storageKey+'-form')||'null');
  if(draftMode)setMode(draftMode);else if(saved)restore(saved);else setMode(caps.modes.design.available===false?(voiceRows.length?'saved':'upload'):'design');
  await health();
  if(!browsePage)recordingStore?.setModel(backend);
  renderModelChoices();
 }finally{changingModel=false;controls();if(pendingModelRestore){const row=pendingModelRestore;pendingModelRestore=null;restore(row)}}
}
$('voice-model').onchange=()=>{if($('voice-model').value)selectVoiceModel($('voice-model').value).catch(exc=>error(exc.message))};
for(const toggle of queueSwitches())toggle.onchange=event=>setQueuePaused(!event.target.checked);
async function startStudio(){
 if(studioInitialized||studioStarting)return;studioStarting=true;
 try{
  await health();if(!recordingStore)return;
  if(browsePage)watchReturnRecording();
  const initial=await loadModelChoices(),savedModel=recordingStore.model()||recordingStore.selection()?.model;
  const model=availableModels.some(row=>row.id===savedModel)?savedModel:initial;
  if(!model){error('No set-up voice models are ready. Run make setup or reconnect model storage.');return}
  await selectVoiceModel(model);
  if(!await openPageSelection()&&!browsePage){
   const saved=recordingStore.selection();
   if(saved)await openRecording(saved);
   else{
    // Upgrade a previous browser pointer once; subsequent recovery is project-wide.
    const batch=localStorage.getItem(storageKey+'-candidates'),job=localStorage.getItem(storageKey+'-job');
    if(batch&&VoiceRecording.validId(batch))watchCandidates(batch);
    else if(job&&VoiceRecording.validId(job))watch(job);
    else if(healthSnapshot?.active_job)watch(healthSnapshot.active_job);
   }
   localStorage.removeItem(storageKey+'-candidates');localStorage.removeItem(storageKey+'-job');
  }
  if(!browsePage&&!recordingSelection)showEmptyRoom();
  studioInitialized=true;
  if(!browsePage&&location.hash==='#listening-room')requestAnimationFrame(()=>{
   const target=$('listening-room');target.scrollIntoView({block:'start'});target.focus({preventScroll:true});
  });
 }catch(exc){error(exc.message)}finally{studioStarting=false;controls()}
}
startStudio();setInterval(()=>{if(studioInitialized)health();else startStudio()},5000);

function buildBackendSettings(){
 $('backend-settings').replaceChildren();
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha']){
  const spec=caps.controls[caps.backend]?.[key];if(!spec)continue;
  const names={exaggeration:'Exaggeration',cfg_weight:'Voice guidance',duration_factor:'Duration factor · higher is slower',interval_silence:'Pause between generated segments (ms)',emotion_alpha:'Emotion blend'};
  const label=el('label',names[key]);label.htmlFor=key;const input=el('input');input.id=key;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='interval_silence'?50:.05;input.value=spec.default;
  const field=el('div',null,'control-field');field.id=key+'-field';field.append(label,input);$('backend-settings').append(field);
 }
 for(const key of ['steps','guidance','temperature'])if(caps.controls[key])$(key).value=caps.controls[key].default;
 for(const option of $('max-tokens').options){option.disabled=Number(option.value)>caps.controls.max_tokens.max;option.textContent=option.value+(caps.backend==='moss'?' audio frames':option.value==='160'?' tokens · short test':' audio tokens')}
 $('max-tokens').value=String(caps.controls.max_tokens.ui_default??caps.controls.max_tokens.default);
 if(caps.backend==='higgs')$('higgs-text-chunk-size').value=caps.controls.higgs.text_chunk_size.default;

}
$('emotion-reference').onchange=async()=>{
 const file=$('emotion-reference').files[0];if(!file)return;
 uploading=true;controls();
 try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);const record=await api('/api/references',{method:'POST',body});$('emotion-reference-id').value=record.id;$('emotion-reference-info').textContent=record.name+' · '+record.duration+'s';$('emotion-vector').value='';$('index-emotion-mode').value='audio';updateIndexEmotion(true);pendingKey=null;persist()}
 catch(exc){error(exc.message)}finally{uploading=false;controls()}
};
$('clear-emotion-reference').onclick=()=>{$('emotion-reference-id').value='';$('emotion-reference').value='';$('emotion-reference-info').textContent='';$('index-emotion-mode').value='follow';updateIndexEmotion();pendingKey=null;persist()};


function spokenTranscript(row){const backend=row.backend||caps.backend;return (backend==='fish'?row.text.replace(/\[[^\[\]]*\]\s*/g,''):backend==='higgs'?row.text.replace(/<\|[^|>]+\|>\s*/g,''):row.text).trim()}
function savedTranscript(row){return row.duration<=30&&!row.possibly_truncated?spokenTranscript(row):''}
function readIndexVector(){return ['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm'].map(name=>Number($('index-vector-'+name)?.value||0))}
function updateIndexEmotion(chosen=false){
 if(caps?.backend!=='indextts')return;
 const source=$('index-emotion-mode').value;
 $('index-audio-options').hidden=source!=='audio';$('index-vector-options').hidden=source!=='vector';
 if($('emotion_alpha-field'))$('emotion_alpha-field').hidden=source==='follow';
 if(chosen&&source!=='follow')$('emotion_alpha').value='.4';
 const sum=readIndexVector().reduce((a,b)=>a+b,0);$('emotion-vector-sum').textContent=`Total ${sum.toFixed(2)} / 1${sum>1?' — reduce the mixture':''}`;
}
$('index-emotion-mode').onchange=()=>{updateIndexEmotion(true);pendingKey=null;persist()};
function normalizedControl(value){return value.trim().toLowerCase().replaceAll('_',' ').replace(/\s+/g,' ')}
function higgsControlValue(key){
 const input=$('higgs-'+key);if(!input)return '';
 const value=normalizedControl(input.value);if(!value||value==='follow recording')return '';
 return caps.controls.higgs[key].values.find(option=>normalizedControl(option)===value);
}
function unresolvedHiggsControls(){return caps?.backend==='higgs'?['emotion','prosody','style'].filter(key=>$('higgs-'+key)&&higgsControlValue(key)===undefined):[]}
function suspendHiggsDirections(){for(const key of unresolvedHiggsControls()){suspendedHiggsDirections[key]=$('higgs-'+key).value;$('higgs-'+key).value=higgsSelectedControls[key]||'';}}
function updateHiggsSuggestions(){
 if(caps?.backend!=='higgs')return;
 const llm=!!caps.limits.prompt_expansion?.enabled,enabled=llm&&$('prompt-expansion-enabled').checked;
 for(const key of ['emotion','prosody','style']){
  const input=$('higgs-'+key),button=$('higgs-'+key+'-suggest'),hint=$('higgs-'+key+'-hint');if(!button)continue;
  const custom=higgsControlValue(key)===undefined;
  if(!custom)higgsSelectedControls[key]=higgsControlValue(key);
  button.hidden=!custom;button.disabled=!online||!enabled||!modelChoiceReady||busy||enhancing||uploading||submitting||changingModel;
  input.setAttribute('aria-invalid',String(custom));
  hint.textContent=custom?(!llm?'Custom wording needs the local LLM. Enable it at startup, or choose a matching option from the list.':!enabled?'Turn on Prompt expansion to suggest a control for this direction, or choose an option from the list.':'Custom direction: preview a supported '+key+' with Suggest control, then apply it before generating.'):'Search options or type a direction. Leave empty to follow the recording.';
 }
 if($('higgs-directions-notice'))$('higgs-directions-notice').hidden=!Object.keys(suspendedHiggsDirections).length;
}
function higgsControlPicker(key,spec){
 const id='higgs-'+key,field=el('div',null,'control-field'),label=el('label',key[0].toUpperCase()+key.slice(1));label.htmlFor=id;
 const wrapper=el('div',null,'control-picker'),input=el('input');input.id=id;input.type='text';input.maxLength=500;input.autocomplete='off';input.placeholder='Follow recording';
 input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');input.setAttribute('aria-controls',id+'-options');input.setAttribute('aria-describedby',id+'-hint');
 const toggle=el('button','⌄','control-picker-toggle');toggle.type='button';toggle.setAttribute('aria-label','Show '+key+' options');toggle.setAttribute('aria-controls',id+'-options');toggle.setAttribute('aria-expanded','false');
 const list=el('ul',null,'control-picker-options');list.id=id+'-options';list.hidden=true;list.setAttribute('role','listbox');list.setAttribute('aria-label',label.textContent+' options');
 const items=[{value:'',label:'Follow recording'},...spec.values.map(value=>({value,label:value.replaceAll('_',' ')}))];let shown=[],active=-1;
 function close(){list.hidden=true;input.setAttribute('aria-expanded','false');toggle.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;}
 function highlight(){const options=[...list.querySelectorAll('[role="option"]')];options.forEach((node,index)=>node.setAttribute('aria-selected',String(index===active)));if(active>=0){input.setAttribute('aria-activedescendant',options[active].id);options[active].scrollIntoView({block:'nearest'})}else input.removeAttribute('aria-activedescendant');}
 function choose(value){input.value=value;close();input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();close();}
 function open(all=false){
  const query=all?'':normalizedControl(input.value);shown=items.filter(item=>normalizedControl(item.label).includes(query));active=-1;list.replaceChildren();
  shown.forEach((item,index)=>{const option=el('li',item.label);option.id=id+'-option-'+index;option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.onpointerdown=event=>event.preventDefault();option.onclick=()=>choose(item.value);list.append(option)});
  if(!shown.length)list.append(el('li','No matching controls. Preview a suggestion below.','control-picker-empty'));
  list.hidden=false;input.setAttribute('aria-expanded','true');toggle.setAttribute('aria-expanded','true');input.removeAttribute('aria-activedescendant');
 }
 input.oninput=()=>open();input.onfocus=()=>open(!input.value||higgsControlValue(key)!==undefined);
 input.onkeydown=event=>{
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(list.hidden)open();if(shown.length){active=active<0?(event.key==='ArrowDown'?0:shown.length-1):(active+(event.key==='ArrowDown'?1:-1)+shown.length)%shown.length;highlight()}}
  else if(event.key==='Enter'&&!list.hidden){event.preventDefault();if(active>=0)choose(shown[active].value);else if(shown.length===1)choose(shown[0].value);else close();}
  else if(event.key==='Escape'&&!list.hidden){event.preventDefault();event.stopPropagation();close();}
 };
 toggle.onclick=()=>{if(list.hidden){input.focus();open(true)}else close()};
 wrapper.addEventListener('focusout',event=>{if(!wrapper.contains(event.relatedTarget))close()});
 const hint=el('p',null,'hint');hint.id=id+'-hint';
 const suggest=el('button','Suggest control','secondary control-suggest');suggest.type='button';suggest.id=id+'-suggest';suggest.setAttribute('aria-label','Suggest '+key+' control');suggest.hidden=true;suggest.onclick=()=>previewGuidance(key);
 wrapper.append(input,toggle,list);field.append(label,wrapper,hint,suggest);return field;
}
function buildPerformanceControls(){
 higgsSelectedControls={};suspendedHiggsDirections={};
 $('performance-guidance').textContent=caps.limits.performance_guidance||'';
 $('performance-controls').replaceChildren();$('backend-presets').replaceChildren();
 $('pacing-settings').replaceChildren();
 $('rendered-preview-field').hidden=!['fish','higgs'].includes(caps.backend);
 if(caps.backend==='chatterbox'){
  const label=el('label','Delivery candidate');label.htmlFor='chatterbox-delivery-preset';const select=el('select');select.id='chatterbox-delivery-preset';
  for(const [id,name] of [['custom','Current settings'],['default','Recommended · provisional'],['neutral','Upstream neutral · 0.5'],['deliberate','More deliberate'],['expressive','More expressive · experimental']])select.append(new Option(name,id));
  select.onchange=()=>{const values={default:[caps.controls.chatterbox.exaggeration.default,caps.controls.chatterbox.cfg_weight.default],neutral:[.5,.5],deliberate:[.5,.3],expressive:[.7,.3]}[select.value];if(values){$('exaggeration').value=values[0];$('cfg_weight').value=values[1];$('temperature').value='.8';pendingKey=null;persist()}};
  const field=el('div',null,'control-field');field.append(label,select);$('backend-presets').append(field);
 }
 if(caps.backend==='indextts'){
  for(const [name,value] of [['Default duration',1],['Slightly slower · 1.1',1.1]]){const button=el('button',name,'secondary');button.type='button';button.onclick=()=>{$('duration_factor').value=value;pendingKey=null;persist()};$('backend-presets').append(button)}
  $('emotion-vector-fields').replaceChildren();
  for(const name of ['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm']){
   const label=el('label',name[0].toUpperCase()+name.slice(1));label.htmlFor='index-vector-'+name;
   const input=el('input');input.id=label.htmlFor;input.type='number';input.min=0;input.max=1;input.step=.05;input.value=0;input.oninput=()=>updateIndexEmotion();
   const field=el('div',null,'control-field');field.append(label,input);$('emotion-vector-fields').append(field);
  }updateIndexEmotion();
 }
 if(caps.backend==='fish'){
  const label=el('label','Delivery tags · one per line');label.htmlFor='fish-delivery-tags';const input=el('textarea');input.id=label.htmlFor;input.maxLength=244;input.placeholder='low voice';
  const field=el('div',null,'control-field');field.append(label,input,el('p','Up to four short directions. Brackets are added for you. Leave empty to follow the reference; acting can change the accent.','hint'));$('performance-controls').append(field);
 }
 if(caps.backend==='higgs')for(const key of ['emotion','prosody','style'])$('performance-controls').append(higgsControlPicker(key,caps.controls.higgs[key]));
 if(caps.backend==='higgs'){const notice=el('p','Custom directions are paused while Prompt expansion is off. The selected controls or Follow recording will be used. Turn it on to return to your drafts.','hint');notice.id='higgs-directions-notice';notice.hidden=true;$('performance-controls').append(notice);}
 if(caps.controls[caps.backend]?.top_p){
  const details=el('details'),summary=el('summary','Advanced sampling');details.append(summary);
  const fields=el('div',null,'sampling-fields control-stack');
  for(const [key,id,name] of [['top_p','model-top-p','Nucleus sampling'],['top_k','model-top-k','Sampling candidates']]){const spec=caps.controls[caps.backend][key];const label=el('label',name);label.htmlFor=id;const input=el('input');input.id=id;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='top_p'?.05:1;input.value=spec.default;const field=el('div',null,'control-field');field.append(label,input);fields.append(field)}
  details.append(fields);
  $('performance-controls').append(details);
 }
 if(caps.controls[caps.backend]?.punctuation_pauses){
  const label=el('label'),input=el('input');input.id='punctuation-pauses';input.type='checkbox';input.checked=true;input.style.width='auto';input.style.marginRight='8px';
  label.append(input,document.createTextNode('Longer punctuation pauses'));$('pacing-settings').append(label);
  const fields=el('div',null,'two');
  for(const [key,name] of [['sentence_pause_ms','Extra pause after sentences (ms)'],['comma_pause_ms','Extra pause after commas (ms)']]){
   const spec=caps.controls[caps.backend][key],field=el('div',null,'control-field'),label=el('label',caps.backend==='moss'?name.replace('Extra pause','Pause target'):name),number=el('input');label.htmlFor=key;number.id=key;number.type='number';number.min=spec.min;number.max=spec.max;number.step=50;number.value=spec.default;field.append(label,number);fields.append(field);
  }
  const disabledHint=el('p',null,'hint');disabledHint.id='pacing-disabled-hint';disabledHint.hidden=true;
  $('pacing-settings').append(fields,disabledHint,el('p',caps.backend==='moss'?'Targets guide the native model in a single take to preserve voice identity. Exact millisecond pauses are not guaranteed.':'Keeps the same reference voice for each phrase and adds these gaps to its natural pauses. Phrase delivery can differ from a whole-line take. Disable for continuous delivery. Voice design and manual Fish tags use whole-line delivery.','hint'));
  for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).setAttribute('aria-describedby','pacing-disabled-hint');
  input.onchange=updatePacingControls;
 }
}
function pacingAvailable(){return (caps?.backend==='moss'||mode!=='design')&&!(caps?.backend==='fish'&&/\[[^\[\]]*\]/.test($('text').value))}
function updatePacingControls(){
 if(!$('punctuation-pauses'))return;
 const available=pacingAvailable();$('punctuation-pauses').disabled=!available;
 const reason=!available?(mode==='design'?'Disabled because this model uses whole-line delivery for voice design. Record lines with a saved voice or recording to configure pauses.':'Disabled because manual Fish tags use whole-line delivery. Remove the manual tags to configure punctuation pauses.'):!$('punctuation-pauses').checked?'Disabled because Longer punctuation pauses is off. Turn it on to edit sentence and comma waits.':'';
 const placeholder=!available?(mode==='design'?'Design mode':'Manual tags active'):'Enable pauses';
 for(const key of ['sentence_pause_ms','comma_pause_ms']){$(key).disabled=!!reason;$(key).title=reason;disabledPlaceholder($(key),placeholder);}
 $('pacing-disabled-hint').textContent=reason;$('pacing-disabled-hint').hidden=!reason;
}
function restorePerformance(row){
 const options=row[caps.backend]||{};
 if($('punctuation-pauses')){$('punctuation-pauses').checked=options.punctuation_pauses??true;for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).value=options[key]??caps.controls[caps.backend][key].default;updatePacingControls();}
 if(caps.backend==='fish')$('fish-delivery-tags').value=(options.delivery_tags||[]).join('\n');
 if(caps.backend==='higgs'){
  higgsSelectedControls={...Object.fromEntries(['emotion','prosody','style'].map(key=>[key,options[key]||''])),...row.higgs_selected_controls};
  suspendedHiggsDirections={...row.higgs_suspended_directions};
  for(const key of ['emotion','prosody','style'])$('higgs-'+key).value=row.higgs_direction_drafts?.[key]??options[key]??'';
  if(!caps.limits.prompt_expansion?.enabled||!$('prompt-expansion-enabled').checked)suspendHiggsDirections();
  $('higgs-text-chunk-size').value=options.text_chunk_size??caps.controls.higgs.text_chunk_size.default;
 }
 if(caps.controls[caps.backend]?.top_p){$('model-top-p').value=options.top_p??caps.controls[caps.backend].top_p.default;$('model-top-k').value=options.top_k??caps.controls[caps.backend].top_k.default;}
 if(caps.backend==='indextts'){
  const vector=options.emotion_vector||row.emotion_vector,reference=options.emotion_reference_id||row.emotion_reference_id;
  $('index-emotion-mode').value=vector?'vector':reference?'audio':'follow';
  ['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm'].forEach((name,index)=>$('index-vector-'+name).value=vector?.[index]||0);
  updateIndexEmotion();
 }
 updatePerformancePreview();
}
function updatePerformancePreview(){
 updatePacingControls();
 updateHiggsSuggestions();
 if(!caps||!['fish','higgs'].includes(caps.backend))return;
 let text=$('text').value;
 try{
  if(caps.backend==='fish'){
   const tags=$('fish-delivery-tags').value.split('\n').map(x=>x.trim()).filter(Boolean);
   if(tags.length>4||tags.some(x=>x.length>60||/[\[\]<>|{}\x00-\x1f\x7f]/.test(x)))throw Error('Use up to four tags without brackets or control delimiters.');
   if(tags.length&&/^\s*\[/.test(text))throw Error('Choose structured tags or manual leading tags, not both.');
   const manual=[...text.matchAll(/\[([^\[\]]+)\]/g)].map(x=>x[1].trim().toLowerCase());
   if(new Set(tags.map(x=>x.toLowerCase())).size!==tags.length||tags.some(x=>manual.includes(x.toLowerCase())))throw Error('A delivery tag is duplicated.');
   text=tags.map(x=>'['+x+'] ').join('')+text;
  }else{
   const leading=text.match(/^\s*((?:<\|[^|>]+\|>\s*)+)/)?.[0]||'';let prefix='';
   for(const category of ['emotion','prosody','style']){const value=higgsControlValue(category);if(value===undefined)throw Error('Custom '+category+' direction: choose an option or preview and apply Suggest control before generating.');if(value){if(leading.includes('<|'+category+':'))throw Error('Choose a structured or manual leading '+category+' directive, not both.');prefix+='<|'+category+':'+value+'|>';}}
   if(prefix)text=prefix+' '+text;
  }
  $('rendered-preview').textContent=text;
 }catch(exc){$('rendered-preview').textContent=exc.message}
}

$('apply-recommended').onclick=()=>{
 if(!caps)return;
 clearExpansion();
 $('max-tokens').value=String(caps.controls.max_tokens.ui_default??caps.controls.max_tokens.default);
 for(const key of ['steps','guidance','temperature'])if(caps.controls[key])$(key).value=caps.controls[key].default;
 const scoped=caps.controls[caps.backend]||{};
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence'])if(scoped[key])$(key).value=scoped[key].default;
 if(caps.backend==='chatterbox')$('chatterbox-delivery-preset').value='default';
 if(caps.backend==='fish')$('fish-delivery-tags').value='';
 if(caps.backend==='higgs'){
  higgsSelectedControls={};suspendedHiggsDirections={};
  for(const key of ['emotion','prosody','style'])$('higgs-'+key).value='';
  $('higgs-text-chunk-size').value=scoped.text_chunk_size.default;
 }
 if(scoped.top_p){$('model-top-p').value=scoped.top_p.default;$('model-top-k').value=scoped.top_k.default;}
 if(scoped.punctuation_pauses){$('punctuation-pauses').checked=true;for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).value=scoped[key].default;updatePacingControls();}
 if(caps.backend==='indextts'){$('index-emotion-mode').value='follow';updateIndexEmotion();}
 $('clone-mode').value='auto';updateCapabilities();pendingKey=null;updatePerformancePreview();persist();announce('Recommended model performance defaults applied. Voice and dialogue kept.');
};

$('open-queue').onclick=()=>{$('queue-error').hidden=true;$('queue-dialog').showModal();refreshQueue().catch(exc=>error(exc.message))};
$('close-queue').onclick=()=>$('queue-dialog').close();
async function openRecording(selection){
 selection=VoiceRecording.resolveSelection(selection,recordingStore?.selection());
 recordingSelection=selection;generationClock=selection.timing||null;updateElapsed();
 if(selection.kind==='candidates')watchCandidates(selection.id,null,selection.selected);
 else if(selection.kind==='job')watch(selection.id);
 else{
  try{const row=await api('/api/artifacts/'+selection.id);showResult(row);if(!recordingSelection.summary){recordingSelection.summary={status:'done',backend:row.backend,count:1,ready:1};recordingStore?.save(recordingSelection);showReturnRecording(recordingSelection,recordingSelection.summary)}}
  catch(exc){if(exc.status===404){recordingStore?.clear();recordingSelection=null;showReturnRecording(null);showEmptyRoom();history.replaceState(null,'','/');error('This recording is no longer available. Choose another take from Collection.')}else throw exc}
 }
}
async function openPageSelection(){
 if(observabilityPage)return true;
 const params=new URLSearchParams(location.search);
 if(collectionPage){$('show-deleted').checked=params.get('deleted')==='1';setCollection(params.get('tab')==='history'?'history':'voices');return true}
 const selection=VoiceRecording.fromURL(location.search);
 if(selection){await openRecording(selection);return true}
 if(VoiceRecording.validId(params.get('voice'))){const row=await api('/api/voices/'+params.get('voice'));chooseVoice(row,false);return true}
 if(VoiceRecording.validId(params.get('reuse'))){const row=await api('/api/artifacts/'+params.get('reuse'));restore(row);showResult(row);return true}
 return false;
}

const controlHelp={
 'queue-clear':'Removes every pending job across models. The active job, finished audio and History are kept.',
 'queue-processing':'On: waiting jobs run in order. Off: the queue pauses after the active job finishes and waiting jobs stay saved. Generate immediately and Run Now jobs still run while paused. The setting is shared with the queue API and kept after restarts.',
 'show-deleted':'Show recordings deleted within the last 24 hours. Restore brings them back before that deadline. After 24 hours the generator removes their audio and metadata from disk, waiting for unfinished jobs that still need the files.',
 'record-source':'Choose a saved character, upload a reference recording, or use a built-in speaker when available. The same Record lines form handles each source.',
 description:'Describe the voice you want: pitch, age, accent and vocal texture. This designs a voice; save a take to reuse its recording.',
 voice:'Reuse a recording from your voice library for new dialogue. The recording supplies the speaker identity; catalog notes help you find it.',
 reference:'Upload 1–30 seconds of clean speech, up to 25 MB. A sample that already has the intended accent and delivery is a useful starting point.',
 'preset-voice':'Choose a built-in Qwen speaker. You can direct its delivery without designing a new speaker.',
 'steering-emotion':'An experimental Qwen preset control that adds a learned emotion direction. It can also change the voice or pronunciation.',
 'steering-strength':'How strongly the experimental emotion direction is applied, from 0–12. Stronger settings can alter the speaker or pronunciation.',
 'clone-mode':'Auto follows the recorded performance when it has an exact transcript and no new acting directions. Flexible reference accepts new directions; combined and continuation follow the recording.',
 'ref-text':'The exact words already spoken in the reference, not your new dialogue. Leave blank to use a saved transcript. Higgs and Fish require one; Vox combined and continuation also need it.',
 accent:'Describe the accent you want in supported design or delivery modes. A matching reference recording usually provides a stronger accent cue than written instructions.',
 'emotion-preset':'Choose a starting acting direction, then edit the wording below. Available delivery controls depend on the speech model and voice source.',
 text:'The words the character should say. Start with a short passage so you can compare delivery and check that every word was spoken.',
 'index-emotion-mode':'Follow the speaker recording, transfer emotion from another recording, or mix named emotions. The original speaker reference still supplies the voice.',
 'emotion-reference':'A separate recording that supplies emotion for IndexTTS. It changes delivery while the original speaker recording remains the voice reference.',
 'rendered-preview':'Shows the text and any supported performance tags sent to the speech model. Your original dialogue is kept separately.',
 seed:'The random seed used for this take. By default the site chooses a new seed for each generation. Turn off Random seed each generation to enter a fixed number for comparisons. The same seed alone does not preserve a voice across different lines.',
 'random-seed':'Choose a fresh random 32-bit seed for each new Generate click, using the browser’s secure random generator. Turn off to enter a fixed seed. Retries keep the original seed so an uncertain response does not create duplicate work.',
 steps:'Vox generation steps, from 1–30. More steps take longer and do not always produce a better take; the default is 10.',
 guidance:'How strongly Vox follows its voice conditioning and directions, from 1–5. Higher is not always better; compare against the default of 2.',
 'max-tokens':()=>caps?.backend==='moss'?'A ceiling on generated audio frames, not a requested duration. MOSS can raise it to its text-based minimum.':'A safety limit on generated audio, not a requested duration. Reaching it can cut a line short; try a shorter passage or a higher limit.',
 temperature:'Controls sampling variation. Lower values narrow the choices; higher values allow more variation and can reduce consistency. Start with this model’s default.',
 language:'The language of the new dialogue. Available languages depend on the model; this does not choose a regional accent.',
 'output-dir':'An absolute folder on the Mac running the service. Leave blank for the configured output folder; an external drive must be connected.',
 'prompt-expansion-enabled':'Use a small local language model to refine voice and acting directions. Preview and apply guidance for a take; supported voice-design batches can plan different options automatically. Spoken words stay unchanged.',
 'expansion-guidance':'Describe the delivery you want the local language model to help refine. Review its suggestion before applying it; it does not listen to your recording.',
 'candidate-count':'Generate 1–5 options, one at a time. Recorded voices produce alternate takes; supported voice-design modes can explore different voices. The default is two.',
 'candidate-variation':'Distinct designs keep your first description and explore contrasting voice traits for later options. Same prompt changes only the seed. Explicit accent and acting directions remain constraints.',
 'generate-immediately':'Run next after the current batch, ahead of ordinary waiting work. It does not interrupt active generation. Turn off to join the normal queue.',
 'voice-name':'A name for this reusable voice in Collection. Saving it lets you select its recording for later lines.',
 search:'Search generated History or saved voices in the current Collection tab. Voice search includes names, descriptions and tags.',
 'tag-filter':'Select existing tags to show voices matching any selected tag. Clear the selection to show all voices. Tags organize the library; they do not change the generated accent or performance.',
 'voice-sort':'Choose how saved voices are ordered: newest, recently updated, or name.',
 'page-size':'How many entries to show on each Collection page.',
 'queue-status':'Filter the saved jobs by status. Pending includes waiting and running jobs; other models’ jobs wait until that model is served.',
 'queue-size':'How many queue entries to show on each page.',
 'voice-auto-transcribe':'Transcribe this recording locally with a small CPU model. Review names and accented words before saving. Supplied or edited words are kept.',
 'voice-transcription-language':'Auto-detect the spoken language, or choose one to help recognition. This does not change the accent or translate the words.',
 'voice-upload-file':'Import 1–30 seconds of speech into Collection, up to 25 MB. The original recording is preserved; uploading does not train a model.',
 'edit-voice-name':'A searchable name for the saved voice. Renaming it does not change its recording.',
 'edit-voice-description':'Notes about the character for your library. These notes do not replace or change the saved voice recording.',
 'edit-transcript':'The exact words in this saved audio excerpt. Required when using Higgs or Fish; leave empty if uncertain rather than guessing.',
 'edit-voice-tags':'Comma-separated labels for finding this voice later. Use up to 20 tags, with at most 40 characters each.',
 'pause-start':'The start of the entire quiet gap you listened to, in seconds. Select silence inside the recording, not spoken audio.',
 'pause-end':'The end of that quiet gap, in seconds. Listen to the selection before adding it; audible selections are rejected.',
 'pause-minimum':'The minimum total length of each selected gap, in milliseconds. Only missing silence is added; already longer gaps and spoken samples stay unchanged.',
 'pause-output-dir':'Where to save the separate edited recording on the server’s Mac. Leave blank for the configured default; the original stays available.',
 'pause-reviewed':'Confirm you listened to every selected gap and checked it is silence between phrases. The tool does not find sentence boundaries for you.',
 exaggeration:'Chatterbox’s expression strength, from 0–1. Higher values can change pacing or accent, so compare with the recommended default.',
 cfg_weight:'Chatterbox’s guidance strength, from 0–1. It affects how generation follows its conditioning; compare settings while keeping the same reference.',
 duration_factor:'IndexTTS timing factor: values above 1 are slower and values below 1 are faster. It changes delivery length rather than adding an exact gap.',
 interval_silence:'Silence between IndexTTS’s generated segments, in milliseconds. Segments are not necessarily sentences; use punctuation pauses for sentence and comma gaps.',
 emotion_alpha:'How strongly IndexTTS blends the chosen emotion recording or mixture. Requires an emotion source; stronger settings may change the performance more.',
 'chatterbox-delivery-preset':'Starting combinations of Chatterbox expression and guidance. The more expressive option is experimental and may weaken the accent.',
 'fish-delivery-tags':'Up to four short acting cues, one per line. Brackets are added for you. Leave empty to follow the reference; extra acting can change its accent.',
 'higgs-emotion':'Search the supported emotions or type your own acting direction. Suggest control uses the local LLM to preview a recognized emotion; Apply confirms it. Empty follows the recording. Arbitrary text is never inserted as a Higgs tag.',
 'higgs-prosody':'Search speed, pitch and expression controls, or type a direction and preview a supported control with the local LLM. These guide performance rather than setting an exact duration.',
 'higgs-style':'Search singing, shouting and whispering, or type a style direction. Suggest control previews a supported style with the local LLM. Empty follows the recording; unsupported styles can return no suggestion.',
 'higgs-text-chunk-size':'The native runtime divides long text near this character budget, up to 1024. Smaller chunks need fewer audio frames per chunk, but may change pacing, expression and how global tags apply. Start with 1024; try 300–500 if longer text cannot finish.',
 'model-top-p':'How broad a range of likely next sounds the model considers. Lower values narrow the choices; higher values allow more variety.',
 'model-top-k':'Limits the number of likely next choices considered at each step. Start with the model’s default and change one sampling setting at a time.',
 'punctuation-pauses':()=>caps?.backend==='moss'?'Ask MOSS for longer sentence and comma pauses in one complete take. The timings are approximate targets, not measured inserted silence.':'Generate separate phrases with the same voice and add silence at punctuation. Turn off for continuous delivery; phrase generation can change the flow.',
 sentence_pause_ms:()=>caps?.backend==='moss'?'Approximate total sentence-pause target in milliseconds for MOSS. Exact timing is not guaranteed; zero omits this pause target.':'Extra silence after sentence punctuation, in milliseconds, on top of the model’s natural pauses. Zero skips sentence splitting; final punctuation adds no trailing gap.',
 comma_pause_ms:()=>caps?.backend==='moss'?'Approximate total comma-pause target in milliseconds for MOSS. Exact timing is not guaranteed; zero omits this pause target.':'Extra silence after commas, in milliseconds, on top of natural pauses. Zero skips comma splitting; the value is not an exact total gap length.'
};
for(const name of ['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm']){
 controlHelp['index-vector-'+name]=`Contribution of ${name} to the IndexTTS emotion mixture, from 0–1. The total across all eight emotions must not exceed 1.`;
}

const helpTooltip=el('div',null,'control-help');
helpTooltip.id='control-help-tooltip';helpTooltip.hidden=true;helpTooltip.setAttribute('role','tooltip');helpTooltip.setAttribute('popover','manual');
document.body.append(helpTooltip);
let activeHelp=null,helpHideTimer=null;
function hideControlHelp(){
 clearTimeout(helpHideTimer);
 if(typeof helpTooltip.hidePopover==='function'&&helpTooltip.matches(':popover-open'))helpTooltip.hidePopover();
 helpTooltip.hidden=true;
 if(activeHelp){activeHelp.setAttribute('aria-expanded','false');activeHelp.removeAttribute('aria-describedby');}
 activeHelp=null;
}
function showControlHelp(button){
 clearTimeout(helpHideTimer);
 if(activeHelp!==button)hideControlHelp();
 activeHelp=button;
 const control=$(button.dataset.helpFor),copy=controlHelp[button.dataset.helpFor];helpTooltip.textContent=control?.disabled&&control.title?control.title:typeof copy==='function'?copy():copy;
 const owner=button.closest('dialog[open]')||document.body;
 if(helpTooltip.parentElement!==owner)owner.append(helpTooltip);
 helpTooltip.style.visibility='hidden';helpTooltip.hidden=false;
 if(typeof helpTooltip.showPopover==='function'&&!helpTooltip.matches(':popover-open'))helpTooltip.showPopover();
 const anchor=button.getBoundingClientRect(),width=helpTooltip.offsetWidth,height=helpTooltip.offsetHeight;
 helpTooltip.style.left=Math.max(12,Math.min(anchor.left+anchor.width/2-width/2,innerWidth-width-12))+'px';
 helpTooltip.style.top=(anchor.bottom+height+10<innerHeight?anchor.bottom+7:Math.max(12,anchor.top-height-7))+'px';
 helpTooltip.style.visibility='visible';button.setAttribute('aria-expanded','true');button.setAttribute('aria-describedby',helpTooltip.id);
}
function scheduleHelpHide(){
 clearTimeout(helpHideTimer);
 helpHideTimer=setTimeout(()=>{if(activeHelp&&!activeHelp.matches(':hover,:focus')&&!helpTooltip.matches(':hover'))hideControlHelp()},150);
}
controlHelp['queue-processing-dialog']=controlHelp['queue-processing'];
function helpTrigger(id,name){
 const button=el('button','?','help-trigger');
 button.type='button';button.dataset.helpFor=id;button.setAttribute('aria-label','Help: '+name);button.setAttribute('aria-expanded','false');
 button.addEventListener('pointerenter',()=>showControlHelp(button));button.addEventListener('focus',()=>showControlHelp(button));
 button.addEventListener('pointerleave',scheduleHelpHide);button.addEventListener('blur',scheduleHelpHide);
 button.addEventListener('click',event=>{event.preventDefault();showControlHelp(button)});
 return button;
}
function installControlHelp(){
 for(const id of Object.keys(controlHelp)){
  const control=$(id);if(!control)continue;
  const label=document.querySelector(`label[for="${id}"]`)||control.closest('label');
  if(!label){
   // Unlabelled buttons get their help beside the button itself.
   if(control.tagName==='BUTTON'&&!control.parentElement.classList.contains('help-label-row')){const row=el('span',null,'help-label-row help-toggle-row');control.before(row);row.append(control,helpTrigger(id,control.textContent.trim()))}
   continue;
  }
  if(label.parentElement.classList.contains('help-label-row')){label.parentElement.querySelector('.help-trigger').setAttribute('aria-label','Help: '+label.textContent.trim());continue;}
  const row=el('div',null,'help-label-row'+(label.contains(control)?' help-toggle-row':''));
  label.before(row);row.append(label,helpTrigger(id,label.textContent.trim()));
 }
}
helpTooltip.addEventListener('pointerenter',()=>clearTimeout(helpHideTimer));helpTooltip.addEventListener('pointerleave',scheduleHelpHide);
document.addEventListener('pointerdown',event=>{if(activeHelp&&!activeHelp.contains(event.target)&&!helpTooltip.contains(event.target))hideControlHelp()},true);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&activeHelp){event.preventDefault();event.stopPropagation();hideControlHelp()}},true);
document.addEventListener('close',hideControlHelp,true);
window.addEventListener('resize',hideControlHelp);document.addEventListener('scroll',hideControlHelp,true);
installControlHelp();
controlHelp['voice-design-tab']=()=>readyDesignModels().length?'':caps?.modes.design.unavailable_reason||'Switch to a model which supports voice generation: VoxCPM2, Qwen3-TTS, MOSS VoiceGenerator';
controlHelp['record-lines-tab']='Disabled because no set-up model supports recording lines. Run make setup for a reference-capable model such as Higgs TTS 3 or VoxCPM2.';
for(const tab of document.querySelectorAll('[data-mode]')){
 tab.addEventListener('pointerenter',()=>{if(tab.getAttribute('aria-disabled')==='true')showControlHelp(tab)});
 tab.addEventListener('focus',()=>{if(tab.getAttribute('aria-disabled')==='true')showControlHelp(tab)});
 tab.addEventListener('pointerleave',scheduleHelpHide);tab.addEventListener('blur',scheduleHelpHide);
}
