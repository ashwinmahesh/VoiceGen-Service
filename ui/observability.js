(()=>{
'use strict';
const $=id=>document.getElementById(id),svgNS='http://www.w3.org/2000/svg';
const colors={higgs:'#9dcca8',voxcpm2:'#8ebfea',qwen3:'#e8bf83',chatterbox:'#e79eaa',indextts:'#6fd0cc',moss:'#c6c78e',fish:'#c5b9e7'};
const names={};let windowName='5m',view='timeline',model='',offset=0,selectedJob=null,selectedRun=null,requestNumber=0,inFlight=false;
let modelWindow='30d',modelOffset=0,jobModelOffset=0,modelAction='',modelScope='';
const metrics=[{key:'rss_bytes',title:'VoiceGen memory',scope:'Service + child processes · RSS',unit:'GiB',factor:2**30},
 {key:'cpu_percent',title:'VoiceGen CPU',scope:'Service + child processes · 100% = one core',unit:'%',factor:1},
 {key:'gpu_percent',title:'GPU utilization',scope:'Whole Mac · includes other apps',unit:'%',factor:1,max:100},
 {key:'gpu_allocated_bytes',title:'GPU memory allocated',scope:'Whole Mac · shared system memory',unit:'GiB',factor:2**30}];
const number=(value,digits=1)=>Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits}):'Unavailable';
const duration=seconds=>seconds<60?number(seconds)+'s':seconds<3600?number(seconds/60)+'m':number(seconds/3600)+'h';
const timeLabel=(timestamp,long=false)=>new Date(timestamp*1000).toLocaleString(undefined,long?{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}:{hour:'2-digit',minute:'2-digit',second:'2-digit'});
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function svg(tag,attrs,text){const n=document.createElementNS(svgNS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n}
async function api(path){return VoiceRecording.readJSON(path)}
function error(message){$('resource-error').textContent=message;$('resource-error').hidden=!message}
function status(data){const c=data.collector;if(!c)return;$('collector-status').textContent=c.running?'Recording':'Recorder stopped';const stale=c.last_sample_at&&Date.now()/1000-c.last_sample_at>15;$('collector-status').textContent+=(stale?' · waiting for samples':'');const warnings=[c.storage_error,c.lifecycle_error,...(c.counter_notices||[])];if(c.dropped_lifecycle_events)warnings.push(`${c.dropped_lifecycle_events} model event boundaries could not be saved.`);if(c.dropped_samples)warnings.push(`${c.dropped_samples} sample(s) could not be saved in this session.`);error(warnings.filter(Boolean).join(' '))}
function legend(target,data){const items=new Map(data.models.map(row=>[row.backend,row]));target.replaceChildren(...[...items.keys()].map(backend=>{const n=node('span');const dot=node('i');dot.style.background=colors[backend]||'#aaa';n.append(dot,document.createTextNode(names[backend]||backend));return n}));if(!items.size)target.append(node('span','No recorded samples in this window.'))}
function charts(target,data,job=false){
 const focus=document.activeElement?.closest('.plot')?.dataset.metric;
 target.replaceChildren(...metrics.map(metric=>chart(metric,data,job)));
 if(focus&&target.contains(document.activeElement)===false)target.querySelector(`[data-metric="${focus}"]`)?.focus({preventScroll:true});
}
function chart(metric,data,job){
 const card=node('article',undefined,'chart'),heading=node('div',undefined,'chart-heading');heading.append(node('h3',metric.title),node('span',metric.unit,'scope'));
 const scoped=node('div',metric.scope,'scope'),peak=data.summary['peak_'+metric.key],valid=data.points.filter(p=>Number.isFinite(p[metric.key])),latest=valid.at(-1);
 const value=node('div',latest?`${number(latest[metric.key]/metric.factor,metric.unit==='GiB'?2:1)} ${metric.unit}`:'Unavailable','chart-value');value.append(node('small',`Peak ${number(Number.isFinite(peak)?peak/metric.factor:null)}${Number.isFinite(peak)?' '+metric.unit:''}`));
 const plot=node('div',undefined,'plot');plot.dataset.metric=metric.key;plot.tabIndex=0;plot.setAttribute('role','img');plot.setAttribute('aria-label',`${metric.title} line graph. ${metric.scope}. Use left and right arrow keys to inspect samples.`);
 const tooltip=node('p','Hover or use arrow keys to inspect a sample.','chart-tooltip');tooltip.setAttribute('aria-live','polite');
 const width=680,height=215,left=48,right=12,top=18,bottom=28,graph=svg('svg',{viewBox:`0 0 ${width} ${height}`,preserveAspectRatio:'none','aria-hidden':'true'});
 const start=data.start,end=Math.max(start+1,data.end),max=metric.max||Math.max(metric.unit==='GiB'?1:10,...data.points.map(p=>(p['peak_'+metric.key]||0)/metric.factor))*1.12;
 const x=t=>left+(t-start)/(end-start)*(width-left-right),y=v=>height-bottom-v/max*(height-top-bottom);
 for(let i=0;i<=4;i++){const v=max*i/4;graph.append(svg('line',{x1:left,x2:width-right,y1:y(v),y2:y(v),class:'grid'}),svg('text',{x:left-8,y:y(v)+3,'text-anchor':'end'},number(v,max<10?1:0)))}
 for(let i=0;i<=3;i++){const t=start+(end-start)*i/3;const label=job?duration(t-start):(end-start>86400?new Date(t*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'}):timeLabel(t).replace(/:\d\d(?=\s|$)/,''));graph.append(svg('text',{x:x(t),y:height-5,'text-anchor':i===0?'start':i===3?'end':'middle'},label))}
 const groups=new Map();for(const point of data.points){const key=[point.session_id,point.model,job?point.run_id:''].join(':');if(!groups.has(key))groups.set(key,[]);groups.get(key).push(point)}
 for(const points of groups.values()){
  let segment=[],last=null;const flush=()=>{if(!segment.length)return;const color=colors[segment[0].backend]||'#aaa';graph.append(svg('path',{d:segment.map((p,i)=>`${i?'L':'M'}${x(p.ts)} ${y(p[metric.key]/metric.factor)}`).join(' '),fill:'none',stroke:color,'stroke-width':2,'vector-effect':'non-scaling-stroke'}));if(segment.length===1)graph.append(svg('circle',{cx:x(segment[0].ts),cy:y(segment[0][metric.key]/metric.factor),r:3,fill:color}));segment=[]};
  for(const p of points){if(!Number.isFinite(p[metric.key])||(last!==null&&p.ts-last>Math.max(15,data.bucket_seconds*2.5))){flush();last=null}if(Number.isFinite(p[metric.key])){segment.push(p);last=p.last_ts||p.ts}}flush();
 }
 const cursor=svg('line',{x1:0,x2:0,y1:top,y2:height-bottom,stroke:'#d3d4dc','stroke-dasharray':'3 3',visibility:'hidden'});graph.append(cursor);plot.append(graph);
 if(!valid.length)plot.append(node('div',data.points.length?'This counter is unavailable.':'Waiting for recorded samples.','plot-empty'));
 let selected=-1;
 function inspect(index){if(!valid.length)return;selected=Math.max(0,Math.min(index,valid.length-1));const p=valid[selected];cursor.setAttribute('x1',x(p.ts));cursor.setAttribute('x2',x(p.ts));cursor.setAttribute('visibility','visible');tooltip.textContent=`${job?duration(p.ts-start)+' · ':''}${timeLabel(p.ts,true)} · ${names[p.backend]||p.backend} · ${p.model} · ${p.stage} · ${number(p[metric.key]/metric.factor,metric.unit==='GiB'?2:1)} ${metric.unit} · peak ${number(p['peak_'+metric.key]/metric.factor,metric.unit==='GiB'?2:1)} ${metric.unit}${p.samples>1?' · '+p.samples+' samples':''}`}
 plot.onpointermove=e=>{const rect=plot.getBoundingClientRect(),t=start+((e.clientX-rect.left)/rect.width*width-left)/(width-left-right)*(end-start);let best=0;for(let i=1;i<valid.length;i++)if(Math.abs(valid[i].ts-t)<Math.abs(valid[best].ts-t))best=i;inspect(best)};
 plot.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();inspect(e.key==='Home'?0:e.key==='End'?valid.length-1:selected<0?0:selected+(e.key==='ArrowLeft'?-1:1))}};
 card.append(heading,scoped,value,plot,tooltip);return card;
}
function setView(next){view=next;if(next!=='jobs')history.replaceState(null,'',location.pathname+(next==='models'?'?view=models':''));for(const key of ['timeline','jobs','models']){$(key+'-panel').hidden=next!==key;$(key+'-tab').setAttribute('aria-selected',String(next===key))}refresh(true)}
const boundaryNames={runtime_process:'Backend process',weights:'Python weights',native_weights:'Native weights & session',native_release:'Native release',llm_weights:'Prompt LLM weights'};
const reasonNames={first_use:'First use',variant_change:'Variant change',model_switch:'Model switch',memory_release:'Release memory',shutdown:'Server shutdown',failure_cleanup:'Failure cleanup',per_job:'Per-take process',prompt_expansion:'Prompt expansion'};
function lifecycleTable(headings,rows){const scroll=node('div',undefined,'table-scroll'),table=node('table'),thead=node('thead'),tr=node('tr'),tbody=node('tbody');for(const text of headings)tr.append(node('th',text));thead.append(tr);tbody.append(...rows);table.append(thead,tbody);scroll.append(table);return scroll}
function renderLifecycle(target,data,job=false){
 const heading=node('div',undefined,'lifecycle-counts');for(const key of ['success','running','error','interrupted'])heading.append(node('span',`${data.counts[key]||0} ${key==='success'?'completed':key==='error'?'failed':key}`));
 const caption=node('p',data.total?`${data.offset+1}–${data.offset+data.data.length} of ${data.total} operations · All timestamps are local time.`:'No model operations recorded in this range. Warm reuse does not reload weights.','hint');
 const rows=data.data.map(row=>{const tr=node('tr'),started=node('td',timeLabel(row.started_at,true));started.title=new Date(row.started_at*1000).toISOString();const identity=node('td',names[row.backend]||row.backend);identity.append(node('small',row.model));const boundary=node('td',boundaryNames[row.scope]||row.scope);boundary.title=data.scopes[row.scope]||'';const state=node('td',row.status==='error'?'Failed':row.status==='success'?'Completed':row.status==='running'?'In progress':'Interrupted');state.className='event-state '+row.status;
  tr.append(started,identity,node('td',row.action==='load'?'Load':'Unload'),boundary,node('td',Number.isFinite(row.duration_seconds)?number(row.duration_seconds,3)+'s':row.status==='running'?'Measuring…':'Unknown'),state,node('td',reasonNames[row.reason]||row.reason));
  const trigger=node('td');if(row.job_id){const a=node('a',row.job_id.slice(0,8)+' ↗');a.href='/observability?'+new URLSearchParams({job:row.job_id,...(row.run_id?{run:row.run_id}:{})});trigger.append(a,node('small','Attempt '+row.attempt))}else trigger.textContent='Outside a job';tr.append(trigger);return tr});
 const children=[heading,caption];
 const measured=data.data.filter(row=>Number.isFinite(row.duration_seconds));
 if(measured.length){
  const card=node('article',undefined,'chart lifecycle-plot'),graph=svg('svg',{viewBox:'0 0 840 190',role:'img','aria-label':'Individual operation durations on this page. Circles are loads, squares are unloads. No interpolation between events.'});
  const min=job?Math.min(...data.data.map(r=>r.started_at)):data.start??Math.min(...data.data.map(r=>r.started_at)),max=Math.max(min+1,job?Math.max(...data.data.map(r=>r.status==='running'?Date.now()/1000:r.ended_at??r.started_at)):data.end),peak=Math.max(.01,...measured.map(r=>r.duration_seconds))*1.15;
  const x=t=>50+(t-min)/(max-min)*775,y=v=>157-v/peak*133;
  for(let i=0;i<=3;i++){const v=peak*i/3;graph.append(svg('line',{x1:50,x2:825,y1:y(v),y2:y(v),class:'grid'}),svg('text',{x:43,y:y(v)+3,'text-anchor':'end'},number(v,2)+'s'));const t=min+(max-min)*i/3;graph.append(svg('text',{x:x(t),y:181,'text-anchor':i===0?'start':i===3?'end':'middle'},(max-min>86400?new Date(t*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'}):timeLabel(t))))}
  for(const row of measured){const description=`${timeLabel(row.started_at,true)} · ${names[row.backend]||row.backend} · ${row.action} · ${boundaryNames[row.scope]} · ${number(row.duration_seconds,3)}s · ${row.status}`;const mark=svg(row.action==='load'?'circle':'rect',row.action==='load'?{cx:x(row.started_at),cy:y(row.duration_seconds),r:4}:{x:x(row.started_at)-4,y:y(row.duration_seconds)-4,width:8,height:8});mark.setAttribute('fill',colors[row.backend]||'#aaa');mark.setAttribute('tabindex','0');mark.setAttribute('role','img');mark.setAttribute('aria-label',description);mark.append(svg('title',{},description));graph.append(mark)}
  card.append(node('h3','Individual durations · current page only'),node('p','● Load   ■ Unload · Hover or focus a mark for its operation. No lines connect separate operations.','hint'),graph);children.push(card);
 }
 if(rows.length)children.push(lifecycleTable(['Started','Model','Operation','Boundary','Duration','Status','Reason','Triggered by'],rows));
 if(data.summaries.length){children.push(node('h3','Successful operations · all matching events','lifecycle-summary-title'),node('p','Mean and maximum are grouped by model and boundary. Scopes can overlap; do not add their durations.','hint'),lifecycleTable(['Model','Operation','Boundary','Count','Mean','Maximum'],data.summaries.map(row=>{const tr=node('tr'),modelCell=node('td',names[row.backend]||row.backend);modelCell.append(node('small',row.model));tr.append(modelCell,node('td',row.action==='load'?'Load':'Unload'),node('td',boundaryNames[row.scope]),node('td',row.count.toLocaleString()),node('td',number(row.mean_seconds,3)+'s'),node('td',number(row.max_seconds,3)+'s'));return tr})));}
 target.replaceChildren(...children);
}
function clearJobLog(){
 $('job-log-caption').textContent='Job-specific output, when retained. Updates with the charts every five seconds.';
 $('job-log-output').textContent='';$('job-log-output').hidden=true;$('job-log-notice').textContent='Loading log for this attempt…';$('job-log-error').hidden=true;$('copy-job-log').disabled=true;
}
function recordedStages(job){
 const rows=[];let previous=null;
 for(const point of job.points){if(point.stage!==previous){rows.push({ts:point.ts,stage:point.stage});previous=point.stage}}
 if(job.selected_run.ended_at)rows.push({ts:job.selected_run.ended_at,stage:{done:'Finished',error:'Generation failed',interrupted:'Generation interrupted'}[job.selected_run.status]||job.selected_run.status});
 $('job-stage-list').replaceChildren(...rows.slice(-200).map(row=>{const item=node('li');item.append(node('span','+'+duration(Math.max(0,row.ts-job.start)),'stage-time'),node('span',row.stage));return item}));
 if(!rows.length)$('job-stage-list').append(node('li','No recorded stage changes for this attempt.'));
}
async function loadJobLog(job,token){
 try{
  const logs=await api('/api/jobs/'+encodeURIComponent(job.job_id)+'/logs?attempt='+job.selected_run.attempt);if(token!==requestNumber)return;
  const output=$('job-log-output'),follow=output.hidden||output.scrollHeight-output.scrollTop-output.clientHeight<24;
  if(output.textContent!==logs.text)output.textContent=logs.text;
  output.hidden=!logs.available||!logs.text;
  if(follow)output.scrollTop=output.scrollHeight;
  $('job-log-caption').textContent=`Attempt ${logs.attempt}${logs.source?' · '+logs.source:''} · Latest ${logs.max_bytes/1024} KiB${logs.truncated?' · earlier output omitted':''} · Updates every five seconds`;
  $('job-log-notice').textContent=logs.notice||'';
  $('job-log-error').textContent=logs.error||'';$('job-log-error').hidden=!logs.error;
  $('copy-job-log').disabled=!logs.text;
 }catch(exc){if(token!==requestNumber)return;clearJobLog();$('job-log-notice').textContent='Log unavailable: '+exc.message;}
}
async function loadJob(identity,run=null){clearJobLog();selectedJob=identity;selectedRun=run;jobModelOffset=0;const params=new URLSearchParams(location.search);params.set('job',identity);if(run)params.set('run',run);else params.delete('run');history.replaceState(null,'','?'+params);await refresh(true)}
async function jobLifecycle(token){
 const params=new URLSearchParams({window:'all',job_id:selectedJob,limit:25,offset:jobModelOffset});if(selectedRun)params.set('run_id',selectedRun);
 const events=await api('/api/observability/models?'+params);if(token!==requestNumber)return null;
 renderLifecycle($('job-lifecycle-history'),events,true);$('job-models-prev').disabled=jobModelOffset===0;$('job-models-next').disabled=!events.has_more;return events;
}
async function loadJobResources(token){
 let currentJob=null;
 try{currentJob=await api('/api/jobs/'+encodeURIComponent(selectedJob))}catch(exc){if(exc.status!==404&&token===requestNumber)$('job-log-notice').textContent='Recording details temporarily unavailable.'}
 if(token!==requestNumber)return;
 $('job-recording').href=VoiceRecording.url(currentJob?.candidate_batch_id?{kind:'candidates',id:currentJob.candidate_batch_id,selected:currentJob.result?.id}:{kind:'job',id:selectedJob});$('job-recording').hidden=false;
 let job;
 try{const suffix=selectedRun?'?run_id='+encodeURIComponent(selectedRun):'';job=await api('/api/observability/jobs/'+encodeURIComponent(selectedJob)+suffix)}
 catch(exc){
  if(token!==requestNumber)return;if(exc.status!==404)throw exc;
  const events=await jobLifecycle(token);if(token!==requestNumber)return;
  if(!events?.total){
   if(!currentJob)throw exc;
   const waiting=['queued','running'].includes(currentJob.status);
   $('job-detail').hidden=false;$('job-title').textContent='Job '+selectedJob.slice(0,10);
   $('job-caption').textContent=`${names[currentJob.backend]||currentJob.backend} · ${currentJob.stage||currentJob.status}`;
   $('job-attempt').replaceChildren(new Option('No recorded attempt yet',''));$('job-attempt').disabled=true;$('job-legend').replaceChildren();
   $('job-charts').replaceChildren(node('p',waiting?'Waiting for resource samples. This page updates automatically when generation starts.':'No retained resource samples for this recording. Audio remains available through Return to recording.','hint'));
   $('job-stage-list').replaceChildren(node('li',waiting?'Waiting for generation to start.':'No retained stage samples.'));
   clearJobLog();$('job-log-notice').textContent=waiting?'Logs will appear here if this model records them.':'No generation log recorded for this attempt.';
   return;
  }
  $('job-detail').hidden=false;$('job-title').textContent='Job '+selectedJob.slice(0,10);$('job-caption').textContent='Retained model operations · resource curves and sampled stages are kept for seven days.';
  $('job-attempt').replaceChildren(new Option(selectedRun?'Selected attempt':'All retained attempts',selectedRun||''));$('job-attempt').disabled=true;$('job-legend').replaceChildren();
  $('job-charts').replaceChildren(node('p','No retained resource samples for this attempt. Model load/unload history remains available below.','hint'));
  $('job-stage-list').replaceChildren(node('li','No retained stage samples for this attempt.'));
  const attempt=events.data.find(row=>row.attempt)?.attempt;
  if(selectedRun&&attempt)await loadJobLog({job_id:selectedJob,selected_run:{attempt}},token);
  else{clearJobLog();$('job-log-notice').textContent='Choose a retained attempt from Model activity to inspect any available generation log.'}
  return;
 }
 if(token!==requestNumber)return;
 selectedRun=job.selected_run.id;$('job-detail').hidden=false;$('job-title').textContent='Job '+selectedJob.slice(0,10);
 $('job-caption').textContent=`${names[job.selected_run.backend]||job.selected_run.backend} · ${job.selected_run.model} · ${job.selected_run.status} · ${duration(job.end-job.start)} · ${job.summary.samples} samples`;
 $('job-attempt').disabled=false;$('job-attempt').replaceChildren(...job.runs.map(row=>new Option(`Attempt ${row.attempt} · ${timeLabel(row.started_at,true)}`,row.id)));$('job-attempt').value=selectedRun;
 legend($('job-legend'),job);charts($('job-charts'),job,true);recordedStages(job);
 try{await jobLifecycle(token)}catch(exc){if(token===requestNumber)$('job-lifecycle-history').replaceChildren(node('p',exc.message,'hint error'))}
 if(token===requestNumber)await loadJobLog(job,token);
}
async function refresh(force=false){
 if(inFlight&&!force)return;const token=++requestNumber;inFlight=true;$('refresh-resources').disabled=true;
 try{
  const historyParams=new URLSearchParams({window:windowName});if(model)historyParams.set('model',model);
  const data=await api('/api/observability?'+historyParams);if(token!==requestNumber)return;
  if(!Object.keys(names).length){for(const row of data.available_models){names[row.id]=row.name;$('resource-model').append(new Option(row.name,row.id))}}
  status(data);
  if(view==='timeline'){
   legend($('history-legend'),data);charts($('history-charts'),data);
   $('history-caption').textContent=`${data.summary.samples.toLocaleString()} samples · ${timeLabel(data.start,true)} – ${timeLabel(data.end,true)} · ${data.bucket_seconds}s buckets${data.truncated?' · latest 5,000 buckets shown':''}`;
  }else if(view==='models'){
   const params=new URLSearchParams({window:modelWindow,limit:25,offset:modelOffset});if(model)params.set('model',model);if(modelAction)params.set('action',modelAction);if(modelScope)params.set('scope',modelScope);
   const events=await api('/api/observability/models?'+params);if(token!==requestNumber)return;if(modelOffset&&!events.data.length){modelOffset=0;return refresh(true)}
   renderLifecycle($('model-lifecycle-history'),events);$('models-prev').disabled=modelOffset===0;$('models-next').disabled=!events.has_more;
  }else{
   const params=new URLSearchParams({limit:25,offset});if(model)params.set('model',model);
   const jobs=await api('/api/observability/jobs?'+params);if(token!==requestNumber)return;if(offset&&!jobs.data.length){offset=0;return refresh(true)}
   $('jobs-count').textContent=jobs.total?`${jobs.offset+1}–${jobs.offset+jobs.data.length} of ${jobs.total} attempts`:'';
   $('jobs-empty').hidden=jobs.data.length>0;$('jobs-prev').disabled=offset===0;$('jobs-next').disabled=!jobs.has_more;
   $('resource-jobs').replaceChildren(...jobs.data.map(row=>{
    const tr=node('tr');tr.dataset.selected=String(row.job_id===selectedJob&&(!selectedRun||selectedRun===row.id));
    const job=node('td',row.job_id.slice(0,10));job.title=row.job_id;job.append(node('small',`${names[row.backend]||row.backend} · attempt ${row.attempt}`));
    for(const cell of [job,node('td',timeLabel(row.started_at,true)),node('td',row.status),node('td',duration((row.ended_at||Date.now()/1000)-row.started_at)),node('td',Number.isFinite(row.peak_rss_bytes)?number(row.peak_rss_bytes/2**30)+' GiB':'Unavailable'),node('td',Number.isFinite(row.peak_cpu_percent)?number(row.peak_cpu_percent)+'%':'Unavailable'),node('td',Number.isFinite(row.peak_gpu_percent)?number(row.peak_gpu_percent)+'%':'Unavailable')])tr.append(cell);
    const action=node('td'),button=node('button','Charts & logs','secondary');button.onclick=()=>loadJob(row.job_id,row.id);action.append(button);tr.append(action);return tr;
   }));
   if(selectedJob)await loadJobResources(token);
  }
 }catch(exc){if(token===requestNumber)error(exc.message)}finally{if(token===requestNumber){inFlight=false;$('refresh-resources').disabled=false}}
}
$('timeline-tab').onclick=()=>setView('timeline');$('jobs-tab').onclick=()=>setView('jobs');$('models-tab').onclick=()=>setView('models');
document.querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>{windowName=b.dataset.window;document.querySelectorAll('[data-window]').forEach(el=>el.setAttribute('aria-pressed',String(el===b)));refresh(true)});
$('resource-model').onchange=()=>{model=$('resource-model').value;offset=modelOffset=0;selectedJob=selectedRun=null;$('job-detail').hidden=true;history.replaceState(null,'',location.pathname+(view==='models'?'?view=models':''));refresh(true)};
document.querySelectorAll('[data-model-window]').forEach(button=>button.onclick=()=>{modelWindow=button.dataset.modelWindow;modelOffset=0;document.querySelectorAll('[data-model-window]').forEach(el=>el.setAttribute('aria-pressed',String(el===button)));refresh(true)});
$('lifecycle-action').onchange=()=>{modelAction=$('lifecycle-action').value;modelOffset=0;refresh(true)};$('lifecycle-scope').onchange=()=>{modelScope=$('lifecycle-scope').value;modelOffset=0;refresh(true)};
$('models-prev').onclick=()=>{modelOffset=Math.max(0,modelOffset-25);refresh(true)};$('models-next').onclick=()=>{modelOffset+=25;refresh(true)};
$('job-models-prev').onclick=()=>{jobModelOffset=Math.max(0,jobModelOffset-25);refresh(true)};$('job-models-next').onclick=()=>{jobModelOffset+=25;refresh(true)};
$('refresh-resources').onclick=()=>refresh(true);$('jobs-prev').onclick=()=>{offset=Math.max(0,offset-25);refresh(true)};$('jobs-next').onclick=()=>{offset+=25;refresh(true)};
$('job-attempt').onchange=()=>loadJob(selectedJob,$('job-attempt').value);
$('copy-job-log').onclick=async()=>{try{await navigator.clipboard.writeText($('job-log-output').textContent);$('job-log-notice').textContent='Visible log copied.'}catch{$('job-log-notice').textContent='Could not copy automatically. Select the log text and copy it.'}};
function resumeResources(){if(!document.hidden)refresh(true)}
document.addEventListener('visibilitychange',resumeResources);window.addEventListener('pageshow',resumeResources);window.addEventListener('online',resumeResources);window.addEventListener('focus',resumeResources);
const params=new URLSearchParams(location.search);if(/^[a-f0-9]{32}$/.test(params.get('job')||'')){selectedJob=params.get('job');selectedRun=/^[a-f0-9]{32}$/.test(params.get('run')||'')?params.get('run'):null;setView('jobs')}else if(params.get('view')==='models')setView('models');else refresh();
setInterval(()=>{if(!document.hidden)refresh()},5000);

})();
