
const $=id=>document.getElementById(id);let mode='design',collection='history',reference=null,latest=null,voiceRows=[],historyRows=[],busy=false,online=false,uploading=false,watching=null;
let pageSize = 24, caps=null, storageKey='voice-studio', historyPage={data:[],total:0,has_more:false}, pendingKey=null;
let voicePage = {data: [], total: 0, has_more: false};
let catalogOffset = 0, searchTimer = null, catalogRequest = 0, voiceEditor = null;
const presets={natural:'Natural, conversational delivery with relaxed pacing.',joy:'Excited and joyful, with an audible smile and lively pacing.',sadness:'Quiet sadness and disappointment, speaking slowly with a trembling voice.',anger:'Angry and frustrated, with firm emphasis and a tense, forceful delivery.',fear:'Frightened and anxious, with a trembling voice and urgent, uneven pacing.',relief:'Deeply relieved, softening into warmth with a gentle exhale.',sarcasm:'Dry, understated sarcasm with deliberate pauses and restrained emphasis.',whisper:'A soft, intimate whisper, speaking gently and slowly.',custom:''};
async function api(path,options={}){const response=await fetch(path,options);if(!response.ok){let body;try{body=await response.json()}catch{}throw Error(typeof body?.detail==='string'?body.detail:body?.detail?.[0]?.msg||`Request failed (${response.status})`)}return response.json()}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function error(message){$('error').textContent=message;$('error').hidden=false}function announce(text){$('announcement').textContent=text}
function controls(){ $('generate').disabled=!online||busy||uploading;$('unload').disabled=!online||busy;$('new-seed').disabled=busy }
function setMode(next){if(caps){if(next==='design'&&caps.modes.design.available===false)next='upload';if((next==='saved'||next==='upload')&&caps.modes.reference.available===false)next='design'}pendingKey=null;mode=next;for(const button of document.querySelectorAll('[data-mode]'))button.setAttribute('aria-selected',button.dataset.mode===mode);for(const name of ['design','saved','upload','preset'])$(name+'-panel').hidden=name!==mode;updateCapabilities();persist()}
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>setMode(button.dataset.mode));
$('emotion-preset').onchange=()=>{$('emotion').value=presets[$('emotion-preset').value];persist()};$('emotion').oninput=()=>{$('emotion-preset').value='custom';persist()};
function count(){$('count').textContent=`${$('text').value.length} / ${caps?.limits.text_characters||3000}`}count();
function settings(){
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=referenceMode?(caps?.modes.reference.emotion&&$('clone-mode').value!=='continuation'):mode==='preset'||caps?.modes.design.emotion;
 const result={text:$('text').value,description:mode==='design'?$('description').value:'',
   emotion:direction?$('emotion').value:'',accent:direction?$('accent').value:'',
   seed:Number($('seed').value),max_tokens:caps?.limits.max_tokens_fixed?1000:Number($('max-tokens').value),
   preset_voice:mode==='preset'?$('preset-voice').value||null:null,
   voice_id:mode==='saved'?$('voice').value||null:null,
   reference_id:mode==='upload'?reference?.id||null:null,
   clone_mode:referenceMode&&caps?.modes.reference.continuation?$('clone-mode').value:'reference',
   language:$('language').value||'auto',output_dir:$('output-dir').value.trim()||null};
 for(const key of ['steps','guidance','temperature'])if(caps?.controls[key])result[key]=Number($(key).value);
 if(referenceMode&&(caps?.modes.reference.transcript&&caps?.backend!=='voxcpm2'||result.clone_mode==='continuation'))result.ref_text=$('ref-text').value||null;
 if(mode==='preset'&&$('steering-emotion').value){result.steering_emotion=$('steering-emotion').value;result.steering_strength=Number($('steering-strength').value)}
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence'])if(caps?.controls[key])result[key]=Number($(key).value);
 if(caps?.controls.emotion_vector){
   if($('emotion-reference-id').value)result.emotion_reference_id=$('emotion-reference-id').value;
   if($('emotion-vector').value.trim())result.emotion_vector=$('emotion-vector').value.split(',').map(Number);
   if(result.emotion_reference_id||result.emotion_vector)result.emotion_alpha=Number($('emotion_alpha').value);
 }
 return result;
}
function persist(){if(caps)localStorage.setItem(storageKey+'-form',JSON.stringify({...settings(),description:$('description').value,accent:$('accent').value,emotion:$('emotion').value,mode,preset:$('emotion-preset').value}))}
function updateCapabilities(){
 if(!caps)return;
 const referenceMode=mode==='saved'||mode==='upload';
 const direction=referenceMode?(caps.modes.reference.emotion&&$('clone-mode').value!=='continuation'):mode==='preset'||caps.modes.design.emotion;
 $('emotion').disabled=$('emotion-preset').disabled=$('accent').disabled=!direction;
 $('emotion-hint').textContent=referenceMode&&$('clone-mode').value==='continuation'?'Continuation follows the reference delivery. Choose Voice reference to direct a different emotion or accent.':direction?'Direct the performance in words. Voice identity and expression are approximate.':'This model takes its voice from the recording. Use only the controls and inline tags listed below.';
 $('accent-hint').textContent=direction?'A prompt direction, not a trained accent pack. A recording with the desired accent is usually the stronger cue.':'The reference recording supplies the accent in this mode.';
 for(const key of ['steps','guidance','temperature'])$(key+'-field').hidden=!caps.controls[key];
 document.querySelector('[data-mode="preset"]').hidden=!caps.modes.preset?.available;
 document.querySelector('[data-mode="design"]').hidden=caps.modes.design.available===false;
 for(const name of ['saved','upload'])document.querySelector('[data-mode="'+name+'"]').hidden=caps.modes.reference.available===false;
 $('language-field').hidden=caps.languages.length<2;
 $('text').maxLength=caps.limits.text_characters;
 $('max-tokens-field').hidden=!!caps.limits.max_tokens_fixed;
 $('index-emotion').hidden=!caps.controls.emotion_vector;
 $('inline-hint').hidden=!caps.limits.inline_controls;
 $('inline-hint').textContent=caps.backend==='fish'?'Inline delivery: [whisper], [pause], [with strong accent]. Tags guide delivery; audition accent retention.':caps.backend==='higgs'?'Inline delivery: <|emotion:amusement|>, <|prosody:expressive_high|>, <|prosody:pause|>, <|prosody:long_pause|>. Sound events also need matching written sounds.':'';
 $('reference-options').hidden=!referenceMode;
 $('clone-mode-field').hidden=!caps.modes.reference.continuation;
 const transcript=referenceMode&&(caps.modes.reference.transcript&&caps.backend!=='voxcpm2'||$('clone-mode').value==='continuation');
 $('transcript-field').hidden=!transcript;
 $('ref-text-label').textContent=caps.backend==='qwen3'?'Exact reference transcript (optional)':'Exact reference transcript (or use saved transcript)';
 $('transcript-hint').textContent=caps.modes.reference.transcript_required?'This model requires the exact words in the reference recording. Supply them here or in the saved voice.':caps.backend==='qwen3'?'An exact transcript improves cloning. Leave blank to use the saved transcript, or speaker embedding mode when none is saved.':'Continuation requires the exact words in the saved recording or uploaded reference. Supply them here or in the voice catalog.';
 count();
 $('edit-transcript-field').hidden=false;
 $('steering-field').hidden=!caps.extensions?.emotion_steering;
 $('steering-strength-field').hidden=!$('steering-emotion').value;
}
$('clone-mode').onchange=()=>{updateCapabilities();persist()};
$('steering-emotion').onchange=()=>{if($('steering-emotion').value){if($('emotion-preset').value==='natural'){$('emotion').value='';$('emotion-preset').value='custom'}if(Number($('temperature').value)===.9)$('temperature').value='1.1'}updateCapabilities();persist()};
function restore(row){
 $('text').value=row.text||'';$('description').value=row.description||'';$('emotion').value=row.preset_voice&&!row.mode?[row.description,row.emotion].filter(Boolean).map(x=>x.trim().replace(/\.+$/,'')).join('. '):row.emotion||'';
 $('accent').value=row.accent||'';$('emotion-preset').value='custom';
 $('clone-mode').value=row.clone_mode||'reference';$('steering-emotion').value=row.steering_emotion||'';
 $('steering-strength').value=row.steering_strength??8;
 for(const [key,id]of [['seed','seed'],['steps','steps'],['guidance','guidance'],['temperature','temperature'],['language','language'],['output_dir','output-dir'],['max_tokens','max-tokens']])if(row[key]!=null)$(id).value=row[key];
 if(row.preset_voice&&caps.modes.preset?.available){$('preset-voice').value=row.preset_voice;setMode('preset')}
 else if(row.voice_id&&voiceRows.some(v=>v.id===row.voice_id)){$('voice').value=row.voice_id;setMode('saved');voicePreview()}
 else if(row.reference_id){reference={id:row.reference_id};$('reference-info').textContent='Using the reference saved with this take.';$('reference-preview').src=`/api/audio/references/${row.reference_id}`;$('reference-preview').hidden=false;setMode('upload')}
 else setMode('design');
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha'])if($(key))$(key).value=row[key]??caps.controls[key]?.default??1;
 $('emotion-reference-id').value=row.emotion_reference_id||'';$('emotion-vector').value=row.emotion_vector?.join(',')||'';
 $('emotion-reference-info').textContent=row.emotion_reference_id?'Using the saved emotion recording.':'';
 $('ref-text').value=row.ref_text||'';updateCapabilities();count();persist();
}
$('form').addEventListener('input',()=>{pendingKey=null;count();persist()});$('form').addEventListener('change',()=>{pendingKey=null;persist()});
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
      const reuse = el('button', 'Reuse settings', 'secondary'); reuse.type = 'button';
      reuse.onclick = () => { restore(row); $('text').focus(); }; actions.append(reuse);
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
function waveform(peaks){const canvas=$('waveform'),ratio=devicePixelRatio||1;canvas.width=canvas.clientWidth*ratio;canvas.height=110*ratio;const c=canvas.getContext('2d');c.clearRect(0,0,canvas.width,canvas.height);c.fillStyle='#c3b9ff';if(!peaks?.length)return;const step=canvas.width/peaks.length;peaks.forEach((peak,i)=>{const h=Math.max(2*ratio,peak*canvas.height*.9);c.fillRect(i*step,(canvas.height-h)/2,Math.max(1,step*.55),h)})}
function showResult(row){latest=row;const details=[row.preset_voice?'Preset '+row.preset_voice:'',row.clone_mode==='continuation'?'Continued reference performance':'',row.accent?'Accent direction: '+row.accent:'',row.steering_emotion?`Experimental ${row.steering_emotion} steering · ${row.steering_strength}`:''].filter(Boolean);$('direction-summary').textContent=details.join(' · ');$('direction-summary').hidden=!details.length;$('empty').hidden=true;$('working').hidden=true;$('result').hidden=false;$('player').src=row.audio_url;$('download').href=row.audio_url+'?download=true';$('metrics').textContent=`${row.duration.toFixed(1)} seconds · ${row.sample_rate/1000} kHz · ${row.timing_scope?'Total process':'Synthesis'} ${row.generation_seconds}s${row.cold_load&&Number.isFinite(row.load_seconds)?` · Load ${row.load_seconds}s`:``}`;$('result-seed').textContent=`Seed ${row.seed}`;$('path').textContent=row.path;$('truncated').hidden=!row.possibly_truncated;$('save-hint').textContent='Use a clean, natural take as your character reference. Up to the first 30 seconds are saved.';waveform(row.waveform)}
$('reuse').onclick=()=>{if(latest){restore(latest);$('text').focus()}};
async function watch(identity){if(watching===identity)return;watching=identity;busy=true;controls();$('error').hidden=true;$('empty').hidden=true;$('result').hidden=true;$('working').hidden=false;const started=Date.now();localStorage.setItem(storageKey+'-job',identity);try{while(true){const job=await api('/api/jobs/'+identity);$('stage').textContent=job.stage;$('elapsed').textContent=`${Math.floor((Date.now()-started)/1000)} seconds elapsed`;if(job.status==='done'){localStorage.removeItem(storageKey+'-job');showResult(job.result);await refresh();announce('Speech ready');break}if(job.status==='error'){localStorage.removeItem(storageKey+'-job');throw Error(job.error);}await new Promise(resolve=>setTimeout(resolve,1500))}}catch(exc){error(exc.message);$('working').hidden=true;$('empty').hidden=!latest;if(latest)showResult(latest)}finally{busy=false;watching=null;controls()}}
$('form').onsubmit=async event=>{event.preventDefault();if(busy||uploading)return;const request=settings();if(mode==='preset'&&!request.preset_voice){error('Choose an expressive preset voice first.');return}if(mode==='saved'&&!request.voice_id){error('Choose a saved voice first.');return}if(mode==='upload'&&!request.reference_id){error('Choose an audio reference first.');return}busy=true;controls();try{pendingKey=pendingKey||crypto.randomUUID();const job=await post('/api/generate',{...request,idempotency_key:pendingKey});pendingKey=null;watch(job.id)}catch(exc){busy=false;controls();error(exc.message)}};
document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();if(!$('generate').disabled)$('form').requestSubmit()}});
$('save-voice').onclick = () => {
  if (latest) openVoiceEditor({output_id: latest.id, name: $('voice-name').value,
    description: latest.description || '', transcript:latest.duration<=30?latest.text:'',tags: []});
};
$('reference').onchange=async()=>{const file=$('reference').files[0];reference=null;$('reference-preview').hidden=true;$('save-reference').hidden=true;if(!file)return;uploading=true;controls();$('reference-info').textContent='Preparing reference…';try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);body.append('transcript',$('ref-text').value);reference=await api('/api/references',{method:'POST',body});$('reference-info').textContent=`${reference.name} · ${reference.duration}s`;$('reference-preview').src=reference.audio_url;$('reference-preview').hidden=false;$('save-reference').hidden=false}catch(exc){$('reference-info').textContent='';error(exc.message)}finally{uploading=false;controls()}};
$('save-reference').onclick = () => {
  if (reference) openVoiceEditor({reference_id: reference.id,
    name: (reference.name || 'Uploaded voice').replace(/\.[^.]+$/, '').slice(0, 100), description: '', transcript:$('ref-text').value,tags: []});
};
$('unload').onclick=async()=>{try{await post('/api/model/unload',{});announce('Model memory released. It reloads on the next take.');await health()}catch(exc){error(exc.message)}};
async function health(){try{const state=await api('/health');online=state.downloaded&&state.status!=='error';$('status').dataset.offline=!online;$('status').textContent=state.status==='error'?(state.last_error||'Setup incomplete'):state.busy?state.status:state.model_loaded?'Model loaded · Ready':caps?.limits.model_residency==='per job subprocess'?'Ready · Loads per take':'Ready · Loads on first take';if(state.active_job&&!watching)watch(state.active_job);else if(!watching)busy=state.busy}catch{online=false;$('status').textContent='Server offline';$('status').dataset.offline=true}controls()}
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
  if (!voiceRows.some(item => item.id === row.id)) voiceRows.push(row);
  if (![...$('voice').options].some(option => option.value === row.id)) $('voice').append(new Option(row.name, row.id));
  $('voice').value = row.id; setMode('saved'); voicePreview();
  if (focus) { $('text').focus(); $('form').scrollIntoView({block: 'start'}); }
}
function openVoiceEditor(context) {
  voiceEditor = context;
  $('voice-editor-title').textContent = context.edit_id ? 'Edit voice details' : 'Save a voice';
  $('voice-editor-save').textContent = context.edit_id ? 'Save changes' : 'Save voice';
  $('voice-editor-hint').textContent = context.edit_id ?
    'Update the catalog details. The saved recording stays the same.' :
    'Name this character, describe it, and add tags so you can find it later.';
  $('edit-voice-name').value = context.name || '';
  $('edit-voice-description').value = context.description || '';
  $('edit-voice-tags').value = (context.tags || []).join(', ');
  $('edit-transcript').value=context.transcript||'';
  $('voice-editor-error').hidden = true;
  $('voice-editor').showModal(); $('edit-voice-name').focus();
}
$('voice-editor-cancel').onclick = () => $('voice-editor').close();
$('voice-editor-form').onsubmit = async event => {
  event.preventDefault(); if (!voiceEditor) return;
  const context = voiceEditor;
  const details = {name: $('edit-voice-name').value.trim(), description: $('edit-voice-description').value.trim(),
    tags: $('edit-voice-tags').value.split(',').map(tag => tag.trim()).filter(Boolean),transcript:$('edit-transcript').value.trim()};
  if (details.tags.length > 20 || details.tags.some(tag => tag.length > 40)) {
    $('voice-editor-error').textContent = 'Use up to 20 tags, each no more than 40 characters.';
    $('voice-editor-error').hidden = false; return;
  }
  $('voice-editor-save').disabled = true; $('voice-editor-cancel').disabled = true;
  try {
    let row;
    if (context.edit_id) row = await api('/api/voices/' + context.edit_id,
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
    $('voice-editor-save').disabled = false; $('voice-editor-cancel').disabled = false;
  }
};
$('tag-filter').onchange = $('voice-sort').onchange = () => {catalogOffset = 0; loadVoicePage().catch(exc => error(exc.message));};
$('voice-prev').onclick = () => {catalogOffset = Math.max(0, catalogOffset - pageSize); loadCollection().catch(exc => error(exc.message));};
$('voice-next').onclick = () => {catalogOffset += pageSize; loadCollection().catch(exc => error(exc.message));};
$('browse-voices').onclick = $('catalog-link').onclick = event => {
  event.preventDefault(); setCollection('voices'); $('voice-catalog').scrollIntoView({block: 'start'});
};
$('page-size').onchange=()=>{pageSize=Number($('page-size').value);catalogOffset=0;loadCollection().catch(exc=>error(exc.message))};
(async()=>{try{caps=await api('/api/capabilities');storageKey=caps.service;document.title=caps.name+' Voice Studio';$('brand-name').textContent=caps.name;$('model-badge').textContent=`${caps.sample_rate/1000} kHz · ${caps.quantization} · On your Mac`;$('model-footer').textContent=`${caps.name} · ${caps.quantization} · ${caps.limits.runtime||'MLX'} on Apple Silicon`;$('storage-footer').textContent=caps.storage;$('output-dir').placeholder=caps.output_dir;$('language').replaceChildren(...caps.languages.map(x=>new Option(x,x)));$('preset-voice').replaceChildren(...caps.presets.map(x=>new Option(`${x.name} · ${x.language}`,x.id)));for(const emotion of caps.extensions?.emotion_steering?.emotions||[])$('steering-emotion').append(new Option(emotion[0].toUpperCase()+emotion.slice(1),emotion));$('settings-hint').textContent=(caps.limits.max_tokens_fixed?'This upstream Chatterbox API uses a fixed 1000-token ceiling. ':caps.backend==='moss'?'The audio-frame ceiling can be raised by the upstream text-derived minimum. ':'The audio token limit is a safety cap. Reaching it can cut a take short. ')+(caps.limits.memory_note||'A matching seed alone does not lock a voice.');buildBackendSettings();updateCapabilities();await refresh();const saved=JSON.parse(localStorage.getItem(storageKey+'-form')||'null');if(saved)restore(saved);else setMode(caps.modes.design.available===false?'upload':'design');await health();const job=localStorage.getItem(storageKey+'-job');if(job&&!watching)watch(job)}catch(exc){error(exc.message)}setInterval(health,5000)})();

function buildBackendSettings(){
 $('backend-settings').replaceChildren();
 for(const key of ['exaggeration','cfg_weight','duration_factor','interval_silence','emotion_alpha']){
  const spec=caps.controls[key];if(!spec)continue;
  const names={exaggeration:'Exaggeration',cfg_weight:'Voice guidance',duration_factor:'Duration factor · higher is slower',interval_silence:'Pause between generated segments (ms)',emotion_alpha:'Emotion blend'};
  const label=el('label',names[key]);label.htmlFor=key;const input=el('input');input.id=key;input.type='number';input.min=spec.min;input.max=spec.max;input.step=key==='interval_silence'?50:.05;input.value=spec.default;
  $('backend-settings').append(label,input);
 }
 for(const key of ['steps','guidance','temperature'])if(caps.controls[key])$(key).value=caps.controls[key].default;
 for(const option of $('max-tokens').options){option.disabled=Number(option.value)>caps.controls.max_tokens.max;if(caps.backend==='moss')option.textContent=option.value+' audio frames'}

}
$('emotion-reference').onchange=async()=>{
 const file=$('emotion-reference').files[0];if(!file)return;
 uploading=true;controls();
 try{if(file.size>25*1024*1024)throw Error('Choose a file under 25 MB.');const body=new FormData();body.append('file',file);const record=await api('/api/references',{method:'POST',body});$('emotion-reference-id').value=record.id;$('emotion-reference-info').textContent=record.name+' · '+record.duration+'s';$('emotion-vector').value='';pendingKey=null;persist()}
 catch(exc){error(exc.message)}finally{uploading=false;controls()}
};
$('clear-emotion-reference').onclick=()=>{$('emotion-reference-id').value='';$('emotion-reference').value='';$('emotion-reference-info').textContent='';pendingKey=null;persist()};
