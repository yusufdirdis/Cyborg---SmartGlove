import React, {useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ArrowDownToLine, ArrowUpFromLine, Camera, CircleHelp, Cpu, Hand, LayoutDashboard, Menu, MessageSquare, Mic, MicOff, Pause, Play, Plus, RotateCcw, Save, Settings, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Square, Trash2, X} from 'lucide-react'
import type {FunctionCall, LiveConnectConfig, LiveServerMessage, Session} from '@google/genai'
import './index.css'

type Page = 'Dashboard'|'Manual Control'|'Macro Studio'|'AI Vision'|'AI Assistant'|'Calibration'|'Devices'|'Settings'
type Step = {positions: Record<number,number>,duration_ms:number}
type Macro = {id:string,name:string,description:string,kind:'pose'|'sequence',steps:Step[]}
type Calibration = {servo_id:number,name:string,open_count:number,closed_count:number,min_count:number,max_count:number,direction:'increasing'|'decreasing',speed:number,verified:boolean,jog_limits_verified:boolean}
type Status = {mode:string,positions:Record<number,number>,target:Record<number,number>,stopped:boolean,playing:boolean,last_event:string,vision:{label:string,confidence:number,source:string}|null,devices:Record<string,string>}
type HardwareStatus = {connected:boolean,port:string|null,servos:{id:number,model:number,position:number|null,voltage_v:number|null,temperature_c:number|null,moving:boolean|null,mode:number|null,torque_enabled:boolean|null}[],error:string|null,checked_at:number}
type JogPreview = {servo_id:number,start:number,target:number,delta:number,can_execute:boolean,blocked_reason:string|null,token?:string,phrase?:string,expires_in_seconds?:number}
type PositionPreview = {servo_id:number,start:number,target:number,speed:number,can_execute:boolean,blocked_reason:string|null,token?:string,phrase?:string,expires_in_seconds?:number}
type HardwareControlInfo = {writes_enabled:boolean,step_counts:number,position_speed_min:number,position_speed_max:number,position_speed_default:number}
const servos = ['Pinky finger','Ring finger','Middle finger','Index finger','Thumb flexion (up/down)','Thumb side-to-side']
const maxServoCount = 4000
const servoMounts = [
 {id:4,location:'Top left'}, {id:3,location:'Top right'},
 {id:5,location:'Middle left'}, {id:2,location:'Middle right'},
 {id:6,location:'Bottom left'}, {id:1,location:'Bottom right'},
] as const
const blank = ():Record<number,number>=>Object.fromEntries([1,2,3,4,5,6].map(id=>[id,0]))
const initialStatus:Status={mode:'simulation',positions:blank(),target:blank(),stopped:false,playing:false,last_event:'Connecting',vision:null,devices:{controller:'Not connected',glasses:'Not connected',camera:'Browser preview only'}}
async function api<T>(path:string,options?:RequestInit):Promise<T>{const response=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options}); if(!response.ok){let message=`Request failed (${response.status})`;try{const error=await response.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail)}catch{}throw Error(message)}return response.status===204?undefined as T:response.json()}
const json=(body:unknown)=>JSON.stringify(body)
type HardwareStop = {attempted:boolean,failed_ids?:number[],message:string}
const stopNotice=(result:{hardware_stop?:HardwareStop})=>`Software stop latched. ${result.hardware_stop?.message==='Physical writes are disabled.'?'Simulation halted.':result.hardware_stop?.message||'Use the physical power cutoff if the glove moves.'}`
type ProviderName = 'gemini'|'openrouter'|'huggingface'|'offline'
type Provider = {name:ProviderName,label:string,available:boolean,model:string|null}
type Proposal = {id:string,provider:string,expires_in_s:number,command:{type:'pose',positions:Record<number,number>,label:string}|{type:'macro',macro_id:string,name:string}|{type:'save_macro',name:string,steps:Step[],append_to:{macro_id:string,name:string}|null}}
type ChatReply = {provider:string,reply:string,proposal:Proposal|null,stopped:boolean,hardware_stop?:HardwareStop}
type Turn = {role:'user'|'assistant',text:string,provider?:string,proposal?:Proposal|null,at:number,state?:'pending'|'sent'|'saved'|'cancelled'|'expired'}
type SpeechResultEvent = {resultIndex:number,results:ArrayLike<ArrayLike<{transcript:string}> & {isFinal:boolean}>}
type Recognizer = {lang:string,interimResults:boolean,continuous:boolean,onresult:((e:SpeechResultEvent)=>void)|null,onend:(()=>void)|null,onerror:((e:{error:string})=>void)|null,start:()=>void,stop:()=>void,abort:()=>void}
const speechErrors:Record<string,string>={'not-allowed':'Microphone is blocked. Click the mic icon in the address bar, allow it, then try again.','service-not-allowed':'Speech recognition is blocked in this browser. Use Chrome or Edge with microphone access allowed.','audio-capture':'No working microphone found. Plug one in, close apps using it, and check Windows Settings › Privacy › Microphone.','network':'The speech service is unreachable. Voice needs an internet connection in Chrome or Edge.','no-speech':'Didn’t hear anything. Click the mic and start speaking.','language-not-supported':'English speech recognition is not available in this browser.'}
const micError=(e:unknown)=>{const name=e instanceof DOMException?e.name:'';return name==='NotAllowedError'||name==='SecurityError'?speechErrors['not-allowed']:name==='NotFoundError'||name==='NotReadableError'||name==='OverconstrainedError'?speechErrors['audio-capture']:`Could not open the microphone (${name||String(e)}).`}
const speechCtor=():(new()=>Recognizer)|undefined=>{const w=window as unknown as {SpeechRecognition?:new()=>Recognizer,webkitSpeechRecognition?:new()=>Recognizer};return w.SpeechRecognition||w.webkitSpeechRecognition}

function App(){
 const [page,setPage]=useState<Page>('Dashboard'),[status,setStatus]=useState<Status>(initialStatus),[hardware,setHardware]=useState<HardwareStatus|null>(null),[macros,setMacros]=useState<Macro[]>([]),[cal,setCal]=useState<Calibration[]>([]),[manual,setManual]=useState<Record<number,number>>(blank()),[error,setError]=useState(''),[notice,setNotice]=useState(''),[connected,setConnected]=useState(false),[menuOpen,setMenuOpen]=useState(false)
 const refresh=async()=>{try{const [m,c,s]=await Promise.all([api<Macro[]>('/macros'),api<Calibration[]>('/calibration'),api<Status>('/status')]);setMacros(m);setCal(c);setStatus(s);setManual(s.positions);setConnected(true)}catch(e){setError(String(e));setConnected(false)}}
 useEffect(()=>{const reload=()=>{api<Macro[]>('/macros').then(setMacros).catch(()=>{})};window.addEventListener('macros-changed',reload);return()=>window.removeEventListener('macros-changed',reload)},[])
 useEffect(()=>{refresh();const scheme=location.protocol==='https:'?'wss':'ws';let socket:WebSocket;let timer:number;let disposed=false;const connect=()=>{if(disposed)return;socket=new WebSocket(`${scheme}://${location.host}/ws/telemetry`);socket.onopen=()=>setConnected(true);socket.onmessage=e=>{const s:Status=JSON.parse(e.data);setStatus(s)};socket.onclose=()=>{if(!disposed){setConnected(false);timer=window.setTimeout(connect,2500)}}};connect();return()=>{disposed=true;clearTimeout(timer);socket?.close()}},[])
 const scanHardware=async()=>{try{setHardware(await api<HardwareStatus>('/hardware/status'))}catch(e){setHardware({connected:false,port:null,servos:[],error:e instanceof Error?e.message:String(e),checked_at:Date.now()/1000})}}
 useEffect(()=>{scanHardware();const timer=window.setInterval(scanHardware,20000);return()=>clearInterval(timer)},[])
 const action=async<T,>(work:()=>Promise<T>,success?:string)=>{try{setError('');const result=await work();if(success)setNotice(success);return result}catch(e){setError(e instanceof Error?e.message:String(e));return undefined}}
 const play=async(m:Macro)=>{await action(()=>api(`/macros/${m.id}/play`,{method:'POST'}),`Playing ${m.name} in simulation`)}
 const stop=async()=>{const result=await action(()=>api<Status & {hardware_stop?:HardwareStop}>('/control/stop',{method:'POST'}));if(result){cancelPendingProposals();setNotice(stopNotice(result))}}
 const reset=()=>action(()=>api<Status>('/control/reset',{method:'POST'}),'Simulation resumed')
 const applyManual=()=>action(()=>api('/control/pose',{method:'POST',body:json({positions:manual})}),'Pose sent to simulation')
 const nav:[Page,React.ElementType][]=[['Dashboard',LayoutDashboard],['Manual Control',SlidersHorizontal],['Macro Studio',Hand],['AI Vision',Camera],['AI Assistant',MessageSquare],['Calibration',Settings2],['Devices',Cpu],['Settings',Settings]]
 const navigate=(next:Page)=>{setPage(next);setMenuOpen(false);setNotice('');window.scrollTo({top:0,behavior:'smooth'})}
 return <div className="app-layout">
  <aside className={`app-sidebar ${menuOpen?'open':''}`}>
   <div className="sidebar-top"><button className="sidebar-brand" onClick={()=>navigate('Dashboard')} aria-label="handoff home"><span className="sidebar-brand-icon"><Hand size={25} strokeWidth={2}/></span><span>handoff<span className="brand-dot">.</span></span></button><button className="sidebar-close" onClick={()=>setMenuOpen(false)} aria-label="Close menu"><X size={20}/></button></div>
   <div className="sidebar-section-label">YOUR WORKSPACE</div>
   <nav className="sidebar-nav" aria-label="Main navigation">{nav.map(([name,Icon])=><button key={name} className={page===name?'active':''} onClick={()=>navigate(name)}><Icon size={18} strokeWidth={1.9}/><span>{name==='Dashboard'?'Overview':name}</span>{name==='Macro Studio'&&<small>{macros.length}</small>}</button>)}</nav>
   <div className="sidebar-bottom"><div className="sidebar-section-label">SYSTEM</div><div className="sidebar-status"><span className={`status-dot ${connected?'online':''}`}/><div><b>{connected?'Local API connected':'API offline'}</b><small>Manual motors require confirmation</small></div></div></div>
  </aside>
  {menuOpen&&<button className="sidebar-overlay" aria-label="Close menu" onClick={()=>setMenuOpen(false)}/>}
  <div className="app-main"><header className="app-header"><div className="header-title"><button className="mobile-menu" onClick={()=>setMenuOpen(true)} aria-label="Open menu"><Menu size={21}/></button><div><strong>{page==='Dashboard'?'handoff':page}</strong><span>{page==='Dashboard'?'Overview dashboard':subtitles[page]}</span></div></div><div className="header-meta"><span className="header-connection"><i className={connected?'on':''}/>{connected?'API connected':'API offline'}</span><span className="header-mode">LOCAL CONTROL</span></div></header>
   <main className="app-content">
    {error&&<div role="alert" className="message message-error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
    {notice&&<div role="status" className="message message-success"><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss notice"><X size={16}/></button></div>}
    {page!=='Dashboard'&&<div className="page-heading"><div><div className="section-kicker">WORKSPACE / {page.toUpperCase()}</div><h1>{page}</h1><p>{subtitles[page]}</p></div><div className="page-actions"><button className="stop-button" onClick={stop}><Square size={13} fill="currentColor"/> Stop</button>{status.stopped&&<button className="btn btn-secondary flex items-center gap-2" onClick={reset}><RotateCcw size={16}/> Reset software stop</button>}</div></div>}
    {status.stopped&&<div className="stop-message"><Pause size={20}/><div><b>Software stop is latched.</b> Simulation is halted. If hardware control was enabled, servo torque-off was attempted; use the physical power cutoff if the glove moves.</div>{page==='Dashboard'&&<button className="btn btn-secondary" onClick={reset}>Reset software stop</button>}</div>}
    {page==='Dashboard'&&<Dashboard status={status} hardware={hardware} macros={macros} play={play} go={navigate} stop={stop}/>}
    {page==='Manual Control'&&<Manual status={status} hardware={hardware} cal={cal} onReading={setHardware} action={action} manual={manual} setManual={setManual} apply={applyManual} go={navigate}/>}
    {page==='Macro Studio'&&<MacroStudio macros={macros} status={status} refresh={refresh} action={action} play={play} manual={manual}/>}
    {page==='AI Vision'&&<Vision/>}
    {page==='AI Assistant'&&<Assistant macros={macros} action={action} setNotice={setNotice} setError={setError}/>}
    {page==='Calibration'&&<CalibrationPage cal={cal} hardware={hardware} onReading={setHardware} refresh={refresh} action={action}/>}
    {page==='Devices'&&<Devices status={status} hardware={hardware} scan={scanHardware} action={action} go={navigate}/>}
    {page==='Settings'&&<SettingsPage action={action}/>}
   </main>
  </div>
 <LiveButton macros={macros} setNotice={setNotice}/>
 </div>
}
const subtitles:Record<Page,string>={Settings:'Connect AI providers for the assistant. Keys stay on the local server.',Dashboard:'A clear view of your hand, gestures, and connected systems.', 'Manual Control':'Set each servo position and speed, or try a simulated pose.', 'Macro Studio':'Create, edit, and play gestures and movement sequences.', 'AI Vision':'Preview a camera feed from your browser.','AI Assistant':'Ask by text or voice; every movement waits for your Confirm and runs in simulation.',Calibration:'Record direct-test limits and gesture positions separately.',Devices:'See live servo status and check small movement commands.'}
function Eyebrow({children}:{children:React.ReactNode}){return <div className="text-[11px] uppercase tracking-[.18em] text-[#7c8f85] font-extrabold">{children}</div>}
function Pill({children,tone='green'}:{children:React.ReactNode,tone?:'green'|'gray'|'amber'}){return <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold ${tone==='green'?'bg-[#e3f7e8] text-[#247658]':tone==='amber'?'bg-[#fff0d8] text-[#976a20]':'bg-[#edf0ed] text-[#708079]'}`}>{children}</span>}
function HandGraphic(){
 return <div className="hand-diagram hand-photo-stage">
  <img className="hand-photo" src="/images/handoff-glove.png" alt="Five-finger white glove with black tendon guides and cords"/>
  <span className="diagram-label">GLOVE</span>
 </div>
}
function Dashboard({status,hardware,macros,play,go,stop}:{status:Status,hardware:HardwareStatus|null,macros:Macro[],play:(m:Macro)=>void,go:(p:Page)=>void,stop:()=>void}){
 const hardwareConnected=hardware?.connected===true
 return <div className="overview-stack">
 <section className="overview-panel control-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={22}/></span><div className="panel-head-copy"><h1>Control your hand</h1><p>Preview your glove's movements.</p></div><button className="stop-button panel-stop" onClick={stop}><Square size={12} fill="currentColor"/> Stop</button></div>
  <div className="hardware-connection" role="status" aria-label={hardwareConnected?'Hardware connected':'Hardware disconnected'}><span className={`hardware-indicator ${hardwareConnected?'on':''}`} aria-hidden="true"/>Hardware connected</div>
  <div className="control-workspace"><div className="hand-stage"><div className="stage-head"><b>YOUR GLOVE</b><span>{status.playing?'MOVING':'READY'}</span></div><HandGraphic/><div className="stage-foot">Channel values shown in the simulation panel</div></div><div className="control-details"><div className="detail-heading"><div><span className="section-kicker">MOVEMENT</span><h2>Finger movement</h2></div><span className="detail-status"><i/> Simulation only</span></div><div className="channel-list">{servos.map((name,i)=><div className="channel-row" key={name}><span className="channel-index">{String(i+1).padStart(2,'0')}</span><span className="channel-name">{name}</span><span className="channel-meter"><i style={{width:`${status.positions[i+1]}%`}}/></span><b>{status.positions[i+1]}%</b></div>)}</div><button className="dark-action" onClick={()=>go('Manual Control')}>Open manual controls <span aria-hidden="true">↗</span></button></div></div>
  <div className="panel-note"><span className="status-dot online"/><b>Simulation active</b><span>{hardwareConnected?'Six servos responding. Use Manual Control for confirmed motor moves.':'Saved gestures use simulation; direct motor moves are in Manual Control.'}</span></div>
 </section>
 <section className="overview-panel gestures-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={21}/></span><div className="panel-head-copy"><div className="section-kicker">SAVED MOVEMENTS</div><h2>Gestures, ready to test.</h2><p>Choose a macro to preview it in the simulated hand.</p></div><button className="plain-link" onClick={()=>go('Macro Studio')}>Open Macro Studio <span aria-hidden="true">↗</span></button></div><div className="overview-gesture-grid">{macros.slice(0,4).map((m,i)=><button key={m.id} disabled={status.stopped} onClick={()=>play(m)} className="overview-gesture"><span className="gesture-index">{String(i+1).padStart(2,'0')}</span><span><b>{m.name}</b><small>{m.description||'Custom gesture'}</small></span><Play size={15} fill="currentColor"/></button>)}</div><div className="panel-note"><span className="status-dot"/><span>{macros.length} saved macros</span></div></section>
 </div>}
type ManualAction = <T,>(work:()=>Promise<T>,success?:string)=>Promise<T|undefined>
type ProviderSetting = {name:ProviderName,label:string,key_set:boolean,key_hint:string|null,model:string,default_model:string}
type ProviderTest = {ok:boolean,message:string,latency_ms:number|null}
function SettingsPage({action}:{action:ManualAction}){
 const [rows,setRows]=useState<ProviderSetting[]>([]),[keys,setKeys]=useState<Record<string,string>>({}),[models,setModels]=useState<Record<string,string>>({}),[tests,setTests]=useState<Record<string,ProviderTest|'testing'>>({}),[clearing,setClearing]=useState(''),[busy,setBusy]=useState('')
 const load=async()=>{const list=await action(()=>api<ProviderSetting[]>('/settings/providers'));if(list){setRows(list);setModels(Object.fromEntries(list.map(p=>[p.name,p.model===p.default_model?'':p.model])))}}
 useEffect(()=>{load()},[])
 const replace=(row:ProviderSetting)=>setRows(r=>r.map(x=>x.name===row.name?row:x))
 const draftKey=(p:ProviderSetting)=>(keys[p.name]??'').trim(),draftModel=(p:ProviderSetting)=>(models[p.name]??'').trim(),dirty=(p:ProviderSetting)=>!!draftKey(p)||draftModel(p)!==(p.model===p.default_model?'':p.model)
 const save=async(p:ProviderSetting)=>{const key=draftKey(p),body:{api_key?:string,model:string}={model:draftModel(p)};if(key)body.api_key=key;setBusy(p.name);try{const row=await action(()=>api<ProviderSetting>(`/settings/providers/${p.name}`,{method:'PUT',body:json(body)}),`${p.label} settings saved`);if(row){replace(row);setKeys(k=>({...k,[p.name]:''}));setTests(t=>{const{[p.name]:_,...rest}=t;return rest})}return row}finally{setBusy('')}}
 const clear=async(p:ProviderSetting)=>{setBusy(p.name);try{const row=await action(()=>api<ProviderSetting>(`/settings/providers/${p.name}/key`,{method:'DELETE'}),`${p.label} key cleared`);if(row)replace(row)}finally{setBusy('');setClearing('')}}
 const test=async(p:ProviderSetting)=>{if(dirty(p)){const row=await save(p);if(!row?.key_set)return};setTests(t=>({...t,[p.name]:'testing'}));const result=await action(()=>api<ProviderTest>(`/settings/providers/${p.name}/test`,{method:'POST'}));setTests(t=>({...t,[p.name]:result??{ok:false,message:'Test request failed',latency_ms:null}}))}
 return <div className="space-y-5 max-w-[860px]">
  <div className="card p-5"><Eyebrow>AI providers</Eyebrow><p className="text-xs text-[#839187] mt-2">Keys are saved on the server in backend/.env and are never shown again; only the last four characters appear. The Offline provider needs no key. Changes apply immediately.</p></div>
  {rows.map(p=>{const result=tests[p.name];return <div key={p.name} className="card p-5">
   <div className="flex flex-wrap justify-between items-center gap-2"><div className="font-bold text-sm">{p.label}</div><Pill tone={p.key_set?'green':'gray'}>{p.key_set?`Key set ${p.key_hint??''}`:'No key'}</Pill></div>
   <div className="grid sm:grid-cols-2 gap-3 mt-4">
    <label className="text-xs font-bold text-[#6b7f72]">API key<input className="field mt-1 w-full" type="password" autoComplete="off" spellCheck={false} maxLength={500} placeholder={p.key_set?`Paste new key to replace ${p.key_hint??''}`:'Paste new key'} value={keys[p.name]??''} onChange={e=>setKeys(k=>({...k,[p.name]:e.target.value}))}/></label>
    <label className="text-xs font-bold text-[#6b7f72]">Model<input className="field mt-1 w-full" spellCheck={false} maxLength={200} placeholder={p.default_model} value={models[p.name]??''} onChange={e=>setModels(m=>({...m,[p.name]:e.target.value}))}/></label>
   </div>
   <div className="flex flex-wrap items-center gap-2 mt-4">
    <button className="btn btn-primary" disabled={busy===p.name} onClick={()=>save(p)}>Save</button>
    <button className="btn btn-secondary" disabled={busy===p.name||result==='testing'||(!p.key_set&&!draftKey(p))} title={!p.key_set&&!draftKey(p)?'Paste a key first':undefined} onClick={()=>test(p)}>{result==='testing'?'Testing…':dirty(p)?'Save & test':'Test'}</button>
    {p.key_set&&(clearing===p.name?<><span className="text-xs text-[#63796a]">Remove the saved {p.label} key?</span><button className="btn btn-secondary" disabled={busy===p.name} onClick={()=>clear(p)}>Yes, clear key</button><button className="btn btn-secondary" onClick={()=>setClearing('')}>Keep</button></>:<button className="btn btn-secondary" disabled={busy===p.name} onClick={()=>setClearing(p.name)}>Clear key</button>)}
   </div>
   {result&&result!=='testing'&&<p className={`text-xs mt-3 font-bold ${result.ok?'text-[#247658]':'text-[#b4533f]'}`}>{result.ok?`OK · ${result.latency_ms} ms · ${result.message}`:result.message}</p>}
   {!p.key_set&&!draftKey(p)&&<p className="text-[11px] text-[#839187] mt-2">Paste a key to enable Test.</p>}
   <p className="text-[11px] text-[#839187] mt-2">Using model {p.model}{p.model===p.default_model?' (default)':''}. Leave Model empty to use the default.</p>
  </div>})}
 </div>
}
function HardwareManual({status,hardware,cal,onReading,action,go}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],onReading:(reading:HardwareStatus)=>void,action:ManualAction,go:(page:Page)=>void}){
 const [control,setControl]=useState<HardwareControlInfo|null>(null)
 const [targets,setTargets]=useState<Record<number,number>>({})
 const [speeds,setSpeeds]=useState<Record<number,number>>(()=>{try{return JSON.parse(localStorage.getItem('handoff-manual-speeds')||'{}')}catch{return {}}})
 const [preview,setPreview]=useState<PositionPreview|null>(null)
 const [offBody,setOffBody]=useState(false),[powerCut,setPowerCut]=useState(false),[cordClear,setCordClear]=useState(false)
 const [busy,setBusy]=useState(false)
 const [monitoring,setMonitoring]=useState<{servoId:number,target:number,started:number,timeout:number}|null>(null)
 const [localError,setLocalError]=useState('')
 useEffect(()=>{api<HardwareControlInfo>('/hardware/control').then(setControl).catch(e=>setLocalError(String(e)))},[])
 useEffect(()=>{localStorage.setItem('handoff-manual-speeds',JSON.stringify(speeds))},[speeds])
 useEffect(()=>{if(!hardware)return;setTargets(current=>{const next={...current};for(const servo of hardware.servos){if(servo.position!=null&&next[servo.id]===undefined)next[servo.id]=servo.position}return next})},[hardware])
 useEffect(()=>{if(status.stopped)setMonitoring(null)},[status.stopped])
 useEffect(()=>{
  if(!monitoring)return
  let cancelled=false
  const timer=window.setInterval(async()=>{
   try{
    const reading=await api<HardwareStatus>('/hardware/status')
    if(cancelled)return
    onReading(reading)
    const servo=reading.servos.find(item=>item.id===monitoring.servoId)
    if(servo?.moving===false&&servo.position!=null&&Math.abs(servo.position-monitoring.target)<=20){setMonitoring(null);return}
    if(Date.now()-monitoring.started>monitoring.timeout){setLocalError(`Servo ${monitoring.servoId} has not reported reaching ${monitoring.target}. Use Stop or the physical power cutoff if motion continues.`);setMonitoring(null)}
   }catch(e){if(!cancelled)setLocalError(e instanceof Error?e.message:String(e))}
  },1200)
  return()=>{cancelled=true;window.clearInterval(timer)}
 },[monitoring,onReading])
 const updateTarget=(id:number,value:number)=>{setTargets(current=>({...current,[id]:value}));setPreview(null);setLocalError('')}
 const updateSpeed=(id:number,value:number)=>{setSpeeds(current=>({...current,[id]:value}));setPreview(null);setLocalError('')}
 const check=async(id:number)=>{
  const c=cal.find(item=>item.servo_id===id)
  const target=targets[id],speed=speeds[id]??control?.position_speed_default??250
  if(!c||!Number.isInteger(target)||target<c.min_count||target>c.max_count){setLocalError(`Servo ${id} target must be within its saved measured limits.`);return}
  setBusy(true);setPreview(null);setLocalError('')
  try{const result=await action(()=>api<PositionPreview>('/hardware/position/preview',{method:'POST',body:json({servo_id:id,target,speed})}));if(result)setPreview(result)}finally{setBusy(false)}
 }
 const send=async()=>{
  if(!preview?.token||!preview.phrase)return
  setBusy(true);setLocalError('')
  try{
   const result=await action(()=>api<{accepted:boolean,servo_id:number,target:number,speed:number}>('/hardware/position/execute',{method:'POST',body:json({token:preview.token,phrase:preview.phrase,off_body:offBody,power_cut_tested:powerCut,cord_path_clear:cordClear})}),`Servo ${preview.servo_id} moving toward ${preview.target} at speed ${preview.speed}.`)
   if(result){setMonitoring({servoId:result.servo_id,target:result.target,started:Date.now(),timeout:Math.max(12000,Math.abs(preview.target-preview.start)/preview.speed*3000+10000)});setPreview(null);setOffBody(false);setCordClear(false)}
  }finally{setBusy(false)}
 }
 return <section className="card p-5 md:p-7 mb-5">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><Eyebrow>Physical glove</Eyebrow><h2 className="text-xl font-bold mt-1">Servo positions</h2><p className="text-sm text-[#63796a] mt-2 max-w-2xl">Choose a count and speed for one servo, then check and confirm the move. Sliders only set a target; they do not move the glove by themselves.</p></div><Pill tone={control?.writes_enabled?'green':'amber'}>{control?.writes_enabled?'Motor control ready':'Motor control locked'}</Pill></div>
  {!control?.writes_enabled&&<p className="text-xs text-[#976a20] mt-4">The local API is running with physical writes disabled. Start it in hardware control mode to use these buttons.</p>}
  {localError&&<div role="alert" className="message message-error mt-4">{localError}<button onClick={()=>setLocalError('')} aria-label="Dismiss warning"><X size={16}/></button></div>}
  <div className="grid lg:grid-cols-2 gap-3 mt-6">{servos.map((name,index)=>{
   const id=index+1,c=cal.find(item=>item.servo_id===id),live=hardware?.servos.find(item=>item.id===id)
   const minimum=c?.min_count??0,maximum=c?.max_count??4000,target=targets[id]??live?.position??minimum,speed=speeds[id]??control?.position_speed_default??250
   const valid=Number.isInteger(target)&&target>=minimum&&target<=maximum
   return <div key={id} className="rounded-xl border border-[#e5ebe5] p-4 bg-[#fcfdfc]">
    <div className="flex justify-between gap-2 items-start"><div><div className="text-[10px] font-extrabold tracking-[.13em] text-[#8d9d90]">SERVO 0{id} · {servoMounts.find(m=>m.id===id)?.location.toUpperCase()}</div><h3 className="font-bold text-base mt-1 mb-0">{name}</h3></div><span className="text-xs text-[#607669] text-right">Now <b>{live?.position??'—'}</b><br/>{live?.moving?'Moving':'Stopped'}</span></div>
    <div className="grid grid-cols-[1fr_100px] gap-3 items-end mt-5"><label className="text-xs font-bold text-[#6b7f72]">Target count<input aria-label={`${name} target count slider`} type="range" min={minimum} max={maximum} value={Math.max(minimum,Math.min(maximum,target))} onChange={e=>updateTarget(id,Number(e.target.value))} className="w-full mt-3" disabled={!c}/></label><input aria-label={`${name} target count`} className="field" type="number" min={minimum} max={maximum} step="1" value={target} onChange={e=>updateTarget(id,Number(e.target.value))}/></div>
    <div className="flex justify-between text-[10px] text-[#94a39a] mt-1"><span>{minimum} min</span><span>{maximum} max</span></div>
    <div className="grid grid-cols-[1fr_65px] gap-3 items-center mt-4"><label className="text-xs font-bold text-[#6b7f72]">Speed · {speed} counts/s<input aria-label={`${name} speed`} type="range" min={control?.position_speed_min??50} max={control?.position_speed_max??500} step="25" value={speed} onChange={e=>updateSpeed(id,Number(e.target.value))} className="w-full mt-2"/></label><span className="text-right text-[10px] text-[#87978a]">50–500</span></div>
    <div className="flex flex-wrap items-center gap-2 mt-4"><button className="btn btn-primary" disabled={busy||!!monitoring||!control?.writes_enabled||status.stopped||!hardware?.connected||!c?.jog_limits_verified||!valid||live?.moving!==false} onClick={()=>check(id)}>Check move</button><button className="btn btn-secondary" disabled={live?.position==null} onClick={()=>updateTarget(id,live!.position!)}>Use current</button>{!c?.jog_limits_verified&&<button className="text-xs font-bold text-[#976a20] underline" onClick={()=>go('Calibration')}>Verify limits</button>}</div>
   </div>
  })}</div>
  {monitoring&&<div role="status" className="rounded-xl bg-[#edf5ec] text-[#446947] text-sm p-4 mt-4">Servo {monitoring.servoId} is moving toward {monitoring.target}. Its position refreshes here. Use Stop or the physical power cutoff if anything catches.</div>}
  {preview&&<div className="rounded-xl border border-[#dce9df] bg-[#f8faf7] p-5 mt-5"><div className="text-sm font-bold">Servo {preview.servo_id}: {preview.start} → {preview.target} counts at {preview.speed} counts/s</div><p className="text-xs text-[#63796a] mt-1">No command has been sent. This confirmation expires in 30 seconds. The servo will not return automatically.</p>
   {preview.blocked_reason?<p className="text-sm text-[#976a20] mt-3">{preview.blocked_reason}</p>:<div className="space-y-3 mt-4">
    <label className="block text-xs"><input type="checkbox" checked={offBody} onChange={e=>setOffBody(e.target.checked)} className="mr-2"/>The glove is off my hand.</label>
    <label className="block text-xs"><input type="checkbox" checked={powerCut} onChange={e=>setPowerCut(e.target.checked)} className="mr-2"/>I tested the physical servo power cutoff for this session and can reach it.</label>
    <label className="block text-xs"><input type="checkbox" checked={cordClear} onChange={e=>setCordClear(e.target.checked)} className="mr-2"/>The cord path is clear and this target stays within measured travel.</label>
    <div className="flex gap-2"><button className="btn btn-primary" disabled={busy||status.stopped||!offBody||!powerCut||!cordClear} onClick={send}>Confirm move</button><button className="btn btn-secondary" onClick={()=>setPreview(null)}>Cancel</button></div>
   </div>}
  </div>}
  <p className="text-xs text-[#839187] mt-5">Only one servo moves at a time. Saved gestures and the percentage sliders below still use simulation. Stop attempts to turn off servo torque; the physical power switch is the reliable cutoff.</p>
 </section>
}
function Manual({status,hardware,cal,onReading,action,manual,setManual,apply,go}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],onReading:(reading:HardwareStatus)=>void,action:ManualAction,manual:Record<number,number>,setManual:(p:Record<number,number>)=>void,apply:()=>void,go:(p:Page)=>void}){
 return <><HardwareManual status={status} hardware={hardware} cal={cal} onReading={onReading} action={action} go={go}/><div className="grid xl:grid-cols-[1.1fr_.9fr] gap-5"><div className="card p-5 md:p-7"><div className="flex justify-between items-start mb-6"><div><Eyebrow>Simulation</Eyebrow><h2 className="text-xl font-bold mt-1">Finger controls</h2></div><Pill>0–100% movement</Pill></div><div className="space-y-5">{servos.map((name,i)=><div key={name} className="rounded-xl border border-[#e8ece8] p-4"><div className="flex justify-between mb-4"><div className="font-bold text-sm"><span className="text-teal mr-2">0{i+1}</span>{name}</div><span className="text-teal font-bold">{manual[i+1]}%</span></div><input aria-label={`${name} simulation movement`} type="range" min="0" max="100" value={manual[i+1]} onChange={e=>setManual({...manual,[i+1]:Number(e.target.value)})} className="w-full"/><div className="flex justify-between text-[10px] text-[#94a39a] mt-1"><span>{i<4?'Open':'0%'}</span><span>{i<4?'Closed':'100%'}</span></div></div>)}</div><div className="flex gap-2 mt-5"><button className="btn btn-primary flex items-center gap-2" disabled={status.stopped} onClick={apply}><Play size={16}/> Apply to simulation</button><button className="btn btn-secondary" onClick={()=>setManual(blank())}>Set all to 0%</button></div></div><div className="space-y-5"><div className="card p-5"><Eyebrow>Live preview</Eyebrow><h2 className="text-xl font-bold mt-1 mb-4">Glove</h2><HandGraphic/></div><div className="card p-5 flex items-start gap-3"><CircleHelp className="text-teal shrink-0" size={20}/><div className="text-sm text-[#62756a] leading-6">These sliders use percentages in the simulator. Actual servo counts depend on measured endpoints. <button className="text-teal font-bold" onClick={()=>go('Calibration')}>Set calibration →</button></div></div></div></div></>
}

function MacroStudio({macros,status,refresh,action,play,manual}:{macros:Macro[],status:Status,refresh:()=>Promise<void>,action:<T>(work:()=>Promise<T>,success?:string)=>Promise<T|undefined>,play:(m:Macro)=>void,manual:Record<number,number>}){const [selected,setSelected]=useState<string|null>(null),[draft,setDraft]=useState<Omit<Macro,'id'>>({name:'',description:'',kind:'pose',steps:[{positions:blank(),duration_ms:700}]}),[editing,setEditing]=useState(false),file=useRef<HTMLInputElement>(null)
 const edit=(m:Macro)=>{setSelected(m.id);setDraft({name:m.name,description:m.description,kind:m.kind,steps:structuredClone(m.steps)});setEditing(true)}
 const fresh=()=>{setSelected(null);setDraft({name:'New gesture',description:'',kind:'pose',steps:[{positions:{...manual},duration_ms:700}]});setEditing(true)}
 const save=async()=>{if(!draft.name.trim())return;const path=selected?`/macros/${selected}`:'/macros';const result=await action(()=>api<Macro>(path,{method:selected?'PUT':'POST',body:json(draft)}),selected?'Macro updated':'Macro created');if(result){await refresh();setEditing(false);setSelected(null)}}
 const remove=async(m:Macro)=>{if(!window.confirm(`Delete “${m.name}”?`))return;await action(()=>api(`/macros/${m.id}`,{method:'DELETE'}),'Macro deleted');await refresh()}
 const exportFile=async()=>{const data=await action(()=>api('/macros/export'));if(!data)return;const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='handoff-macros.json';a.click();URL.revokeObjectURL(url)}
 const importFile=async(f:File)=>{try{const data=JSON.parse(await f.text());const result=await action(()=>api<{imported:number}>('/macros/import',{method:'POST',body:json(data)}));if(result)await refresh()}catch{alert('Could not read this JSON file.')}}
 return <><div className="flex flex-wrap gap-2 mb-5"><button className="btn btn-primary flex items-center gap-2" onClick={fresh}><Plus size={17}/> New macro</button><button className="btn btn-secondary flex items-center gap-2" onClick={exportFile}><ArrowDownToLine size={16}/> Export</button><button className="btn btn-secondary flex items-center gap-2" onClick={()=>file.current?.click()}><ArrowUpFromLine size={16}/> Import</button><input ref={file} type="file" accept="application/json,.json" className="hidden" onChange={e=>{if(e.target.files?.[0])importFile(e.target.files[0]);e.currentTarget.value=''}}/></div><div className="macro-table"><div className="macro-table-head"><span>GESTURE</span><span>TYPE</span><span>CHANNELS</span><span>ACTIONS</span></div>{macros.map((m,i)=><div key={m.id} className="macro-table-row"><span className="macro-index">{String(i+1).padStart(2,'0')}</span><div className="macro-title"><b>{m.name}</b><small>{m.description||'Custom movement'}</small></div><span className="macro-kind">{m.kind==='pose'?'Pose':`Sequence · ${m.steps.length} steps`}</span><div className="macro-channel-bars" aria-label="First step position by servo">{servos.map((_,j)=><span key={j}><i style={{height:`${m.steps[0].positions[j+1]}%`}}/></span>)}</div><div className="macro-actions"><button disabled={status.stopped} className="macro-play" onClick={()=>play(m)}><Play size={13} fill="currentColor"/> Play</button><button aria-label={`Edit ${m.name}`} onClick={()=>edit(m)}><Settings2 size={16}/></button><button aria-label={`Delete ${m.name}`} onClick={()=>remove(m)}><Trash2 size={15}/></button></div></div>)}</div>
 {editing&&<div className="fixed inset-0 bg-[#0d2520]/60 z-40 flex justify-end" onClick={()=>setEditing(false)}><div className="h-full w-full max-w-[590px] bg-[#f8faf7] overflow-y-auto mini-scroll p-6 md:p-8" onClick={e=>e.stopPropagation()}><div className="flex justify-between items-start"><div><Eyebrow>Macro editor</Eyebrow><h2 className="text-2xl font-bold mt-1">{selected?'Edit macro':'Create macro'}</h2></div><button aria-label="Close editor" onClick={()=>setEditing(false)}><X/></button></div><label className="block text-sm font-bold mt-7 mb-2">Name</label><input className="field" value={draft.name} maxLength={60} onChange={e=>setDraft({...draft,name:e.target.value})}/><label className="block text-sm font-bold mt-4 mb-2">Description</label><textarea className="field" rows={2} value={draft.description} maxLength={240} onChange={e=>setDraft({...draft,description:e.target.value})}/><label className="block text-sm font-bold mt-4 mb-2">Type</label><select className="field" value={draft.kind} onChange={e=>{const kind=e.target.value as 'pose'|'sequence';setDraft({...draft,kind,steps:kind==='pose'?draft.steps.slice(0,1):draft.steps})}}><option value="pose">Pose · one position</option><option value="sequence">Sequence · timed steps</option></select><div className="mt-7 space-y-4">{draft.steps.map((step,stepIndex)=><div key={stepIndex} className="card p-5"><div className="flex justify-between items-center mb-4"><h3 className="font-bold">Step {stepIndex+1}</h3>{draft.kind==='sequence'&&draft.steps.length>1&&<button className="text-red-600 text-xs font-bold flex items-center gap-1" onClick={()=>setDraft({...draft,steps:draft.steps.filter((_,i)=>i!==stepIndex)})}><Trash2 size={14}/> Remove</button>}</div><div className="grid grid-cols-2 gap-3">{servos.map((name,i)=><label key={i} className="text-xs font-semibold text-[#62776c]">{name}<div className="flex gap-2 items-center"><input aria-label={`${name} step ${stepIndex+1}`} className="w-full" type="range" min="0" max="100" value={step.positions[i+1]} onChange={e=>{const steps=structuredClone(draft.steps);steps[stepIndex].positions[i+1]=Number(e.target.value);setDraft({...draft,steps})}}/><span className="w-7 text-right text-teal">{step.positions[i+1]}</span></div></label>)}</div><label className="block text-xs font-bold text-[#62776c] mt-4">Move duration (ms)</label><input className="field mt-1" type="number" min="100" max="10000" value={step.duration_ms} onChange={e=>{const steps=structuredClone(draft.steps);steps[stepIndex].duration_ms=Number(e.target.value);setDraft({...draft,steps})}}/></div>)}</div>{draft.kind==='sequence'&&<button className="btn btn-secondary mt-4 flex items-center gap-2" disabled={draft.steps.length>=30} onClick={()=>setDraft({...draft,steps:[...draft.steps,{positions:{...draft.steps.at(-1)!.positions},duration_ms:700}]})}><Plus size={16}/> Add step</button>}<div className="sticky bottom-0 bg-[#f8faf7] py-5 mt-6 border-t flex gap-2"><button className="btn btn-primary flex-1 flex justify-center gap-2" onClick={save}><Save size={16}/> Save macro</button><button className="btn btn-secondary" onClick={()=>setEditing(false)}>Cancel</button></div></div></div>}
 </>}
const providerOrder:ProviderName[]=['gemini','openrouter','huggingface']
const cancelPendingProposals=()=>window.dispatchEvent(new Event('assistant-stop'))
const macrosChanged=()=>window.dispatchEvent(new Event('macros-changed'))
async function confirmProposal(p:Proposal):Promise<'sent'|'saved'|'expired'>{const response=await fetch(`/api/assistant/proposals/${encodeURIComponent(p.id)}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'}});if(response.ok){const body=await response.json().catch(()=>({}));if(body.mode==='saved'){macrosChanged();return 'saved'}return 'sent'}if(response.status===410)return 'expired';const body=await response.json().catch(()=>({})),detail=typeof body.detail==='string'?body.detail:`Request failed (${response.status})`;throw Error(response.status===409?`Not confirmed: ${detail}`:detail)}
async function cancelProposal(p:Proposal){await api(`/assistant/proposals/${encodeURIComponent(p.id)}/cancel`,{method:'POST'})}
function useProposalActions(setTurns:React.Dispatch<React.SetStateAction<Turn[]>>,setNotice:(m:string)=>void,setError:(m:string)=>void){const [busyId,setBusyId]=useState(''),busy=useRef(false);const mark=(id:string,state:Turn['state'])=>setTurns(t=>t.map(x=>x.proposal?.id===id?{...x,state}:x));useEffect(()=>{const stop=()=>setTurns(t=>t.map(x=>x.state==='pending'?{...x,state:'cancelled'}:x));window.addEventListener('assistant-stop',stop);return()=>window.removeEventListener('assistant-stop',stop)},[setTurns]);const run=async(p:Proposal,confirm:boolean)=>{if(busy.current)return;busy.current=true;setBusyId(p.id);setError('');try{const state=confirm?await confirmProposal(p):(await cancelProposal(p),'cancelled' as const);mark(p.id,state);if(state==='sent')setNotice('Sent to simulation');if(state==='saved')setNotice('Saved to Macro Studio')}catch(e){setError(e instanceof Error?e.message:String(e))}finally{busy.current=false;setBusyId('')}};return {busyId,confirm:(p:Proposal)=>run(p,true),cancel:(p:Proposal)=>run(p,false)}}
function ProposalCard({turn,macros,now,busyId,confirm,cancel}:{turn:Turn,macros:Macro[],now:number,busyId:string,confirm:(p:Proposal)=>void,cancel:(p:Proposal)=>void}){const p=turn.proposal;if(!p)return null;const state=turn.state==='pending'&&now>=turn.at+p.expires_in_s*1000?'expired':turn.state,macro=p.command.type==='macro'?macros.find(m=>m.id===(p.command as {macro_id:string}).macro_id):undefined,c=p.command,saving=c.type==='save_macro',positions=c.type==='pose'?c.positions:c.type==='save_macro'?c.steps[0]?.positions:macro?.steps[0]?.positions,saveSteps:Step[]|undefined=c.type==='pose'?[{positions:c.positions,duration_ms:700}]:macro?.steps;return <div className="card p-4 mt-3" aria-label={saving?'Save proposal':'Movement proposal'}><div className="flex flex-wrap justify-between items-start gap-2"><div><Eyebrow>{saving?'Proposed save':'Proposed movement'} · {p.provider}</Eyebrow><div className="font-bold text-sm mt-1">{c.type==='pose'?c.label:c.name}</div>{c.type==='save_macro'&&<div className="text-xs text-[#63796a] mt-1">{c.append_to?`Add ${c.steps.length} step${c.steps.length===1?'':'s'} to ${c.append_to.name}`:`New ${c.steps.length===1?'pose':`sequence · ${c.steps.length} steps`}`} · saves only, nothing moves</div>}</div>{state==='pending'?<Pill tone="amber">Awaiting approval</Pill>:<span className="text-xs font-bold text-[#63796a]">{state==='sent'?'Sent to simulation':state==='saved'?'Saved to Macro Studio':state==='cancelled'?'Cancelled':'Expired'}</span>}</div>{positions&&<div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2 mt-3">{servos.map((name,j)=><div key={name} className="text-[11px] text-[#63796a]"><div className="flex justify-between"><span>{name}</span><b className="text-[#1e4937]">{positions[j+1]??0}%</b></div><span className="channel-meter block mt-1"><i style={{width:`${positions[j+1]??0}%`}}/></span></div>)}</div>}{state==='pending'&&<div className="flex gap-2 mt-4"><button className="btn btn-primary" disabled={!!busyId} onClick={()=>confirm(p)}>Confirm</button><button className="btn btn-secondary" disabled={!!busyId} onClick={()=>cancel(p)}>Cancel</button></div>}{!saving&&saveSteps&&<SaveAsMacro steps={saveSteps} defaultName={c.type==='pose'?c.label:`${c.name} copy`} macros={macros}/>}</div>}
// Manual save from a proposal card: a new macro, or the steps appended to an existing one. Saving never moves the hand.
function SaveAsMacro({steps,defaultName,macros}:{steps:Step[],defaultName:string,macros:Macro[]}){
 const [open,setOpen]=useState(false),[name,setName]=useState(defaultName.slice(0,60)),[target,setTarget]=useState('new'),[duration,setDuration]=useState(700),[state,setState]=useState<'idle'|'saving'|'saved'>('idle'),[error,setError]=useState('')
 const save=async()=>{
  setError('');const timed=steps.length===1?[{...steps[0],duration_ms:duration}]:steps,existing=macros.find(m=>m.id===target)
  if(target==='new'&&!name.trim()){setError('Give the macro a name.');return}
  if(existing&&existing.steps.length+timed.length>30){setError(`${existing.name} would have more than 30 steps.`);return}
  setState('saving')
  try{
   if(existing)await api(`/macros/${existing.id}`,{method:'PUT',body:json({name:existing.name,description:existing.description,kind:'sequence',steps:[...existing.steps,...timed]})})
   else await api('/macros',{method:'POST',body:json({name:name.trim(),description:'Saved from AI assistant',kind:timed.length===1?'pose':'sequence',steps:timed})})
   setState('saved');macrosChanged()
  }catch(e){setState('idle');setError(e instanceof Error?e.message:String(e))}
 }
 if(state==='saved')return <p className="text-xs font-bold text-[#247658] mt-3">Saved to Macro Studio{target!=='new'?` · added to ${macros.find(m=>m.id===target)?.name??'macro'}`:''}</p>
 if(!open)return <button className="btn btn-secondary mt-3 flex items-center gap-2" onClick={()=>setOpen(true)}><Save size={15}/> Save as macro</button>
 return <div className="border border-[#e8ece8] rounded-xl p-3 mt-3 space-y-2">
  <label className="text-xs font-bold text-[#6b7f72] block">Save to<select className="field mt-1 w-full" value={target} onChange={e=>setTarget(e.target.value)}><option value="new">New macro</option>{macros.map(m=><option key={m.id} value={m.id}>Add step{steps.length===1?'':'s'} to {m.name}</option>)}</select></label>
  {target==='new'&&<label className="text-xs font-bold text-[#6b7f72] block">Name<input className="field mt-1 w-full" maxLength={60} value={name} onChange={e=>setName(e.target.value)}/></label>}
  {steps.length===1&&<label className="text-xs font-bold text-[#6b7f72] block">Step duration (ms)<input className="field mt-1 w-full" type="number" min={100} max={10000} step={50} value={duration} onChange={e=>setDuration(Math.min(10000,Math.max(100,Number(e.target.value)||700)))}/></label>}
  {error&&<p className="text-xs text-[#b4533f]">{error}</p>}
  <div className="flex gap-2"><button className="btn btn-primary" disabled={state==='saving'} onClick={save}>{state==='saving'?'Saving…':'Save'}</button><button className="btn btn-secondary" disabled={state==='saving'} onClick={()=>{setOpen(false);setError('')}}>Close</button></div>
  <p className="text-[11px] text-[#839187]">Saving adds the movement to Macro Studio. Nothing moves.</p>
 </div>
}
function Assistant({macros,action,setNotice,setError}:{macros:Macro[],action:ManualAction,setNotice:(m:string)=>void,setError:(m:string)=>void}){
 const [providers,setProviders]=useState<Provider[]>([]),[provider,setProvider]=useState<string>('offline'),[turns,setTurns]=useState<Turn[]>([]),[input,setInput]=useState(''),[waiting,setWaiting]=useState(false)
 const [listening,setListening]=useState(false),[transcribing,setTranscribing]=useState(false),[speak,setSpeak]=useState(false),[now,setNow]=useState(Date.now())
 const proposalActions=useProposalActions(setTurns,setNotice,setError)
 const recognizer=useRef<Recognizer|null>(null),recorder=useRef<MediaRecorder|null>(null),sendRef=useRef<(text?:string)=>void>(()=>{}),speakRef=useRef(false),bottom=useRef<HTMLDivElement>(null)
 const supported=!!speechCtor(),canSpeak='speechSynthesis' in window,canRecord=typeof MediaRecorder!=='undefined'&&!!navigator.mediaDevices
 const geminiVoice=canRecord&&providers.some(p=>p.name==='gemini'&&p.available),voiceReady=geminiVoice||supported
 speakRef.current=speak
 useEffect(()=>{api<Provider[]>('/assistant/providers').then(list=>{setProviders(list);const first=providerOrder.find(n=>list.some(p=>p.name===n&&p.available));setProvider(first??'offline')}).catch(e=>setError(e instanceof Error?e.message:String(e)))},[])
 useEffect(()=>{if(!turns.some(t=>t.state==='pending'))return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[turns])
 useEffect(()=>{bottom.current?.scrollIntoView?.({block:'nearest'})},[turns.length])
 useEffect(()=>()=>{recognizer.current?.abort();if(recorder.current?.state==='recording'){recorder.current.onstop=null;recorder.current.stop()};if('speechSynthesis' in window)window.speechSynthesis.cancel()},[])
 const current=providers.find(p=>p.name===provider)
 const send=async(override?:string)=>{
  const text=(override??input).trim()
  if(!text||waiting)return
  const history=turns.slice(-10).map(t=>({role:t.role,text:t.text}))
  setInput('');setWaiting(true)
  setTurns(t=>[...t,{role:'user',text,at:Date.now()}])
  try{
   const result=await action(()=>api<ChatReply>('/assistant/chat',{method:'POST',body:json({provider,message:text,history})}))
   if(result){
    setTurns(t=>[...(result.stopped?t.map(x=>x.state==='pending'?{...x,state:'cancelled' as const}:x):t),{role:'assistant',text:result.reply,provider:result.provider,proposal:result.proposal,at:Date.now(),state:result.proposal?'pending':undefined}])
    if(result.stopped){cancelPendingProposals();setNotice(stopNotice(result));window.speechSynthesis?.cancel()}
    else if(speakRef.current&&canSpeak&&result.reply)window.speechSynthesis.speak(new SpeechSynthesisUtterance(result.reply))
   }
  }finally{setWaiting(false)}
 }
 sendRef.current=send
 // Gemini voice: record a short clip, transcribe it on the server, then send the text like a typed message.
 const toggleRecording=async()=>{
  if(recorder.current){recorder.current.stop();return}
  setError('');window.speechSynthesis?.cancel()
  let stream:MediaStream
  try{stream=await navigator.mediaDevices.getUserMedia({audio:true})}catch(e){setError(micError(e));return}
  const type=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t))
  const rec=new MediaRecorder(stream,type?{mimeType:type}:undefined),chunks:Blob[]=[]
  const limit=window.setTimeout(()=>{if(rec.state==='recording')rec.stop()},15000)
  rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)}
  rec.onstop=async()=>{
   clearTimeout(limit);stream.getTracks().forEach(track=>track.stop());recorder.current=null;setListening(false)
   const blob=new Blob(chunks,{type:rec.mimeType||type||'audio/webm'})
   if(!blob.size){setError('Nothing was recorded. Click the mic and try again.');return}
   setTranscribing(true)
   try{
    const response=await fetch('/api/assistant/transcribe',{method:'POST',headers:{'Content-Type':blob.type},body:blob}),body=await response.json().catch(()=>({}))
    if(!response.ok){setError(typeof body.detail==='string'?body.detail:`Transcription failed (${response.status})`);return}
    if(!body.text){setError('Didn’t catch that. Click the mic and try again.');return}
    sendRef.current(body.text)
   }catch(e){setError(e instanceof Error?e.message:String(e))}finally{setTranscribing(false)}
  }
  recorder.current=rec;rec.start();setListening(true)
 }
 const toggleMic=()=>{
  if(geminiVoice){toggleRecording();return}
  if(listening){recognizer.current?.stop();return}
  const Ctor=speechCtor();if(!Ctor)return
  setError('');setListening(true);window.speechSynthesis?.cancel()
  // Ask for the mic first: it shows the permission prompt and names device problems precisely.
  const begin=async()=>{
   try{const stream=await navigator.mediaDevices.getUserMedia({audio:true});stream.getTracks().forEach(track=>track.stop())}catch(e){setListening(false);setError(micError(e));return}
   const r=new Ctor();r.lang='en-US';r.interimResults=true;r.continuous=false
   let heard='',sent=false
   r.onresult=e=>{let text='',final=false;for(let i=e.resultIndex;i<e.results.length;i++){text+=e.results[i][0].transcript;if(e.results[i].isFinal)final=true};heard=text;if(final&&!sent){sent=true;setInput('');sendRef.current(text)}else if(!final)setInput(text)}
   r.onend=()=>{setListening(false);recognizer.current=null;if(!sent&&heard.trim()){sent=true;setInput('');sendRef.current(heard)}}
   r.onerror=e=>{if(e.error!=='aborted')setError(speechErrors[e.error]??`Voice input failed (${e.error}).`)}
   recognizer.current=r
   try{r.start()}catch(e){setListening(false);recognizer.current=null;setError(`Voice input could not start (${e instanceof Error?e.message:String(e)}).`)}
  }
  begin()
 }
 return <div className="space-y-5 max-w-[860px]">
  <div className="card p-5">
   <div className="flex flex-wrap items-end justify-between gap-3"><label className="text-xs font-bold text-[#6b7f72]">Provider<select className="field mt-1" value={provider} onChange={e=>setProvider(e.target.value)}>{providers.map(p=><option key={p.name} value={p.name} disabled={!p.available}>{p.label}{p.available?'':' (no key)'}</option>)}{!providers.length&&<option value="offline">Offline</option>}</select></label><span className="text-xs text-[#839187]">{current?.model?`Model: ${current.model}`:'No model'}</span></div>
   <p className="text-xs text-[#839187] mt-3">The assistant only proposes simulated moves. It cannot drive the Devices hardware test. Say or type “stop” to stop immediately.</p>
  </div>
  <div className="card p-5">
   <div className="min-h-[160px] max-h-[460px] overflow-y-auto mini-scroll" aria-live="polite">
    {!turns.length&&<p className="text-xs text-[#839187]">Try “make a fist” or “play Open hand”. Nothing moves until you click Confirm.</p>}
    {turns.map((t,i)=><div key={i} className="border border-[#e8ece8] rounded-xl px-4 py-3 mb-2 text-[13px] leading-5"><div className="text-[10px] uppercase tracking-[.12em] font-extrabold text-[#8d9d90] mb-1">{t.role==='user'?'You':`Assistant (${t.provider??provider})`}</div><div className="whitespace-pre-wrap">{t.text}</div>{t.proposal&&<ProposalCard turn={t} macros={macros} now={now} {...proposalActions}/>}</div>)}
    {waiting&&<p className="text-xs text-[#839187]">Assistant is thinking…</p>}
    <div ref={bottom}/>
   </div>
   <div className="flex gap-2 mt-4"><input className="field flex-1" aria-label="Message" placeholder={listening?'Listening…':'Ask for a pose or macro'} value={input} maxLength={500} disabled={waiting} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
    {voiceReady&&<button className={`btn ${listening?'btn-primary':'btn-secondary'} flex items-center gap-2`} disabled={waiting||transcribing} onClick={toggleMic} aria-pressed={listening} aria-label={listening?'Stop listening':'Start voice input'}><Mic size={16}/>{listening?<span className="text-xs">{geminiVoice?'Recording':'Listening'}… click to stop</span>:transcribing?<span className="text-xs">Transcribing…</span>:null}</button>}
    <button className="btn btn-primary" disabled={waiting||!input.trim()} onClick={()=>send()}>Send</button></div>
   <div className="flex flex-wrap items-center gap-4 mt-3 text-xs text-[#839187]">
    {canSpeak&&<label><input type="checkbox" checked={speak} onChange={e=>{setSpeak(e.target.checked);if(!e.target.checked)window.speechSynthesis.cancel()}} className="mr-2"/>Speak replies</label>}
    {voiceReady?<span>Voice: {geminiVoice?'Gemini transcription':'browser speech (add a Gemini key in Settings for better accuracy)'}</span>:<span>Voice needs a Gemini key in Settings, or Chrome or Edge</span>}
    <span>Spoken or typed words never confirm a move; only the Confirm button does.</span>
   </div>
  </div>
 </div>
}
type LiveCredentials = {token:string,model:string,expires_at:string,system_instruction:LiveConnectConfig['systemInstruction'],tools:LiveConnectConfig['tools']}
type LiveState = 'Connecting'|'Live'|'Reconnecting'|'Ended'|'Error'
type LiveControls = {end:()=>void,mute:()=>void,pause:()=>void,camera:()=>Promise<void>,resumeAudio:()=>void}
const pcmWorklet=`class LivePCM extends AudioWorkletProcessor {constructor(){super();this.ratio=sampleRate/16000;this.weight=0;this.sum=0;this.index=0;this.buffer=new ArrayBuffer(3200);this.view=new DataView(this.buffer)}process(inputs){const samples=inputs[0]?.[0];if(!samples)return true;for(const sample of samples){let remaining=1;while(remaining>1e-8){const take=Math.min(remaining,this.ratio-this.weight);this.sum+=sample*take;this.weight+=take;remaining-=take;if(this.weight>=this.ratio-1e-8){const value=Math.max(-1,Math.min(1,this.sum/this.ratio));this.view.setInt16(this.index*2,Math.round(value*(value<0?32768:32767)),true);this.index++;this.sum=0;this.weight=0;if(this.index===1600){this.port.postMessage(this.buffer,[this.buffer]);this.buffer=new ArrayBuffer(3200);this.view=new DataView(this.buffer);this.index=0}}}}return true}}registerProcessor('live-pcm',LivePCM)`
const pcmBase64=(data:ArrayBuffer)=>{const bytes=new Uint8Array(data);let text='';for(let i=0;i<bytes.length;i++)text+=String.fromCharCode(bytes[i]);return btoa(text)}
const liveClock=(seconds:number)=>`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`
async function liveSession(signal:AbortSignal):Promise<LiveCredentials>{const response=await fetch('/api/assistant/live/session',{method:'POST',signal,headers:{'Content-Type':'application/json'}}),body=await response.json().catch(()=>({}));if(!response.ok)throw Error(response.status===409?'Add a Gemini key in Settings':typeof body.detail==='string'?body.detail:`Request failed (${response.status})`);return body}
function LiveButton({macros,setNotice}:{macros:Macro[],setNotice:(m:string)=>void}){const [open,setOpen]=useState(false),button=useRef<HTMLButtonElement>(null);const close=()=>{setOpen(false);button.current?.focus()};useEffect(()=>{if(!open)return;const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);button.current?.focus()}};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[open]);return <><button ref={button} className="fixed bottom-5 right-5 z-30 rounded-full bg-[#171d19] text-white w-[68px] h-[68px] flex flex-col items-center justify-center gap-1 text-xs font-extrabold" onClick={()=>setOpen(v=>!v)} aria-expanded={open} aria-controls="gemini-live-panel" aria-label={open?'Close Gemini Live':'Open Gemini Live'}><Sparkles size={21}/>Live</button>{open&&<LivePanel macros={macros} setNotice={setNotice} close={close}/>}</>}
function LivePanel({macros,setNotice,close}:{macros:Macro[],setNotice:(m:string)=>void,close:()=>void}){
 const [state,setState]=useState<LiveState>('Connecting'),[error,setError]=useState(''),[muted,setMuted]=useState(false),[paused,setPaused]=useState(false),[camera,setCamera]=useState(false),[cameraBusy,setCameraBusy]=useState(false),[audioBlocked,setAudioBlocked]=useState(false),[elapsed,setElapsed]=useState(0),[videoSession,setVideoSession]=useState(false),[turns,setTurns]=useState<Turn[]>([]),[now,setNow]=useState(Date.now())
 const video=useRef<HTMLVideoElement>(null),closeButton=useRef<HTMLButtonElement>(null),bottom=useRef<HTMLDivElement>(null),controls=useRef<LiveControls|null>(null),noticeRef=useRef(setNotice),proposalActions=useProposalActions(setTurns,setNotice,setError)
 useEffect(()=>{noticeRef.current=setNotice},[setNotice]);useEffect(()=>{closeButton.current?.focus()},[]);useEffect(()=>{bottom.current?.scrollIntoView?.({block:'nearest'})},[turns.length]);useEffect(()=>{if(!turns.some(t=>t.state==='pending'))return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[turns])
 useEffect(()=>{
  let disposed=false,ended=false,connecting=false,epoch=0,handle='',session:Session|null=null,mic:MediaStream|null=null,cam:MediaStream|null=null,input:AudioContext|null=null,output:AudioContext|null=null,source:MediaStreamAudioSourceNode|null=null,worklet:AudioWorkletNode|null=null,silent:GainNode|null=null,isMuted=false,cameraOpening=false,usedVideo=false,started=0,nextAudio=0,stopVersion=0,heard='',stopHeard=false,isPaused=false
  const abort=new AbortController(),sources=new Set<AudioBufferSourceNode>(),cancelledTools=new Set<string>(),toolProposals=new Map<string,string>(),seenTools=new Set<string>(),timers=new Set<number>();let transcriptOpen:{user:boolean,assistant:boolean}={user:false,assistant:false}
  const alive=()=>!disposed&&!ended,clearPlayback=()=>{for(const node of sources){node.onended=null;try{node.stop()}catch{}node.disconnect()};sources.clear();nextAudio=0},closeSession=()=>{const old=session;session=null;try{old?.close()}catch{}},stopCamera=()=>{cam?.getTracks().forEach(t=>t.stop());cam=null;if(video.current)video.current.srcObject=null;setCamera(false)}
  const cleanup=()=>{epoch++;abort.abort();for(const timer of timers){clearTimeout(timer);clearInterval(timer)};timers.clear();closeSession();mic?.getTracks().forEach(t=>t.stop());mic=null;stopCamera();if(worklet){worklet.port.onmessage=null;worklet.port.close();worklet.disconnect();worklet=null};source?.disconnect();silent?.disconnect();clearPlayback();if(input){void input.close().catch(()=>{});input=null};if(output){void output.close().catch(()=>{});output=null}}
  const fail=(message:string)=>{if(!alive())return;ended=true;cleanup();setState('Error');setError(message)},end=()=>{if(!alive())return;ended=true;cleanup();setState('Ended')}
  const stopEvent=()=>{stopVersion++;clearPlayback()};window.addEventListener('assistant-stop',stopEvent)
  const wait=(ms:number)=>new Promise<void>(resolve=>{const done=()=>{clearTimeout(timer);timers.delete(timer);abort.signal.removeEventListener('abort',done);resolve()},timer=window.setTimeout(done,ms);timers.add(timer);abort.signal.addEventListener('abort',done,{once:true})})
  const transcript=(role:'user'|'assistant',text:string)=>{if(!text)return;const append=transcriptOpen[role];transcriptOpen[role]=true;setTurns(list=>{if(append){let index=list.length-1;while(index>=0&&(list[index].role!==role||list[index].proposal))index--;if(index>=0)return list.map((turn,i)=>i===index?{...turn,text:turn.text+text}:turn)}return [...list,{role,text,provider:'gemini-live',at:Date.now()}]})}
  const playback=(data:string)=>{if(!output||!alive()||isPaused)return;const raw=atob(data),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0)),view=new DataView(bytes.buffer),length=Math.floor(bytes.length/2);if(!length)return;const buffer=output.createBuffer(1,length,24000),samples=buffer.getChannelData(0);for(let i=0;i<length;i++)samples[i]=view.getInt16(i*2,true)/32768;const node=output.createBufferSource();node.buffer=buffer;node.connect(output.destination);sources.add(node);node.onended=()=>{sources.delete(node);node.disconnect()};const at=Math.max(nextAudio,output.currentTime+.02);node.start(at);nextAudio=at+buffer.duration;if(output.state==='suspended')setAudioBlocked(true)}
  const latchStop=async()=>{const result=await api<Status & {hardware_stop?:HardwareStop}>('/control/stop',{method:'POST',signal:abort.signal});cancelPendingProposals();if(alive())noticeRef.current(stopNotice(result))}
  // A spoken stop is caught from the user's own transcript, so it never depends on the model choosing to call stop_hand.
  const listenForStop=(text:string)=>{heard+=text;if(stopHeard||!/(stop|halt|freeze|emergency)/i.test(heard))return;stopHeard=true;clearPlayback();void latchStop().catch(e=>{if(alive())setError(e instanceof Error?e.message:String(e))})}
  const tool=async(call:FunctionCall,connection:Session,id:number)=>{
   if(!alive()||id!==epoch)return;const key=call.id??`${id}:${call.name}`;if(seenTools.has(key))return;seenTools.add(key);const version=stopVersion;let response:Record<string,unknown>
   try{if(call.name==='stop_hand'){await latchStop();response={result:'Stopped.'}}
    else if(call.name==='propose_pose'||call.name==='propose_macro'||call.name==='propose_save_macro'){const args=call.args??{},saving=call.name==='propose_save_macro',body=call.name==='propose_pose'?{action:'pose',positions:args.positions,label:args.label}:saving?{action:'save_macro',name:args.name,steps:args.steps,append_to_macro_id:args.append_to_macro_id}:{action:'macro',macro_id:args.macro_id},proposal=await api<Proposal>('/assistant/live/proposal',{method:'POST',body:json(body),signal:abort.signal});toolProposals.set(key,proposal.id);const cancelled=cancelledTools.has(key)||version!==stopVersion||!alive();if(cancelled){void cancelProposal(proposal).catch(()=>{});response={error:'Proposal cancelled; nothing moved.'}}else{setTurns(t=>[...t,{role:'assistant',text:saving?'Proposed saving a macro. Confirm to save it.':'Proposed movement in simulation. Confirm to run it.',provider:'gemini-live',proposal,at:Date.now(),state:'pending'}]);response={result:saving?'Shown to the user; waiting for their Confirm click. Not saved yet.':'Shown to the user; waiting for their Confirm click. Not moved yet.'}}}
    else response={error:'Unknown tool. Only simulated proposals and stop are available.'}
   }catch(e){response={error:e instanceof Error?e.message:String(e)};if(call.name==='stop_hand'&&alive())setError(String(response.error))}
   if(alive()&&id===epoch&&session===connection){try{connection.sendToolResponse({functionResponses:[{id:call.id,name:call.name,response}]})}catch{void connect(true)}}
  }
  const message=(msg:LiveServerMessage,connection:Session,id:number)=>{if(!alive()||id!==epoch)return;if(msg.sessionResumptionUpdate?.newHandle)handle=msg.sessionResumptionUpdate.newHandle;const content=msg.serverContent;if(content?.interrupted){clearPlayback();transcriptOpen.assistant=false};if(content?.inputTranscription?.text){listenForStop(content.inputTranscription.text);transcript('user',content.inputTranscription.text)};if(content?.outputTranscription?.text)transcript('assistant',content.outputTranscription.text);if(!content?.interrupted)for(const part of content?.modelTurn?.parts??[]){if(part.inlineData?.data&&part.inlineData.mimeType?.startsWith('audio/pcm'))playback(part.inlineData.data)};if(content?.turnComplete){transcriptOpen={user:false,assistant:false};heard='';stopHeard=false};for(const key of msg.toolCallCancellation?.ids??[]){cancelledTools.add(key);const proposalId=toolProposals.get(key);if(proposalId)setTurns(t=>t.map(x=>x.proposal?.id===proposalId&&x.state==='pending'?{...x,state:'cancelled'}:x))};for(const call of [...(msg.toolCall?.functionCalls??[])].sort((a,b)=>Number(b.name==='stop_hand')-Number(a.name==='stop_hand')))void tool(call,connection,id);if(msg.goAway)void connect(true)}
  const setupMic=async()=>{if(mic)return;let stream:MediaStream;try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false})}catch(e){throw Error(micError(e))};if(!alive()){stream.getTracks().forEach(t=>t.stop());return};mic=stream;stream.getAudioTracks().forEach(t=>t.enabled=!isMuted);input=new AudioContext();const context=input,url=URL.createObjectURL(new Blob([pcmWorklet],{type:'application/javascript'}));try{await context.audioWorklet.addModule(url)}catch(e){throw Error(micError(e))}finally{URL.revokeObjectURL(url)};if(!alive())return;source=context.createMediaStreamSource(stream);worklet=new AudioWorkletNode(context,'live-pcm');silent=context.createGain();silent.gain.value=0;source.connect(worklet);worklet.connect(silent);silent.connect(context.destination);worklet.port.onmessage=e=>{if(!alive()||isMuted||isPaused||connecting||!session)return;try{session.sendRealtimeInput({audio:{data:pcmBase64(e.data as ArrayBuffer),mimeType:'audio/pcm;rate=16000'}})}catch{void connect(true)}};await context.resume()}
  const connect=async(resume:boolean)=>{
   if(!alive()||connecting)return;connecting=true;epoch++;closeSession();clearPlayback();setState(resume?'Reconnecting':'Connecting');setError('');let lastError='Could not connect to Gemini Live.'
   for(let attempt=0;attempt<(resume?3:1)&&alive();attempt++){
    const id=++epoch;let timeout=0,earlyClose=false;const queued:LiveServerMessage[]=[];let next:Session|null=null
    try{const credentials=await liveSession(abort.signal);if(!alive())return;const {GoogleGenAI,Modality}=await import('@google/genai');if(!alive())return;const ai=new GoogleGenAI({apiKey:credentials.token,httpOptions:{apiVersion:'v1alpha'}});const pending=ai.live.connect({model:credentials.model,config:{responseModalities:[Modality.AUDIO],systemInstruction:credentials.system_instruction,tools:credentials.tools,inputAudioTranscription:{},outputAudioTranscription:{},sessionResumption:handle?{handle}:{}},callbacks:{onmessage:msg=>{if(!alive()||id!==epoch)return;if(next)message(msg,next,id);else queued.push(msg)},onerror:()=>{if(alive()&&id===epoch){if(connecting)earlyClose=true;else void connect(true)}},onclose:()=>{if(alive()&&id===epoch){if(connecting)earlyClose=true;else void connect(true)}}}});void pending.then(s=>{if(!alive()||id!==epoch)s.close()},()=>{});next=await Promise.race([pending,new Promise<never>((_,reject)=>{timeout=window.setTimeout(()=>reject(Error('Gemini Live connection timed out.')),15000);timers.add(timeout)})]);clearTimeout(timeout);timers.delete(timeout);if(!alive()||id!==epoch){next.close();return};if(earlyClose){next.close();throw Error('Gemini Live closed while connecting.')};session=next;started=Date.now();usedVideo=!!cam;setVideoSession(usedVideo);setElapsed(0);connecting=false;setState('Live');for(const msg of queued)message(msg,next,id);if(alive()&&!mic){try{await setupMic()}catch(e){fail(e instanceof Error?e.message:micError(e))}}return
    }catch(e){clearTimeout(timeout);timers.delete(timeout);epoch++;next?.close();lastError=e instanceof Error?e.message:String(e);if(!alive())return;if(lastError==='Add a Gemini key in Settings')break;if(resume&&attempt<2)await wait(1000*(attempt+1))}
   };connecting=false;fail(lastError)
  }
  controls.current={end,pause:()=>{isPaused=!isPaused;setPaused(isPaused);if(isPaused){clearPlayback();if(session&&!connecting)try{session.sendRealtimeInput({audioStreamEnd:true})}catch{void connect(true)}}},mute:()=>{isMuted=!isMuted;mic?.getAudioTracks().forEach(t=>t.enabled=!isMuted);setMuted(isMuted);if(isMuted&&session&&!connecting)try{session.sendRealtimeInput({audioStreamEnd:true})}catch{void connect(true)}},camera:async()=>{if(!alive()||cameraOpening)return;if(cam){stopCamera();return};cameraOpening=true;setCameraBusy(true);try{const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640}},audio:false});if(!alive()){stream.getTracks().forEach(t=>t.stop());return};cam=stream;if(video.current){video.current.srcObject=stream;await video.current.play()};if(!alive())return;usedVideo=true;setVideoSession(true);setCamera(true);setError('')}catch(e){if(alive()){stopCamera();setError(`Could not open the camera: ${e instanceof Error?e.message:String(e)}`)}}finally{cameraOpening=false;if(!disposed)setCameraBusy(false)}},resumeAudio:()=>{void output?.resume().then(()=>{if(alive())setAudioBlocked(false)}).catch(e=>{if(alive())setError(String(e))});void input?.resume().catch(e=>{if(alive())setError(micError(e))})}}
  const canvas=document.createElement('canvas'),frames=window.setInterval(()=>{if(!alive()||isPaused||!cam||!session||connecting||!video.current?.videoWidth)return;canvas.width=640;canvas.height=Math.round(640*video.current.videoHeight/video.current.videoWidth);canvas.getContext('2d')?.drawImage(video.current,0,0,canvas.width,canvas.height);try{session.sendRealtimeInput({video:{data:canvas.toDataURL('image/jpeg',.6).split(',')[1],mimeType:'image/jpeg'}})}catch{void connect(true)}},1000);timers.add(frames)
  const ticker=window.setInterval(()=>{if(!alive())return;setNow(Date.now());if(session&&!connecting){const seconds=Math.floor((Date.now()-started)/1000);setElapsed(seconds);if(seconds>=(usedVideo?120:900))void connect(true)}},1000);timers.add(ticker)
  try{output=new AudioContext({sampleRate:24000});void output.resume().then(()=>{if(alive())setAudioBlocked(output?.state==='suspended')}).catch(()=>{if(alive())setAudioBlocked(true)});void connect(false)}catch(e){fail(e instanceof Error?e.message:String(e))}
  return()=>{disposed=true;ended=true;cleanup();window.removeEventListener('assistant-stop',stopEvent);controls.current=null}
 },[])
  const active=state!=='Ended'&&state!=='Error'
 return <section id="gemini-live-panel" role="dialog" aria-labelledby="gemini-live-title" className="card fixed z-30 bottom-[100px] right-0 sm:right-5 w-full sm:w-[380px] max-h-[calc(100dvh-120px)] flex flex-col p-5"><div className="flex items-start justify-between gap-2"><div><Eyebrow>Voice + camera</Eyebrow><h2 id="gemini-live-title" className="font-bold text-xl mt-1">Gemini Live</h2></div><button ref={closeButton} aria-label="Close Live panel" onClick={close}><X size={20}/></button></div><div className="flex justify-between items-center mt-3"><span role="status"><Pill tone={state==='Live'?'green':state==='Connecting'||state==='Reconnecting'?'amber':'gray'}>{state==='Reconnecting'?'Reconnecting…':state}</Pill></span><span className="text-xs text-[#63796a]" aria-label="Session timer">{liveClock(elapsed)} / {videoSession?'2:00':'15:00'}</span></div><p className="text-xs text-[#839187] mt-3">Moves run in simulation and wait for your Confirm click. Pause holds the conversation; saying “stop” or the page Stop button stops the hand.</p><div className="flex flex-wrap gap-2 mt-3"><button className="btn btn-secondary flex gap-1 items-center" disabled={!active} aria-pressed={muted} onClick={()=>controls.current?.mute()}>{muted?<MicOff size={15}/>:<Mic size={15}/>} {muted?'Unmute':'Mute'}</button><button className="btn btn-secondary flex gap-1 items-center" disabled={!active||cameraBusy} aria-pressed={camera} onClick={()=>void controls.current?.camera()}><Camera size={15}/>{camera?'Camera off':'Camera on'}</button><button className="btn btn-secondary" disabled={!active} onClick={()=>controls.current?.end()}>End</button><button className="btn btn-secondary flex gap-1 items-center" disabled={!active} aria-pressed={paused} onClick={()=>controls.current?.pause()}>{paused?<Play size={15}/>:<Pause size={15}/>} {paused?'Resume':'Pause'}</button></div>{audioBlocked&&active&&<button className="btn btn-secondary mt-2" onClick={()=>controls.current?.resumeAudio()}>Enable audio playback</button>}<div className="overflow-y-auto mini-scroll mt-3 min-h-0"><video ref={video} autoPlay playsInline muted aria-label="Live camera preview" className={`w-full rounded-[16px] bg-[#171d19] mb-3 ${camera?'':'hidden'}`}/>{camera&&<p className="text-xs text-[#839187] mb-3">Camera frames are sent to Gemini once per second.</p>}{error&&<p role="alert" className="message message-error text-xs mb-3">{error}</p>}<div aria-live="polite">{!turns.length&&<p className="text-xs text-[#839187]">Speak to Gemini, or turn on the camera and ask it to mirror a gesture.</p>}{turns.map((t,i)=><div key={i} className="border border-[#e8ece8] rounded-[16px] px-3 py-3 mb-2 text-[13px] leading-5"><div className="text-[10px] uppercase tracking-[.12em] font-extrabold text-[#8d9d90] mb-1">{t.role==='user'?'You':'Gemini Live'}</div><div className="whitespace-pre-wrap">{t.text}</div>{t.proposal&&<ProposalCard turn={t} macros={macros} now={now} {...proposalActions}/>}</div>)}</div><div ref={bottom}/></div></section>
}
function Vision(){
 const video=useRef<HTMLVideoElement>(null)
 const stream=useRef<MediaStream|null>(null)
 const [camera,setCamera]=useState(false)
 const [cameraError,setCameraError]=useState('')
 useEffect(()=>()=>{stream.current?.getTracks().forEach(track=>track.stop())},[])
 const toggle=async()=>{
  if(stream.current){
   stream.current.getTracks().forEach(track=>track.stop())
   stream.current=null
   if(video.current)video.current.srcObject=null
   setCamera(false)
   return
  }
  try{
   const next=await navigator.mediaDevices.getUserMedia({video:true,audio:false})
   stream.current=next
   if(video.current)video.current.srcObject=next
   setCamera(true)
   setCameraError('')
  }catch(error){setCameraError(error instanceof Error?error.message:String(error))}
 }
 return <div className="vision-preview"><div className="card p-5 md:p-6"><div className="flex justify-between items-start mb-4"><div><Eyebrow>Camera input</Eyebrow><h2 className="text-xl font-bold mt-1">Live preview</h2></div><Pill tone={camera?'green':'gray'}>{camera?'Browser camera':'Camera off'}</Pill></div><div className="aspect-video rounded-2xl bg-[#172d29] grid place-items-center overflow-hidden relative"><video ref={video} autoPlay playsInline muted className={`w-full h-full object-cover ${camera?'':'hidden'}`}/>{!camera&&<div className="text-center text-[#97b7a9]"><Camera className="mx-auto mb-3" size={36}/><div className="font-bold">No camera preview</div><div className="text-xs mt-1">Connect a browser camera to view its feed</div></div>}<span className="absolute left-4 bottom-4 rounded-full bg-[#152d29]/80 text-white text-[10px] px-3 py-1.5 font-bold">NO OBJECT DETECTOR RUNNING</span></div>{cameraError&&<p className="text-red-600 text-xs mt-2">{cameraError}</p>}<button className="btn btn-primary mt-4" onClick={toggle}>{camera?'Stop preview':'Start camera preview'}</button><p className="text-xs text-[#849087] mt-3">Camera video stays in this browser. The preview does not analyze frames or identify objects.</p></div></div>
}
type CalibrationCountField = 'open_count' | 'closed_count' | 'min_count' | 'max_count'
const calibrationFields: [CalibrationCountField, string][] = [
 ['open_count', 'Open position'],
 ['closed_count', 'Closed position'],
 ['min_count', 'Minimum limit'],
 ['max_count', 'Maximum limit'],
]
function CalibrationPage({cal,hardware,onReading,refresh,action}:{
 cal:Calibration[],hardware:HardwareStatus|null,onReading:(reading:HardwareStatus)=>void,
 refresh:()=>Promise<void>,action:<T>(work:()=>Promise<T>,success?:string)=>Promise<T|undefined>
}){
 const [draft,setDraft]=useState<Calibration[]>([])
 const [capturing,setCapturing]=useState<string|null>(null)
 useEffect(()=>setDraft(cal.map(c=>({...c}))),[cal])
 const change=(id:number,patch:Partial<Calibration>)=>setDraft(current=>current.map(c=>c.servo_id===id?{...c,...patch,verified:false,jog_limits_verified:'min_count' in patch||'max_count' in patch?false:c.jog_limits_verified}:c))
 const capture=async(id:number,key:CalibrationCountField,label:string)=>{
  const captureId=`${id}-${key}`
  setCapturing(captureId)
  try{
   await action(async()=>{
    const reading=await api<HardwareStatus>('/hardware/status')
    onReading(reading)
    const servo=reading.servos.find(item=>item.id===id)
    const position=servo?.position
    if(!servo || typeof position!=='number' || !Number.isInteger(position) || position<0 || position>maxServoCount){
     throw Error(reading.error || `No valid position reading from Servo ${id}. Check its connection and retry.`)
    }
    if(servo.moving!==false)throw Error(`Servo ${id} may be moving. Wait until it is still, then retry.`)
    change(id,{[key]:position})
    return position
   },`Captured Servo ${id} ${label.toLowerCase()} in the unsaved form`)
  }finally{setCapturing(null)}
 }
 const save=async(c:Calibration)=>{
  const result=await action(()=>api(`/calibration/${c.servo_id}`,{method:'PUT',body:json(c)}),`Servo ${c.servo_id} calibration saved`)
  if(result)await refresh()
 }
 return <>
  <div className="rounded-2xl p-5 mb-5 bg-[#fff6df] border border-[#f0e3ba] text-[#7b652e] flex gap-3">
   <ShieldCheck size={22} className="shrink-0"/>
   <div className="text-sm leading-6"><b>Measure before verifying.</b> The app accepts raw counts from 0 to 4000. Direct-test limits and open/closed positions are checked separately. The starting values are placeholders. Capture reads the current position; it does not move a motor or save calibration.</div>
  </div>
  <div className="grid xl:grid-cols-2 gap-4">{draft.map(c=>{
   const live=hardware?.servos.find(servo=>servo.id===c.servo_id)
   return <div className="card p-5" key={c.servo_id}>
    <div className="flex justify-between items-center mb-5"><div><Eyebrow>Servo 0{c.servo_id} · {servoMounts.find(mount=>mount.id===c.servo_id)?.location}</Eyebrow><h2 className="font-bold text-lg mt-1">{c.name}</h2></div><div className="flex flex-wrap gap-1 justify-end"><Pill tone={c.jog_limits_verified?'green':'amber'}>{c.jog_limits_verified?'Test limits checked':'Test limits unverified'}</Pill><Pill tone={c.verified?'green':'gray'}>{c.verified?'Gestures calibrated':'Gestures uncalibrated'}</Pill></div></div>
    <div className="rounded-xl border border-[#e8ece8] bg-[#f8faf7] px-4 py-3 mb-4 flex flex-wrap justify-between gap-2 text-sm"><span className="text-[#63796a]">Latest position reading</span><b className="text-[#1e4937]">{live?.position==null?'No reading':`${live.position} counts`}</b></div>
    <div className="grid grid-cols-2 gap-3">{calibrationFields.map(([key,fingerLabel])=>{
     const label=c.servo_id>=5 && key==='open_count'?'Position A':c.servo_id>=5 && key==='closed_count'?'Position B':fingerLabel
     const inputId=`servo-${c.servo_id}-${key}`
     return <div key={key} className="min-w-0"><label htmlFor={inputId} className="text-xs font-bold text-[#6b7f72]">{label}</label><input id={inputId} className="field mt-1" type="number" min="0" max={maxServoCount} value={c[key]} onChange={e=>change(c.servo_id,{[key]:Math.min(maxServoCount,Math.max(0,Number(e.target.value)))})}/><button type="button" className="text-xs font-bold text-teal mt-2 disabled:opacity-50" disabled={capturing!==null} onClick={()=>capture(c.servo_id,key,label)} aria-label={`Capture ${label.toLowerCase()} for Servo ${c.servo_id}`}>{capturing===`${c.servo_id}-${key}`?'Reading…':'Capture current position'}</button></div>
    })}</div>
    <div className="grid grid-cols-2 gap-3 mt-4"><label className="text-xs font-bold text-[#6b7f72]">Position direction<select className="field mt-1" value={c.direction} onChange={e=>change(c.servo_id,{direction:e.target.value as Calibration['direction']})}><option value="increasing">Counts increase</option><option value="decreasing">Counts decrease</option></select></label><label className="text-xs font-bold text-[#6b7f72]">Gesture speed cap (%)<input className="field mt-1" type="number" min="1" max="100" value={c.speed} onChange={e=>change(c.servo_id,{speed:Number(e.target.value)})}/></label></div>
    <label className="flex items-start gap-2 text-xs text-[#63796a] mt-4 leading-5"><input type="checkbox" checked={c.jog_limits_verified||false} onChange={e=>setDraft(current=>current.map(x=>x.servo_id===c.servo_id?{...x,jog_limits_verified:e.target.checked}:x))} className="mt-1"/> I measured the minimum and maximum counts for a small direct test on this servo.</label>
    <label className="flex items-start gap-2 text-xs text-[#63796a] mt-3 leading-5"><input type="checkbox" checked={c.verified} onChange={e=>setDraft(current=>current.map(x=>x.servo_id===c.servo_id?{...x,verified:e.target.checked}:x))} className="mt-1"/> I also measured open and closed positions and direction for saved gestures.</label>
    <button className="btn btn-primary mt-4 flex items-center gap-2" onClick={()=>save(c)}><Save size={15}/> Save calibration</button>
   </div>
  })}</div>
 </>
}
function Devices({status,hardware,scan,action,go}:{status:Status,hardware:HardwareStatus|null,scan:()=>Promise<void>,action:<T>(work:()=>Promise<T>,success?:string)=>Promise<T|undefined>,go:(page:Page)=>void}){
 const [servoId,setServoId]=useState(1)
 const [delta,setDelta]=useState<10|-10>(10)
 const [preview,setPreview]=useState<JogPreview|null>(null)
 const [phrase,setPhrase]=useState('')
 const [offBody,setOffBody]=useState(false)
 const [powerCut,setPowerCut]=useState(false)
 const [cordClear,setCordClear]=useState(false)
 const [busy,setBusy]=useState(false)
 const check=async()=>{
  setBusy(true);setPreview(null);setPhrase('')
  try{const result=await action(()=>api<JogPreview>('/hardware/jog/preview',{method:'POST',body:json({servo_id:servoId,delta})}));if(result)setPreview(result)}finally{setBusy(false)}
 }
 const send=async()=>{
  if(!preview?.token)return
  setBusy(true)
  try{
   const result=await action(()=>api<{accepted:boolean}>('/hardware/jog/execute',{method:'POST',body:json({token:preview.token,phrase,off_body:offBody,power_cut_tested:powerCut,cord_path_clear:cordClear})}),'One 10-count command sent. Refresh status before another move.')
   setPreview(null);setPhrase('');setOffBody(false);setPowerCut(false);setCordClear(false)
   if(result)await scan()
  }finally{setBusy(false)}
 }
 const controllerState=hardware?.connected?'Connected':hardware?.port?(hardware.servos.length?`${hardware.servos.length}/6 responding`:'USB detected'):'Not connected'
 const items=[
  {name:'Servo controller',state:controllerState,detail:'USB connection and live position',Icon:Cpu},
  {name:'Smart glasses',state:status.devices.glasses??'Not connected',detail:'Glasses camera connection',Icon:Camera},
 ]
 return <div className="space-y-5">
  <div className="device-list">{items.map(({name,state,detail,Icon})=><div key={name} className="card p-5 flex items-center gap-4"><div className="p-3 rounded-xl bg-[#edf6ef] text-teal"><Icon size={22}/></div><div className="flex-1"><div className="font-bold">{name}</div><div className="text-xs text-[#839187] mt-1">{detail}</div></div><Pill tone={state.trim().toLowerCase()==='connected'?'green':hardware?.port&&name==='Servo controller'?'amber':'gray'}>{state}</Pill></div>)}</div>
  <div className="card p-5 max-w-[1040px]">
   <div className="flex flex-wrap justify-between gap-3 items-start"><div><Eyebrow>Servo bus</Eyebrow><h2 className="text-xl font-bold mt-1">Live status</h2><p className="text-xs text-[#839187] mt-1">Arranged like your photo, with the glove above the servo mount. Positions are raw counts; direct-test limits are recorded in Calibration.</p></div><button className="btn btn-secondary" onClick={scan}>Refresh status</button></div>
   {hardware?.error&&<p className="text-xs text-[#9b6633] mt-4">{hardware.error}</p>}
   {hardware?.port&&<p className="text-xs text-[#839187] mt-4">Controller: {hardware.port}</p>}
   <div className="grid md:grid-cols-2 gap-2 mt-4">{servoMounts.map(({id,location})=>{
    const servo=hardware?.servos.find(item=>item.id===id)
    return <div key={id} className="rounded-xl border border-[#e8ece8] px-4 py-3 flex justify-between gap-3"><div><div className="text-[10px] uppercase tracking-[.12em] font-bold text-[#839187]">{location}</div><b className="text-sm">ID {id}</b><span className="text-xs text-[#839187] ml-2">{servos[id-1]}</span></div><div className="text-right text-xs text-[#63796a]">{servo?<><b className="text-[#1e4937]">{servo.position??'—'} counts</b><div>{servo.voltage_v?.toFixed(1)??'—'} V · {servo.temperature_c??'—'}°C</div><div>{servo.mode===0?'Position mode':servo.mode==null?'Mode unknown':`Mode ${servo.mode}`} · {servo.torque_enabled==null?'Torque unknown':servo.torque_enabled?'Torque on':'Torque off'}</div></>:<span>No response</span>}</div></div>
   })}</div>
   <p className="text-xs text-[#839187] mt-4">Status reads do not move the glove. Direct position controls are on Manual Control; saved gestures still run in simulation.</p>
  </div>
  <div className="card p-5 max-w-[1040px]">
   <Eyebrow>Direct servo control</Eyebrow><h2 className="text-xl font-bold mt-1">Small movement test</h2>
   <p className="text-sm text-[#63796a] mt-2 leading-6">Check one 10-count step using the same Feetech controller code as the terminal test. The preview reads the motor and shows its target without moving it. Physical movement stays locked until this servo has measured limits and hardware control is enabled.</p>
   <div className="grid sm:grid-cols-[1fr_1fr_auto] gap-3 mt-5 items-end">
    <label className="text-xs font-bold text-[#6b7f72]">Servo<select className="field mt-1" value={servoId} onChange={e=>{setServoId(Number(e.target.value));setPreview(null)}}>{servoMounts.map(({id,location})=><option key={id} value={id}>ID {id} · {servos[id-1]} · {location}</option>)}</select></label>
    <label className="text-xs font-bold text-[#6b7f72]">Direction<select className="field mt-1" value={delta} onChange={e=>{setDelta(Number(e.target.value) as 10|-10);setPreview(null)}}><option value={10}>+10 counts</option><option value={-10}>−10 counts</option></select></label>
    <button className="btn btn-secondary" disabled={busy} onClick={check}>{busy?'Checking…':'Check move'}</button>
   </div>
   {preview&&<div className="rounded-xl border border-[#dce9df] bg-[#f8faf7] p-4 mt-4"><div className="text-sm font-bold">ID {preview.servo_id}: {preview.start} → {preview.target} counts</div><p className="text-xs text-[#63796a] mt-1">This is a {preview.delta>0?'+10':'−10'} count proposal. No command was sent.</p>
    {preview.blocked_reason?<div className="text-sm text-[#976a20] mt-3">{preview.blocked_reason} <button className="font-bold underline" onClick={()=>go('Calibration')}>Open Calibration</button></div>:<div className="mt-4 space-y-3">
     <p className="text-xs text-[#63796a]">This preview expires in 30 seconds. Keep the glove off your hand and use the independent power cutoff if movement is unexpected.</p>
     <label className="block text-xs"><input type="checkbox" checked={offBody} onChange={e=>setOffBody(e.target.checked)} className="mr-2"/>The glove is off my hand.</label>
     <label className="block text-xs"><input type="checkbox" checked={powerCut} onChange={e=>setPowerCut(e.target.checked)} className="mr-2"/>I tested the physical servo power cutoff for this session.</label>
     <label className="block text-xs"><input type="checkbox" checked={cordClear} onChange={e=>setCordClear(e.target.checked)} className="mr-2"/>The cord path is clear and this step stays within measured travel.</label>
     <label className="block text-xs font-bold text-[#6b7f72]">Type {preview.phrase} to confirm<input className="field mt-1" value={phrase} onChange={e=>setPhrase(e.target.value)} autoComplete="off"/></label>
     <button className="btn btn-primary" disabled={busy||status.stopped||!offBody||!powerCut||!cordClear||phrase.trim()!==preview.phrase} onClick={send}>Send one small move</button>
    </div>}
   </div>}
   <p className="text-xs text-[#839187] mt-4">The app never enables torque automatically or runs the terminal scripts’ multi-servo and return-home commands. Stop latches software control and attempts torque-off only when hardware writes are enabled; the physical power cutoff remains the reliable stop.</p>
  </div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
