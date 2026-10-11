import React, {useEffect, useMemo, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {Anchor, ArrowUpRight, Camera, Check, CircleAlert, Cpu, Eraser, Hand, LayoutDashboard, Lock, Menu, Mic, MicOff, Pause, Pencil, PhoneOff, Play, PlugZap, Plus, RefreshCw, RotateCcw, Save, SendHorizontal, Settings, SlidersHorizontal, Sparkles, Square, Trash2, Undo2, User, Volume2, X, type LucideIcon} from 'lucide-react'
import {Canvas, useFrame, useThree} from '@react-three/fiber'
import * as THREE from 'three'
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js'
import {RoundedBoxGeometry} from 'three/examples/jsm/geometries/RoundedBoxGeometry.js'
import type {FunctionCall, LiveConnectConfig, LiveServerMessage, Session} from '@google/genai'
import './index.css'

type Page = 'Dashboard'|'Manual Control'|'Macro Studio'|'AI Vision'|'AI Assistant'|'Devices'|'Settings'
type Step = {positions: Record<number,number>,duration_ms:number}
type Macro = {id:string,name:string,description:string,kind:'pose'|'sequence',steps:Step[],source?:'you'|'assistant'}
type Calibration = {servo_id:number,name:string,open_count:number,closed_count:number,min_count:number,max_count:number,direction:'increasing'|'decreasing',speed:number,verified:boolean,jog_limits_verified:boolean}
type Physical = {connected:boolean,positions:Record<number,number|null>,read_at:number|null,error:string|null}
type Status = {mode:string,positions:Record<number,number>,target:Record<number,number>,stopped:boolean,playing:boolean,last_event:string,vision:{label:string,confidence:number,source:string}|null,physical:Physical,devices:Record<string,string>}
type HardwareStatus = {connected:boolean,port:string|null,servos:{id:number,model:number,position:number|null,voltage_v:number|null,temperature_c:number|null,moving:boolean|null,mode:number|null,torque_enabled:boolean|null}[],error:string|null,checked_at:number}
type HardwareControlInfo = {writes_enabled:boolean,step_counts:number,position_speed_min:number,position_speed_max:number,position_speed_default:number}
const servos = ['Pinky finger','Ring finger','Middle finger','Index finger','Thumb flexion (up/down)','Thumb side-to-side']
// IDs 1–6 include both thumb flexion (5) and side-to-side opposition (6).
const allServoIds = [1,2,3,4,5,6] as const
const maxServoCount = 4000
const servoMounts = [
 {id:4,location:'Top left'}, {id:3,location:'Top right'},
 {id:5,location:'Middle left'}, {id:2,location:'Middle right'},
 {id:6,location:'Bottom left'}, {id:1,location:'Bottom right'},
] as const
const blank = ():Record<number,number>=>Object.fromEntries([1,2,3,4,5,6].map(id=>[id,0]))
const poseCounts=(positions:Record<number,number>)=>Object.fromEntries(Object.entries(positions).map(([id,value])=>[id,Math.round(value*maxServoCount/100)]))
const initialStatus:Status={mode:'simulation',positions:blank(),target:blank(),stopped:false,playing:false,last_event:'Connecting',vision:null,physical:{connected:false,positions:{},read_at:null,error:null},devices:{controller:'Not connected',glasses:'Not connected',camera:'Browser preview only'}}
const physicalCurls=(physical:Physical|undefined,cal:Calibration[]):Record<number,number|null>=>Object.fromEntries(allServoIds.map(id=>{const count=physical?.connected?physical.positions[id]:null,c=cal.find(item=>item.servo_id===id);return [id,count==null||!c||c.closed_count===c.open_count?null:Math.max(0,Math.min(100,Math.round((count-c.open_count)/(c.closed_count-c.open_count)*100)))]}))
async function api<T>(path:string,options?:RequestInit):Promise<T>{const response=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options}); if(!response.ok){let message=`Request failed (${response.status})`;try{const error=await response.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail)}catch{}throw Error(message)}return response.status===204?undefined as T:response.json()}
const json=(body:unknown)=>JSON.stringify(body)
// The public demo (feat/deploy) builds with VITE_API_URL and has no glove: hardware routes answer 403 there.
const isDemo=Boolean(import.meta.env.VITE_API_URL)
function LocalOnly({title,children}:{title:string,children:React.ReactNode}){return <div className="local-only card"><Lock size={20}/><div><b>{title}</b><p>{children}</p></div></div>}
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
 const [page,setPage]=useState<Page>('Dashboard'),[status,setStatus]=useState<Status>(initialStatus),[hardware,setHardware]=useState<HardwareStatus|null>(null),[macros,setMacros]=useState<Macro[]>([]),[cal,setCal]=useState<Calibration[]>([]),[error,setError]=useState(''),[notice,setNotice]=useState(''),[connected,setConnected]=useState(false),[menuOpen,setMenuOpen]=useState(false)
 const refresh=async()=>{try{const [m,c,s]=await Promise.all([api<Macro[]>('/macros'),api<Calibration[]>('/calibration'),api<Status>('/status')]);setMacros(m);setCal(c);setStatus(s);setConnected(true)}catch(e){setError(String(e));setConnected(false)}}
 useEffect(()=>{const reload=()=>{api<Macro[]>('/macros').then(setMacros).catch(()=>{})};window.addEventListener('macros-changed',reload);window.addEventListener('focus',reload);const timer=window.setInterval(reload,15000);return()=>{window.removeEventListener('macros-changed',reload);window.removeEventListener('focus',reload);clearInterval(timer)}},[])
 useEffect(()=>{refresh();const scheme=location.protocol==='https:'?'wss':'ws';let socket:WebSocket;let timer:number;let disposed=false;const connect=()=>{if(disposed)return;socket=new WebSocket(`${scheme}://${location.host}/ws/telemetry`);socket.onopen=()=>setConnected(true);socket.onmessage=e=>{const s:Status=JSON.parse(e.data);setStatus(s)};socket.onclose=()=>{if(!disposed){setConnected(false);timer=window.setTimeout(connect,2500)}}};connect();return()=>{disposed=true;clearTimeout(timer);socket?.close()}},[])
 const scanHardware=async()=>{try{setHardware(await api<HardwareStatus>('/hardware/status'))}catch(e){setHardware({connected:false,port:null,servos:[],error:e instanceof Error?e.message:String(e),checked_at:Date.now()/1000})}}
 useEffect(()=>{scanHardware();const timer=window.setInterval(scanHardware,20000);return()=>clearInterval(timer)},[])
 const action=async<T,>(work:()=>Promise<T>,success?:string)=>{try{setError('');const result=await work();if(success)setNotice(success);return result}catch(e){setError(e instanceof Error?e.message:String(e));return undefined}}
 const play=async(m:Macro)=>{await action(()=>api(`/macros/${m.id}/play`,{method:'POST'}),`Playing ${m.name} in simulation`)}
 const stop=async()=>{const result=await action(()=>api<Status & {hardware_stop?:HardwareStop}>('/control/stop',{method:'POST'}));if(result){cancelPendingProposals();setNotice(stopNotice(result))}}
 const reset=()=>action(()=>api<Status>('/control/reset',{method:'POST'}),'Simulation resumed')
 const nav:[Page,LucideIcon,string][]=[['Dashboard',LayoutDashboard,'overview'],['Macro Studio',Hand,'macro'],['Manual Control',SlidersHorizontal,'manual'],['AI Vision',Camera,'vision'],['Settings',Settings,'settings']]
 const navigate=(next:Page)=>{setPage(next);setMenuOpen(false);setNotice('');window.scrollTo({top:0,behavior:'smooth'})}
 return <div className="app-layout">
  <aside className={`app-sidebar ${menuOpen?'open':''}`}>
   <div className="sidebar-top"><button className="sidebar-brand" onClick={()=>navigate('Dashboard')} aria-label="handoff home"><span className="sidebar-brand-icon"><Hand size={25} strokeWidth={2}/></span><span>handoff<span className="brand-dot">.</span></span></button><button className="sidebar-close" onClick={()=>setMenuOpen(false)} aria-label="Close menu"><X size={20}/></button></div>
   <div className="sidebar-section-label">YOUR WORKSPACE</div>
   <nav className="sidebar-nav" aria-label="Main navigation">{nav.map(([name,Icon,slug])=><button key={name} className={`nav-${slug}${page===name?' active':''}`} aria-current={page===name?'page':undefined} onClick={()=>navigate(name)}><Icon size={18} strokeWidth={1.9}/><span>{name==='Dashboard'?'Overview':name}</span>{name==='Macro Studio'&&<small>{macros.length}</small>}</button>)}</nav>
   <div className="sidebar-bottom"><div className="sidebar-section-label">SYSTEM</div><div className="sidebar-status"><span className={`status-dot ${connected?'online':''}`}/><div><b>{connected?'Local API connected':'API offline'}</b><small>{isDemo?'Simulation only on this site':'Targets require confirmation'}</small></div></div></div>
  </aside>
  {menuOpen&&<button className="sidebar-overlay" aria-label="Close menu" onClick={()=>setMenuOpen(false)}/>}
  <div className="app-main"><header className="app-header"><div className="header-title"><button className="mobile-menu" onClick={()=>setMenuOpen(true)} aria-label="Open menu"><Menu size={21}/></button><div><strong>{page==='Dashboard'?'Overview':page}</strong><span>{page==='Dashboard'?'Overview dashboard':subtitles[page]}</span></div></div><div className="header-meta"><span className="header-connection"><i className={connected?'on':''}/>{connected?'API connected':'API offline'}</span>{status.stopped&&<button className="btn btn-secondary btn-sm" onClick={reset}><RotateCcw size={14}/> Reset software stop</button>}<LiveButton macros={macros} setNotice={setNotice}/><button className="stop-button" onClick={stop}><Square size={12} fill="currentColor"/> Stop</button></div></header>
   <main className="app-content">
    {error&&<div role="alert" className="message message-error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
    {notice&&<div role="status" className="message message-success"><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss notice"><X size={16}/></button></div>}
    {page!=='Dashboard'&&<div className="page-heading"><div><div className="section-kicker">WORKSPACE / {page.toUpperCase()}</div><h1>{page}</h1><p>{subtitles[page]}</p></div></div>}
    {status.stopped&&<div className="stop-message"><Pause size={20}/><div><b>Software stop is latched.</b> Simulation is halted. If hardware control was enabled, servo torque-off was attempted; use the physical power cutoff if the glove moves.</div></div>}
    {page==='Dashboard'&&<Dashboard status={status} hardware={hardware} cal={cal} macros={macros} play={play} go={navigate} onReading={setHardware} action={action}/>}
    {page==='Manual Control'&&<Manual status={status} hardware={hardware} onReading={setHardware}/>}
    {page==='Macro Studio'&&<div className="studio-split"><div className="studio-left"><MacroStudio macros={macros} status={status} cal={cal} refresh={refresh} action={action} play={play} manual={Object.fromEntries(allServoIds.map(id=>[id,hardware?.servos.find(servo=>servo.id===id)?.position??(status.positions[id]??0)*40]))}/></div><div className="studio-right"><Assistant macros={macros} action={action} setNotice={setNotice} setError={setError}/></div></div>}
    {page==='AI Vision'&&(isDemo?<LocalOnly title="AI Vision runs on the local site">Object detection and hand tracking need the camera and models on the computer next to the glove.</LocalOnly>:<Vision/>)}
    {page==='Settings'&&<div className="settings-stack"><h2 className="settings-section-title">Devices</h2>{isDemo?<LocalOnly title="Devices are shown on the local site only">The servo controller and glasses connect to the computer next to the glove.</LocalOnly>:<Devices status={status} hardware={hardware} scan={scanHardware}/>}<h2 className="settings-section-title">Gemini</h2><SettingsPage action={action}/></div>}
   </main>
  </div>
 </div>
}
const subtitles:Record<Page,string>={Settings:'Connected devices and your Gemini API key. The key stays on this computer.',Dashboard:'A clear view of your hand, gestures, and connected systems.', 'Manual Control':'Stage and confirm physical servo targets.', 'Macro Studio':'Build gestures by hand, or ask Gemini to draft them. Nothing moves until you click Confirm.', 'AI Vision':'Run local object detection and hand tracking from your camera. Nothing here moves the glove.','AI Assistant':'Ask by text or voice; every movement waits for your Confirm and runs in simulation.',Devices:'See connected devices and live servo status.'}
function Origin({source}:{source?:'you'|'assistant'}){return source==='assistant'?<span className="origin ai"><Sparkles size={11}/>From the assistant</span>:<span className="origin you"><User size={11}/>From you</span>}
function Eyebrow({children}:{children:React.ReactNode}){return <div className="text-[10px] uppercase tracking-[.13em] text-[#63796a] font-extrabold">{children}</div>}
function Pill({children,tone='green'}:{children:React.ReactNode,tone?:'green'|'gray'|'amber'}){return <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold ${tone==='green'?'bg-[#e3f7e8] text-[#247658]':tone==='amber'?'bg-[#fff0d8] text-[#976a20]':'bg-[#edf0ed] text-[#708079]'}`}>{children}</span>}
// 3D model of the real glove, seen from the back of a right hand (thumb on the left). The black cord guides,
// anchor block and tubes sit on the back of the hand; curling bends the fingers away from the camera, toward the plain white palm.
const gloveMaterial=new THREE.MeshStandardMaterial({color:'#eeede6',roughness:.9})
const guideMaterial=new THREE.MeshStandardMaterial({color:'#1b1d1c',roughness:.55})
const holeMaterial=new THREE.MeshStandardMaterial({color:'#050606',roughness:1})
const threadMaterial=new THREE.MeshStandardMaterial({color:'#f7f6f0',roughness:.6})
const tubeMaterial=new THREE.MeshStandardMaterial({color:'#f4f4ef',roughness:.35})
type Vec3=[number,number,number]
type FingerSpec={id:number,x:number,y:number,splay:number,radius:number,lengths:Vec3}
// Proportions from the glove photos: middle finger longest, pinky short, thick padded fingers.
const fingerSpecs:FingerSpec[]=[
 {id:4,x:-1.3,y:3.25,splay:.06,radius:.41,lengths:[1.45,1,.85]}, // index
 {id:3,x:-.43,y:3.4,splay:.01,radius:.43,lengths:[1.65,1.12,.9]}, // middle
 {id:2,x:.44,y:3.3,splay:-.04,radius:.41,lengths:[1.5,1.05,.85]}, // ring
 {id:1,x:1.28,y:3,splay:-.12,radius:.36,lengths:[1.15,.8,.72]}, // pinky
]
const jointMax:Vec3=[1.4,1.75,1.1] // knuckle, middle and tip joint bend at 100 % curl (radians)
const BACK_Z=.525 // back-of-hand surface
// Curved anchor blocks: arcs of radius R hugging the hand. The back block sits near the wrist, slightly toward the thumb; the palm
// block sits at the heel of the palm. Palm-side parts are drawn turned half a turn about the hand's long axis, so PALM_PLATE's x is mirrored.
type Plate={x:number,y:number,height:number,R:number,thickness:number,arc:number}
const BACK_PLATE:Plate={x:-.2,y:1.2,height:1.25,R:3.2,thickness:.4,arc:.35}
const PALM_PLATE:Plate={x:0,y:.55,height:1,R:3.2,thickness:.36,arc:.33}
const GUIDE_DEPTH=.19 // how far a guide's raised channel stands off the glove surface
const plateHoles=(plate:Plate)=>[-.26,-.13,0,.13,.26].map(angle=>{const r=plate.R+plate.thickness/2;return {angle,x:plate.x+r*Math.sin(angle),z:BACK_Z-plate.R+r*Math.cos(angle)}})
const THUMB_TWIST=-.8 // radians (~46°) the thumb is rotated about its long axis relative to the fingers
// Thumb motion values were fitted (scratch optimiser over 75,625 poses) so the Pinch macro brings the thumb tip pad-to-pad with the index tip,
// a fist rests the thumb across the front of the curled fingers, and nothing ever collides or tucks under the fingers.
const THUMB={root:[-1.5,1.11,-.35] as Vec3,lengths:[1.3,1.05,.9] as Vec3,radii:[.46,.43,.4] as Vec3,bend:[0,1,.54] as Vec3,maxLift:1.5,
 spread:.6,sideSpread:.4,flexSwing:.61,sideSwing:.54,roll:1.14,straighten:.37}
// Thumb base motion, like a real thumb's base joint: it swings forward in front of the palm (X) while rolling on its own axis (Y, opposition),
// so its bend points across the palm toward the pinky instead of down toward the wrist; Z swings it in the palm plane toward the index.
// Flexion and side-to-side each bring some forward swing; the collision "lift" adds more and straightens the thumb so it lies over a fist.
const thumbRootEuler=(flex:number,side:number,lift:number)=>{const swing=flex*THUMB.flexSwing+side*THUMB.sideSwing+lift;return new THREE.Euler(-swing,-swing*THUMB.roll,THUMB.spread-side*THUMB.sideSpread,'ZXY')}
const thumbBends=(flex:number,lift:number)=>THUMB.bend.map(bend=>flex*bend*(1-THUMB.straighten*lift/THUMB.maxLift)) as Vec3
// Collision avoidance: the thumb swings out from the palm ("lift") just far enough to clear the palm and the curled fingers,
// so it always rests on top of them like a natural hand. Segments are capsules (axis start, end, radius) in hand coordinates.
type Capsule={a:THREE.Vector3,b:THREE.Vector3,r:number}
const chainCapsules=(start:THREE.Matrix4,lengths:Vec3,radii:Vec3,bends:Vec3):Capsule[]=>{const m=start.clone();return lengths.map((length,i)=>{m.multiply(new THREE.Matrix4().makeRotationX(-bends[i]));const capsule={a:new THREE.Vector3().applyMatrix4(m),b:new THREE.Vector3(0,length,0).applyMatrix4(m),r:radii[i]};m.multiply(new THREE.Matrix4().makeTranslation(0,length,0));return capsule})}
const fingerCapsules=(spec:FingerSpec,curl:number)=>chainCapsules(new THREE.Matrix4().makeTranslation(spec.x,spec.y,0).multiply(new THREE.Matrix4().makeRotationZ(spec.splay)),spec.lengths,[spec.radius,spec.radius*.95,spec.radius*.9],jointMax.map(max=>curl*max) as Vec3)
const thumbCapsules=(flex:number,side:number,lift:number)=>chainCapsules(new THREE.Matrix4().makeTranslation(...THUMB.root).multiply(new THREE.Matrix4().makeRotationFromEuler(thumbRootEuler(flex,side,lift))).multiply(new THREE.Matrix4().makeRotationY(THUMB_TWIST)),THUMB.lengths,THUMB.radii,thumbBends(flex,lift))
const segmentDistance=(p1:THREE.Vector3,q1:THREE.Vector3,p2:THREE.Vector3,q2:THREE.Vector3)=>{ // closest distance between two line segments
 const d1=q1.clone().sub(p1),d2=q2.clone().sub(p2),r=p1.clone().sub(p2),a=d1.dot(d1),e=d2.dot(d2),f=d2.dot(r),clamp=THREE.MathUtils.clamp
 let s=0,t=0
 if(a<1e-9&&e<1e-9)return r.length()
 if(a<1e-9)t=clamp(f/e,0,1)
 else{const c=d1.dot(r);if(e<1e-9)s=clamp(-c/a,0,1);else{const b=d1.dot(d2),denom=a*e-b*b;s=denom>1e-9?clamp((b*f-c*e)/denom,0,1):0;t=(b*s+f)/e;if(t<0){t=0;s=clamp(-c/a,0,1)}else if(t>1){t=1;s=clamp((b-c)/a,0,1)}}}
 return p1.clone().addScaledVector(d1,s).sub(p2.clone().addScaledVector(d2,t)).length()
}
const boxDistance=(p:THREE.Vector3,center:Vec3,half:Vec3)=>Math.hypot(Math.max(Math.abs(p.x-center[0])-half[0],0),Math.max(Math.abs(p.y-center[1])-half[1],0),Math.max(Math.abs(p.z-center[2])-half[2],0))
// The palm with its thin layer of palm-side guides and cords, plus the palm anchor block standing proud of the heel of the palm.
const palmDistance=(p:THREE.Vector3)=>Math.min(boxDistance(p,[0,1.7,-.05],[1.775,1.8,BACK_Z+.05]),boxDistance(p,[-PALM_PLATE.x,PALM_PLATE.y,-(BACK_Z+PALM_PLATE.thickness/2)],[1.25,PALM_PLATE.height/2,PALM_PLATE.thickness/2]))
// The thumb's base knuckle (start of segment 1) blends into the fleshy pad of the palm, so the palm check starts partway along it;
// the padded palm may also give a little (0.15) where the thumb presses on it. Contact distances allow for one guide's depth
// (guides are narrow and rarely meet face to face); the palm's own guide layer and block are part of palmDistance. Segment 0 sits inside the thumb pad, where a curled fingertip may rest.
const palmSamples=[[],[.4,.6,.8,1],[0,.2,.4,.6,.8,1]]
const thumbCollides=(thumb:Capsule[],fingers:Capsule[])=>thumb.some((segment,i)=>(i>0&&fingers.some(finger=>segmentDistance(segment.a,segment.b,finger.a,finger.b)<segment.r+finger.r+GUIDE_DEPTH+.03))||palmSamples[i].some(t=>palmDistance(segment.a.clone().lerp(segment.b,t))<segment.r-.15))
// True when part of the thumb sits between a curled finger and the palm, i.e. tucked under the fingers instead of over them.
const thumbUnder=(thumb:Capsule[],fingers:Capsule[])=>thumb.slice(1).some(segment=>[0,.5,1].some(t=>{const point=segment.a.clone().lerp(segment.b,t);return fingers.some(finger=>{const mid=finger.a.clone().lerp(finger.b,.5);return mid.z<-.6&&Math.hypot(mid.x-point.x,mid.y-point.y)<.6&&point.z>mid.z+.05})}))
// Swing the thumb out from its natural position only as far as needed to clear the palm and fingers and rest on top of them.
const solveThumbLift=(flex:number,side:number,curls:Record<number,number>):number=>{
 const fingers=fingerSpecs.flatMap(spec=>fingerCapsules(spec,curls[spec.id]??0)),step=.05,steps=Math.round(THUMB.maxLift/step)
 for(let k=0;k<=steps;k++){const thumb=thumbCapsules(flex,side,k*step);if(!thumbCollides(thumb,fingers)&&!thumbUnder(thumb,fingers))return k*step}
 return THUMB.maxLift
}
const ease=(from:number,to:number,dt:number)=>from+(to-from)*(1-Math.exp(-dt*12))
type HandTargets=React.RefObject<Record<number,number>>
function Thread({from,to,radius=.022,material=threadMaterial}:{from:Vec3,to:Vec3,radius?:number,material?:THREE.Material}){
 const {position,quaternion,length}=useMemo(()=>{const start=new THREE.Vector3(...from),end=new THREE.Vector3(...to),span=end.clone().sub(start);return {position:start.clone().add(end).multiplyScalar(.5),quaternion:new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),span.clone().normalize()),length:span.length()}},[from.join(),to.join()])
 return <mesh position={position} quaternion={quaternion} material={material}><cylinderGeometry args={[radius,radius,length,6]}/></mesh>
}
// A cord guide: flat black plate, raised centre channel for the cord, white stitching through a slot on each side.
function Guide({width,position}:{width:number,position:Vec3}){
 return <group position={position}><mesh material={guideMaterial}><boxGeometry args={[width,.44,.06]}/></mesh><mesh material={guideMaterial} position={[0,0,.11]}><boxGeometry args={[width*.42,.44,.16]}/></mesh>{[-1,1].map(side=><mesh key={side} material={threadMaterial} position={[side*width*.36,0,.035]}><boxGeometry args={[.05,.24,.012]}/></mesh>)}</group>
}
// One guide and its cord on the +z surface of a segment; the tip segment ends the cord in a knot.
function CordLine({length,surface,width,at,tip}:{length:number,surface:number,width:number,at:number,tip?:boolean}){
 const cordZ=surface+.14
 return <><Guide width={width} position={[0,length*at,surface+.03]}/><Thread from={[0,-.05,cordZ]} to={[0,tip?length*at:length,cordZ]}/>{tip&&<mesh material={threadMaterial} position={[0,length*at+.25,cordZ]}><sphereGeometry args={[.08,8,8]}/></mesh>}</>
}
// One finger segment, with a guide and cord on the back and another on the palm side, like the real glove.
function Phalanx({length,radius,guideAt,palmAt=.5,tip}:{length:number,radius:number,guideAt:number,palmAt?:number,tip?:boolean}){
 const surface=radius*.92,width=Math.max(.5,radius*1.55)
 return <><mesh position={[0,length/2,0]} material={gloveMaterial}><capsuleGeometry args={[radius,length,6,16]}/></mesh><CordLine length={length} surface={surface} width={width} at={guideAt} tip={tip}/><group rotation={[0,Math.PI,0]}><CordLine length={length} surface={surface} width={width} at={palmAt} tip={tip}/></group></>
}
function Finger({spec,targets,curls}:{spec:FingerSpec,targets:HandTargets,curls:HandTargets}){
 const joints=[useRef<THREE.Group>(null),useRef<THREE.Group>(null),useRef<THREE.Group>(null)],curl=useRef(0),[a,b,c]=spec.lengths,r=spec.radius
 useFrame((_,dt)=>{curl.current=ease(curl.current,(targets.current[spec.id]??0)/100,dt);curls.current[spec.id]=curl.current;joints.forEach((joint,i)=>{if(joint.current)joint.current.rotation.x=-curl.current*jointMax[i]})})
 return <group position={[spec.x,spec.y,0]} rotation={[0,0,spec.splay]}><group ref={joints[0]}><Phalanx length={a} radius={r} guideAt={.55}/><group ref={joints[1]} position={[0,a,0]}><Phalanx length={b} radius={r*.95} guideAt={.5}/><group ref={joints[2]} position={[0,b,0]}><Phalanx length={c} radius={r*.9} guideAt={.78} palmAt={.45} tip/></group></group></group></group>
}
function Thumb({targets,curls}:{targets:HandTargets,curls:HandTargets}){
 const root=useRef<THREE.Group>(null),joints=[useRef<THREE.Group>(null),useRef<THREE.Group>(null),useRef<THREE.Group>(null)],flex=useRef(0),side=useRef(0),lift=useRef(0),solved=useRef({inputs:'',lift:0})
 // Runs after the fingers' frame updates (it is rendered after them), so it solves against the curls on screen this frame.
 useFrame((_,dt)=>{
  flex.current=ease(flex.current,(targets.current[5]??0)/100,dt) // Servo 5: thumb flexion (up/down), bending toward the palm
  side.current=ease(side.current,(targets.current[6]??0)/100,dt) // Servo 6: thumb side-to-side, swinging in front of the palm
  const f=flex.current,s=side.current,inputs=[f,s,...fingerSpecs.map(spec=>curls.current[spec.id]??0)].map(value=>value.toFixed(3)).join()
  if(inputs!==solved.current.inputs)solved.current={inputs,lift:solveThumbLift(f,s,curls.current)}
  lift.current=solved.current.lift>lift.current?solved.current.lift:ease(lift.current,solved.current.lift,dt) // rise at once to stay clear, settle back smoothly
  root.current?.rotation.copy(thumbRootEuler(f,s,lift.current));const bends=thumbBends(f,lift.current);joints.forEach((joint,i)=>joint.current?.rotation.set(-bends[i],0,0))
 })
 // The thumb is turned about its own axis (THUMB_TWIST) so its guides face partly outward, like the real glove,
 // and its joints bend across the palm toward the pinky instead of straight back.
 const [l0,l1,l2]=THUMB.lengths,[r0,r1,r2]=THUMB.radii
 return <group ref={root} position={THUMB.root}><group rotation={[0,THUMB_TWIST,0]}><group ref={joints[0]}><Phalanx length={l0} radius={r0} guideAt={.6}/><group ref={joints[1]} position={[0,l0,0]}><Phalanx length={l1} radius={r1} guideAt={.5}/><group ref={joints[2]} position={[0,l1,0]}><Phalanx length={l2} radius={r2} guideAt={.75} palmAt={.45} tip/></group></group></group></group></group>
}
function AnchorBlock({plate}:{plate:Plate}){
 const geometry=useMemo(()=>{
  const {R,thickness:t,arc,height}=plate,shape=new THREE.Shape(),steps=24
  for(let i=0;i<=steps;i++){const angle=-arc+2*arc*i/steps;if(i)shape.lineTo((R+t)*Math.sin(angle),(R+t)*Math.cos(angle));else shape.moveTo((R+t)*Math.sin(angle),(R+t)*Math.cos(angle))}
  for(let i=steps;i>=0;i--){const angle=-arc+2*arc*i/steps;shape.lineTo(R*Math.sin(angle),R*Math.cos(angle))}
  const extruded=new THREE.ExtrudeGeometry(shape,{depth:height,bevelEnabled:true,bevelSize:.04,bevelThickness:.04,bevelSegments:2,curveSegments:24})
  extruded.rotateX(Math.PI/2);extruded.translate(0,height/2,0);return extruded
 },[plate])
 const mid=plate.R+plate.thickness/2
 return <group position={[plate.x,plate.y,BACK_Z-plate.R]}>
  <mesh geometry={geometry} material={guideMaterial}/>
  {plateHoles(plate).map(({angle})=><mesh key={angle} material={holeMaterial} position={[mid*Math.sin(angle),plate.height/2+.045,mid*Math.cos(angle)]}><cylinderGeometry args={[.075,.075,.02,12]}/></mesh>)}
  {[-1,1].map(end=>{const angle=end*(plate.arc+.07);return <group key={end} position={[mid*Math.sin(angle),0,mid*Math.cos(angle)]} rotation={[0,angle,0]}><mesh material={guideMaterial}><boxGeometry args={[.24,plate.height*.85,plate.thickness*.55]}/></mesh><mesh material={threadMaterial} position={[0,0,plate.thickness*.28+.01]}><boxGeometry args={[.05,plate.height*.5,.02]}/></mesh></group>})}
 </group>
}
// White tubes leave the wrist side of an anchor block and run down past the cuff toward the forearm.
function Tube({plate,x,z}:{plate:Plate,x:number,z:number}){
 const geometry=useMemo(()=>{const top=plate.y-plate.height/2,spread=x-plate.x;return new THREE.TubeGeometry(new THREE.CatmullRomCurve3([new THREE.Vector3(x,top+.05,z),new THREE.Vector3(x+spread*.1,-.6,z+.08),new THREE.Vector3(x+spread*.4,-2.1,z+.18),new THREE.Vector3(x+spread*1.2,-3.5,z+.4)]),24,.065,8)},[plate,x,z])
 return <mesh geometry={geometry} material={tubeMaterial}/>
}
// One side's hardware fixed to the hand: anchor block, tubes, a row of guides at the knuckles and the cords between them.
// Fingers are listed index→pinky; holes run thumb→pinky on the back and pinky→thumb on the palm (its x is mirrored).
function CordSide({plate,knuckleY,mirror}:{plate:Plate,knuckleY:number,mirror:boolean}){
 const holes=plateHoles(plate),holeTop=plate.y+plate.height/2+.04,guideTop=BACK_Z+.03+.13,sign=mirror?-1:1
 return <><AnchorBlock plate={plate}/>{holes.map(({x,z})=><Tube key={x} plate={plate} x={x} z={z}/>)}
  {fingerSpecs.map((spec,i)=>{const hole=holes[mirror?3-i:i+1],x=sign*spec.x,knuckle:Vec3=[x*.92,knuckleY,guideTop];return <React.Fragment key={spec.id}><Guide width={.62} position={[x*.92,knuckleY,BACK_Z+.03]}/><Thread from={[hole.x,holeTop,hole.z]} to={knuckle}/><Thread from={knuckle} to={[x,spec.y-.05,spec.radius*.92+.14]}/></React.Fragment>})}</>
}
// Orbit controls that glide back to the straight-on home view whenever resetKey changes (e.g. switching Simulated/Physical).
function Orbit({resetKey}:{resetKey:string}){
 const {camera,gl}=useThree(),controls=useRef<OrbitControls|null>(null),homing=useRef(false)
 useEffect(()=>{
  const next=new OrbitControls(camera,gl.domElement);next.target.set(0,2.7,0);next.enablePan=false;next.enableZoom=false;next.enableDamping=true;next.rotateSpeed=.6;next.update();next.saveState()
  const cancel=()=>{homing.current=false};next.addEventListener('start',cancel);controls.current=next
  return()=>{next.removeEventListener('start',cancel);next.dispose()}
 },[camera,gl])
 useEffect(()=>{homing.current=true},[resetKey])
 useFrame((_,dt)=>{
  const c=controls.current;if(!c)return
  if(homing.current){
   // Swing around the target along the sphere (not straight through the hand), easing angle, distance and target together.
   const k=1-Math.exp(-dt*9),now=new THREE.Spherical().setFromVector3(camera.position.clone().sub(c.target)),home=new THREE.Spherical().setFromVector3(c.position0.clone().sub(c.target0))
   const turn=Math.atan2(Math.sin(home.theta-now.theta),Math.cos(home.theta-now.theta))
   now.theta+=turn*k;now.phi+=(home.phi-now.phi)*k;now.radius+=(home.radius-now.radius)*k;c.target.lerp(c.target0,k)
   camera.position.copy(c.target).add(new THREE.Vector3().setFromSpherical(now))
   if(camera.position.distanceTo(c.position0)<.02&&c.target.distanceTo(c.target0)<.01){c.reset();homing.current=false}
  }
  c.update()
 })
 return null
}
function Hand3D({values,dim,viewKey}:{values:Record<number,number|null>,dim?:boolean,viewKey:string}){
 const targets=useRef<Record<number,number>>({}),curls=useRef<Record<number,number>>({})
 targets.current=Object.fromEntries(Object.entries(values).map(([id,value])=>[id,value??0]))
 const palm=useMemo(()=>new RoundedBoxGeometry(3.55,3.6,BACK_Z*2,4,.45),[]),cuff=useMemo(()=>new RoundedBoxGeometry(3.1,2.3,.98,4,.4),[])
 return <div className="hand-3d" role="img" aria-label="Rotatable 3D glove model" style={{opacity:dim?.45:1}}>
  <Canvas camera={{position:[0,2.7,17.5],fov:32}} dpr={[1,2]}>
   <hemisphereLight args={['#ffffff','#c9d3cb',1.3]}/><directionalLight position={[4,6,8]} intensity={1.6}/><directionalLight position={[-5,2,-4]} intensity={.5}/>
   <Orbit resetKey={viewKey}/>
   {/* Straight on, shifted right so the palm, fingers and thumb sit centred in the frame. */}
   <group position={[.55,0,0]}>
    <mesh geometry={palm} material={gloveMaterial} position={[0,1.7,0]}/>
    <mesh geometry={cuff} material={gloveMaterial} position={[0,-.95,0]}/>
    <CordSide plate={BACK_PLATE} knuckleY={2.4} mirror={false}/>
    {/* Palm side, from the underside photos: the same hardware turned half a turn about the hand's long axis. */}
    <group rotation={[0,Math.PI,0]}><CordSide plate={PALM_PLATE} knuckleY={2.75} mirror/></group>
    {fingerSpecs.map(spec=><Finger key={spec.id} spec={spec} targets={targets} curls={curls}/>)}
    <Thumb targets={targets} curls={curls}/>
   </group>
  </Canvas>
 </div>
}
function Dashboard({status,hardware,cal,macros,play,go,onReading,action}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],macros:Macro[],play:(m:Macro)=>void,go:(p:Page)=>void,onReading:(reading:HardwareStatus)=>void,action:ManualAction}){
 const hardwareConnected=hardware?.connected===true
 const [manualOpen,setManualOpen]=useState(false)
 // One Simulated / Physical switch drives Your glove, Finger movement and Saved movements.
 const [source,setSource]=useState<'sim'|'physical'>(()=>{try{return localStorage.getItem('handoff-hand-source')==='physical'?'physical':'sim'}catch{return 'sim'}})
 useEffect(()=>{try{localStorage.setItem('handoff-hand-source',source)}catch{}},[source])
 const [control,setControl]=useState<HardwareControlInfo|null>(null)
 useEffect(()=>{api<HardwareControlInfo>('/hardware/control').then(setControl).catch(()=>{})},[])
 const [running,setRunning]=useState<string|null>(null)
 const physical=source==='physical',live=status.physical?.connected===true,locked=control?.writes_enabled===false
 const canRun=physical&&live&&!locked&&!status.stopped
 const shown:Record<number,number|null>=physical?physicalCurls(status.physical,cal):status.positions
 const torqueReady=(hardware?.servos.length??0)===6&&!!hardware?.servos.every(servo=>servo.torque_enabled===true&&servo.mode===0)
 const blocked=isDemo?'Only available on the local site next to the glove':!live?(status.physical?.error||'Servo controller not connected'):locked?'Motor control is locked on the server':status.stopped?'Software stop is latched':''
 const modeLabel=!physical?'Simulation':isDemo?'Physical · local only':!live?'Physical · offline':locked?'Physical · locked':'Physical · live'
 const runOnGlove=async(m:Macro)=>{if(running||!canRun)return;setRunning(m.id);await action(()=>api(`/macros/${m.id}/hardware/run`,{method:'POST'}),`“${m.name}” completed on glove`);setRunning(null)}
 const resetSim=()=>action(()=>api('/control/pose',{method:'POST',body:json({positions:Object.fromEntries(allServoIds.map(id=>[id,0]))})}),'Simulated hand reset to open')
 const resetTitle=status.stopped?'Software stop is latched':undefined
 const savedSub=!physical?'Choose a macro to preview it in the simulated hand.':canRun?`Click Run on glove to play a macro on the real glove. Stop works at any time.${torqueReady?'':' Torque is off: turn it on in manual controls so the glove can hold each pose.'}`:`${blocked}, so macros can’t run on the glove.`
 return <div className="overview-stack">
 <section className="overview-panel control-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={22}/></span><div className="panel-head-copy"><h1>Control your hand</h1><p>Preview gestures in simulation, or run them on the glove.</p></div></div>
  <div className="hardware-connection" role="status"><span className={`hardware-indicator ${hardwareConnected?'on':''}`} aria-hidden="true"/>{hardwareConnected?`Hardware connected · ${hardware?.servos.length??0} of 6 servos`:hardware?.port?'Controller found · servos not responding':'Not connected'}</div>
  <div className={`control-workspace${manualOpen?' manual-open':''}`}><div className="control-left"><div className="hand-stage"><div className="stage-head"><b>YOUR GLOVE</b><div className="hand-source" role="group" aria-label="Glove mode for every box on this page"><button className={physical?'':'on'} aria-pressed={!physical} onClick={()=>setSource('sim')}>Simulated</button><button className={physical?'on':''} aria-pressed={physical} onClick={()=>setSource('physical')}>Physical</button></div><span>{physical?(live?(running?'MOVING':'LIVE'):'OFFLINE'):status.playing?'MOVING':'READY'}</span></div><div className="hand-diagram hand-photo-stage"><Hand3D values={shown} dim={physical&&!live} viewKey={source}/>{physical&&!live&&<div className="hand-overlay">{isDemo?'Local site only':status.physical?.error||'Servo controller not connected'}</div>}<span className="diagram-label">3D · DRAG TO ROTATE</span>{!physical&&<button className="btn btn-line btn-sm stage-reset" disabled={status.stopped} title={resetTitle} onClick={()=>void resetSim()}><RotateCcw size={14}/> Reset to open</button>}</div><div className="stage-foot">{physical?(live?"Live servo positions, mapped with each servo's saved open and closed counts":'Connect the servo controller in Settings to see live positions'):'Simulated positions from saved gestures'}</div></div><div className="control-details"><div className="detail-heading"><div><span className="section-kicker">MOVEMENT</span><h2>Finger movement</h2></div><span className={`mode-pill ${!physical?'sim':live&&!locked?'live':'off'}`}><i/>{modeLabel}</span></div><div className="channel-list">{servos.map((name,i)=>{const value=shown[i+1];return <div className="channel-row" key={name}><span className="channel-index">{String(i+1).padStart(2,'0')}</span><span className="channel-name">{name}</span><span className="channel-meter"><i style={{width:`${value??0}%`}}/></span><b>{value==null?'—':`${value}%`}</b></div>})}</div><button className={`btn ${manualOpen?'btn-secondary':'btn-primary'} control-toggle`} aria-expanded={manualOpen} aria-controls="overview-manual" onClick={()=>setManualOpen(open=>!open)}><SlidersHorizontal size={15}/> {manualOpen?'Close manual controls':'Open manual controls'}</button></div></div>{manualOpen&&<section id="overview-manual" className="overview-manual" aria-label="Manual controls"><button className="btn btn-secondary btn-icon overview-manual-close" aria-label="Close manual controls" onClick={()=>setManualOpen(false)}><X size={16}/></button><Manual status={status} hardware={hardware} onReading={onReading}/></section>}</div>
 </section>
 <section className="overview-panel gestures-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={21}/></span><div className="panel-head-copy"><div className="section-kicker">SAVED MOVEMENTS</div><h2>Gestures, ready to test.</h2><p>{savedSub}</p></div><div className="gestures-side"><span className={`mode-pill ${!physical?'sim':live&&!locked?'live':'off'}`}><i/>{modeLabel}</span><button className="plain-link" onClick={()=>go('Macro Studio')}>Open Macro Studio <ArrowUpRight size={14} aria-hidden="true"/></button></div></div><div className="overview-gesture-scroll"><div className="overview-gesture-grid">{macros.map((m,i)=>physical?<div key={m.id} className="overview-gesture physical"><span className="gesture-index">{String(i+1).padStart(2,'0')}</span><span><b>{m.name}</b><small className="gesture-meta"><Origin source={m.source}/><span>{m.kind==='pose'?'Pose':`Sequence · ${m.steps.length} steps`} · {m.description||'Custom gesture'}</span></small></span><button className="btn btn-primary btn-sm" disabled={!canRun||!!running} title={!canRun?blocked:running?'Another move is running':undefined} aria-label={`Run ${m.name} on the glove`} onClick={()=>void runOnGlove(m)}><Hand size={13}/> {running===m.id?'Running…':'Run on glove'}</button></div>:<button key={m.id} disabled={status.stopped} onClick={()=>play(m)} className="overview-gesture" aria-label={`Preview ${m.name} in the simulated hand`}><span className="gesture-index">{String(i+1).padStart(2,'0')}</span><span><b>{m.name}</b><small className="gesture-meta"><Origin source={m.source}/><span>{m.kind==='pose'?'Pose':`Sequence · ${m.steps.length} steps`} · {m.description||'Custom gesture'}</span></small></span><Play size={15} fill="currentColor"/></button>)}</div></div><div className="panel-note"><span className="status-dot online"/><b>{macros.length} saved macros</b><span>synced with Macro Studio</span></div></section>
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
  {rows.filter(p=>p.name==='gemini').map(p=>{const result=tests[p.name];return <div key={p.name} className="card p-5">
   <div className="flex flex-wrap justify-between items-start gap-2"><div><div className="font-bold text-[15px]">Gemini API key</div><div className="text-xs text-[#63796a] mt-1">Powers the assistant in Macro Studio and Gemini Live.</div></div><Pill tone={p.key_set?'green':'gray'}>{p.key_set?`Key saved · ${p.key_hint??''}`:'No key'}</Pill></div>
   <div className="grid sm:grid-cols-2 gap-3 mt-4">
    <label className="text-xs font-bold text-[#6b7f72]">API key<input className="field mt-1 w-full" type="password" autoComplete="off" spellCheck={false} maxLength={500} placeholder={p.key_set?`Paste new key to replace ${p.key_hint??''}`:'Paste new key'} value={keys[p.name]??''} onChange={e=>setKeys(k=>({...k,[p.name]:e.target.value}))}/></label>
    <label className="text-xs font-bold text-[#6b7f72]">Model<input className="field mt-1 w-full" spellCheck={false} maxLength={200} placeholder={p.default_model} value={models[p.name]??''} onChange={e=>setModels(m=>({...m,[p.name]:e.target.value}))}/></label>
   </div>
   <div className="flex flex-wrap items-center gap-2 mt-4">
    <button className="btn btn-primary" disabled={busy===p.name} onClick={()=>save(p)}><Save size={15}/> Save key</button>
    <button className="btn btn-secondary" disabled={busy===p.name||result==='testing'||(!p.key_set&&!draftKey(p))} title={!p.key_set&&!draftKey(p)?'Paste a key first':undefined} onClick={()=>test(p)}><PlugZap size={15}/> {result==='testing'?'Testing…':dirty(p)?'Save & test':'Test'}</button>
    {p.key_set&&(clearing===p.name?<><span className="text-xs text-[#63796a] ml-auto">Remove the saved Gemini key?</span><button className="btn btn-danger-solid" disabled={busy===p.name} onClick={()=>clear(p)}><Eraser size={15}/> Yes, clear key</button><button className="btn btn-secondary" onClick={()=>setClearing('')}>Keep</button></>:<button className="btn btn-secondary btn-icon btn-danger ml-auto" aria-label="Clear Gemini key" title="Clear key" disabled={busy===p.name} onClick={()=>setClearing(p.name)}><Eraser size={16}/></button>)}
   </div>
   {result&&result!=='testing'&&<p className={`text-xs mt-3 font-bold ${result.ok?'text-[#247658]':'text-[#b4533f]'}`}>{result.ok?`OK · ${result.latency_ms} ms · ${result.message}`:result.message}</p>}
   {!p.key_set&&!draftKey(p)&&<p className="text-[11px] text-[#63796a] mt-2">Paste a key to enable Test.</p>}
   <p className="text-[11px] text-[#63796a] mt-2">Using model {p.model}{p.model===p.default_model?' (default)':''}. Leave Model empty to use the default.</p>
   <p className="text-[11px] text-[#63796a] mt-1">The key is saved in backend/.env and never shown again; only the last four characters appear.</p>
  </div>})}
 </div>
}
// Torque readiness: stage-based progress (read → settle → hold) from data the page already has. Never a timer.
function GloveMarker({nudge}:{nudge:boolean}){return <svg className={`torque-hand${nudge?' nudging':''}`} width="34" height="20" viewBox="0 0 40 24" aria-hidden="true"><g fill="#fff" stroke="#18201d" strokeWidth="1.5"><rect x="1" y="8" width="7.5" height="11.5" rx="2"/><rect x="9.5" y="1.6" width="9" height="5" rx="2.5"/><rect x="17" y="10.6" width="8.4" height="3.6" rx="1.8"/><rect x="17" y="14.1" width="7.8" height="3.6" rx="1.8"/><rect x="17" y="17.6" width="6.6" height="3.4" rx="1.7"/><rect x="16" y="4" width="22.5" height="6.2" rx="3.1"/><rect x="6.5" y="4" width="15" height="17.5" rx="5"/></g><g fill="#18201d"><rect x="25.2" y="5.3" width="2.6" height="3.6" rx=".5"/><rect x="31.6" y="5.3" width="2.6" height="3.6" rx=".5"/><rect x="10.2" y="9.4" width="2.2" height="3.2" rx=".5"/><rect x="14.4" y="9.4" width="2.2" height="3.2" rx=".5"/></g></svg>}
function TorqueReadiness({hardware,torqueOffIds,available,busy,onEnable}:{hardware:HardwareStatus,torqueOffIds:number[],available:boolean,busy:boolean,onEnable:()=>void}){
 const settled=hardware.servos.filter(servo=>servo.moving===false).length
 const phase=busy?'aligning':settled>=6?'ready':'settling'
 const pct=phase==='aligning'?83:phase==='ready'?66:34+Math.round(Math.min(settled,6)/6*32)
 const copy={settling:['Waiting for servos to settle',`${settled} of 6 stationary. Best to wait until all six have stopped.`],ready:['Ready to hold position','Sets each stored target to where the servo is now, then turns torque on. Nothing moves.'],aligning:['Aligning targets and turning torque on…','Checking each servo is stationary and healthy. No movement command is sent.']}[phase]
 const stages=[['Read servos',true],[phase==='settling'?`Settle · ${settled}/6`:'Settled',phase!=='settling'],['Hold',false]] as const
 return <div className="torque-strip mt-4"><div className="torque-copy" role="status" aria-live="polite"><div className="section-kicker">TORQUE OFF · SERVO{torqueOffIds.length===1?'':'S'} {torqueOffIds.join(', ')}</div><b>{copy[0]}</b><span>{copy[1]}</span></div><div className="torque-progress"><div className="torque-track" role="progressbar" aria-label="Torque readiness" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={copy[0]}><i className="torque-rail"/><i className="torque-fill" style={{width:`${pct}%`}}/><i className="torque-notch" style={{left:'33.333%'}}/><i className="torque-notch" style={{left:'66.666%'}}/><span className="torque-hand-wrap" style={{left:`${pct}%`}}><GloveMarker nudge={phase==='aligning'}/></span></div><div className="torque-stages">{stages.map(([label,done],i)=><span key={i} className={done?'done':i===(phase==='settling'?1:2)?'current':''}>{label}</span>)}</div></div><button className={`btn ${phase==='settling'?'btn-secondary':'btn-primary'}`} onClick={onEnable} disabled={!available||busy}><Anchor size={15}/> {busy?'Aligning…':'Align and hold torque'}</button></div>
}
function HardwareManual({status,hardware,onReading}:{status:Status,hardware:HardwareStatus|null,onReading:(reading:HardwareStatus)=>void}){
 const [control,setControl]=useState<HardwareControlInfo|null>(null)
 const [targets,setTargets]=useState<Record<number,number>>({})
 const [speeds,setSpeeds]=useState<Record<number,number>>(()=>{try{return JSON.parse(localStorage.getItem('handoff-manual-speeds')||'{}')}catch{return {}}})
 const [localError,setLocalError]=useState('')
 const [localNotice,setLocalNotice]=useState('')
 const [torqueBusy,setTorqueBusy]=useState(false)
 const [allServoTarget,setAllServoTarget]=useState(0)
 const [movingIds,setMovingIds]=useState<number[]>([])
 const [pending,setPending]=useState<Record<number,number>>({})
 const sending=useRef(false)
 useEffect(()=>{api<HardwareControlInfo>('/hardware/control').then(setControl).catch(e=>setLocalError(String(e)))},[])
 useEffect(()=>{localStorage.setItem('handoff-manual-speeds',JSON.stringify(speeds))},[speeds])
 // Untouched sliders follow the real servo position (live feed, else the last status scan), so a move made elsewhere
 // (Open hand, Test on glove, Stop) never leaves a stale starting count that would send the finger the opposite way.
 useEffect(()=>{const live=status.physical?.connected?status.physical.positions:null,scanned=Object.fromEntries((hardware?.servos??[]).map(servo=>[servo.id,servo.position]));setTargets(current=>{let changed=false;const next={...current};for(const id of allServoIds){if(pending[id]!==undefined)continue;const position=live?.[id]??scanned[id];if(position!=null&&next[id]!==position){next[id]=position;changed=true}}return changed?next:current})},[hardware,status.physical,pending])
 useEffect(()=>{if(status.stopped){setPending({});setMovingIds([]);setAllServoTarget(0);setTargets(Object.fromEntries((hardware?.servos??[]).filter(servo=>servo.position!=null).map(servo=>[servo.id,servo.position!])))}},[status.stopped])
 const confirm=async()=>{
  if(sending.current)return
  const ids=Object.keys(pending).map(Number).sort((a,b)=>a-b)
  if(!ids.length)return
  if(!control?.writes_enabled||status.stopped||!hardware?.connected){setLocalError('Motor control is unavailable. No target was sent.');return}
  const moves=ids.map(id=>({servo_id:id,target:pending[id],speed:speeds[id]??control.position_speed_default}))
  for(const move of moves){
   if(!Number.isInteger(move.target)||move.target<0||move.target>maxServoCount){
    setLocalError(`Servo ${move.servo_id} target must be a whole number from 0 to ${maxServoCount}. No target was sent.`)
    return
   }
   const liveServo=hardware.servos.find(item=>item.id===move.servo_id)
   if(liveServo?.torque_enabled!==true||liveServo.mode!==0){
    setLocalError(`Servo ${move.servo_id} torque is off or it is not in position mode. No target was sent.`)
    return
   }
  }
  sending.current=true
  setMovingIds(ids)
  setLocalError('')
  setLocalNotice('')
  try{
   const result=await api<{final_positions:Record<number,number>}>('/hardware/live',{method:'POST',body:json({moves})})
   setPending({})
   setAllServoTarget(0)
   setLocalNotice(`Movement settled: ${moves.map(move=>`Servo ${move.servo_id} ${move.servo_id===5?'(thumb flexion) ':''}→ ${result.final_positions[move.servo_id]}`).join(' · ')}.`)
   const reading=await api<HardwareStatus>('/hardware/status')
   onReading(reading)
  }catch(e){
   setLocalError(e instanceof Error?e.message:String(e))
   try{
    const reading=await api<HardwareStatus>('/hardware/status')
    onReading(reading)
    setTargets(Object.fromEntries(reading.servos.filter(servo=>servo.position!=null).map(servo=>[servo.id,servo.position!])))
    setPending({})
    setAllServoTarget(0)
   }catch{}
  }
  finally{
   sending.current=false
   setMovingIds([])
  }
 }
 const enableTorque=async()=>{
  if(torqueBusy||!torqueOffIds.length)return
  if(!control?.writes_enabled||status.stopped||!hardware?.connected){setLocalError('Torque control is unavailable. No torque command was sent.');return}
  setTorqueBusy(true);setLocalError('')
  try{
   await api('/hardware/torque/enable',{method:'POST',body:json({servo_ids:torqueOffIds})})
   onReading(await api<HardwareStatus>('/hardware/status'))
  }catch(e){setLocalError(e instanceof Error?e.message:String(e))}
  finally{setTorqueBusy(false)}
 }
 const stage=(id:number,value:number)=>{
  if(sending.current)return
  setTargets(current=>({...current,[id]:value}))
  setPending(current=>({...current,[id]:value}))
  setAllServoTarget(0)
  setLocalError('')
  setLocalNotice('')
 }
 const stageAllServos=(value:number)=>{
  if(sending.current)return
  const updates=Object.fromEntries(allServoIds.map(id=>[id,value]))
  setTargets(current=>({...current,...updates}))
  setPending(current=>({...current,...updates}))
  setAllServoTarget(value)
  setLocalError('')
  setLocalNotice('')
 }
 const discard=()=>{setPending({});setAllServoTarget(0);setTargets(Object.fromEntries((hardware?.servos??[]).filter(servo=>servo.position!=null).map(servo=>[servo.id,servo.position!])));setLocalError('');setLocalNotice('')}
 const pendingCount=Object.keys(pending).length
 const available=!!control?.writes_enabled&&!status.stopped&&!!hardware?.connected
 const torqueOffIds=(hardware?.servos??[]).filter(servo=>servo.torque_enabled===false).map(servo=>servo.id)
 const previewValues:Record<number,number>=Object.fromEntries(allServoIds.map(id=>{
  const count=targets[id]??hardware?.servos.find(servo=>servo.id===id)?.position??0
  return [id,Math.round(Math.max(0,Math.min(maxServoCount,count))/maxServoCount*100)]
 }))
 // These controls intentionally send raw count targets, matching multi_servo.py.
 const allFingersAvailable=available&&!movingIds.length
 const servoControl=(id:number)=>{
  const index=id-1,name=servos[index],live=hardware?.servos.find(item=>item.id===id)
  const minimum=0,maximum=maxServoCount,target=targets[id]??live?.position??minimum,speed=speeds[id]??control?.position_speed_default??2400
  const editable=available&&!movingIds.length
  return <div key={id} className="rounded-xl border border-[#e5ebe5] p-3 bg-[#fcfdfc]">
   <div className="flex justify-between gap-2 items-center"><div className="min-w-0"><div className="text-[9px] font-extrabold tracking-[.1em] text-[#63796a]">SERVO 0{id} · {servoMounts.find(m=>m.id===id)?.location.toUpperCase()}</div><h3 className="font-bold text-sm mt-1 mb-0 leading-tight">{name}</h3></div><span className="text-[10px] text-[#607669] text-right shrink-0 flex flex-col items-end gap-1"><span>Now <b>{live?.position??'—'}</b> · {movingIds.includes(id)?'Sending':pending[id]!==undefined?'Pending':!live?'No reading':live.moving?'Moving':'Stopped'}</span><span className={`torque-chip${live?.torque_enabled===true?' on':''}`}>{live?.torque_enabled===true?'Holding':live?.torque_enabled===false?'Torque off':'Torque unknown'}</span></span></div>
   <div className="grid grid-cols-[1fr_76px] gap-2 items-center mt-3"><input aria-label={`${name} target count slider`} type="range" min={minimum} max={maximum} value={Math.max(minimum,Math.min(maximum,target))} onChange={e=>stage(id,Number(e.target.value))} className="w-full" disabled={!editable}/><input aria-label={`${name} target count`} className="field !py-1.5 !px-2 text-xs" type="number" min={minimum} max={maximum} step="1" value={target} disabled={!editable} onChange={e=>stage(id,Number(e.target.value))}/></div>
   <div className="flex justify-between text-[9px] text-[#63796a] mt-0.5"><span>0</span><span>Target count</span><span>4000</span></div>
   <div className="grid grid-cols-[max-content_1fr] gap-2 items-center mt-2"><span className="text-[10px] font-bold text-[#6b7f72] whitespace-nowrap">Speed {speed}</span><input aria-label={`${name} speed`} type="range" min={control?.position_speed_min??50} max={control?.position_speed_max??2400} step="25" value={speed} onChange={e=>setSpeeds(current=>({...current,[id]:Number(e.target.value)}))} className="w-full" disabled={!editable}/></div>
  </div>
 }
 return <section className="card p-5 md:p-7 mb-5">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><Eyebrow>Physical glove</Eyebrow><h2 className="text-xl font-bold mt-1">Live servo positions</h2><p className="text-sm text-[#63796a] mt-2 max-w-2xl">Stage raw targets from 0–4000, then click Confirm movement. Multiple targets are sent together in one synchronized packet.</p></div><Pill tone={control?.writes_enabled?'green':'amber'}>{control?.writes_enabled?'Control ready':'Motor control locked'}</Pill></div>
  {!!hardware?.connected&&torqueOffIds.length>0&&<TorqueReadiness hardware={hardware} torqueOffIds={torqueOffIds} available={available} busy={torqueBusy} onEnable={enableTorque}/>}
  {localError&&<div role="alert" className="message message-error mt-4">{localError}<button onClick={()=>setLocalError('')} aria-label="Dismiss warning"><X size={16}/></button></div>}
  {localNotice&&<div role="status" className="message message-success mt-4">{localNotice}</div>}
  <div className="mt-5 rounded-xl border border-[#d9e6dc] bg-[#f5faf6] p-3 md:px-4"><div className="flex items-center justify-between gap-3"><h3 className="font-bold text-sm">All servos</h3><b className="text-sm text-[#1e4937] whitespace-nowrap">{allServoTarget} counts</b></div><input aria-label="All servos target count slider" type="range" min="0" max={maxServoCount} step="1" value={allServoTarget} onChange={e=>stageAllServos(Number(e.target.value))} disabled={!allFingersAvailable} className="w-full mt-2"/><div className="flex justify-between text-[10px] text-[#63796a] mt-0.5"><span>0</span><span>2000</span><span>4000</span></div></div>
  <div className="manual-grid grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(290px,0.9fr)_minmax(0,1fr)] gap-3 mt-4 items-center">
   <div className="manual-col flex flex-col gap-3 xl:col-start-1 xl:row-start-1">{[1,2,3].map(servoControl)}</div>
   <div className="manual-preview rounded-xl border border-[#e5ebe5] bg-[#fcfdfc] p-2 xl:col-start-2 xl:row-start-1"><div className="hand-diagram hand-photo-stage rounded-lg" style={{height:420}}><Hand3D values={previewValues} viewKey="manual-target-preview"/><span className="diagram-label">3D · DRAG TO ROTATE</span></div></div>
   <div className="manual-col flex flex-col gap-3 xl:col-start-3 xl:row-start-1">{[4,5,6].map(servoControl)}</div>
  </div>
  <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-[#d9e6dc] bg-[#f5faf6] p-3"><div className="flex-1 text-sm text-[#4f6658]">{!!hardware?.connected&&torqueOffIds.length>0?'Torque is off. Turn it on above before confirming movement.':pendingCount?`${pendingCount} servo ${pendingCount===1?'target':'targets'} pending. Confirm sends them together.`:'No pending movement.'}</div><button className="btn btn-secondary" onClick={discard} disabled={!pendingCount||!!movingIds.length}><Undo2 size={15}/> Discard changes</button><button className="btn btn-primary" onClick={confirm} disabled={!pendingCount||!available||!!movingIds.length}><Check size={15}/> {movingIds.length?'Sending / waiting for settle…':'Confirm movement'}</button></div>
  <p className="text-xs text-[#63796a] mt-5">Only Confirm movement sends physical targets. Manual targets use raw servo counts and may exceed calibrated travel; inspect the glove path before moving. Device health checks and Stop remain active. Keep the physical power cutoff within reach.</p>
 </section>
}
function Manual({status,hardware,onReading}:{status:Status,hardware:HardwareStatus|null,onReading:(reading:HardwareStatus)=>void}){
 return isDemo?<LocalOnly title="Manual control works on the local site only">This online demo has no glove attached. Open handoff on the computer connected to the glove to move servos. You can still preview gestures on Overview.</LocalOnly>:<HardwareManual status={status} hardware={hardware} onReading={onReading}/>
}
function MacroStudio({macros,status,cal,refresh,action,play,manual}:{macros:Macro[],status:Status,cal:Calibration[],refresh:()=>Promise<void>,action:<T>(work:()=>Promise<T>,success?:string)=>Promise<T|undefined>,play:(m:Macro)=>void,manual:Record<number,number>}){const [selected,setSelected]=useState<string|null>(null),[draft,setDraft]=useState<Omit<Macro,'id'>>({name:'',description:'',kind:'pose',steps:[{positions:blank(),duration_ms:700}]}),[editing,setEditing]=useState(false),[previewMacroId,setPreviewMacroId]=useState<string>(''),[followSim,setFollowSim]=useState(false),[previewSource,setPreviewSource]=useState<'simulated'|'physical'>('simulated'),[editorStepIndex,setEditorStepIndex]=useState(0)
 const edit=(m:Macro)=>{setSelected(m.id);setDraft({name:m.name,description:m.description,kind:m.kind,steps:structuredClone(m.steps)});setEditorStepIndex(0);setEditing(true)}
 const fresh=()=>{setSelected(null);setDraft({name:'New gesture',description:'',kind:'pose',steps:[{positions:{...manual},duration_ms:700}]});setEditorStepIndex(0);setEditing(true)}
 const previewMacro=macros.find(m=>m.id===previewMacroId)??macros[0];const previewStep=previewMacro?.steps[0];const editorStep=draft.steps[Math.min(editorStepIndex,draft.steps.length-1)];const physicalPreview=previewSource==='physical',physicalConnected=status.physical.connected,physicalValues=physicalCurls(status.physical,cal);const gloveValues=physicalPreview?physicalValues:followSim?Object.fromEntries(allServoIds.map(id=>[id,Math.max(0,Math.min(100,status.positions[id]??0))])):Object.fromEntries(allServoIds.map(id=>[id,Math.max(0,Math.min(100,((previewStep?.positions[id]??0)/maxServoCount)*100))]));const editorGloveValues=Object.fromEntries(allServoIds.map(id=>[id,Math.max(0,Math.min(100,((editorStep?.positions[id]??0)/maxServoCount)*100))]));
 const save=async()=>{if(!draft.name.trim())return;const path=selected?`/macros/${selected}`:'/macros';const result=await action(()=>api<Macro>(path,{method:selected?'PUT':'POST',body:json(draft)}),selected?'Macro updated':'Macro created');if(result){await refresh();setEditing(false);setSelected(null)}}
 const remove=async(m:Macro)=>{if(!window.confirm(`Delete “${m.name}”?`))return;await action(()=>api(`/macros/${m.id}`,{method:'DELETE'}),'Macro deleted');await refresh()}
 const [hardwareRunning,setHardwareRunning]=useState<string|null>(null)
 const testOnGlove=async(m:Macro)=>{if(hardwareRunning)return;setHardwareRunning(m.id);await action(()=>api(`/macros/${m.id}/hardware/run`,{method:'POST'}),`“${m.name}” completed on glove`);setHardwareRunning(null)}
 return <><div className="flex flex-wrap gap-2 mb-5"><button className="btn btn-primary flex items-center gap-2" onClick={fresh}><Plus size={17}/> New macro</button></div><section className="card p-5 mb-5"><div className="flex flex-wrap justify-between items-center gap-3 mb-3"><div><Eyebrow>Glove preview</Eyebrow><h2 className="text-xl font-bold mt-1">{physicalPreview?'Live glove':previewMacro?.name??'No saved macros'}</h2></div><div className="flex flex-wrap items-center gap-2"><div className="vision-view-switch" role="group" aria-label="Glove preview source"><button type="button" aria-pressed={!physicalPreview} className={!physicalPreview?'selected':''} onClick={()=>setPreviewSource('simulated')}>Simulated</button><button type="button" aria-pressed={physicalPreview} className={physicalPreview?'selected':''} onClick={()=>setPreviewSource('physical')}>Physical</button></div>{!physicalPreview&&previewMacro&&<><label className="sr-only" htmlFor="macro-preview-select">Macro to preview</label><select id="macro-preview-select" className="field !w-auto" value={previewMacro.id} onChange={e=>{setPreviewMacroId(e.target.value);setFollowSim(false)}}>{macros.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></>}</div></div><div className="grid md:grid-cols-[minmax(250px,.8fr)_minmax(0,1.2fr)] gap-4 items-center"><div className="hand-diagram hand-photo-stage min-h-[280px]"><Hand3D values={gloveValues} dim={physicalPreview&&!physicalConnected} viewKey={`macro-${previewSource}-${previewMacro?.id??'live'}`}/>{physicalPreview&&!physicalConnected&&<div className="hand-overlay">{status.physical.error||'Servo controller not connected'}</div>}{physicalPreview&&physicalConnected&&cal.some(item=>!item.verified)&&<span className="hand-badge">Calibration unverified</span>}<span className="diagram-label">3D · DRAG TO ROTATE</span></div><div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3">{servos.map((name,i)=>{const count=physicalPreview?(status.physical.positions[i+1]??null):followSim?Math.round((status.positions[i+1]??0)*maxServoCount/100):(previewStep?.positions[i+1]??0);return <div key={name} className="text-xs text-[#63796a]"><div className="flex justify-between gap-2"><span>{name}</span><b className="text-[#1e4937]">{count??'—'}</b></div><span className="channel-meter block mt-1"><i style={{width:`${gloveValues[i+1]??0}%`}}/></span></div>})}</div></div><p className="text-xs text-[#718278] mt-2">{physicalPreview?"Physical view shows live servo positions mapped using saved open and closed counts.":"Simulation preview · counts map to the model’s 0–100% movement range."}</p></section><div className="macro-list"><div className="macro-list-head"><span>GESTURE</span><span>ACTIONS</span></div>{macros.map((m,i)=><div key={m.id} className="macro-row"><span className="macro-index">{String(i+1).padStart(2,'0')}</span><div className="macro-name"><b>{m.name}</b><span className="macro-meta"><Origin source={m.source}/><span className="macro-meta-text">{m.kind==='pose'?'Pose':`Sequence · ${m.steps.length} steps`} · {m.description||'Custom movement'}</span></span></div><div className="macro-actions"><button className="macro-test" disabled={isDemo||!!hardwareRunning} title={isDemo?'Only available on the local site next to the glove':undefined} onClick={()=>testOnGlove(m)}><Hand size={14}/>{hardwareRunning===m.id?'Running…':'Test on glove'}</button><button disabled={status.stopped} className="macro-play" onClick={()=>{setPreviewMacroId(m.id);setPreviewSource('simulated');setFollowSim(true);play(m)}}><Play size={11} fill="currentColor"/>Play</button><button className="macro-edit" aria-label={`Edit ${m.name}`} title="Edit" onClick={()=>edit(m)}><Pencil size={15}/></button><button className="macro-delete" aria-label={`Delete ${m.name}`} title="Delete" onClick={()=>remove(m)}><Trash2 size={15}/></button></div></div>)}</div>
 {editing&&<div className="fixed inset-0 bg-[#0d2520]/60 z-40 flex justify-end" onClick={()=>setEditing(false)}><div className="h-full w-full max-w-[1100px] bg-[#f8faf7] overflow-y-auto mini-scroll p-6 md:p-8" onClick={e=>e.stopPropagation()}><div className="flex justify-between items-start"><div><Eyebrow>Macro editor</Eyebrow><h2 className="text-2xl font-bold mt-1">{selected?'Edit macro':'Create macro'}</h2></div><button aria-label="Close editor" onClick={()=>setEditing(false)}><X/></button></div><div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(300px,.8fr)] gap-6 mt-7 items-start"><div><label className="block text-sm font-bold mb-2">Name</label><input className="field" value={draft.name} maxLength={60} onChange={e=>setDraft({...draft,name:e.target.value})}/><label className="block text-sm font-bold mt-4 mb-2">Description</label><textarea className="field" rows={2} value={draft.description} maxLength={240} onChange={e=>setDraft({...draft,description:e.target.value})}/><label className="block text-sm font-bold mt-4 mb-2">Type</label><select className="field" value={draft.kind} onChange={e=>{const kind=e.target.value as 'pose'|'sequence';setDraft({...draft,kind,steps:kind==='pose'?draft.steps.slice(0,1):draft.steps})}}><option value="pose">Pose · one position</option><option value="sequence">Sequence · timed steps</option></select><div className="mt-7 space-y-4">{draft.steps.map((step,stepIndex)=><div key={stepIndex} className="card p-5"><div className="flex justify-between items-center mb-4"><h3 className="font-bold">Step {stepIndex+1}</h3>{draft.kind==='sequence'&&draft.steps.length>1&&<button className="text-red-600 text-xs font-bold flex items-center gap-1" onClick={()=>setDraft({...draft,steps:draft.steps.filter((_,i)=>i!==stepIndex)})}><Trash2 size={14}/> Remove</button>}</div><div className="grid grid-cols-2 gap-3">{servos.map((name,i)=><label key={i} className="text-xs font-semibold text-[#62776c]">{name} · target count<div className="flex gap-2 items-center"><input aria-label={`${name} step ${stepIndex+1} target count`} className="w-full" type="range" min="0" max="4000" step="1" value={step.positions[i+1]} onChange={e=>{const steps=structuredClone(draft.steps);steps[stepIndex].positions[i+1]=Number(e.target.value);setDraft({...draft,steps})}}/><input aria-label={`${name} step ${stepIndex+1} target count value`} className="field !py-1 !px-1.5 w-[72px] text-xs text-right" type="number" min="0" max="4000" step="1" value={step.positions[i+1]} onChange={e=>{const steps=structuredClone(draft.steps);steps[stepIndex].positions[i+1]=Math.max(0,Math.min(4000,Number(e.target.value)||0));setDraft({...draft,steps})}}/></div><div className="flex justify-between text-[9px] text-[#63796a]"><span>0</span><span>Target count</span><span>4000</span></div></label>)}</div><label className="block text-xs font-bold text-[#62776c] mt-4">Move duration (ms)</label><input className="field mt-1" type="number" min="100" max="10000" value={step.duration_ms} onChange={e=>{const steps=structuredClone(draft.steps);steps[stepIndex].duration_ms=Number(e.target.value);setDraft({...draft,steps})}}/></div>)}</div>{draft.kind==='sequence'&&<button className="btn btn-secondary mt-4 flex items-center gap-2" disabled={draft.steps.length>=30} onClick={()=>setDraft({...draft,steps:[...draft.steps,{positions:{...draft.steps.at(-1)!.positions},duration_ms:700}]})}><Plus size={16}/> Add step</button>}<div className="sticky top-4 card p-4"><Eyebrow>Live glove preview</Eyebrow><p className="text-xs text-[#63796a] mt-1">Showing target counts for the selected step.</p><div className="hand-diagram hand-photo-stage mt-3 min-h-[300px]"><Hand3D values={editorGloveValues} viewKey={`macro-editor-${editorStepIndex}`}/><span className="diagram-label">3D · DRAG TO ROTATE</span></div><label className="block text-xs font-bold mt-3 mb-1" htmlFor="editor-preview-step">Preview step</label><select id="editor-preview-step" className="field" value={Math.min(editorStepIndex,draft.steps.length-1)} onChange={e=>setEditorStepIndex(Number(e.target.value))}>{draft.steps.map((_,i)=><option key={i} value={i}>Step {i+1}</option>)}</select><div className="grid grid-cols-2 gap-x-3 gap-y-2 mt-3">{servos.map((name,i)=><div key={name} className="text-[10px] text-[#63796a]"><span>{name}</span><b className="block text-[#1e4937]">{editorStep?.positions[i+1]??0} counts</b></div>)}</div></div></div><div className="sticky bottom-0 bg-[#f8faf7] py-5 mt-6 border-t flex gap-2"><button className="btn btn-primary flex-1 flex justify-center gap-2" onClick={save}><Save size={16}/> Save macro</button><button className="btn btn-secondary" onClick={()=>setEditing(false)}>Cancel</button></div></div></div></div>}
 </>}
const cancelPendingProposals=()=>window.dispatchEvent(new Event('assistant-stop'))
const macrosChanged=()=>window.dispatchEvent(new Event('macros-changed'))
async function confirmProposal(p:Proposal):Promise<'sent'|'saved'|'expired'>{const response=await fetch(`/api/assistant/proposals/${encodeURIComponent(p.id)}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'}});if(response.ok){const body=await response.json().catch(()=>({}));if(body.mode==='saved'){macrosChanged();return 'saved'}return 'sent'}if(response.status===410)return 'expired';const body=await response.json().catch(()=>({})),detail=typeof body.detail==='string'?body.detail:`Request failed (${response.status})`;throw Error(response.status===409?`Not confirmed: ${detail}`:detail)}
async function cancelProposal(p:Proposal){await api(`/assistant/proposals/${encodeURIComponent(p.id)}/cancel`,{method:'POST'})}
function useProposalActions(setTurns:React.Dispatch<React.SetStateAction<Turn[]>>,setNotice:(m:string)=>void,setError:(m:string)=>void){const [busyId,setBusyId]=useState(''),busy=useRef(false);const mark=(id:string,state:Turn['state'])=>setTurns(t=>t.map(x=>x.proposal?.id===id?{...x,state}:x));useEffect(()=>{const stop=()=>setTurns(t=>t.map(x=>x.state==='pending'?{...x,state:'cancelled'}:x));window.addEventListener('assistant-stop',stop);return()=>window.removeEventListener('assistant-stop',stop)},[setTurns]);const run=async(p:Proposal,confirm:boolean)=>{if(busy.current)return;busy.current=true;setBusyId(p.id);setError('');try{const state=confirm?await confirmProposal(p):(await cancelProposal(p),'cancelled' as const);mark(p.id,state);if(state==='sent')setNotice('Sent to simulation');if(state==='saved')setNotice('Saved to Macro Studio')}catch(e){setError(e instanceof Error?e.message:String(e))}finally{busy.current=false;setBusyId('')}};return {busyId,confirm:(p:Proposal)=>run(p,true),cancel:(p:Proposal)=>run(p,false)}}
function ProposalCard({turn,macros,now,busyId,confirm,cancel}:{turn:Turn,macros:Macro[],now:number,busyId:string,confirm:(p:Proposal)=>void,cancel:(p:Proposal)=>void}){const p=turn.proposal;if(!p)return null;const state=turn.state==='pending'&&now>=turn.at+p.expires_in_s*1000?'expired':turn.state,macro=p.command.type==='macro'?macros.find(m=>m.id===(p.command as {macro_id:string}).macro_id):undefined,c=p.command,saving=c.type==='save_macro',countTargets=c.type!=='pose',positions=c.type==='pose'?c.positions:c.type==='save_macro'?c.steps[0]?.positions:macro?.steps[0]?.positions,saveSteps:Step[]|undefined=c.type==='pose'?[{positions:poseCounts(c.positions),duration_ms:700}]:macro?.steps;return <div className="card p-4 mt-3" aria-label={saving?'Save proposal':'Movement proposal'}><div className="flex flex-wrap justify-between items-start gap-2"><div><Eyebrow>{saving?'Proposed save':'Proposed movement'} · {p.provider}</Eyebrow><div className="font-bold text-sm mt-1">{c.type==='pose'?c.label:c.name}</div>{c.type==='save_macro'&&<div className="text-xs text-[#63796a] mt-1">{c.append_to?`Add ${c.steps.length} step${c.steps.length===1?'':'s'} to ${c.append_to.name}`:`New ${c.steps.length===1?'pose':`sequence · ${c.steps.length} steps`}`} · saves only, nothing moves</div>}</div>{state==='pending'?<Pill tone="amber">Awaiting approval</Pill>:<span className="text-xs font-bold text-[#63796a]">{state==='sent'?'Sent to simulation':state==='saved'?'Saved to Macro Studio':state==='cancelled'?'Cancelled':'Expired'}</span>}</div>{positions&&<div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2 mt-3">{servos.map((name,j)=><div key={name} className="text-[11px] text-[#63796a]"><div className="flex justify-between"><span>{name}</span><b className="text-[#1e4937]">{positions[j+1]??0}{countTargets?' counts':'%'}</b></div><span className="channel-meter block mt-1"><i style={{width:`${countTargets?(positions[j+1]??0)/maxServoCount*100:(positions[j+1]??0)}%`}}/></span></div>)}</div>}{state==='pending'&&<div className="flex gap-2 mt-4"><button className="btn btn-primary" disabled={!!busyId} onClick={()=>confirm(p)}><Check size={15}/> Confirm</button><button className="btn btn-secondary" disabled={!!busyId} onClick={()=>cancel(p)}><X size={15}/> Cancel</button></div>}{!saving&&saveSteps&&<SaveAsMacro steps={saveSteps} defaultName={c.type==='pose'?c.label:`${c.name} copy`} macros={macros}/>}</div>}
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
   else await api('/macros',{method:'POST',body:json({name:name.trim(),description:'Saved from AI assistant',kind:timed.length===1?'pose':'sequence',source:'assistant',steps:timed})})
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
  <div className="flex gap-2"><button className="btn btn-primary" disabled={state==='saving'} onClick={save}><Save size={15}/> {state==='saving'?'Saving…':'Save'}</button><button className="btn btn-secondary" disabled={state==='saving'} onClick={()=>{setOpen(false);setError('')}}>Close</button></div>
  <p className="text-[11px] text-[#63796a]">Saving adds the movement to Macro Studio. Nothing moves.</p>
 </div>
}
function Assistant({macros,action,setNotice,setError}:{macros:Macro[],action:ManualAction,setNotice:(m:string)=>void,setError:(m:string)=>void}){
 const [providers,setProviders]=useState<Provider[]>([]),[turns,setTurns]=useState<Turn[]>([]),[input,setInput]=useState(''),[waiting,setWaiting]=useState(false)
 const provider='gemini'
 const [listening,setListening]=useState(false),[transcribing,setTranscribing]=useState(false),[speak,setSpeak]=useState(false),[now,setNow]=useState(Date.now())
 const proposalActions=useProposalActions(setTurns,setNotice,setError)
 const recognizer=useRef<Recognizer|null>(null),recorder=useRef<MediaRecorder|null>(null),sendRef=useRef<(text?:string)=>void>(()=>{}),speakRef=useRef(false),bottom=useRef<HTMLDivElement>(null)
 const supported=!!speechCtor(),canSpeak='speechSynthesis' in window,canRecord=typeof MediaRecorder!=='undefined'&&!!navigator.mediaDevices
 const geminiVoice=canRecord&&providers.some(p=>p.name==='gemini'&&p.available),voiceReady=geminiVoice||supported
 speakRef.current=speak
 useEffect(()=>{api<Provider[]>('/assistant/providers').then(setProviders).catch(e=>setError(e instanceof Error?e.message:String(e)))},[])
 useEffect(()=>{if(!turns.some(t=>t.state==='pending'))return;const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[turns])
 useEffect(()=>{bottom.current?.scrollIntoView?.({block:'nearest'})},[turns.length])
 useEffect(()=>()=>{recognizer.current?.abort();if(recorder.current?.state==='recording'){recorder.current.onstop=null;recorder.current.stop()};if('speechSynthesis' in window)window.speechSynthesis.cancel()},[])
 const current=providers.find(p=>p.name==='gemini')
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
   <div className="flex flex-wrap items-center justify-between gap-3"><div><Eyebrow>Gemini assistant</Eyebrow><h2 className="text-lg font-bold mt-1">Ask Gemini</h2></div><div className="text-right"><Pill tone={current?.available?'green':'gray'}>{current?.available?'Gemini ready':'Gemini key needed'}</Pill>{current?.model&&<div className="text-xs text-[#63796a] mt-2">{current.model}</div>}</div></div>
   {!current?.available&&<p className="text-xs text-[#63796a] mt-3">Add a Gemini API key in Settings to enable the assistant.</p>}
   <p className="text-xs text-[#63796a] mt-3">Gemini only proposes simulated moves. It cannot run Test on glove. Say or type “stop” to stop immediately.</p>
  </div>
  <div className="card p-5">
   <div className="min-h-[160px] max-h-[460px] overflow-y-auto mini-scroll" aria-live="polite">
    {!turns.length&&<p className="text-xs text-[#63796a]">Try asking Gemini “make a fist” or “play Open hand”. Nothing moves until you click Confirm.</p>}
    {turns.map((t,i)=><div key={i} className="border border-[#e8ece8] rounded-xl px-4 py-3 mb-2 text-[13px] leading-5"><div className="text-[10px] uppercase tracking-[.12em] font-extrabold text-[#63796a] mb-1">{t.role==='user'?'You':'Gemini'}</div><div className="whitespace-pre-wrap">{t.text}</div>{t.proposal&&<ProposalCard turn={t} macros={macros} now={now} {...proposalActions}/>}</div>)}
    {waiting&&<p className="text-xs text-[#63796a]">Assistant is thinking…</p>}
    <div ref={bottom}/>
   </div>
   <div className="flex gap-2 mt-4"><input className="field flex-1" aria-label="Message to Gemini" placeholder={listening?'Listening…':'Ask Gemini for a pose or macro'} value={input} maxLength={500} disabled={waiting} onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
    {voiceReady&&<button className={`btn ${listening?'btn-primary':'btn-secondary'} flex items-center gap-2`} disabled={waiting||transcribing} onClick={toggleMic} aria-pressed={listening} aria-label={listening?'Stop listening':'Start voice input'}><Mic size={16}/>{listening?<span className="text-xs">{geminiVoice?'Recording':'Listening'}… click to stop</span>:transcribing?<span className="text-xs">Transcribing…</span>:null}</button>}
    <button className="btn btn-primary" disabled={waiting||!input.trim()} onClick={()=>send()}><SendHorizontal size={15}/> Send</button></div>
   <div className="flex flex-wrap items-center gap-4 mt-3 text-xs text-[#63796a]">
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
function LiveButton({macros,setNotice}:{macros:Macro[],setNotice:(m:string)=>void}){const [open,setOpen]=useState(false),button=useRef<HTMLButtonElement>(null);const close=()=>{setOpen(false);button.current?.focus()};useEffect(()=>{if(!open)return;const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);button.current?.focus()}};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape)},[open]);return <><button ref={button} className="btn btn-primary live-button" onClick={()=>setOpen(v=>!v)} aria-expanded={open} aria-controls="gemini-live-panel" aria-label={open?'Close Gemini Live':'Open Gemini Live'}><Sparkles size={15}/><span className="live-text">Live</span></button>{open&&<LivePanel macros={macros} setNotice={setNotice} close={close}/>}</>}
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
 return <section id="gemini-live-panel" role="dialog" aria-labelledby="gemini-live-title" className="card fixed z-30 top-[72px] right-0 sm:right-5 w-full sm:w-[380px] max-h-[calc(100dvh-88px)] flex flex-col p-5"><div className="flex items-start justify-between gap-2"><div><Eyebrow>Voice + camera</Eyebrow><h2 id="gemini-live-title" className="font-bold text-xl mt-1">Gemini Live</h2></div><button ref={closeButton} aria-label="Close Live panel" onClick={close}><X size={20}/></button></div><div className="flex justify-between items-center mt-3"><span role="status"><Pill tone={state==='Live'?'green':state==='Connecting'||state==='Reconnecting'?'amber':'gray'}>{state==='Reconnecting'?'Reconnecting…':state}</Pill></span><span className="text-xs text-[#63796a]" aria-label="Session timer">{liveClock(elapsed)} / {videoSession?'2:00':'15:00'}</span></div><p className="text-xs text-[#63796a] mt-3">Moves run in simulation and wait for your Confirm click. Pause holds the conversation; saying “stop” or the page Stop button stops the hand.</p><div className="flex flex-wrap gap-2 mt-3"><button className="btn btn-secondary flex gap-1 items-center" disabled={!active} aria-pressed={muted} onClick={()=>controls.current?.mute()}>{muted?<MicOff size={15}/>:<Mic size={15}/>} {muted?'Unmute':'Mute'}</button><button className="btn btn-secondary flex gap-1 items-center" disabled={!active||cameraBusy} aria-pressed={camera} onClick={()=>void controls.current?.camera()}><Camera size={15}/>{camera?'Camera off':'Camera on'}</button><button className="btn btn-secondary" disabled={!active} onClick={()=>controls.current?.end()}><PhoneOff size={15}/> End</button><button className="btn btn-secondary flex gap-1 items-center" disabled={!active} aria-pressed={paused} onClick={()=>controls.current?.pause()}>{paused?<Play size={15}/>:<Pause size={15}/>} {paused?'Resume':'Pause'}</button></div>{audioBlocked&&active&&<button className="btn btn-secondary mt-2" onClick={()=>controls.current?.resumeAudio()}><Volume2 size={15}/> Enable audio playback</button>}<div className="overflow-y-auto mini-scroll mt-3 min-h-0"><video ref={video} autoPlay playsInline muted aria-label="Live camera preview" className={`w-full rounded-[16px] bg-[#171d19] mb-3 ${camera?'':'hidden'}`}/>{camera&&<p className="text-xs text-[#63796a] mb-3">Camera frames are sent to Gemini once per second.</p>}{error&&<p role="alert" className="message message-error text-xs mb-3">{error}</p>}<div aria-live="polite">{!turns.length&&<p className="text-xs text-[#63796a]">Speak to Gemini, or turn on the camera and ask it to mirror a gesture.</p>}{turns.map((t,i)=><div key={i} className="border border-[#e8ece8] rounded-[16px] px-3 py-3 mb-2 text-[13px] leading-5"><div className="text-[10px] uppercase tracking-[.12em] font-extrabold text-[#63796a] mb-1">{t.role==='user'?'You':'Gemini Live'}</div><div className="whitespace-pre-wrap">{t.text}</div>{t.proposal&&<ProposalCard turn={t} macros={macros} now={now} {...proposalActions}/>}</div>)}</div><div ref={bottom}/></div></section>
}
type VisionConfig={source:'esp32'|'webcam',camera_url:string,webcam_index:number,fallback_to_webcam:boolean,object_detection_enabled:boolean,hand_tracking_enabled:boolean,confidence_threshold:number}
type VisionRuntime={started:boolean,camera_connected:boolean,camera_state:string,active_source:string|null,camera_notice:string|null,fps:number,frames_processed:number,error:string|null,components:Record<string,string>,config:VisionConfig,detections_count:number}
type VisionDetection={label:string,class_id:number,confidence:number,box:number[]}
type VisionHand={gesture:string,confidence:number,handedness:string|null,landmark_count:number,fingers:Record<string,number>}
type VisionImitation={gesture:string,fingers:Record<string,number>,servo_targets:Record<string,{movement:string,normalized_target:number}>,execution_enabled:boolean,hardware_commands_sent:boolean}
type VisionCameraDevice={index:number,name:string,is_default:boolean}
type VisionCameraList={cameras:VisionCameraDevice[],default_index:number|null,error:string|null}
const emptyVisionConfig:VisionConfig={source:'esp32',camera_url:'http://192.168.4.1:81/stream',webcam_index:0,fallback_to_webcam:false,object_detection_enabled:true,hand_tracking_enabled:true,confidence_threshold:.35}
const visionSettingsKey='handoffVisionSettings'
function readVisionSettings():Partial<VisionConfig>{try{return JSON.parse(localStorage.getItem(visionSettingsKey)||'{}')}catch{return {}}}
function Vision(){
 const [runtime,setRuntime]=useState<VisionRuntime|null>(null)
 const [settings,setSettings]=useState<VisionConfig>({...emptyVisionConfig,...readVisionSettings()})
 const [view,setView]=useState<'default'|'object-detection'>('default')
 const [rawFrameUrl,setRawFrameUrl]=useState('')
 const [busy,setBusy]=useState(false)
 const [error,setError]=useState('')
 const [notice,setNotice]=useState('')
 const [detections,setDetections]=useState<VisionDetection[]>([])
 const [hand,setHand]=useState<VisionHand|null>(null)
 const [imitation,setImitation]=useState<VisionImitation|null>(null)
 const [pollKey,setPollKey]=useState(0)
 const [cameras,setCameras]=useState<VisionCameraDevice[]>([])
 const [cameraListError,setCameraListError]=useState('')
 const [scanningCameras,setScanningCameras]=useState(false)
 const connected=!!runtime?.camera_connected
 const saveSettings=(next:VisionConfig)=>{setSettings(next);try{localStorage.setItem(visionSettingsKey,JSON.stringify(next))}catch{}}
 const updateSettings=(patch:Partial<VisionConfig>)=>saveSettings({...settings,...patch})
 const scanCameras=async()=>{
  setScanningCameras(true);setCameraListError('')
  try{const list=await api<VisionCameraList>('/vision/cameras');setCameras(list.cameras);if(list.error)setCameraListError(list.error)}
  catch(e){setCameraListError(e instanceof Error?e.message:String(e))}
  finally{setScanningCameras(false)}
 }
 useEffect(()=>{void scanCameras()},[])
 useEffect(()=>{
  if(!connected||view!=='default'){
   setRawFrameUrl('')
   return
  }
  let disposed=false,inFlight=false,currentUrl=''
  const refreshRawFrame=async()=>{
   if(inFlight)return
   inFlight=true
   try{
    const response=await fetch(`/api/vision/frame.jpg?view=default&t=${Date.now()}`,{cache:'no-store'})
    if(!response.ok)throw new Error(`Camera frame request failed (${response.status})`)
    const nextUrl=URL.createObjectURL(await response.blob())
    if(disposed){URL.revokeObjectURL(nextUrl);return}
    const previousUrl=currentUrl
    currentUrl=nextUrl
    setRawFrameUrl(nextUrl)
    if(previousUrl)URL.revokeObjectURL(previousUrl)
   }catch{
    if(!disposed)setRawFrameUrl('')
   }finally{inFlight=false}
  }
  void refreshRawFrame()
  const timer=window.setInterval(()=>void refreshRawFrame(),200)
  return()=>{disposed=true;window.clearInterval(timer);if(currentUrl)URL.revokeObjectURL(currentUrl)}
 },[connected,view])
 useEffect(()=>{
  let disposed=false,busyPolling=false
  const refresh=async()=>{
   if(busyPolling)return
   busyPolling=true
   try{
    const [status,objects,handResult,imitate]=await Promise.all([
     api<VisionRuntime>('/vision/status'),api<{detections:VisionDetection[]}>('/vision/detections'),
     api<{hand:VisionHand|null}>('/vision/hand'),api<VisionImitation>('/vision/imitation'),
    ])
    if(disposed)return
    setRuntime(status);setDetections(objects.detections);setHand(handResult.hand);setImitation(imitate)
    if(!localStorage.getItem(visionSettingsKey))saveSettings({...emptyVisionConfig,...status.config})
   }catch(e){if(!disposed)setError(e instanceof Error?e.message:String(e))}
   finally{busyPolling=false}
  }
  void refresh()
  const timer=window.setInterval(()=>void refresh(),900)
  return()=>{disposed=true;window.clearInterval(timer)}
 },[pollKey])
 const start=async()=>{
  setBusy(true);setError('');setNotice('Connecting to the camera and loading local models…')
  try{const next=await api<VisionRuntime>('/vision/start',{method:'POST',body:json(settings)});setRuntime(next);setNotice('Inference is running in the backend. Both camera views use the same live analysis.');setPollKey(value=>value+1)}
  catch(e){setError(e instanceof Error?e.message:String(e));setNotice('')}
  finally{setBusy(false)}
 }
 const stop=async()=>{
  setBusy(true);setError('');setNotice('')
  try{const next=await api<VisionRuntime>('/vision/stop',{method:'POST'});setRuntime(next);setNotice('Camera inference stopped.')}
  catch(e){setError(e instanceof Error?e.message:String(e))}
  finally{setBusy(false)}
 }
 const streamUrl=connected?`/api/vision/stream.mjpg?view=${view}`:undefined
 const imageUrl=view==='default'?rawFrameUrl:streamUrl
 const serverError=runtime?.error
 const componentStatus=(name:string)=>runtime?.components[name]??'waiting'
 const fingerLabels:Record<string,string>={index:'Index',middle:'Middle',ring:'Ring',pinky:'Pinky',thumb_bend:'Thumb bend',thumb_opposition:'Thumb opposition'}
 const servoLabels:Record<string,string>={'1':'Pinky','2':'Ring','3':'Middle','4':'Index','5':'Thumb bend','6':'Thumb opposition'}
 // Results from a run that has stopped are labelled, never shown as live.
 const stale=!runtime?.started&&(detections.length>0||!!hand)
 return <div className="vision-workspace">
  <section className="card vision-controls p-5 md:p-6">
   <div className="flex flex-wrap justify-between items-start gap-3 mb-5"><div><Eyebrow>Local camera inference</Eyebrow><h2 className="text-xl font-bold mt-1">Camera and models</h2><p className="text-xs text-[#63796a] mt-2">YOLO and hand tracking run on the backend while either camera view is selected.</p></div><Pill tone={connected?'green':runtime?.started?'amber':'gray'}>{connected?`Connected · ${runtime?.active_source}`:runtime?.camera_state??'Checking runtime'}</Pill></div>
   <div className="vision-settings-grid">
    <label className="vision-field">Camera source<select className="field" value={settings.source} onChange={e=>updateSettings({source:e.target.value as VisionConfig['source']})}><option value="esp32">ESP32 camera stream</option><option value="webcam">Local webcam</option></select></label>
    {settings.source==='esp32'?<label className="vision-field vision-url">ESP32 stream URL<input className="field" value={settings.camera_url} spellCheck={false} placeholder="http://192.168.4.1:81/stream" onChange={e=>updateSettings({camera_url:e.target.value})}/></label>:<div className="vision-field">Camera input<div className="flex gap-2"><select className="field min-w-0 flex-1" value={settings.webcam_index} onChange={e=>updateSettings({webcam_index:Number(e.target.value)})} aria-label="Select camera input">{cameras.map(camera=><option key={camera.index} value={camera.index}>{camera.name}{camera.is_default?' · Mac default':''}</option>)}{!cameras.some(camera=>camera.index===settings.webcam_index)&&<option value={settings.webcam_index}>{cameras.length?`Camera ${settings.webcam_index} (not detected)`:'No cameras detected'}</option>}</select><button type="button" className="btn btn-secondary btn-icon" onClick={()=>void scanCameras()} disabled={scanningCameras||runtime?.started} aria-label={scanningCameras?'Scanning for cameras':'Refresh camera list'} title={runtime?.started?'Stop inference before refreshing the camera list':'Refresh camera list'}><RefreshCw size={16} className={scanningCameras?'spin':undefined}/></button></div>{cameraListError&&<small className="text-[#b4533f] font-normal">{cameraListError}</small>}</div>}
   </div>
   <div className="vision-settings-row"><div className="vision-field">Analysis<div className="vision-checks">
    <label className="vision-check"><input type="checkbox" checked={settings.object_detection_enabled} onChange={e=>updateSettings({object_detection_enabled:e.target.checked})}/> Run object detection</label>
    <label className="vision-check"><input type="checkbox" checked={settings.hand_tracking_enabled} onChange={e=>updateSettings({hand_tracking_enabled:e.target.checked})}/> Track hand and gestures</label>
    {settings.source==='esp32'&&<label className="vision-check"><input type="checkbox" checked={settings.fallback_to_webcam} onChange={e=>updateSettings({fallback_to_webcam:e.target.checked})}/> Use webcam if ESP32 is unavailable</label>}
    </div></div>
    <label className="vision-field vision-confidence"><span className="vision-confidence-label"><span>Object confidence</span><b>{settings.confidence_threshold.toFixed(2)}</b></span><input type="range" min="0.05" max="0.95" step="0.05" value={settings.confidence_threshold} onChange={e=>updateSettings({confidence_threshold:Number(e.target.value)})}/></label>
   </div>
   <div className="vision-actions"><button className="btn btn-primary" disabled={busy} onClick={()=>void start()}><Play size={14} fill="currentColor"/> {busy?'Working…':runtime?.started?'Restart inference':'Start inference'}</button><button className="btn btn-secondary" disabled={busy||!runtime?.started} onClick={()=>void stop()}><Square size={12} fill="currentColor"/> Stop inference</button><span className="vision-component-status"><span className={`vision-chip${componentStatus('object_detection')==='ready'?' ok':''}`}>YOLO {componentStatus('object_detection')}</span><span className={`vision-chip${componentStatus('hand_tracking')==='ready'?' ok':''}`}>Hands {componentStatus('hand_tracking')}</span></span></div>
   {error&&<p role="alert" className="message message-error mt-4 mb-0">{error}</p>}{serverError&&<p role="status" className="vision-runtime-error"><CircleAlert size={15}/><span>{serverError}</span></p>}{runtime?.camera_notice&&<p role="status" className="vision-runtime-notice">{runtime.camera_notice}</p>}{notice&&!error&&<p role="status" className="vision-runtime-notice">{notice}</p>}
  </section>
  <div className="vision-live-grid">
   <section className="card vision-camera-card p-5 md:p-6">
    <div className="vision-camera-heading"><div><Eyebrow>Live stream</Eyebrow><h2 className="text-xl font-bold mt-1">{view==='default'?'Default view':'Object detection view'}</h2></div><div className="vision-view-switch" role="group" aria-label="Camera display mode"><button type="button" aria-pressed={view==='default'} className={view==='default'?'selected':''} onClick={()=>setView('default')}>Default view</button><button type="button" aria-pressed={view==='object-detection'} className={view==='object-detection'?'selected':''} onClick={()=>setView('object-detection')}>Object detection view</button></div></div>
    <div className="vision-stream" aria-live="polite">{connected&&imageUrl?<img key={view} src={imageUrl} alt={view==='default'?'Raw camera view; analysis continues in the backend':'Camera view with object boxes and hand landmarks'}/>:<div className="vision-stream-empty"><Camera size={38}/><b>{connected?'Loading camera frame…':runtime?.started?'Connecting to camera…':'Start inference to show the camera'}</b><span>{connected?'The live raw preview is refreshing.':runtime?.started?serverError??'The backend is opening the selected camera stream.':'Inference will continue in the backend while either view is selected.'}</span></div>}</div>
    <div className="vision-inference-note"><span className={`status-dot ${runtime?.started?'online':''}`}/><span>Backend inference stays active in both views. The object detection view adds boxes and hand landmarks to the camera feed.</span></div>
    <div className="vision-stats"><div><small>Source</small><b>{runtime?.started?runtime.active_source??'—':'—'}</b></div><div><small>Camera</small><b>{runtime?.camera_state??'stopped'}</b></div><div><small>FPS</small><b>{runtime?.started?runtime.fps.toFixed(1):'—'}</b></div><div><small>Frames analyzed</small><b>{runtime?.started?runtime.frames_processed.toLocaleString():'—'}</b></div><div><small>Objects</small><b>{runtime?.started?runtime.detections_count:'—'}</b></div></div>
   </section>
   <div className={`vision-results${stale?' stale':''}`}>
    <section className="card p-5"><div className="flex items-center justify-between gap-3 mb-3"><h2 className="text-lg font-bold m-0">Detected objects</h2><span className="flex gap-2 items-center">{stale&&<Pill tone="gray">From the last run</Pill>}<Pill tone="gray">{detections.length}</Pill></span></div>{detections.length?<div className="vision-detection-list">{detections.slice(0,12).map((item,index)=><div className="vision-detection" key={`${item.class_id}-${index}`}><b>{item.label}</b><span>{(item.confidence*100).toFixed(1)}%</span><small>[{item.box.map(Math.round).join(', ')}]</small></div>)}</div>:<p className="text-xs text-[#63796a] mb-0">{connected?'No objects detected in the latest frame.':'Object detections will appear here when inference starts.'}</p>}</section>
    <section className="card p-5"><div className="flex items-center justify-between gap-3"><h2 className="text-lg font-bold m-0">Hand and gesture</h2><Pill tone={hand?'green':'gray'}>{hand?.gesture??'No hand'}</Pill></div><p className="text-xs text-[#63796a] mt-2">{hand?`${hand.landmark_count} landmarks · ${hand.handedness??'Hand'} · confidence ${hand.confidence.toFixed(2)}`:'Hand tracking results will appear here.'}</p><div className="vision-value-grid">{Object.entries(fingerLabels).map(([key,label])=>{const value=imitation?.fingers?.[key]??0;return <div className="vision-value" key={key}><div><span>{label}</span><b>{value.toFixed(2)}</b></div><i><em style={{width:`${Math.round(value*100)}%`}}/></i></div>})}</div></section>
    <section className="card p-5"><div className="flex items-center justify-between gap-3 mb-1"><h2 className="text-lg font-bold m-0">Gesture imitation</h2><Pill tone="amber">Simulation only</Pill></div><p className="text-xs text-[#63796a] mt-2">Normalized targets update from the hand estimate. No hardware commands are sent.</p><div className="vision-value-grid vision-servo-grid">{Object.entries(servoLabels).map(([id,label])=>{const value=imitation?.servo_targets?.[id]?.normalized_target??0;return <div className="vision-value" key={id}><div><span>{label}</span><b>{value.toFixed(2)}</b></div><i><em style={{width:`${Math.round(value*100)}%`}}/></i></div>})}</div><span className="sr-only">Execution {imitation?.execution_enabled?'enabled':'disabled'}; hardware commands {imitation?.hardware_commands_sent?'sent':'not sent'}.</span></section>
   </div>
  </div>
 </div>
}
function Devices({status,hardware,scan}:{status:Status,hardware:HardwareStatus|null,scan:()=>Promise<void>}){
 const controllerState=hardware?.connected?'Connected':hardware?.port?(hardware.servos.length?`${hardware.servos.length}/6 responding`:'USB detected'):'Not connected'
 const items=[
  {name:'Servo controller',state:controllerState,detail:'USB connection and live position',Icon:Cpu},
  {name:'Smart glasses',state:status.devices.glasses??'Not connected',detail:'Glasses camera connection',Icon:Camera},
 ]
 return <div className="space-y-5">
  <div className="device-list">{items.map(({name,state,detail,Icon})=><div key={name} className="card p-5 flex items-center gap-4"><div className="p-3 rounded-xl bg-[#edf6ef] text-teal"><Icon size={22}/></div><div className="flex-1"><div className="font-bold">{name}</div><div className="text-xs text-[#63796a] mt-1">{detail}</div></div><Pill tone={state.trim().toLowerCase()==='connected'?'green':hardware?.port&&name==='Servo controller'?'amber':'gray'}>{state}</Pill></div>)}</div>
  <div className="card p-5 max-w-[1040px]">
   <div className="flex flex-wrap justify-between gap-3 items-start"><div><Eyebrow>Servo bus</Eyebrow><h2 className="text-xl font-bold mt-1">Live status</h2><p className="text-xs text-[#63796a] mt-1">One row per servo, in ID order. Positions are raw counts out of 4000; saved travel limits remain active.</p></div><button className="btn btn-secondary btn-expand" aria-label="Refresh status" onClick={scan}><RefreshCw size={16}/><span className="btn-label" aria-hidden="true">Refresh status</span></button></div>
   {hardware?.error&&<p className="text-xs text-[#9b6633] mt-4">{hardware.error}</p>}
   {hardware?.port&&<p className="text-xs text-[#63796a] mt-4">Controller: {hardware.port}</p>}
   <div className="servo-table mt-4" role="table" aria-label="Servo status"><div className="servo-row servo-head" role="row"><span role="columnheader">Servo</span><span role="columnheader">Finger · mount</span><span role="columnheader" className="num">Position</span><span role="columnheader" className="num">Voltage</span><span role="columnheader" className="num">Temp</span><span role="columnheader" className="mid">Torque</span></div>{allServoIds.map(id=>{
    const servo=hardware?.servos.find(item=>item.id===id),location=servoMounts.find(mount=>mount.id===id)?.location??''
    return <div key={id} className="servo-row" role="row"><span role="cell"><span className="servo-id">{String(id).padStart(2,'0')}</span></span><span role="cell" className="servo-name"><b>{servos[id-1]}</b><small>{location}</small></span>{servo?<><span role="cell" className="num servo-pos"><span><b>{servo.position??'—'}</b><small> / 4000</small></span><i className="channel-meter"><i style={{width:`${servo.position==null?0:Math.max(0,Math.min(100,servo.position/maxServoCount*100))}%`}}/></i></span><span role="cell" className="num"><b>{servo.voltage_v?.toFixed(1)??'—'}</b><small> V</small></span><span role="cell" className="num"><b>{servo.temperature_c??'—'}</b><small> °C</small></span><span role="cell" className="mid servo-torque"><span className={`torque-chip${servo.torque_enabled===true?' on':''}`}>{servo.torque_enabled==null?'Unknown':servo.torque_enabled?'On':'Off'}</span><small>{servo.mode===0?'Position mode':servo.mode==null?'Mode unknown':`Mode ${servo.mode}`}</small></span></>:<span role="cell" className="servo-missing">No response</span>}</div>
   })}</div>
   {!!hardware&&<p className="text-xs text-[#63796a] mt-3">Last read {new Date(hardware.checked_at*1000).toLocaleTimeString()}</p>}
   <p className="text-xs text-[#63796a] mt-4">Status reads do not move the glove. Direct position controls are on Manual Control; saved gestures still run in simulation.</p>
  </div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
