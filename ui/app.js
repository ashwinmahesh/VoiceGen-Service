
const $=id=>document.getElementById(id);let mode='design',collection='history',reference=null,latest=null,voiceRows=[],historyRows=[],busy=false,online=false,uploading=false,watching=null,submitting=false;
const collectionPage=location.pathname.startsWith('/collection');
if(collectionPage){$('page-title').textContent='Your collection';$('page-description').textContent='Find saved voices, listen to takes, and reuse a performance in the studio.';}
$(collectionPage?'catalog-link':'studio-link').setAttribute('aria-current','page');
let pageSize = 24, caps=null, storageKey='voice-studio', historyPage={data:[],total:0,has_more:false}, pendingKey=null;
let voicePage = {data: [], total: 0, has_more: false};
let currentCandidates=null, candidateCards=new Map();
let appliedExpansion=null,pendingExpansion=null,enhancing=false,expansionChoice=null;
let catalogOffset = 0, searchTimer = null, catalogRequest = 0, voiceEditor = null, voiceSaving = false;
const presets={natural:'Natural, conversational delivery with relaxed pacing.',joy:'Excited and joyful, with an audible smile and lively pacing.',sadness:'Quiet sadness and disappointment, speaking slowly with a trembling voice.',anger:'Angry and frustrated, with firm emphasis and a tense, forceful delivery.',fear:'Frightened and anxious, with a trembling voice and urgent, uneven pacing.',relief:'Deeply relieved, softening into warmth with a gentle exhale.',sarcasm:'Dry, understated sarcasm with deliberate pauses and restrained emphasis.',whisper:'A soft, intimate whisper, speaking gently and slowly.',custom:''};
async function api(path,options={}){const response=await fetch(path,options);if(!response.ok){let body;try{body=await response.json()}catch{}throw Error(typeof body?.detail==='string'?body.detail:body?.detail?.[0]?.msg||`Request failed (${response.status})`)}return response.json()}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function error(message){const target=$($('queue-dialog').open?'queue-error':collectionPage?'page-error':'error');target.textContent=message;target.hidden=false}function announce(text){$('announcement').textContent=text}
function controls(){ $('generate').disabled=!online||submitting||uploading||enhancing;$('unload').disabled=!online||busy||enhancing;$('new-seed').disabled=busy;updatePromptExpansion() }
function clearExpansion(){appliedExpansion=null;pendingExpansion=null;updatePromptExpansion();}
function updatePromptExpansion(){
 if(!caps)return;
 const available=!!caps.limits.prompt_expansion?.enabled,enabled=available&&$('prompt-expansion-enabled').checked,backend=caps.backend;
 $('prompt-expansion-enabled').disabled=!available||enhancing;
 updateCandidateVariation();
 const profile=caps.models[backend]?.prompt_expansion,profileMode=['saved','upload'].includes(mode)?'reference':mode;
 const spec=profile?.modes[profileMode];
 const supported=!!spec?.available&&(!spec.clone_modes||spec.clone_modes.includes($('clone-mode').value));
 $('expansion-profile').hidden=!profile;
 $('expansion-profile-info').textContent=profile?`${profile.name} · ${spec?.label||profileMode} · Profile v${profile.version}`:'';
 $('expansion-profile-rules').textContent=spec?.available?(supported?spec.instructions:'This reference mode follows recorded performance. Choose flexible Voice reference to use delivery instructions.'):spec?.reason||'No expansion specification for this mode.';
 $('expansion-guidance-field').hidden=!enabled||!supported;
 $('expand-guidance').disabled=!online||!enabled||!supported||busy||enhancing||uploading||submitting||!!appliedExpansion;
 $('expand-guidance').textContent=enhancing?'Expanding guidance…':'Preview expanded guidance';
 $('prompt-expansion-hint').textContent=!available?'LLM disabled at startup. Restart with make serve without NO_LLM=1 to enable.':!enabled?'Off. No LLM previews or per-option planning; distinct designs use built-in directions.':!supported?(spec?.reason||'This mode follows recorded performance. Choose flexible Voice reference to direct a different delivery.'):'On. Design batches use LLM contrast planning. For a single take or reference delivery, preview and apply guidance below. Dialogue and transcripts stay unchanged.';
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
function setMode(next){if(caps){if(next==='design'&&caps.modes.design.available===false)next='upload';if((next==='saved'||next==='upload')&&caps.modes.reference.available===false)next='design'}pendingKey=null;clearExpansion();mode=next;for(const button of document.querySelectorAll('[data-mode]'))button.setAttribute('aria-selected',button.dataset.mode===mode);for(const name of ['design','saved','upload','preset'])$(name+'-panel').hidden=name!==mode;updateCapabilities();updatePromptExpansion();persist()}
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>setMode(button.dataset.mode));
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
 const direction=referenceMode?(caps?.modes.reference.emotion&&$('clone-mode').value==='reference'):mode==='preset'||caps?.modes.design.emotion;
 const result={prompt_expansion_id:appliedExpansion?.id||null,candidate_count:Number($('candidate-count').value),candidate_use_llm:!!caps?.limits.prompt_expansion?.enabled&&$('prompt-expansion-enabled').checked,candidate_variation:mode==='design'?$('candidate-variation').value:'auto',generate_immediately:$('generate-immediately').checked,text:$('text').value,description:mode==='design'?$('description').value:'',
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
 if(caps?.backend==='higgs')for(const key of ['emotion','prosody','style'])if($('higgs-'+key).value)options[key]=$('higgs-'+key).value;
 if(modelControls.top_p){options.top_p=Number($('model-top-p').value);options.top_k=Number($('model-top-k').value);}
 if(modelControls.punctuation_pauses){options.punctuation_pauses=$('punctuation-pauses').checked&&pacingAvailable();for(const key of ['sentence_pause_ms','comma_pause_ms'])options[key]=Number($(key).value);}
 if(Object.keys(options).length)result[caps.backend]=options;
 return result;
}
function persist(){if(caps)try{localStorage.setItem(storageKey+'-form',JSON.stringify({...settings(),prompt_expansion_choice:expansionChoice,prompt_expansion:appliedExpansion,description:$('description').value,accent:$('accent').value,emotion:$('emotion').value,mode,preset:$('emotion-preset').value}))}catch{}}
function updateCapabilities(){
 if(!caps)return;
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=referenceMode?(caps.modes.reference.emotion&&$('clone-mode').value==='reference'):mode==='preset'||caps.modes.design.emotion;
 $('emotion').disabled=$('emotion-preset').disabled=$('accent').disabled=!direction;
 $('emotion-hint').textContent=referenceMode&&$('clone-mode').value==='auto'?'Auto follows recorded performance when transcribed. Retained emotion/accent fields are not sent; choose flexible Voice reference to apply them.':referenceMode&&['continuation','combined'].includes($('clone-mode').value)?'Continuation and combined cloning follow the reference delivery. Choose Voice reference to direct a different emotion or accent.':direction?'Direct the performance in words. Voice identity and expression are approximate.':'This model takes its voice from the recording. Use only the controls and inline tags listed below.';
 $('accent-hint').textContent=direction?'A prompt direction, not a trained accent pack. A recording with the desired accent is usually the stronger cue.':'The reference recording supplies the accent in this mode.';
 for(const key of ['steps','guidance','temperature'])$(key+'-field').hidden=!caps.controls[key];
 document.querySelector('[data-mode="preset"]').hidden=!caps.modes.preset?.available;
 document.querySelector('[data-mode="design"]').hidden=caps.modes.design.available===false;
 for(const name of ['saved','upload'])document.querySelector('[data-mode="'+name+'"]').hidden=caps.modes.reference.available===false;
 $('language-field').hidden=caps.languages.length<2;
 $('text').maxLength=caps.limits.text_characters;
 $('max-tokens-field').hidden=!!caps.limits.max_tokens_fixed;
 $('index-emotion').hidden=!caps.controls[caps.backend]?.emotion_vector;
 $('inline-hint').hidden=!caps.limits.inline_controls;
 $('inline-hint').textContent=caps.backend==='fish'?'Manual local tags such as [whisper] and [pause] remain available. Delivery tags guide performance; audition accent retention.':caps.backend==='higgs'?'Manual local tokens such as <|prosody:pause|> and <|prosody:long_pause|> remain available. They do not specify exact milliseconds.':'';
 $('reference-options').hidden=!referenceMode;
 for(const option of $('clone-mode').options)option.hidden=!(caps.modes.reference.clone_modes||['auto','reference']).includes(option.value);
 if(!(caps.modes.reference.clone_modes||['auto','reference']).includes($('clone-mode').value))$('clone-mode').value='auto';
 $('clone-mode-field').hidden=!caps.modes.reference.continuation;
 const transcript=referenceMode&&(caps.modes.reference.transcript&&caps.backend!=='voxcpm2'||['auto','continuation','combined'].includes($('clone-mode').value));
 $('transcript-field').hidden=!transcript;
 $('ref-text-label').textContent=caps.backend==='qwen3'?'Exact reference transcript (optional)':'Exact reference transcript (or use saved transcript)';
 $('transcript-hint').textContent=caps.modes.reference.transcript_required?'This model requires the exact words in the reference recording. Supply them here or in the saved voice.':caps.backend==='qwen3'?'An exact transcript improves cloning. Leave blank to use the saved transcript, or speaker embedding mode when none is saved.':'Auto follows recorded performance when an exact transcript is available; otherwise it uses voice reference. Explicit combined/continuation require a transcript.';
 count();
 $('edit-transcript-field').hidden=false;
 updatePerformancePreview();
 updatePacingControls();
 $('steering-field').hidden=!caps.extensions?.emotion_steering;
 $('steering-strength-field').hidden=!$('steering-emotion').value;
}
$('clone-mode').onchange=()=>{updateCapabilities();persist()};
$('steering-emotion').onchange=()=>{if($('steering-emotion').value){if($('emotion-preset').value==='natural'){$('emotion').value='';$('emotion-preset').value='custom'}if(Number($('temperature').value)===.9)$('temperature').value='1.1'}updateCapabilities();persist()};
function restore(row){
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
 else setMode('design');
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha'])if($(key))$(key).value=row[caps.backend]?.[key]??row[key]??caps.controls[caps.backend]?.[key]?.default??1;
 $('emotion-reference-id').value=row[caps.backend]?.emotion_reference_id||row.emotion_reference_id||'';$('emotion-vector').value=(row[caps.backend]?.emotion_vector||row.emotion_vector)?.join(',')||'';
 $('emotion-reference-info').textContent=(row[caps.backend]?.emotion_reference_id||row.emotion_reference_id)?'Using the saved emotion recording.':'';
 restorePerformance(row);$('ref-text').value=row.ref_text||'';appliedExpansion=row.candidate_variant?null:row.prompt_expansion||null;updateCapabilities();updatePromptExpansion();count();persist();
}
$('form').addEventListener('input',event=>{if(['description','emotion','accent','voice','reference','ref-text','clone-mode','preset-voice','fish-delivery-tags','higgs-emotion','higgs-prosody','higgs-style'].includes(event.target.id))clearExpansion();pendingKey=null;count();updatePerformancePreview();persist()});$('form').addEventListener('change',event=>{if(['voice','reference','clone-mode','preset-voice','higgs-emotion','higgs-prosody','higgs-style'].includes(event.target.id))clearExpansion();pendingKey=null;persist()});
$('new-seed').onclick=()=>{pendingKey=null;$('seed').value=crypto.getRandomValues(new Uint32Array(1))[0];persist();announce('New seed selected')};
function voicePreview(){const row=voiceRows.find(v=>v.id===$('voice').value);$('voice-preview').hidden=!row;if(row){$('ref-text').value=row.transcript||'';$('voice-preview').src=row.audio_url;$('voice-hint').textContent=row.description||'Reference identity is reused; emotion is directed separately.'}}$('voice').onchange=voicePreview;
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
      reuse.onclick = () => { if(collectionPage){location.href='/?reuse='+encodeURIComponent(row.id);return}restore(row); $('text').focus(); }; actions.append(reuse);
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
function showResult(row){if(collectionPage){location.href='/?artifact='+encodeURIComponent(row.id);return}latest=row;renderExpansion(candidateExpansion(row),'result-expansion-details','Saved with this take');for(const [id,card] of candidateCards)card.element.dataset.selected=String(id===row.id);if(currentCandidates&&row.candidate_batch_id!==currentCandidates.id)$('candidate-results').hidden=true;$('result-title').textContent=row.candidate_index?'Option '+row.candidate_index+' · selected take':'Your latest take';const details=[row.preset_voice?'Preset '+row.preset_voice:'',row.clone_mode==='combined'?'Combined identity and recorded performance':row.clone_mode==='continuation'?'Continued reference performance':'',row.accent?'Accent direction: '+row.accent:'',row.steering_emotion?`Experimental ${row.steering_emotion} steering · ${row.steering_strength}`:''].filter(Boolean);$('direction-summary').textContent=details.join(' · ');$('direction-summary').hidden=!details.length;$('empty').hidden=true;$('working').hidden=true;$('result').hidden=false;$('player').src=row.audio_url;$('download').href=row.audio_url+'?download=true';$('metrics').textContent=`${row.duration.toFixed(1)} seconds · ${row.sample_rate/1000} kHz · ${row.transformation?`Pause edit ${row.transformation.processing_seconds}s · Original ${row.timing_scope?'process total':'synthesis'}`:row.timing_scope?'Total process':'Synthesis'} ${row.generation_seconds}s${row.cold_load&&Number.isFinite(row.load_seconds)?` · Load ${row.load_seconds}s`:``}`;$('result-seed').textContent=`Seed ${row.seed}`;$('path').textContent=row.path;$('truncated').hidden=!row.possibly_truncated;$('save-hint').textContent='Use a clean, natural take as your character reference. Up to the first 30 seconds are saved.';waveform(row.waveform)}
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
   save.onclick=()=>openVoiceEditor({output_id:job.result.id,name:'',description:job.result.description||'',transcript:job.result.duration<=30?spokenTranscript(job.result):'',tags:[]});
   download.href=job.result.audio_url+'?download=true';download.download='';actions.append(select,save,download);card.append(actions);
  }else if(job.status==='error'&&!job.paused&&job.backend===caps?.backend){
   const retry=el('button','Retry this option','secondary');retry.type='button';retry.onclick=async()=>{try{await post('/api/queue/'+job.id+'/retry',{});watchCandidates(batch.id)}catch(exc){error(exc.message)}};card.append(retry);
  }
  if(previous)previous.element.replaceWith(card);else $('candidate-cards').append(card);
  candidateCards.set(job.id,{signature,element:card});
 }
}
async function watchCandidates(identity){
 if(collectionPage){location.href='/?candidates='+encodeURIComponent(identity);return}
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
 if(collectionPage){location.href='/?job='+encodeURIComponent(identity);return}if(watching===identity)return;localStorage.removeItem(storageKey+'-candidates');$('candidate-results').hidden=true;watching=identity;controls();$('error').hidden=true;$('empty').hidden=true;$('result').hidden=true;$('working').hidden=false;
 const started=Date.now();localStorage.setItem(storageKey+'-job',identity);
 try{while(watching===identity){const job=await api('/api/jobs/'+identity);if(watching!==identity)return;
  $('stage').textContent=job.stage;
  $('elapsed').textContent=`${Math.floor((Date.now()-started)/1000)} seconds since monitoring began`;
  if(job.status==='done'){localStorage.removeItem(storageKey+'-job');showResult(job.result);await refresh();announce('Speech ready');break}
  if(job.status==='error'||job.status==='deleted'){localStorage.removeItem(storageKey+'-job');throw Error(job.error||'This pending take was removed.');}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }}catch(exc){if(watching===identity){error(exc.message);$('working').hidden=true;$('empty').hidden=!latest;if(latest)showResult(latest)}}
 finally{if(watching===identity){watching=null;controls();refreshQueue().catch(()=>{})}}
}
$('form').onsubmit=async event=>{
 event.preventDefault();if(submitting||uploading)return;let request;try{request=settings()}catch(exc){error(exc.message);return}
 if(mode==='preset'&&!request.preset_voice){error('Choose an expressive preset voice first.');return}
 if(mode==='saved'&&!request.voice_id){error('Choose a saved voice first.');return}
 if(mode==='upload'&&!request.reference_id){error('Choose an audio reference first.');return}
 submitting=true;controls();
 try{pendingKey=pendingKey||crypto.randomUUID();const batch=await post('/api/candidates',{...request,idempotency_key:pendingKey});pendingKey=null;watchCandidates(batch.id);await refreshQueue()}
 catch(exc){error(exc.message)}finally{submitting=false;controls()}
};
document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();if(!$('generate').disabled)$('form').requestSubmit()}});
$('save-voice').onclick = () => {
  if (latest) openVoiceEditor({output_id: latest.id, name: $('voice-name').value,
    description: latest.description || '', transcript:latest.duration<=30?spokenTranscript(latest):'',tags: []});
};
$('reference').onchange=async()=>{const file=$('reference').files[0];reference=null;$('reference-preview').hidden=true;$('save-reference').hidden=true;if(!file)return;uploading=true;controls();$('reference-info').textContent='Preparing reference…';try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);body.append('transcript',$('ref-text').value);reference=await api('/api/references',{method:'POST',body});$('reference-info').textContent=`${reference.name} · ${reference.duration}s`;$('reference-preview').src=reference.audio_url;$('reference-preview').hidden=false;$('save-reference').hidden=false}catch(exc){$('reference-info').textContent='';error(exc.message)}finally{uploading=false;controls()}};
$('save-reference').onclick = () => {
  if (reference) openVoiceEditor({reference_id: reference.id,
    name: (reference.name || 'Uploaded voice').replace(/\.[^.]+$/, '').slice(0, 100), description: '', transcript:$('ref-text').value,tags: []});
};
$('unload').onclick=async()=>{try{await post('/api/model/unload',{});announce('Model memory released. It reloads on the next take.');await health()}catch(exc){error(exc.message)}};
async function health(){try{const state=await api('/health');online=state.downloaded&&!state.runtime_errors?.length;$('status').dataset.offline=!online;$('status').textContent=state.status==='error'?(state.last_error||'Setup incomplete'):state.busy?state.status:state.model_loaded?'Model loaded · Ready':caps?.limits.model_residency==='per job subprocess'?'Ready · Loads per take':'Ready · Loads on first take';busy=state.busy;if(state.active_job&&!watching&&!collectionPage&&!localStorage.getItem(storageKey+'-candidates'))watch(state.active_job);await refreshQueue()}catch{online=false;$('status').textContent='Server offline';$('status').dataset.offline=true}controls()}
let queueOffset=0,queuePage=null,queueRequest=0;
async function refreshQueue(){
 const token=++queueRequest;
 const page=await api('/api/queue?'+new URLSearchParams({status:$('queue-status').value,limit:$('queue-size').value,offset:queueOffset}));
 if(token!==queueRequest)return;
 if(queueOffset&&queueOffset>=page.total){queueOffset=0;return refreshQueue()}
 queuePage=page;$('queue-entries').replaceChildren();$('queue-badge').textContent=(page.counts.queued||0)+(page.counts.running||0)||'';
 $('queue-count').textContent=`${page.counts.queued||0} pending · ${page.counts.running||0} running · ${page.counts.error||0} failed`;
 $('queue-clear').disabled=!(page.counts.queued>0);$('queue-prev').disabled=queueOffset===0;$('queue-next').disabled=!page.has_more;
 $('queue-page').textContent=page.total?`${queueOffset+1}–${queueOffset+page.data.length} of ${page.total}`:'No matching entries';
 for(const row of page.data){
  const card=el('article',null,'card queue-entry'),content=el('div',null,'queue-entry-content');
  content.append(el('strong',`${row.status} · ${row.backend||'legacy'} · ${row.generate_immediately?'Immediate':'Normal FIFO'}`));
  content.append(el('p',row.requested?.text||row.id),el('p',row.stage,'hint'));
  if(row.error)content.append(el('p',row.error,'error'));
  const actions=el('div',null,'card-actions queue-actions');
  if(row.candidate_batch_id){const options=el('button','View all options','secondary');options.type='button';options.onclick=()=>{$('queue-dialog').close();watchCandidates(row.candidate_batch_id)};actions.append(options);}
  const inspect=el('button',row.status==='done'?'Open take':'Monitor','secondary');inspect.type='button';inspect.onclick=()=>{$('queue-dialog').close();row.result?showResult(row.result):watch(row.id)};actions.append(inspect);
  if(row.status==='queued'){const remove=el('button','Remove pending','secondary');remove.type='button';remove.onclick=async()=>{try{await api('/api/queue/'+row.id,{method:'DELETE'});await refreshQueue()}catch(exc){error(exc.message)}};actions.append(remove)}
  if(row.status==='error'&&!row.paused&&row.backend===caps?.backend){const retry=el('button','Retry same entry','secondary');retry.type='button';retry.onclick=async()=>{try{const job=await post('/api/queue/'+row.id+'/retry',{});$('queue-dialog').close();watch(job.id);await refreshQueue()}catch(exc){error(exc.message)}};actions.append(retry)}
  card.append(content,actions);$('queue-entries').append(card);
 }
}
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
  if(collectionPage&&focus){location.href='/?voice='+encodeURIComponent(row.id);return;}
  if (!voiceRows.some(item => item.id === row.id)) voiceRows.push(row);
  if (![...$('voice').options].some(option => option.value === row.id)) $('voice').append(new Option(row.name, row.id));
  $('voice').value = row.id; setMode('saved'); voicePreview();
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
(async()=>{try{caps=await api('/api/capabilities');$('prompt-expansion-enabled').checked=!!caps.limits.prompt_expansion?.enabled;storageKey=caps.service;document.title=caps.name+' Voice Studio';$('brand-name').textContent=caps.name;$('model-badge').textContent=`${caps.sample_rate/1000} kHz · ${caps.quantization} · On your Mac`;$('model-footer').textContent=`${caps.name} · ${caps.quantization} · ${caps.limits.runtime||'MLX'} on Apple Silicon`;$('storage-footer').textContent=caps.storage;$('output-dir').placeholder=caps.output_dir;$('language').replaceChildren(...caps.languages.map(x=>new Option(x,x)));$('preset-voice').replaceChildren(...caps.presets.map(x=>new Option(`${x.name} · ${x.language}`,x.id)));for(const emotion of caps.extensions?.emotion_steering?.emotions||[])$('steering-emotion').append(new Option(emotion[0].toUpperCase()+emotion.slice(1),emotion));$('settings-hint').textContent=(caps.limits.max_tokens_fixed?'This upstream Chatterbox API uses a fixed 1000-token ceiling. ':caps.backend==='moss'?'The audio-frame ceiling can be raised by the upstream text-derived minimum. ':'The audio token limit is a safety cap. Reaching it can cut a take short. ')+(caps.limits.memory_note||'A matching seed alone does not lock a voice.');const notice=caps.models[caps.backend];if(notice?.notice){$('license-text').textContent=notice.notice;$('license-link').href=notice.license_url;$('license-notice').hidden=false}buildBackendSettings();buildPerformanceControls();updateCapabilities();await refresh();const saved=JSON.parse(localStorage.getItem(storageKey+'-form')||'null');if(saved)restore(saved);else setMode(caps.modes.design.available===false?'upload':'design');await health();const pageSelected=await openPageSelection();const batch=localStorage.getItem(storageKey+'-candidates'),job=localStorage.getItem(storageKey+'-job');if(!watching&&!collectionPage&&!pageSelected){if(batch)watchCandidates(batch);else if(job)watch(job)}}catch(exc){error(exc.message)}setInterval(health,5000)})();

function buildBackendSettings(){
 $('backend-settings').replaceChildren();
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha']){
  const spec=caps.controls[caps.backend]?.[key];if(!spec)continue;
  const names={exaggeration:'Exaggeration',cfg_weight:'Voice guidance',duration_factor:'Duration factor · higher is slower',interval_silence:'Pause between generated segments (ms)',emotion_alpha:'Emotion blend'};
  const label=el('label',names[key]);label.htmlFor=key;const input=el('input');input.id=key;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='interval_silence'?50:.05;input.value=spec.default;
  const field=el('div');field.id=key+'-field';field.append(label,input);$('backend-settings').append(field);
 }
 for(const key of ['steps','guidance','temperature'])if(caps.controls[key])$(key).value=caps.controls[key].default;
 for(const option of $('max-tokens').options){option.disabled=Number(option.value)>caps.controls.max_tokens.max;if(caps.backend==='moss')option.textContent=option.value+' audio frames'}

}
$('emotion-reference').onchange=async()=>{
 const file=$('emotion-reference').files[0];if(!file)return;
 uploading=true;controls();
 try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);const record=await api('/api/references',{method:'POST',body});$('emotion-reference-id').value=record.id;$('emotion-reference-info').textContent=record.name+' · '+record.duration+'s';$('emotion-vector').value='';$('index-emotion-mode').value='audio';updateIndexEmotion(true);pendingKey=null;persist()}
 catch(exc){error(exc.message)}finally{uploading=false;controls()}
};
$('clear-emotion-reference').onclick=()=>{$('emotion-reference-id').value='';$('emotion-reference').value='';$('emotion-reference-info').textContent='';$('index-emotion-mode').value='follow';updateIndexEmotion();pendingKey=null;persist()};


function spokenTranscript(row){const backend=row.backend||caps.backend;return (backend==='fish'?row.text.replace(/\[[^\[\]]*\]\s*/g,''):backend==='higgs'?row.text.replace(/<\|[^|>]+\|>\s*/g,''):row.text).trim()}
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
  $('backend-presets').append(label,select);
 }
 if(caps.backend==='indextts'){
  for(const [name,value] of [['Default duration',1],['Slightly slower · 1.1',1.1]]){const button=el('button',name,'secondary');button.type='button';button.onclick=()=>{$('duration_factor').value=value;pendingKey=null;persist()};$('backend-presets').append(button)}
  $('emotion-vector-fields').replaceChildren();
  for(const name of ['happy','angry','sad','afraid','disgusted','melancholic','surprised','calm']){
   const label=el('label',name[0].toUpperCase()+name.slice(1));label.htmlFor='index-vector-'+name;
   const input=el('input');input.id=label.htmlFor;input.type='number';input.min=0;input.max=1;input.step=.05;input.value=0;input.oninput=()=>updateIndexEmotion();
   $('emotion-vector-fields').append(label,input);
  }updateIndexEmotion();
 }
 if(caps.backend==='fish'){
  const label=el('label','Delivery tags · one per line');label.htmlFor='fish-delivery-tags';const input=el('textarea');input.id=label.htmlFor;input.maxLength=244;input.placeholder='low voice';
  $('performance-controls').append(label,input,el('p','Up to four short directions. Brackets are added for you. Leave empty to follow the reference; acting can change the accent.','hint'));
 }
 if(caps.backend==='higgs')for(const key of ['emotion','prosody','style']){
  const label=el('label',key[0].toUpperCase()+key.slice(1));label.htmlFor='higgs-'+key;const select=el('select');select.id=label.htmlFor;select.append(new Option('Follow recording',''));
  for(const value of caps.controls.higgs[key].values)select.append(new Option(value.replaceAll('_',' '),value));$('performance-controls').append(label,select);
 }
 if(caps.controls[caps.backend]?.top_p){
  const details=el('details'),summary=el('summary','Advanced sampling');details.append(summary);
  for(const [key,id,name] of [['top_p','model-top-p','Nucleus sampling'],['top_k','model-top-k','Sampling candidates']]){const spec=caps.controls[caps.backend][key];const label=el('label',name);label.htmlFor=id;const input=el('input');input.id=id;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='top_p'?.05:1;input.value=spec.default;details.append(label,input)}
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
   const spec=caps.controls[caps.backend][key],field=el('div'),label=el('label',caps.backend==='moss'?name.replace('Extra pause','Pause target'):name),number=el('input');label.htmlFor=key;number.id=key;number.type='number';number.min=spec.min;number.max=spec.max;number.step=50;number.value=spec.default;field.append(label,number);fields.append(field);
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
 if(caps.backend==='higgs')for(const key of ['emotion','prosody','style'])$('higgs-'+key).value=options[key]||'';
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
 for(const key of ['steps','guidance','temperature'])if(caps.controls[key])$(key).value=caps.controls[key].default;
 const scoped=caps.controls[caps.backend]||{};
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence'])if(scoped[key])$(key).value=scoped[key].default;
 if(caps.backend==='chatterbox')$('chatterbox-delivery-preset').value='default';
 if(caps.backend==='fish')$('fish-delivery-tags').value='';
 if(caps.backend==='higgs')for(const key of ['emotion','prosody','style'])$('higgs-'+key).value='';
 if(scoped.top_p){$('model-top-p').value=scoped.top_p.default;$('model-top-k').value=scoped.top_k.default;}
 if(scoped.punctuation_pauses){$('punctuation-pauses').checked=true;for(const key of ['sentence_pause_ms','comma_pause_ms'])$(key).value=scoped[key].default;updatePacingControls();}
 if(caps.backend==='indextts'){$('index-emotion-mode').value='follow';updateIndexEmotion();}
 $('clone-mode').value='auto';updateCapabilities();pendingKey=null;updatePerformancePreview();persist();announce('Recommended model performance defaults applied. Voice and dialogue kept.');
};

$('open-queue').onclick=()=>{$('queue-error').hidden=true;$('queue-dialog').showModal();refreshQueue().catch(exc=>error(exc.message))};
$('close-queue').onclick=()=>$('queue-dialog').close();
async function openPageSelection(){
 const params=new URLSearchParams(location.search);
 if(params.get('candidates')&&!collectionPage){watchCandidates(params.get('candidates'));return true}
 if(collectionPage){if(params.get('tab')==='voices')setCollection('voices');return}
 if(/^[a-f0-9]{32}$/.test(params.get('voice')||'')){const row=await api('/api/voices/'+params.get('voice'));chooseVoice(row,false);return true;}
 else if(/^[a-f0-9]{32}$/.test(params.get('artifact')||params.get('reuse')||'')){const row=await api('/api/artifacts/'+(params.get('artifact')||params.get('reuse')));if(params.has('reuse'))restore(row);showResult(row);return true;}
 else if(/^[a-f0-9]{32}$/.test(params.get('job')||'')){watch(params.get('job'));return true;}
 return false;
}
