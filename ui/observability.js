'use strict';
const $=id=>document.getElementById(id),svgNS='http://www.w3.org/2000/svg';
const colors={higgs:'#9dcca8',voxcpm2:'#8ebfea',qwen3:'#e8bf83',chatterbox:'#e79eaa',indextts:'#6fd0cc',moss:'#c6c78e',fish:'#c5b9e7'};
const names={};let windowName='5m',view='timeline',model='',offset=0,selectedJob=null,selectedRun=null,requestNumber=0,inFlight=false;
const metrics=[{key:'rss_bytes',title:'VoiceGen memory',scope:'Service + child processes · RSS',unit:'GiB',factor:2**30},
 {key:'cpu_percent',title:'VoiceGen CPU',scope:'Service + child processes · 100% = one core',unit:'%',factor:1},
 {key:'gpu_percent',title:'GPU utilization',scope:'Whole Mac · includes other apps',unit:'%',factor:1,max:100},
 {key:'gpu_allocated_bytes',title:'GPU memory allocated',scope:'Whole Mac · shared system memory',unit:'GiB',factor:2**30}];
const number=(value,digits=1)=>Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits}):'Unavailable';
const duration=seconds=>seconds<60?number(seconds)+'s':seconds<3600?number(seconds/60)+'m':number(seconds/3600)+'h';
const timeLabel=(timestamp,long=false)=>new Date(timestamp*1000).toLocaleString(undefined,long?{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}:{hour:'2-digit',minute:'2-digit',second:'2-digit'});
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function svg(tag,attrs,text){const n=document.createElementNS(svgNS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n}
async function api(path){const r=await fetch(path);if(!r.ok){let body;try{body=await r.json()}catch{}throw Error(typeof body?.detail==='string'?body.detail:`Resource history unavailable (${r.status}). Try again shortly.`)}return r.json()}
function error(message){$('resource-error').textContent=message;$('resource-error').hidden=!message}
function status(data){const c=data.collector;if(!c)return;$('collector-status').textContent=c.running?'Recording':'Recorder stopped';const stale=c.last_sample_at&&Date.now()/1000-c.last_sample_at>15;$('collector-status').textContent+=(stale?' · waiting for samples':'');const warnings=[c.storage_error,...(c.counter_notices||[])];if(c.dropped_samples)warnings.push(`${c.dropped_samples} sample(s) could not be saved in this session.`);error(warnings.filter(Boolean).join(' '))}
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
function setView(next){view=next;if(next==='timeline')history.replaceState(null,'',location.pathname);$('timeline-panel').hidden=next!=='timeline';$('jobs-panel').hidden=next!=='jobs';$('timeline-tab').setAttribute('aria-selected',String(next==='timeline'));$('jobs-tab').setAttribute('aria-selected',String(next==='jobs'));refresh(true)}
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
async function loadJob(identity,run=null){clearJobLog();selectedJob=identity;selectedRun=run;const params=new URLSearchParams(location.search);params.set('job',identity);if(run)params.set('run',run);else params.delete('run');history.replaceState(null,'','?'+params);await refresh(true)}
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
   if(selectedJob){const suffix=selectedRun?'?run_id='+encodeURIComponent(selectedRun):'';const job=await api('/api/observability/jobs/'+encodeURIComponent(selectedJob)+suffix);if(token!==requestNumber)return;
    selectedRun=job.selected_run.id;$('job-detail').hidden=false;$('job-title').textContent='Job '+selectedJob.slice(0,10);
    $('job-caption').textContent=`${names[job.selected_run.backend]||job.selected_run.backend} · ${job.selected_run.model} · ${job.selected_run.status} · ${duration(job.end-job.start)} · ${job.summary.samples} samples`;
    $('job-attempt').replaceChildren(...job.runs.map(row=>new Option(`Attempt ${row.attempt} · ${timeLabel(row.started_at,true)}`,row.id)));$('job-attempt').value=selectedRun;
    legend($('job-legend'),job);charts($('job-charts'),job,true);recordedStages(job);await loadJobLog(job,token);
   }
  }
 }catch(exc){if(token===requestNumber)error(exc.message)}finally{if(token===requestNumber){inFlight=false;$('refresh-resources').disabled=false}}
}
$('timeline-tab').onclick=()=>setView('timeline');$('jobs-tab').onclick=()=>setView('jobs');
document.querySelectorAll('[data-window]').forEach(b=>b.onclick=()=>{windowName=b.dataset.window;document.querySelectorAll('[data-window]').forEach(el=>el.setAttribute('aria-pressed',String(el===b)));refresh(true)});
$('resource-model').onchange=()=>{model=$('resource-model').value;offset=0;selectedJob=selectedRun=null;$('job-detail').hidden=true;history.replaceState(null,'',location.pathname);refresh(true)};
$('refresh-resources').onclick=()=>refresh(true);$('jobs-prev').onclick=()=>{offset=Math.max(0,offset-25);refresh(true)};$('jobs-next').onclick=()=>{offset+=25;refresh(true)};
$('job-attempt').onchange=()=>loadJob(selectedJob,$('job-attempt').value);
$('copy-job-log').onclick=async()=>{try{await navigator.clipboard.writeText($('job-log-output').textContent);$('job-log-notice').textContent='Visible log copied.'}catch{$('job-log-notice').textContent='Could not copy automatically. Select the log text and copy it.'}};
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
const params=new URLSearchParams(location.search);if(/^[a-f0-9]{32}$/.test(params.get('job')||'')){selectedJob=params.get('job');selectedRun=/^[a-f0-9]{32}$/.test(params.get('run')||'')?params.get('run'):null;setView('jobs')}else refresh();
setInterval(()=>{if(!document.hidden)refresh()},5000);
