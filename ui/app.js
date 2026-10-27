
const $=id=>document.getElementById(id);let mode='design',collection='history',reference=null,latest=null,voiceRows=[],historyRows=[],busy=false,online=false,uploading=false,watching=null,submitting=false,queueWritable=true;
const collectionPage=location.pathname.startsWith('/collection'),observabilityPage=location.pathname.startsWith('/observability');
const browsePage=collectionPage||observabilityPage;
document.title=(observabilityPage?'Observability':collectionPage?'Collection':'Studio')+' · Voice Studios';
if(collectionPage){$('page-title').textContent='Your collection';$('page-description').textContent='Find saved voices, listen to takes, and reuse a performance in the studio.';}
$(observabilityPage?'observability-link':collectionPage?'catalog-link':'studio-link').setAttribute('aria-current','page');
let availableModels=[],modelChoiceReady=false,changingModel=false,queuePaused=false,awaitingSourceModel=false,lastModelList=null,pendingModelRestore=null;
function sourceMode(source=mode){return ['saved','upload'].includes(source)?'reference':source}
function acceptsTextDirections(){return ['saved','upload'].includes(mode)?!!caps?.modes.reference.emotion&&!['continuation','combined'].includes($('clone-mode').value):mode==='preset'||!!caps?.modes.design.emotion}
function readySourceModels(source=mode){return availableModels.filter(row=>row.modes?.includes(sourceMode(source)))}
function readyDesignModels(){return readySourceModels('design')}
let pageSize = 24, caps=null, storageKey='voice-studio', historyPage={data:[],total:0,has_more:false}, pendingKey=null;
let voicePage = {data: [], total: 0, has_more: false};
let currentCandidates=null, candidateCards=new Map();
let appliedExpansion=null,pendingExpansion=null,enhancing=false,expansionChoice=null;
let catalogOffset = 0, searchTimer = null, catalogRequest = 0, voiceEditor = null, voiceSaving = false;
const presets={natural:'Natural, conversational delivery with relaxed pacing.',joy:'Excited and joyful, with an audible smile and lively pacing.',sadness:'Quiet sadness and disappointment, speaking slowly with a trembling voice.',anger:'Angry and frustrated, with firm emphasis and a tense, forceful delivery.',fear:'Frightened and anxious, with a trembling voice and urgent, uneven pacing.',relief:'Deeply relieved, softening into warmth with a gentle exhale.',sarcasm:'Dry, understated sarcasm with deliberate pauses and restrained emphasis.',whisper:'A soft, intimate whisper, speaking gently and slowly.',custom:''};
async function api(path,options={}){const response=await fetch(path,options);if(!response.ok){let body;try{body=await response.json()}catch{}throw Error(typeof body?.detail==='string'?body.detail:body?.detail?.[0]?.msg||`Request failed (${response.status})`)}return response.json()}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function error(message){const target=$($('queue-dialog').open?'queue-error':browsePage?'page-error':'error');target.textContent=message;target.hidden=false}function announce(text){$('announcement').textContent=text}
function controls(){ $('generate').disabled=!online||!queueWritable||submitting||uploading||enhancing||changingModel||transcriptSaving||!modelChoiceReady;$('unload').disabled=!online||busy||enhancing;$('new-seed').disabled=busy;updatePromptExpansion() }
function clearExpansion(){appliedExpansion=null;pendingExpansion=null;updatePromptExpansion();}
let generationClock=null;
function elapsedTime(milliseconds){const seconds=Math.max(0,Math.floor(milliseconds/1000)),minutes=Math.floor(seconds/60);return minutes>=60?`${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`:`${minutes}:${String(seconds%60).padStart(2,'0')}`}
function updateElapsed(){
 const target=$('elapsed');target.hidden=!generationClock;if(!generationClock)return;
 target.textContent=`Elapsed ${elapsedTime((generationClock.end??Date.now())-generationClock.start)} · ${generationClock.label}`;
 target.title='Time since this request was accepted, including time waiting in the queue.';
}
function generationTiming(identity,created,label,completed=null){
 const timestamp=Date.parse(created),end=Date.parse(completed);
 generationClock={identity,start:Number.isFinite(timestamp)?timestamp:generationClock?.identity===identity?generationClock.start:Date.now(),end:Number.isFinite(end)?end:null,label};updateElapsed();
}
function batchTiming(batch){
 const running=batch.jobs.find(job=>job.status==='running'),finished=!['queued','running'].includes(batch.status);
 const completed=batch.jobs.map(job=>job.completed_at).filter(Boolean).sort().at(-1);
 generationTiming(batch.id,batch.created_at,running?`${running.stage} · option ${running.candidate_index} of ${batch.candidate_count}`:finished?`${batch.done_count} of ${batch.candidate_count} ready`:'Waiting in queue',finished?(completed||new Date().toISOString()):null);
}
setInterval(updateElapsed,1000);
function updatePromptExpansion(){
 if(!caps)return;
 const available=modelChoiceReady&&!!caps.limits.prompt_expansion?.enabled,enabled=available&&$('prompt-expansion-enabled').checked,backend=caps.backend;
 $('prompt-expansion-enabled').disabled=!available||enhancing;
 updateCandidateVariation();
 const profile=caps.models[backend]?.prompt_expansion,profileMode=['saved','upload'].includes(mode)?'reference':mode;
 const spec=profile?.modes[profileMode];
 const supported=!!spec?.available&&(!spec.clone_modes||spec.clone_modes.includes($('clone-mode').value));
 $('expansion-profile').hidden=!profile||!modelChoiceReady;
 $('expansion-profile-info').textContent=profile?`${profile.name} · ${spec?.label||profileMode} · Profile v${profile.version}`:'';
 $('expansion-profile-rules').textContent=spec?.available?(supported?spec.instructions:'This reference mode follows recorded performance. Choose flexible Voice reference to use delivery instructions.'):spec?.reason||'No expansion specification for this mode.';
 $('expansion-guidance-field').hidden=!enabled||!supported;
 $('expand-guidance').disabled=!online||!enabled||!supported||busy||enhancing||uploading||submitting||!!appliedExpansion;
 $('expand-guidance').textContent=enhancing?'Expanding guidance…':'Preview expanded guidance';
 $('prompt-expansion-hint').textContent=!modelChoiceReady?'Choose a model to see prompt-expansion support.':!available?'LLM disabled at startup. Restart with make serve without NO_LLM=1 to enable.':!enabled?'Off. No LLM previews or per-option planning; distinct designs use built-in directions.':!supported?(spec?.reason||'This mode follows recorded performance. Choose flexible Voice reference to direct a different delivery.'):'On. Design batches use LLM contrast planning. For a single take or reference delivery, preview and apply guidance below. Dialogue and transcripts stay unchanged.';
 $('expansion-applied').hidden=!appliedExpansion;$('restore-guidance').hidden=!appliedExpansion;
 renderExpansion(appliedExpansion,'applied-expansion-details','Applied to your next generation');
 if(appliedExpansion)$('expansion-applied').textContent='Expanded guidance applied · '+appliedExpansion.llm.model+' · '+appliedExpansion.llm.seconds+'s. Original retained.';
}
function applyGuidanceValues(values){
 for(const [key,value] of Object.entries(values)){
  if(key==='fish')$('fish-delivery-tags').value=value.delivery_tags.join('\n');
  else if(key==='higgs')for(const name of ['emotion','prosody','style'])$('higgs-'+name).value=value[name]||'';
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
function renderExpansion(record,id,caption){
 const panel=$(id);panel.hidden=!record;
 if(!record){delete panel.dataset.receipt;panel.querySelector('.expansion-content').replaceChildren();return}
 if(panel.dataset.receipt===record.id)return;
 panel.dataset.receipt=record.id;panel.open=true;
 const content=panel.querySelector('.expansion-content'),original=el('details'),originalText=el('p',guidancePreview(record.original),'expanded-guidance');
 original.append(el('summary','Original guidance'),originalText);
 content.replaceChildren(el('p',caption,'hint'),el('p',guidancePreview(record.updates),'expanded-guidance'),original,
  el('p',`${record.llm.model} · ${record.llm.seconds}s · ${record.llm.policy_version}`+
   (record.llm[record.context.backend]?` · ${record.llm[record.context.backend].label} · Profile v${record.llm[record.context.backend].version}`:''),'hint'));
}
$('expand-guidance').onclick=async()=>{
 if(enhancing)return;enhancing=true;controls();$('error').hidden=true;
 try{
  const {candidate_count,candidate_variation,candidate_use_llm,...generation}=settings();generation.prompt_expansion_id=null;
  pendingExpansion=await post('/api/prompts/expand',{generation,guidance:$('expansion-guidance').value});
  $('expansion-original').textContent=guidancePreview(pendingExpansion.original);
  $('expansion-updates').textContent=guidancePreview(pendingExpansion.updates);
  const selected=pendingExpansion.llm[caps.backend];
  $('expansion-model').textContent=`${pendingExpansion.llm.model} · ${pendingExpansion.llm.seconds}s · ${pendingExpansion.llm.policy_version}`+(selected?` · ${selected.label} · Profile v${selected.version}`:'');
  $('expansion-preview').showModal();
 }catch(exc){error(exc.message)}finally{enhancing=false;controls()}
};
$('cancel-expansion').onclick=()=>{$('expansion-preview').close();pendingExpansion=null};
$('apply-expansion').onclick=()=>{
 if(!pendingExpansion)return;
 applyGuidanceValues(pendingExpansion.updates);appliedExpansion=pendingExpansion;pendingExpansion=null;
 $('expansion-preview').close();pendingKey=null;updatePromptExpansion();persist();announce('Expanded guidance applied. Dialogue and reference transcript kept.');
};
$('prompt-expansion-enabled').onchange=()=>{expansionChoice=$('prompt-expansion-enabled').checked;if(!expansionChoice&&appliedExpansion){applyGuidanceValues(appliedExpansion.original);clearExpansion()}pendingKey=null;updatePromptExpansion();persist()};
$('restore-guidance').onclick=()=>{if(appliedExpansion)applyGuidanceValues(appliedExpansion.original);clearExpansion();pendingKey=null;persist();};
function setMode(next){
 if(caps){
  if(!readySourceModels(next).length)return;
  awaitingSourceModel=caps.modes[sourceMode(next)]?.available===false;
 }
 pendingKey=null;clearExpansion();mode=next;
 if(['saved','upload'].includes(mode)&&$('emotion-preset').value==='natural'&&$('emotion').value===presets.natural){$('emotion').value='';$('emotion-preset').value='custom'}
 for(const button of document.querySelectorAll('[data-mode]'))button.setAttribute('aria-selected',button.dataset.mode===mode);
 for(const name of ['design','saved','upload','preset'])$(name+'-panel').hidden=name!==mode;
 if(mode==='saved')voicePreview();else transcriptVoice=null;
 renderModelChoices();updateCapabilities();updatePromptExpansion();controls();persist();
}

document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{if(button.getAttribute('aria-disabled')==='true'){showControlHelp(button);return}setMode(button.dataset.mode)});
$('emotion-preset').onchange=()=>{clearExpansion();$('emotion').value=presets[$('emotion-preset').value];persist()};$('emotion').oninput=()=>{$('emotion-preset').value='custom';persist()};
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
function settings(){
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=acceptsTextDirections();
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
  for(const key of ['emotion','prosody','style'])if($('higgs-'+key).value)options[key]=$('higgs-'+key).value;
  options.text_chunk_size=Number($('higgs-text-chunk-size').value);
 }
 if(modelControls.top_p){options.top_p=Number($('model-top-p').value);options.top_k=Number($('model-top-k').value);}
 if(modelControls.punctuation_pauses){options.punctuation_pauses=$('punctuation-pauses').checked&&pacingAvailable();for(const key of ['sentence_pause_ms','comma_pause_ms'])options[key]=Number($(key).value);}
 if(Object.keys(options).length)result[caps.backend]=options;
 return result;
}
function persist(){if(caps&&!awaitingSourceModel)try{localStorage.setItem(storageKey+'-form',JSON.stringify({...settings(),ref_text:$('ref-text').value,prompt_expansion_choice:expansionChoice,prompt_expansion:appliedExpansion,description:$('description').value,accent:$('accent').value,emotion:$('emotion').value,mode,preset:$('emotion-preset').value}))}catch{}}
function updateCapabilities(){
 if(!caps)return;
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=acceptsTextDirections();
 $('emotion').disabled=$('emotion-preset').disabled=$('accent').disabled=!direction;
 $('emotion-hint').textContent=referenceMode&&caps.backend==='voxcpm2'&&$('clone-mode').value==='auto'?'Leave directions empty to follow the transcribed recording. Enter an emotion or accent to use flexible voice conditioning.':referenceMode&&['continuation','combined'].includes($('clone-mode').value)?'Unavailable with Combined or Continue performance: these follow the recorded delivery. Change Reference conditioning to Auto or Voice reference to enter new emotion or accent directions.':direction?'Direct the performance in words. Voice identity and expression are approximate.':'This model takes its voice from the recording. Use its model-specific controls to direct delivery.';
 $('accent-hint').textContent=direction?'A prompt direction, not a trained accent pack. A recording with the desired accent is usually the stronger cue.':referenceMode&&caps.backend==='voxcpm2'?'Accent direction is unavailable with Combined or Continue performance. Choose Auto or Voice reference to edit it.':`Accent direction is unavailable for ${caps.name} reference cloning; the recording supplies the accent.`;
 const structured=referenceMode&&['higgs','fish'].includes(caps.backend);
 $('accent-direction-field').hidden=structured;
 $('emotion-direction-field').hidden=structured;
 $('recorded-accent-hint').hidden=!structured;
 $('recorded-accent-hint').textContent=structured?`${caps.name} uses the recording as its accent cue; the accent can still drift. ${caps.backend==='higgs'?'Use Emotion, Prosody and Style above for delivery. Compare Follow recording before adding acting tags; free-text accent instructions are unsupported.':'Use delivery tags above for expression. Choose VoxCPM2 for editable accent directions.'}`:'';
 for(const key of ['steps','guidance','temperature'])$(key+'-field').hidden=!caps.controls[key];
 document.querySelector('[data-mode="preset"]').hidden=!readySourceModels('preset').length;
 const designTab=document.querySelector('[data-mode="design"]'),designDisabled=!readyDesignModels().length;
 designTab.hidden=false;designTab.setAttribute('aria-disabled',String(designDisabled));
 designTab.dataset.helpFor='voice-design-tab';
 if(designDisabled&&mode==='design')modelChoiceReady=false;
 for(const name of ['saved','upload'])document.querySelector('[data-mode="'+name+'"]').hidden=!readySourceModels('upload').length;
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
 if(awaitingSourceModel){$('emotion-hint').textContent=$('accent-hint').textContent='Choose a model to see its supported performance controls.';$('performance-guidance').hidden=true;$('inline-hint').hidden=true;$('expansion-profile').hidden=true;$('performance-controls').hidden=true;$('rendered-preview-field').hidden=true;$('backend-settings').hidden=true;$('license-notice').hidden=true;$('model-badge').textContent='Choose a model for this voice source';}
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
 restorePerformance(row);$('ref-text').value=row.ref_text??(mode==='saved'?voiceRows.find(v=>v.id===$('voice').value)?.transcript:reference?.transcript)??'';rememberTranscriptDraft();appliedExpansion=row.candidate_variant?null:row.prompt_expansion||null;updateCapabilities();updatePromptExpansion();count();persist();
}
$('form').addEventListener('input',event=>{if(['description','emotion','accent','voice','reference','ref-text','clone-mode','preset-voice','fish-delivery-tags','higgs-emotion','higgs-prosody','higgs-style'].includes(event.target.id))clearExpansion();pendingKey=null;count();updatePerformancePreview();persist()});$('form').addEventListener('change',event=>{if(['voice','reference','clone-mode','preset-voice','higgs-emotion','higgs-prosody','higgs-style'].includes(event.target.id))clearExpansion();pendingKey=null;persist()});
$('new-seed').onclick=()=>{pendingKey=null;$('seed').value=crypto.getRandomValues(new Uint32Array(1))[0];persist();announce('New seed selected')};
const transcriptDrafts=new Map();let transcriptVoice=null,transcriptSaving=false;
function rememberTranscriptDraft(){if(mode==='saved'&&transcriptVoice)transcriptDrafts.set(transcriptVoice,$('ref-text').value);updateTranscriptActions()}
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
function voicePreview(){const row=voiceRows.find(v=>v.id===$('voice').value);$('voice-preview').hidden=!row;if(row){if(mode==='saved'&&transcriptVoice!==row.id){transcriptVoice=row.id;$('ref-text').value=transcriptDrafts.get(row.id)??row.transcript??''}if($('voice-preview').getAttribute('src')!==row.audio_url)$('voice-preview').src=row.audio_url;$('voice-hint').textContent=row.description||'Reference identity is reused; use the supported delivery controls below.'}else transcriptVoice=null;updateTranscriptActions()}
$('voice').onchange=()=>{voicePreview();persist()};
async function refresh() {
  const [history, picker, tags] = await Promise.all([
    api('/api/history?'+new URLSearchParams({q:collection==='history'?$('search').value:'',limit:pageSize,offset:collection==='history'?catalogOffset:0})), api('/api/voices?limit=100'), api('/api/voice-tags')
  ]);
  historyPage=history;historyRows = history.data;
  let selected = $('voice').value;
  if (!selected) {
    try { selected = JSON.parse(localStorage.getItem(storageKey+'-form') || 'null')?.voice_id || ''; } catch {}
  }
  voiceRows = picker.data;
  if (selected && !voiceRows.some(row => row.id === selected)) {
    try { voiceRows.push(await api('/api/voices/' + selected)); } catch {}
  }
  $('voice').replaceChildren(new Option('Choose a saved voice…', ''));
  for (const row of voiceRows) $('voice').append(new Option(row.name, row.id));
  $('voice').value = selected;
  voicePreview();
  const filter = $('tag-filter').value;
  $('tag-filter').replaceChildren(new Option('All tags', ''));
  for (const tag of tags.data) $('tag-filter').append(new Option(`${tag.name} (${tag.count})`, tag.name));
  $('tag-filter').value = filter;
  await loadVoicePage();
}
function el(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node}
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
for(const[id,icon,label]of [['queue-pause','pause','Pause queue'],['queue-clear','clear','Clear pending'],['close-queue','close','Close'],['queue-prev','previous','Previous'],['queue-next','next','Next']])queueLabel($(id),icon,label);
$('open-queue').prepend(queueIcon('queue'));
function renderCards() {
  const isVoices = collection === 'voices';
  $('search').placeholder = isVoices ? 'Search voice names, descriptions, or tags…' : 'Search text, voice descriptions, or emotions…';
  const query = $('search').value.toLowerCase();
  const page=isVoices?voicePage:historyPage;const rows=page.data;
  $('cards').replaceChildren();
  $('voice-filters').hidden = !isVoices;
  $('catalog-count').hidden = false;
  $('voice-pagination').hidden = page.total <= pageSize;
  $('voice-prev').disabled = catalogOffset === 0;
  $('voice-next').disabled = !page.has_more;
  $('catalog-count').textContent = `${page.total} ${isVoices?'saved voices':'takes'}`;
  $('voice-page-label').textContent = page.total ?
    `${catalogOffset + 1}–${catalogOffset + rows.length} of ${page.total}` : 'No voices';
  $('collection-empty').hidden = rows.length > 0;
  $('collection-empty').textContent = (query || (isVoices && $('tag-filter').value)) ?
    'No matching takes or voices.' : isVoices ?
    'Save a generated take or upload a reference to create your voice library.' :
    'Your generated takes will appear here.';
  for (const row of rows) {
    const card = el('article', null, 'card');
    const head = el('div', null, 'card-title');
    head.append(el('strong', isVoices ? row.name : new Date(row.created_at).toLocaleString()),
      el('span', `${row.duration.toFixed(1)}s`, 'tiny'));
    card.append(head, el('p', isVoices ? row.description || 'No description yet.' : row.text, 'library-description'));
    if (isVoices && row.tags.length) {
      const tags = el('div', null, 'tag-list');
      for (const tag of row.tags) {
        const chip = el('button', tag, 'tag-chip');
        chip.type = 'button'; chip.setAttribute('aria-label', 'Filter by tag ' + tag);
        chip.onclick = () => { $('tag-filter').value = tag; catalogOffset = 0; loadVoicePage().catch(exc => error(exc.message)); };
        tags.append(chip);
      }
      card.append(tags);
    }
    if (!isVoices && row.emotion) card.append(el('p', row.emotion));
    if (!isVoices && (row.preset_voice || row.steering_emotion || row.accent)) card.append(el('p',[row.preset_voice?'Preset '+row.preset_voice:'',row.steering_emotion?'Experimental '+row.steering_emotion+' steering':'',row.accent].filter(Boolean).join(' · ')));
    const player = el('audio'); player.controls = true; player.preload = 'none'; player.src = row.audio_url;
    card.append(player);
    const actions = el('div', null, 'card-actions');
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
    const download = el('a', 'Download WAV', 'secondary');
    download.href = row.audio_url + '?download=true'; download.download = '';
    actions.append(download); card.append(actions); $('cards').append(card);
  }
}
document.querySelectorAll('[data-collection]').forEach(button => button.onclick = () => setCollection(button.dataset.collection));
$('search').oninput = () => {
  catalogOffset = 0;
  clearTimeout(searchTimer);
  searchTimer=setTimeout(()=>loadCollection().catch(exc=>error(exc.message)),250);
};
function waveform(peaks){const canvas=$('waveform'),ratio=devicePixelRatio||1;canvas.width=canvas.clientWidth*ratio;canvas.height=110*ratio;const c=canvas.getContext('2d');c.clearRect(0,0,canvas.width,canvas.height);c.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--accent');if(!peaks?.length)return;const step=canvas.width/peaks.length;peaks.forEach((peak,i)=>{const h=Math.max(2*ratio,peak*canvas.height*.9);c.fillRect(i*step,(canvas.height-h)/2,Math.max(1,step*.55),h)})}
function showResult(row){if(browsePage){location.href='/?artifact='+encodeURIComponent(row.id);return}latest=row;renderExpansion(candidateExpansion(row),'result-expansion-details','Saved with this take');for(const [id,card] of candidateCards)card.element.dataset.selected=String(id===row.id);if(currentCandidates&&row.candidate_batch_id!==currentCandidates.id)$('candidate-results').hidden=true;$('result-title').textContent=row.candidate_index?'Option '+row.candidate_index+' · selected take':'Your latest take';const details=[row.preset_voice?'Preset '+row.preset_voice:'',row.clone_mode==='combined'?'Combined identity and recorded performance':row.clone_mode==='continuation'?'Continued reference performance':'',row.accent?'Accent direction: '+row.accent:'',row.steering_emotion?`Experimental ${row.steering_emotion} steering · ${row.steering_strength}`:''].filter(Boolean);$('direction-summary').textContent=details.join(' · ');$('direction-summary').hidden=!details.length;$('empty').hidden=true;$('working').hidden=true;$('result').hidden=false;$('player').src=row.audio_url;$('download').href=row.audio_url+'?download=true';$('metrics').textContent=`${row.duration.toFixed(1)} seconds · ${row.sample_rate/1000} kHz · ${row.transformation?`Pause edit ${row.transformation.processing_seconds}s · Original ${row.timing_scope?'process total':'synthesis'}`:row.timing_scope?'Total process':'Synthesis'} ${row.generation_seconds}s${row.cold_load&&Number.isFinite(row.load_seconds)?` · Load ${row.load_seconds}s`:``}`;$('result-seed').textContent=`Seed ${row.seed}`;$('path').textContent=row.path;$('truncated').hidden=!row.possibly_truncated;$('save-hint').textContent='Use a clean, natural take as your character reference. Up to the first 30 seconds are saved.';waveform(row.waveform)}
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
 pauseSaving=true;for(const id of ['pause-create','pause-cancel','pause-add','pause-minimum','pause-output-dir','pause-reviewed'])$(id).disabled=true;renderPauseSpans();
 try{pausePendingKey=pausePendingKey||crypto.randomUUID();const row=await post('/api/artifacts/'+pauseSource.id+'/pauses',{spans:pauseSpans,reviewed:true,minimum_gap_ms:minimum,output_dir:$('pause-output-dir').value.trim()||null,idempotency_key:pausePendingKey});pausePendingKey=null;$('pause-editor').close();showResult(row);await refresh();announce('Separate pause-adjusted version ready. Original recording preserved.')}
 catch(exc){pauseError(exc.message)}finally{pauseSaving=false;for(const id of ['pause-create','pause-cancel','pause-add','pause-minimum','pause-output-dir','pause-reviewed'])$(id).disabled=false;renderPauseSpans()}
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
   select.type=save.type='button';select.onclick=()=>showResult(job.result);
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
  if(previous)previous.element.replaceWith(card);else $('candidate-cards').append(card);
  candidateCards.set(job.id,{signature,element:card});
 }
}
async function watchCandidates(identity){
 if(browsePage){location.href='/?candidates='+encodeURIComponent(identity);return}
 const token='candidates:'+identity;if(watching===token)return;
 if(latest?.candidate_batch_id!==identity){latest=null;$('result').hidden=true;}
 watching=token;controls();$('error').hidden=true;$('empty').hidden=true;$('working').hidden=true;
 localStorage.setItem(storageKey+'-candidates',identity);localStorage.removeItem(storageKey+'-job');
 try{while(watching===token){
  const batch=await api('/api/candidates/'+identity);if(watching!==token)return;
  renderCandidates(batch);
  if(!latest){const first=batch.jobs.find(job=>job.result);if(first)showResult(first.result)}
  if(!['queued','running'].includes(batch.status)){await refresh();announce(`${batch.done_count} of ${batch.candidate_count} voice options ready.`);break}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }}catch(exc){if(watching===token)error(exc.message+' Your accepted options remain in Queue; reload to reconnect.')}
 finally{if(watching===token){watching=null;controls();refreshQueue().catch(()=>{})}}
}
async function watch(identity){
 if(browsePage){location.href='/?job='+encodeURIComponent(identity);return}if(watching===identity)return;localStorage.removeItem(storageKey+'-candidates');$('candidate-results').hidden=true;watching=identity;controls();$('error').hidden=true;$('empty').hidden=true;$('result').hidden=true;$('working').hidden=false;
 localStorage.setItem(storageKey+'-job',identity);
 try{while(watching===identity){const job=await api('/api/jobs/'+identity);if(watching!==identity)return;
  $('stage').textContent=job.stage;
  $('working-job-resources').href='/observability?job='+encodeURIComponent(identity);
  generationTiming(identity,job.created_at,job.stage,job.completed_at);
  if(job.status==='done'){localStorage.removeItem(storageKey+'-job');showResult(job.result);await refresh();announce('Speech ready');break}
  if(job.status==='error'||job.status==='deleted'){localStorage.removeItem(storageKey+'-job');throw Error(job.error||'This pending take was removed.');}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }}catch(exc){if(watching===identity){error(exc.message);$('working').hidden=true;$('empty').hidden=!latest;if(latest)showResult(latest)}}
 finally{if(watching===identity){watching=null;controls();refreshQueue().catch(()=>{})}}
}
$('form').onsubmit=async event=>{
 event.preventDefault();if(!modelChoiceReady){error('Choose a set-up model that supports this voice source.');return}if(submitting||uploading)return;let request;try{request=settings()}catch(exc){error(exc.message);return}
 if(mode==='preset'&&!request.preset_voice){error('Choose an expressive preset voice first.');return}
 if(mode==='saved'&&!request.voice_id){error('Choose a saved voice first.');return}
 if(mode==='upload'&&!request.reference_id){error('Choose an audio reference first.');return}
 submitting=true;controls();
 generationTiming('submitting',new Date().toISOString(),'Submitting request');
 try{await saveSelectedTranscript();pendingKey=pendingKey||crypto.randomUUID();const batch=await post('/api/candidates',{...request,idempotency_key:pendingKey});pendingKey=null;watchCandidates(batch.id);await refreshQueue()}
 catch(exc){if(generationClock?.identity==='submitting'){generationClock=null;updateElapsed()}error(exc.message)}finally{submitting=false;controls()}
};
document.addEventListener('keydown',event=>{if(!browsePage&&(event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();if(!$('generate').disabled)$('form').requestSubmit()}});
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
let healthInFlight=false;
async function health(){
 if(healthInFlight)return;healthInFlight=true;
 try{
  const state=await api('/health');
  queuePaused=!!state.queue_paused;queueLabel($('queue-pause'),queuePaused?'play':'pause',queuePaused?'Resume queue':'Pause queue');
  if(!changingModel)await loadModelChoices();
  online=state.downloaded&&!state.runtime_errors?.length;
  queueWritable=state.queue_storage?.status!=='unavailable';queueNotices(state);
  $('status').dataset.offline=!online;
  $('status').textContent=state.status==='error'?(state.last_error||'Setup incomplete'):state.busy?state.status:state.model_loaded?'Model loaded · Ready':caps?.limits.model_residency==='per job subprocess'?'Ready · Loads per take':'Ready · Loads on first take';
  busy=state.busy;
  if(queueWritable&&state.active_job&&!watching&&!browsePage&&!localStorage.getItem(storageKey+'-candidates'))watch(state.active_job);
  if(queueWritable){
   try{await refreshQueue()}catch(exc){queueWritable=false;queueNotices({queue_storage:{status:'unavailable',message:exc.message}})}
  }else{
   $('queue-count').textContent='Queue storage unavailable. Displayed entries may be out of date.';
   for(const id of ['queue-clear','queue-prev','queue-next'])$(id).disabled=true;
  }
 }catch(exc){
  online=false;$('status').textContent=exc.message==='Failed to fetch'?'Server offline':exc.message;
  $('status').dataset.offline=true;queueNotices(null);
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
  const card=el('article',null,'card queue-entry'),content=el('div',null,'queue-entry-content');
  card.dataset.jobId=row.id;card.dataset.queueStatus=row.status;
  if(row.status==='queued'){
   const handle=el('button',null,'queue-drag-handle');handle.append(queueIcon('grip'));handle.type='button';handle.dataset.jobId=row.id;
   handle.setAttribute('aria-label','Reorder job: '+(row.requested?.text||row.id).slice(0,90));handle.setAttribute('aria-describedby','queue-reorder-hint');
   handle.title='Drag to reorder, adopting the target’s priority, or use the up and down arrow keys';handle.addEventListener('pointerdown',startQueueDrag);
   handle.addEventListener('lostpointercapture',()=>{if(queueDrag?.handle===handle)finishQueueDrag()});
   handle.onkeydown=event=>{if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();changeQueueOrder(row.id,{direction:event.key==='ArrowUp'?'up':'down'},false,true)}};
   card.append(handle);
  }else{const spacer=el('span',null,'queue-drag-spacer');spacer.setAttribute('aria-hidden','true');card.append(spacer)}
  content.append(el('strong',`${row.status} · ${row.backend||'legacy'} · ${row.generate_immediately?'Immediate':'Normal'}`));
  content.append(el('p',row.requested?.text||row.id),el('p',row.stage,'hint'));
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
function queueDropTarget(y){
 const rows=[...$('queue-entries').querySelectorAll('[data-queue-status="queued"]')].filter(card=>card.dataset.jobId!==queueDrag.identity);
 for(const card of $('queue-entries').children)delete card.dataset.drop;
 const anchor=rows.find(card=>y<card.getBoundingClientRect().top+card.getBoundingClientRect().height/2)||rows.at(-1);
 if(!anchor){queueDrag.target=null;return}
 const placement=y<anchor.getBoundingClientRect().top+anchor.getBoundingClientRect().height/2?'before':'after';
 anchor.dataset.drop=placement;queueDrag.target={[placement+'_id']:anchor.dataset.jobId};
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
 queueDrag={identity:handle.dataset.jobId,handle,pointerId:event.pointerId,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,direction:0,moving:false,target:null};
}
function finishQueueDrag(commit=false){
 if(!queueDrag)return;
 const drag=queueDrag;queueDrag=null;cancelAnimationFrame(drag.frame);
 if(drag.handle.hasPointerCapture(drag.pointerId))drag.handle.releasePointerCapture(drag.pointerId);
 $('queue-entries').classList.remove('queue-dragging');
 for(const card of $('queue-entries').children){delete card.dataset.drop;card.classList.remove('queue-moving')}
 if(commit&&drag.moving&&drag.target)changeQueueOrder(drag.identity,drag.target,false,true);
 else refreshQueue().catch(exc=>error(exc.message));
}
document.addEventListener('pointermove',event=>{
 if(!queueDrag||event.pointerId!==queueDrag.pointerId)return;
 queueDrag.direction=Math.sign(event.clientY-queueDrag.y)||queueDrag.direction;
 queueDrag.x=event.clientX;queueDrag.y=event.clientY;
 if(!queueDrag.moving&&Math.hypot(event.clientX-queueDrag.startX,event.clientY-queueDrag.startY)>6){
  queueDrag.moving=true;$('queue-entries').classList.add('queue-dragging');queueDrag.handle.closest('.queue-entry').classList.add('queue-moving');
  announce('Moving job. Release at the highlighted position, or press Escape to cancel.');queueDragFrame();
 }
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
async function loadCollection(){if(collection==='voices')return loadVoicePage();const page=await api('/api/history?'+new URLSearchParams({q:$('search').value,limit:pageSize,offset:catalogOffset}));historyPage=page;historyRows=page.data;renderCards()}
async function loadVoicePage() {
  const token = ++catalogRequest;
  const params = new URLSearchParams({q: collection === 'voices' ? $('search').value : '',
    tag: $('tag-filter').value, sort: $('voice-sort').value, limit: pageSize, offset: catalogOffset});
  const page = await api('/api/voices?' + params);
  if (token !== catalogRequest) return;
  if (catalogOffset && catalogOffset >= page.total) { catalogOffset = 0; return loadVoicePage(); }
  voicePage = page;
  renderCards();
}
function setCollection(next) {
  clearTimeout(searchTimer); collection = next; catalogOffset = 0;
  document.querySelectorAll('[data-collection]').forEach(button => button.setAttribute('aria-selected', button.dataset.collection === collection));
  $('search').placeholder = next === 'voices' ? 'Search voice names, descriptions, or tags…' : 'Search text, voice descriptions, or emotions…';
  if (next === 'voices') loadVoicePage().catch(exc => error(exc.message));
  else { catalogRequest++; loadCollection().catch(exc=>error(exc.message)); }
}
function chooseVoice(row, focus = true) {
  if(browsePage&&focus){location.href='/?voice='+encodeURIComponent(row.id);return;}
  if (!voiceRows.some(item => item.id === row.id)) voiceRows.push(row);
  if (![...$('voice').options].some(option => option.value === row.id)) $('voice').append(new Option(row.name, row.id));
  $('voice').value = row.id; setMode('saved'); voicePreview();persist();
  if (focus) { $('text').focus(); $('form').scrollIntoView({block: 'start'}); }
}
function openVoiceEditor(context) {
  voiceEditor = context;
  $('voice-editor-title').textContent = context.upload ? 'Upload a voice' : context.edit_id ? 'Edit voice details' : 'Save a voice';
  $('voice-editor-save').textContent = context.upload ? 'Upload to Collection' : context.edit_id ? 'Save changes' : 'Save voice';
  $('voice-upload-field').hidden=!context.upload;$('voice-upload-file').required=!!context.upload;$('voice-upload-file').value='';
  $('voice-editor-hint').textContent = context.upload ? 'Add your own recording to Collection, with a name, description, tags and optional exact transcript.' : context.edit_id ?
    'Update the catalog details. The saved recording stays the same.' :
    'Save named performance variants of the same speaker with mood tags and the exact transcript. Choose the desired recorded delivery before combined cloning.';
  $('edit-voice-name').value = context.name || '';
  $('edit-voice-description').value = context.description || '';
  $('edit-voice-tags').value = (context.tags || []).join(', ');
  $('edit-transcript').value=context.transcript||'';
  $('voice-editor-error').hidden = true;
  $('voice-editor').showModal(); $('edit-voice-name').focus();
}
$('upload-voice').onclick=$('upload-saved-voice').onclick=()=>openVoiceEditor({upload:true});
$('voice-upload-file').onchange=()=>{
 const file=$('voice-upload-file').files[0];
 if(file&&!$('edit-voice-name').value.trim())$('edit-voice-name').value=file.name.replace(/\.[^.]+$/,'').slice(0,100);
};
$('voice-editor-cancel').onclick = () => $('voice-editor').close();
$('voice-editor').addEventListener('cancel',event=>{if(voiceSaving)event.preventDefault()});
$('voice-editor-form').onsubmit = async event => {
  event.preventDefault(); if (!voiceEditor||voiceSaving) return;
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
      const form=new FormData();form.append('file',file);form.append('name',details.name);form.append('description',details.description);form.append('transcript',details.transcript);
      for(const tag of details.tags)form.append('tags',tag);
      row=await api('/api/voices/upload',{method:'POST',body:form});
    }
    else if (context.edit_id) row = await api('/api/voices/' + context.edit_id,
      {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(details)});
    else row = await post('/api/voices', {...details, output_id: context.output_id || null, reference_id: context.reference_id || null});
    if (!context.edit_id) {
      $('search').value = ''; $('tag-filter').value = ''; $('voice-sort').value = 'updated';
      collection = 'voices'; catalogOffset = 0;
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
    $('voice-editor-save').disabled = false; $('voice-editor-cancel').disabled = false;
  }
};
$('tag-filter').onchange = $('voice-sort').onchange = () => {catalogOffset = 0; loadVoicePage().catch(exc => error(exc.message));};
$('voice-prev').onclick = () => {catalogOffset = Math.max(0, catalogOffset - pageSize); loadCollection().catch(exc => error(exc.message));};
$('voice-next').onclick = () => {catalogOffset += pageSize; loadCollection().catch(exc => error(exc.message));};
$('browse-voices').onclick=()=>{location.href='/collection?tab=voices'};
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
  if(draftMode)setMode(draftMode);else if(saved)restore(saved);else setMode(caps.modes.design.available===false?'upload':'design');
  await health();
  const pageSelected=await openPageSelection();
  const batch=localStorage.getItem(storageKey+'-candidates'),job=localStorage.getItem(storageKey+'-job');
  if(!watching&&!browsePage&&!pageSelected){if(batch)watchCandidates(batch);else if(job)watch(job)}
  renderModelChoices();
 }finally{changingModel=false;controls();if(pendingModelRestore){const row=pendingModelRestore;pendingModelRestore=null;restore(row)}}
}
$('voice-model').onchange=()=>{if($('voice-model').value)selectVoiceModel($('voice-model').value).catch(exc=>error(exc.message))};
$('queue-pause').onclick=async()=>{try{const state=await post('/api/queue/'+(queuePaused?'resume':'pause'),{});queuePaused=state.paused;queueLabel($('queue-pause'),queuePaused?'play':'pause',queuePaused?'Resume queue':'Pause queue');await health()}catch(exc){error(exc.message)}};
(async()=>{try{const model=await loadModelChoices();if(model)await selectVoiceModel(model);else error('No set-up voice models are ready. Run make setup or reconnect model storage.')}catch(exc){error(exc.message)}setInterval(health,5000)})();

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
function buildPerformanceControls(){
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
 if(caps.backend==='higgs')for(const key of ['emotion','prosody','style']){
  const label=el('label',key[0].toUpperCase()+key.slice(1));label.htmlFor='higgs-'+key;const select=el('select');select.id=label.htmlFor;select.append(new Option('Follow recording',''));
  for(const value of caps.controls.higgs[key].values)select.append(new Option(value.replaceAll('_',' '),value));
  const field=el('div',null,'control-field');field.append(label,select);$('performance-controls').append(field);
 }
 if(caps.controls[caps.backend]?.top_p){
  const details=el('details'),summary=el('summary','Advanced sampling');details.append(summary);
  const fields=el('div',null,'sampling-fields control-stack');
  for(const [key,id,name] of [['top_p','model-top-p','Nucleus sampling'],['top_k','model-top-k','Sampling candidates']]){const spec=caps.controls[caps.backend][key];const label=el('label',name);label.htmlFor=id;const input=el('input');input.id=id;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='top_p'?.05:1;input.value=spec.default;const field=el('div',null,'control-field');field.append(label,input);fields.append(field)}
  details.append(fields);
  $('performance-controls').append(details);
 }
 if(caps.backend==='qwen3'){
  document.querySelector('[data-mode="design"]').textContent='Design a new voice';document.querySelector('[data-mode="preset"]').textContent='Preset speakers';
  document.querySelector('[data-mode="saved"]').textContent='Clone saved voice';document.querySelector('[data-mode="upload"]').textContent='Clone reference';
 }
 if(caps.controls[caps.backend]?.punctuation_pauses){
  const label=el('label'),input=el('input');input.id='punctuation-pauses';input.type='checkbox';input.checked=true;input.style.width='auto';input.style.marginRight='8px';
  label.append(input,document.createTextNode('Longer punctuation pauses'));$('pacing-settings').append(label);
  const fields=el('div',null,'two');
  for(const [key,name] of [['sentence_pause_ms','Extra pause after sentences (ms)'],['comma_pause_ms','Extra pause after commas (ms)']]){
   const spec=caps.controls[caps.backend][key],field=el('div',null,'control-field'),label=el('label',caps.backend==='moss'?name.replace('Extra pause','Pause target'):name),number=el('input');label.htmlFor=key;number.id=key;number.type='number';number.min=spec.min;number.max=spec.max;number.step=50;number.value=spec.default;field.append(label,number);fields.append(field);
  }
  $('pacing-settings').append(fields,el('p',caps.backend==='moss'?'Targets guide the native model in a single take to preserve voice identity. Exact millisecond pauses are not guaranteed.':'Keeps the same reference voice for each phrase and adds these gaps to its natural pauses. Phrase delivery can differ from a whole-line take. Disable for continuous delivery. Voice design and manual Fish tags use whole-line delivery.','hint'));
  input.onchange=updatePacingControls;
 }
}
function pacingAvailable(){return (caps?.backend==='moss'||mode!=='design')&&!(caps?.backend==='fish'&&/\[[^\[\]]*\]/.test($('text').value))}
function updatePacingControls(){
 if(!$('punctuation-pauses'))return;
 const available=pacingAvailable();$('punctuation-pauses').disabled=!available;
 for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).disabled=!available||!$('punctuation-pauses').checked;
}
function restorePerformance(row){
 const options=row[caps.backend]||{};
 if($('punctuation-pauses')){$('punctuation-pauses').checked=options.punctuation_pauses??true;for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).value=options[key]??caps.controls[caps.backend][key].default;updatePacingControls();}
 if(caps.backend==='fish')$('fish-delivery-tags').value=(options.delivery_tags||[]).join('\n');
 if(caps.backend==='higgs'){
  for(const key of ['emotion','prosody','style'])$('higgs-'+key).value=options[key]||'';
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
   for(const category of ['emotion','prosody','style']){const value=$('higgs-'+category).value;if(value){if(leading.includes('<|'+category+':'))throw Error('Choose a structured or manual leading '+category+' directive, not both.');prefix+='<|'+category+':'+value+'|>';}}
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
async function openPageSelection(){
 if(observabilityPage)return true;
 const params=new URLSearchParams(location.search);
 if(params.get('candidates')&&!collectionPage){watchCandidates(params.get('candidates'));return true}
 if(collectionPage){if(params.get('tab')==='voices')setCollection('voices');return}
 if(/^[a-f0-9]{32}$/.test(params.get('voice')||'')){const row=await api('/api/voices/'+params.get('voice'));chooseVoice(row,false);return true;}
 else if(/^[a-f0-9]{32}$/.test(params.get('artifact')||params.get('reuse')||'')){const row=await api('/api/artifacts/'+(params.get('artifact')||params.get('reuse')));if(params.has('reuse'))restore(row);showResult(row);return true;}
 else if(/^[a-f0-9]{32}$/.test(params.get('job')||'')){watch(params.get('job'));return true;}
 return false;
}

const controlHelp={
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
 seed:'Controls random variation. Keep it fixed when comparing settings; the same seed alone does not preserve a voice across different lines.',
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
 'tag-filter':'Show saved voices with this exact tag. Tags organize the library; they do not change the generated accent or performance.',
 'voice-sort':'Choose how saved voices are ordered: newest, recently updated, or name.',
 'page-size':'How many entries to show on each Collection page.',
 'queue-status':'Filter the saved jobs by status. Pending includes waiting and running jobs; other models’ jobs wait until that model is served.',
 'queue-size':'How many queue entries to show on each page.',
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
 'higgs-emotion':'An optional Higgs emotion for this take. Follow recording adds no emotion tag and is a useful baseline for keeping the reference performance.',
 'higgs-prosody':'An optional Higgs control for speaking speed, pitch or expression. It guides the performance rather than setting an exact audio duration.',
 'higgs-style':'An optional Higgs style: singing, shouting or whispering. Follow recording adds no style tag.',
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
 const copy=controlHelp[button.dataset.helpFor];helpTooltip.textContent=typeof copy==='function'?copy():copy;
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
function installControlHelp(){
 for(const id of Object.keys(controlHelp)){
  const control=$(id);if(!control)continue;
  const label=document.querySelector(`label[for="${id}"]`)||control.closest('label');
  if(!label)continue;
  if(label.parentElement.classList.contains('help-label-row')){label.parentElement.querySelector('.help-trigger').setAttribute('aria-label','Help: '+label.textContent.trim());continue;}
  const row=el('div',null,'help-label-row'+(label.contains(control)?' help-toggle-row':'')),button=el('button','?','help-trigger');
  button.type='button';button.dataset.helpFor=id;button.setAttribute('aria-label','Help: '+label.textContent.trim());button.setAttribute('aria-expanded','false');
  label.before(row);row.append(label,button);
  button.addEventListener('pointerenter',()=>showControlHelp(button));button.addEventListener('focus',()=>showControlHelp(button));
  button.addEventListener('pointerleave',scheduleHelpHide);button.addEventListener('blur',scheduleHelpHide);
  button.addEventListener('click',event=>{event.preventDefault();showControlHelp(button)});
 }
}
helpTooltip.addEventListener('pointerenter',()=>clearTimeout(helpHideTimer));helpTooltip.addEventListener('pointerleave',scheduleHelpHide);
document.addEventListener('pointerdown',event=>{if(activeHelp&&!activeHelp.contains(event.target)&&!helpTooltip.contains(event.target))hideControlHelp()},true);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&activeHelp){event.preventDefault();event.stopPropagation();hideControlHelp()}},true);
document.addEventListener('close',hideControlHelp,true);
window.addEventListener('resize',hideControlHelp);document.addEventListener('scroll',hideControlHelp,true);
installControlHelp();
controlHelp['voice-design-tab']=()=>readyDesignModels().length?'':caps?.modes.design.unavailable_reason||'Switch to a model which supports voice generation: VoxCPM2, Qwen3-TTS, MOSS VoiceGenerator';
const voiceDesignTab=document.querySelector('[data-mode="design"]');
voiceDesignTab.addEventListener('pointerenter',()=>{if(voiceDesignTab.getAttribute('aria-disabled')==='true')showControlHelp(voiceDesignTab)});
voiceDesignTab.addEventListener('focus',()=>{if(voiceDesignTab.getAttribute('aria-disabled')==='true')showControlHelp(voiceDesignTab)});
voiceDesignTab.addEventListener('pointerleave',scheduleHelpHide);voiceDesignTab.addEventListener('blur',scheduleHelpHide);
