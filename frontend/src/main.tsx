import React, {useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ArrowDownToLine, ArrowUpFromLine, Camera, CircleHelp, Cpu, Hand, LayoutDashboard, Menu, Pause, Play, Plus, RotateCcw, Save, Settings2, ShieldCheck, SlidersHorizontal, Square, Trash2, X} from 'lucide-react'
import './index.css'

type Page = 'Dashboard'|'Manual Control'|'Macro Studio'|'AI Vision'|'Calibration'|'Devices'
type Step = {positions: Record<number,number>,duration_ms:number}
type Macro = {id:string,name:string,description:string,kind:'pose'|'sequence',steps:Step[]}
type Calibration = {servo_id:number,name:string,open_count:number,closed_count:number,min_count:number,max_count:number,direction:'increasing'|'decreasing',speed:number,verified:boolean,jog_limits_verified:boolean}
type Status = {mode:string,positions:Record<number,number>,target:Record<number,number>,stopped:boolean,playing:boolean,last_event:string,vision:{label:string,confidence:number,source:string}|null,devices:Record<string,string>}
type HardwareStatus = {connected:boolean,port:string|null,servos:{id:number,model:number,position:number|null,voltage_v:number|null,temperature_c:number|null,moving:boolean|null,mode:number|null,torque_enabled:boolean|null}[],error:string|null,checked_at:number}
type JogPreview = {servo_id:number,start:number,target:number,delta:number,can_execute:boolean,blocked_reason:string|null,token?:string,phrase?:string,expires_in_seconds?:number}
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

function App(){
 const [page,setPage]=useState<Page>('Dashboard'),[status,setStatus]=useState<Status>(initialStatus),[hardware,setHardware]=useState<HardwareStatus|null>(null),[macros,setMacros]=useState<Macro[]>([]),[cal,setCal]=useState<Calibration[]>([]),[manual,setManual]=useState<Record<number,number>>(blank()),[error,setError]=useState(''),[notice,setNotice]=useState(''),[connected,setConnected]=useState(false),[menuOpen,setMenuOpen]=useState(false)
 const refresh=async()=>{try{const [m,c,s]=await Promise.all([api<Macro[]>('/macros'),api<Calibration[]>('/calibration'),api<Status>('/status')]);setMacros(m);setCal(c);setStatus(s);setManual(s.positions);setConnected(true)}catch(e){setError(String(e));setConnected(false)}}
 useEffect(()=>{refresh();const scheme=location.protocol==='https:'?'wss':'ws';let socket:WebSocket;let timer:number;let disposed=false;const connect=()=>{if(disposed)return;socket=new WebSocket(`${scheme}://${location.host}/ws/telemetry`);socket.onopen=()=>setConnected(true);socket.onmessage=e=>{const s:Status=JSON.parse(e.data);setStatus(s)};socket.onclose=()=>{if(!disposed){setConnected(false);timer=window.setTimeout(connect,2500)}}};connect();return()=>{disposed=true;clearTimeout(timer);socket?.close()}},[])
 const scanHardware=async()=>{try{setHardware(await api<HardwareStatus>('/hardware/status'))}catch(e){setHardware({connected:false,port:null,servos:[],error:e instanceof Error?e.message:String(e),checked_at:Date.now()/1000})}}
 useEffect(()=>{scanHardware();const timer=window.setInterval(scanHardware,20000);return()=>clearInterval(timer)},[])
 const action=async<T,>(work:()=>Promise<T>,success?:string)=>{try{setError('');const result=await work();if(success)setNotice(success);return result}catch(e){setError(e instanceof Error?e.message:String(e));return undefined}}
 const play=async(m:Macro)=>{await action(()=>api(`/macros/${m.id}/play`,{method:'POST'}),`Playing ${m.name} in simulation`)}
 const stop=async()=>{const result=await action(()=>api<Status & {hardware_stop?:{attempted:boolean,failed_ids?:number[],message:string}}>('/control/stop',{method:'POST'}));if(result)setNotice(`Software stop latched. ${result.hardware_stop?.message==='Physical writes are disabled.'?'Simulation halted.':result.hardware_stop?.message||'Use the physical power cutoff if the glove moves.'}`)}
 const reset=()=>action(()=>api<Status>('/control/reset',{method:'POST'}),'Simulation resumed')
 const applyManual=()=>action(()=>api('/control/pose',{method:'POST',body:json({positions:manual})}),'Pose sent to simulation')
 const nav:[Page,React.ElementType][]=[['Dashboard',LayoutDashboard],['Manual Control',SlidersHorizontal],['Macro Studio',Hand],['AI Vision',Camera],['Calibration',Settings2],['Devices',Cpu]]
 const navigate=(next:Page)=>{setPage(next);setMenuOpen(false);setNotice('');window.scrollTo({top:0,behavior:'smooth'})}
 return <div className="app-layout">
  <aside className={`app-sidebar ${menuOpen?'open':''}`}>
   <div className="sidebar-top"><button className="sidebar-brand" onClick={()=>navigate('Dashboard')} aria-label="handoff home"><span className="sidebar-brand-icon"><Hand size={25} strokeWidth={2}/></span><span>handoff<span className="brand-dot">.</span></span></button><button className="sidebar-close" onClick={()=>setMenuOpen(false)} aria-label="Close menu"><X size={20}/></button></div>
   <div className="sidebar-section-label">YOUR WORKSPACE</div>
   <nav className="sidebar-nav" aria-label="Main navigation">{nav.map(([name,Icon])=><button key={name} className={page===name?'active':''} onClick={()=>navigate(name)}><Icon size={18} strokeWidth={1.9}/><span>{name==='Dashboard'?'Overview':name}</span>{name==='Macro Studio'&&<small>{macros.length}</small>}</button>)}</nav>
   <div className="sidebar-bottom"><div className="sidebar-section-label">SYSTEM</div><div className="sidebar-status"><span className={`status-dot ${connected?'online':''}`}/><div><b>{connected?'Local API connected':'API offline'}</b><small>Physical targets move on edit</small></div></div></div>
  </aside>
  {menuOpen&&<button className="sidebar-overlay" aria-label="Close menu" onClick={()=>setMenuOpen(false)}/>}
  <div className="app-main"><header className="app-header"><div className="header-title"><button className="mobile-menu" onClick={()=>setMenuOpen(true)} aria-label="Open menu"><Menu size={21}/></button><div><strong>{page==='Dashboard'?'handoff':page}</strong><span>{page==='Dashboard'?'Overview dashboard':subtitles[page]}</span></div></div><div className="header-meta"><span className="header-connection"><i className={connected?'on':''}/>{connected?'API connected':'API offline'}</span><span className="header-mode">LOCAL CONTROL</span></div></header>
   <main className="app-content">
    {error&&<div role="alert" className="message message-error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
    {notice&&<div role="status" className="message message-success"><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss notice"><X size={16}/></button></div>}
    {page!=='Dashboard'&&<div className="page-heading"><div><div className="section-kicker">WORKSPACE / {page.toUpperCase()}</div><h1>{page}</h1><p>{subtitles[page]}</p></div><div className="page-actions"><button className="stop-button" onClick={stop}><Square size={13} fill="currentColor"/> Stop</button>{status.stopped&&<button className="btn btn-secondary flex items-center gap-2" onClick={reset}><RotateCcw size={16}/> Reset software stop</button>}</div></div>}
    {status.stopped&&<div className="stop-message"><Pause size={20}/><div><b>Software stop is latched.</b> Simulation is halted. If hardware control was enabled, servo torque-off was attempted; use the physical power cutoff if the glove moves.</div>{page==='Dashboard'&&<button className="btn btn-secondary" onClick={reset}>Reset software stop</button>}</div>}
    {page==='Dashboard'&&<Dashboard status={status} hardware={hardware} macros={macros} play={play} go={navigate} stop={stop}/>}
    {page==='Manual Control'&&<Manual status={status} hardware={hardware} cal={cal} onReading={setHardware} manual={manual} setManual={setManual} apply={applyManual} go={navigate}/>}
    {page==='Macro Studio'&&<MacroStudio macros={macros} status={status} refresh={refresh} action={action} play={play} manual={manual}/>}
    {page==='AI Vision'&&<Vision/>}
    {page==='Calibration'&&<CalibrationPage cal={cal} hardware={hardware} onReading={setHardware} refresh={refresh} action={action}/>}
    {page==='Devices'&&<Devices status={status} hardware={hardware} scan={scanHardware} action={action} go={navigate}/>}
   </main>
  </div>
 </div>
}
const subtitles:Record<Page,string>={Dashboard:'A clear view of your hand, gestures, and connected systems.', 'Manual Control':'Adjust physical servo targets live, or try a simulated pose.', 'Macro Studio':'Create, edit, and play gestures and movement sequences.', 'AI Vision':'Preview a camera feed from your browser.',Calibration:'Record direct-test limits and gesture positions separately.',Devices:'See live servo status and check small movement commands.'}
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
  <div className="panel-note"><span className="status-dot online"/><b>Simulation active</b><span>{hardwareConnected?'Six servos responding. Physical targets move when edited in Manual Control.':'Saved gestures use simulation; direct motor moves are in Manual Control.'}</span></div>
 </section>
 <section className="overview-panel gestures-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={21}/></span><div className="panel-head-copy"><div className="section-kicker">SAVED MOVEMENTS</div><h2>Gestures, ready to test.</h2><p>Choose a macro to preview it in the simulated hand.</p></div><button className="plain-link" onClick={()=>go('Macro Studio')}>Open Macro Studio <span aria-hidden="true">↗</span></button></div><div className="overview-gesture-grid">{macros.slice(0,4).map((m,i)=><button key={m.id} disabled={status.stopped} onClick={()=>play(m)} className="overview-gesture"><span className="gesture-index">{String(i+1).padStart(2,'0')}</span><span><b>{m.name}</b><small>{m.description||'Custom gesture'}</small></span><Play size={15} fill="currentColor"/></button>)}</div><div className="panel-note"><span className="status-dot"/><span>{macros.length} saved macros</span></div></section>
 </div>}
function HardwareManual({status,hardware,cal,onReading,go}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],onReading:(reading:HardwareStatus)=>void,go:(page:Page)=>void}){
 const [control,setControl]=useState<HardwareControlInfo|null>(null)
 const [targets,setTargets]=useState<Record<number,number>>({})
 const [speeds,setSpeeds]=useState<Record<number,number>>(()=>{try{return JSON.parse(localStorage.getItem('handoff-manual-speeds')||'{}')}catch{return {}}})
 const [localError,setLocalError]=useState('')
 const [movingIds,setMovingIds]=useState<number[]>([])
 const pending=useRef<Record<number,number>>({})
 const timer=useRef<number|null>(null)
 const sending=useRef(false)
 const numberFocused=useRef(false)
 const latest=useRef({speeds,control,cal,status,hardware})
 latest.current={speeds,control,cal,status,hardware}
 useEffect(()=>{api<HardwareControlInfo>('/hardware/control').then(setControl).catch(e=>setLocalError(String(e)))},[])
 useEffect(()=>{localStorage.setItem('handoff-manual-speeds',JSON.stringify(speeds))},[speeds])
 useEffect(()=>{if(!hardware)return;setTargets(current=>{const next={...current};for(const servo of hardware.servos){if(servo.position!=null&&next[servo.id]===undefined)next[servo.id]=servo.position}return next})},[hardware])
 useEffect(()=>()=>{if(timer.current!==null)window.clearTimeout(timer.current)},[])
 useEffect(()=>{if(status.stopped){pending.current={};if(timer.current!==null)window.clearTimeout(timer.current);setMovingIds([])}},[status.stopped])
 const flush=async()=>{
  timer.current=null
  if(numberFocused.current)return
  if(sending.current)return
  const ids=Object.keys(pending.current).map(Number).sort((a,b)=>a-b)
  if(!ids.length)return
  const state=latest.current
  if(!state.control?.writes_enabled||state.status.stopped||!state.hardware?.connected){pending.current={};setLocalError('Motor control is unavailable. No target was sent.');return}
  const moves=ids.map(id=>({servo_id:id,target:pending.current[id],speed:state.speeds[id]??state.control!.position_speed_default}))
  for(const move of moves){const c=state.cal.find(item=>item.servo_id===move.servo_id);if(!c?.jog_limits_verified||!Number.isInteger(move.target)||move.target<c.min_count||move.target>c.max_count){pending.current={};setLocalError(`Servo ${move.servo_id} target is outside its saved limits.`);return}}
  pending.current={}
  sending.current=true
  setMovingIds(ids)
  setLocalError('')
  try{
   await api('/hardware/live',{method:'POST',body:json({moves})})
   const reading=await api<HardwareStatus>('/hardware/status')
   onReading(reading)
  }catch(e){setLocalError(e instanceof Error?e.message:String(e))}
  finally{
   sending.current=false
   setMovingIds([])
   if(Object.keys(pending.current).length)timer.current=window.setTimeout(flush,900)
  }
 }
 const queue=(id:number,value:number)=>{
  setTargets(current=>({...current,[id]:value}))
  const c=latest.current.cal.find(item=>item.servo_id===id)
  if(!Number.isInteger(value)||!c?.jog_limits_verified||value<c.min_count||value>c.max_count){setLocalError(`Servo ${id} target must be within its saved limits.`);return}
  pending.current[id]=value
  setLocalError('')
  if(timer.current!==null)window.clearTimeout(timer.current)
  timer.current=window.setTimeout(flush,900)
 }
 return <section className="card p-5 md:p-7 mb-5">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><Eyebrow>Physical glove</Eyebrow><h2 className="text-xl font-bold mt-1">Live servo positions</h2><p className="text-sm text-[#63796a] mt-2 max-w-2xl">Adjust targets directly. Changes made close together are sent in one synchronized packet; number fields send when you leave the field or press Enter.</p></div><Pill tone={control?.writes_enabled?'green':'amber'}>{control?.writes_enabled?'Live control ready':'Motor control locked'}</Pill></div>
  {localError&&<div role="alert" className="message message-error mt-4">{localError}<button onClick={()=>setLocalError('')} aria-label="Dismiss warning"><X size={16}/></button></div>}
  <div className="grid lg:grid-cols-2 gap-3 mt-6">{servos.map((name,index)=>{
   const id=index+1,c=cal.find(item=>item.servo_id===id),live=hardware?.servos.find(item=>item.id===id)
   const minimum=c?.min_count??0,maximum=c?.max_count??4000,target=targets[id]??live?.position??minimum,speed=speeds[id]??control?.position_speed_default??500
   const available=!!control?.writes_enabled&&!status.stopped&&!!hardware?.connected&&!!c?.jog_limits_verified
   return <div key={id} className="rounded-xl border border-[#e5ebe5] p-4 bg-[#fcfdfc]">
    <div className="flex justify-between gap-2 items-start"><div><div className="text-[10px] font-extrabold tracking-[.13em] text-[#8d9d90]">SERVO 0{id} · {servoMounts.find(m=>m.id===id)?.location.toUpperCase()}</div><h3 className="font-bold text-base mt-1 mb-0">{name}</h3></div><span className="text-xs text-[#607669] text-right">Now <b>{live?.position??'—'}</b><br/>{movingIds.includes(id)?'Sending':live?.moving?'Moving':'Stopped'}</span></div>
    <div className="grid grid-cols-[1fr_100px] gap-3 items-end mt-5"><label className="text-xs font-bold text-[#6b7f72]">Target count<input aria-label={`${name} target count slider`} type="range" min={minimum} max={maximum} value={Math.max(minimum,Math.min(maximum,target))} onChange={e=>queue(id,Number(e.target.value))} className="w-full mt-3" disabled={!available}/></label><input aria-label={`${name} target count`} className="field" type="number" min={minimum} max={maximum} step="1" value={target} disabled={!available} onFocus={()=>{numberFocused.current=true;if(timer.current!==null)window.clearTimeout(timer.current)}} onChange={e=>setTargets(current=>({...current,[id]:Number(e.target.value)}))} onBlur={e=>{numberFocused.current=false;queue(id,Number(e.target.value))}} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}}/></div>
    <div className="flex justify-between text-[10px] text-[#94a39a] mt-1"><span>{minimum} min</span><span>{maximum} max</span></div>
    <div className="grid grid-cols-[1fr_70px] gap-3 items-center mt-4"><label className="text-xs font-bold text-[#6b7f72]">Speed · {speed} counts/s<input aria-label={`${name} speed`} type="range" min={control?.position_speed_min??50} max={control?.position_speed_max??2400} step="25" value={speed} onChange={e=>setSpeeds(current=>({...current,[id]:Number(e.target.value)}))} className="w-full mt-2" disabled={!available}/></label><span className="text-right text-[10px] text-[#87978a]">50–{control?.position_speed_max??2400}</span></div>
    {!c?.jog_limits_verified&&<button className="text-xs font-bold text-[#976a20] underline mt-3" onClick={()=>go('Calibration')}>Verify limits</button>}
   </div>
  })}</div>
  <p className="text-xs text-[#839187] mt-5">Editing a target can move the physical glove. Saved travel limits, device health checks, and Stop remain active. Keep the physical power cutoff within reach.</p>
 </section>
}
function Manual({status,hardware,cal,onReading,manual,setManual,apply,go}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],onReading:(reading:HardwareStatus)=>void,manual:Record<number,number>,setManual:(p:Record<number,number>)=>void,apply:()=>void,go:(p:Page)=>void}){
 return <><HardwareManual status={status} hardware={hardware} cal={cal} onReading={onReading} go={go}/><div className="grid xl:grid-cols-[1.1fr_.9fr] gap-5"><div className="card p-5 md:p-7"><div className="flex justify-between items-start mb-6"><div><Eyebrow>Simulation</Eyebrow><h2 className="text-xl font-bold mt-1">Finger controls</h2></div><Pill>0–100% movement</Pill></div><div className="space-y-5">{servos.map((name,i)=><div key={name} className="rounded-xl border border-[#e8ece8] p-4"><div className="flex justify-between mb-4"><div className="font-bold text-sm"><span className="text-teal mr-2">0{i+1}</span>{name}</div><span className="text-teal font-bold">{manual[i+1]}%</span></div><input aria-label={`${name} simulation movement`} type="range" min="0" max="100" value={manual[i+1]} onChange={e=>setManual({...manual,[i+1]:Number(e.target.value)})} className="w-full"/><div className="flex justify-between text-[10px] text-[#94a39a] mt-1"><span>{i<4?'Open':'0%'}</span><span>{i<4?'Closed':'100%'}</span></div></div>)}</div><div className="flex gap-2 mt-5"><button className="btn btn-primary flex items-center gap-2" disabled={status.stopped} onClick={apply}><Play size={16}/> Apply to simulation</button><button className="btn btn-secondary" onClick={()=>setManual(blank())}>Set all to 0%</button></div></div><div className="space-y-5"><div className="card p-5"><Eyebrow>Live preview</Eyebrow><h2 className="text-xl font-bold mt-1 mb-4">Glove</h2><HandGraphic/></div><div className="card p-5 flex items-start gap-3"><CircleHelp className="text-teal shrink-0" size={20}/><div className="text-sm text-[#62756a] leading-6">These sliders use percentages in the simulator. Actual servo counts depend on measured endpoints. <button className="text-teal font-bold" onClick={()=>go('Calibration')}>Set calibration →</button></div></div></div></div></>
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
