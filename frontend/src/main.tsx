import React, {useEffect, useMemo, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {ArrowDownToLine, ArrowUpFromLine, Camera, Cpu, Hand, LayoutDashboard, Menu, Pause, Play, Plus, RotateCcw, Save, Settings2, ShieldCheck, SlidersHorizontal, Square, Trash2, X, type LucideIcon} from 'lucide-react'
import {Canvas, useFrame, useThree} from '@react-three/fiber'
import * as THREE from 'three'
import {OrbitControls} from 'three/examples/jsm/controls/OrbitControls.js'
import {RoundedBoxGeometry} from 'three/examples/jsm/geometries/RoundedBoxGeometry.js'
import './index.css'

type Page = 'Dashboard'|'Manual Control'|'Macro Studio'|'AI Vision'|'Calibration'|'Devices'
type Step = {positions: Record<number,number>,duration_ms:number}
type Macro = {id:string,name:string,description:string,kind:'pose'|'sequence',steps:Step[]}
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
const initialStatus:Status={mode:'simulation',positions:blank(),target:blank(),stopped:false,playing:false,last_event:'Connecting',vision:null,physical:{connected:false,positions:{},read_at:null,error:null},devices:{controller:'Not connected',glasses:'Not connected',camera:'Browser preview only'}}
// Raw servo counts → 0–100 % curl using each servo's saved open (0 %) and closed (100 %) counts.
const physicalCurls=(physical:Physical|undefined,cal:Calibration[]):Record<number,number|null>=>Object.fromEntries([1,2,3,4,5,6].map(id=>{const count=physical?.connected?physical.positions[id]:null,c=cal.find(item=>item.servo_id===id);return [id,count==null||!c||c.closed_count===c.open_count?null:Math.max(0,Math.min(100,Math.round((count-c.open_count)/(c.closed_count-c.open_count)*100)))]}))
async function api<T>(path:string,options?:RequestInit):Promise<T>{const response=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options}); if(!response.ok){let message=`Request failed (${response.status})`;try{const error=await response.json();message=typeof error.detail==='string'?error.detail:JSON.stringify(error.detail)}catch{}throw Error(message)}return response.status===204?undefined as T:response.json()}
const json=(body:unknown)=>JSON.stringify(body)

function App(){
 const [page,setPage]=useState<Page>('Dashboard'),[status,setStatus]=useState<Status>(initialStatus),[hardware,setHardware]=useState<HardwareStatus|null>(null),[macros,setMacros]=useState<Macro[]>([]),[cal,setCal]=useState<Calibration[]>([]),[error,setError]=useState(''),[notice,setNotice]=useState(''),[connected,setConnected]=useState(false),[menuOpen,setMenuOpen]=useState(false)
 const refresh=async()=>{try{const [m,c,s]=await Promise.all([api<Macro[]>('/macros'),api<Calibration[]>('/calibration'),api<Status>('/status')]);setMacros(m);setCal(c);setStatus(s);setConnected(true)}catch(e){setError(String(e));setConnected(false)}}
 useEffect(()=>{refresh();const scheme=location.protocol==='https:'?'wss':'ws';let socket:WebSocket;let timer:number;let disposed=false;const connect=()=>{if(disposed)return;socket=new WebSocket(`${scheme}://${location.host}/ws/telemetry`);socket.onopen=()=>setConnected(true);socket.onmessage=e=>{const s:Status=JSON.parse(e.data);setStatus(s)};socket.onclose=()=>{if(!disposed){setConnected(false);timer=window.setTimeout(connect,2500)}}};connect();return()=>{disposed=true;clearTimeout(timer);socket?.close()}},[])
 const scanHardware=async()=>{try{setHardware(await api<HardwareStatus>('/hardware/status'))}catch(e){setHardware({connected:false,port:null,servos:[],error:e instanceof Error?e.message:String(e),checked_at:Date.now()/1000})}}
 useEffect(()=>{scanHardware();const timer=window.setInterval(scanHardware,20000);return()=>clearInterval(timer)},[])
 const action=async<T,>(work:()=>Promise<T>,success?:string)=>{try{setError('');const result=await work();if(success)setNotice(success);return result}catch(e){setError(e instanceof Error?e.message:String(e));return undefined}}
 const play=async(m:Macro)=>{await action(()=>api(`/macros/${m.id}/play`,{method:'POST'}),`Playing ${m.name} in simulation`)}
 const stop=async()=>{const result=await action(()=>api<Status & {hardware_stop?:{attempted:boolean,failed_ids?:number[],message:string}}>('/control/stop',{method:'POST'}));if(result){setNotice(`Software stop latched. ${result.hardware_stop?.message==='Physical writes are disabled.'?'Simulation halted.':result.hardware_stop?.message||'Use the physical power cutoff if the glove moves.'}`)}}
 const reset=()=>action(()=>api<Status>('/control/reset',{method:'POST'}),'Simulation resumed')
 const nav:[Page,LucideIcon][]=[['Dashboard',LayoutDashboard],['Manual Control',SlidersHorizontal],['Macro Studio',Hand],['AI Vision',Camera],['Calibration',Settings2],['Devices',Cpu]]
 const navigate=(next:Page)=>{setPage(next);setMenuOpen(false);setNotice('');window.scrollTo({top:0,behavior:'smooth'})}
 return <div className="app-layout">
  <aside className={`app-sidebar ${menuOpen?'open':''}`}>
   <div className="sidebar-top"><button className="sidebar-brand" onClick={()=>navigate('Dashboard')} aria-label="handoff home"><span className="sidebar-brand-icon"><Hand size={25} strokeWidth={2}/></span><span>handoff<span className="brand-dot">.</span></span></button><button className="sidebar-close" onClick={()=>setMenuOpen(false)} aria-label="Close menu"><X size={20}/></button></div>
   <div className="sidebar-section-label">YOUR WORKSPACE</div>
   <nav className="sidebar-nav" aria-label="Main navigation">{nav.map(([name,Icon])=><button key={name} className={page===name?'active':''} onClick={()=>navigate(name)}><Icon size={18} strokeWidth={1.9}/><span>{name==='Dashboard'?'Overview':name}</span>{name==='Macro Studio'&&<small>{macros.length}</small>}</button>)}</nav>
   <div className="sidebar-bottom"><div className="sidebar-section-label">SYSTEM</div><div className="sidebar-status"><span className={`status-dot ${connected?'online':''}`}/><div><b>{connected?'Local API connected':'API offline'}</b><small>Targets require confirmation</small></div></div></div>
  </aside>
  {menuOpen&&<button className="sidebar-overlay" aria-label="Close menu" onClick={()=>setMenuOpen(false)}/>}
  <div className="app-main"><header className="app-header"><div className="header-title"><button className="mobile-menu" onClick={()=>setMenuOpen(true)} aria-label="Open menu"><Menu size={21}/></button><div><strong>{page==='Dashboard'?'handoff':page}</strong><span>{page==='Dashboard'?'Overview dashboard':subtitles[page]}</span></div></div><div className="header-meta"><span className="header-connection"><i className={connected?'on':''}/>{connected?'API connected':'API offline'}</span><span className="header-mode">LOCAL CONTROL</span></div></header>
   <main className="app-content">
    {error&&<div role="alert" className="message message-error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
    {notice&&<div role="status" className="message message-success"><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss notice"><X size={16}/></button></div>}
    {page!=='Dashboard'&&<div className="page-heading"><div><div className="section-kicker">WORKSPACE / {page.toUpperCase()}</div><h1>{page}</h1><p>{subtitles[page]}</p></div><div className="page-actions"><button className="stop-button" onClick={stop}><Square size={13} fill="currentColor"/> Stop</button>{status.stopped&&<button className="btn btn-secondary flex items-center gap-2" onClick={reset}><RotateCcw size={16}/> Reset software stop</button>}</div></div>}
    {status.stopped&&<div className="stop-message"><Pause size={20}/><div><b>Software stop is latched.</b> Simulation is halted. If hardware control was enabled, servo torque-off was attempted; use the physical power cutoff if the glove moves.</div>{page==='Dashboard'&&<button className="btn btn-secondary" onClick={reset}>Reset software stop</button>}</div>}
    {page==='Dashboard'&&<Dashboard status={status} hardware={hardware} cal={cal} macros={macros} play={play} go={navigate} stop={stop}/>}
    {page==='Manual Control'&&<Manual status={status} hardware={hardware} onReading={setHardware}/>}
    {page==='Macro Studio'&&<MacroStudio macros={macros} status={status} refresh={refresh} action={action} play={play} manual={status.positions}/>}
    {page==='AI Vision'&&<Vision/>}
    {page==='Calibration'&&<CalibrationPage cal={cal} hardware={hardware} onReading={setHardware} refresh={refresh} action={action}/>}

    {page==='Devices'&&<Devices status={status} hardware={hardware} scan={scanHardware}/>}
   </main>
  </div>
 </div>
}
const subtitles:Record<Page,string>={Dashboard:'A clear view of your hand, gestures, and connected systems.', 'Manual Control':'Stage and confirm physical servo targets.', 'Macro Studio':'Create, edit, and play gestures and movement sequences.', 'AI Vision':'Preview a camera feed from your browser.',Calibration:'Record measured travel limits and gesture endpoints.',Devices:'See connected devices and live servo status.'}
function Eyebrow({children}:{children:React.ReactNode}){return <div className="text-[11px] uppercase tracking-[.18em] text-[#7c8f85] font-extrabold">{children}</div>}
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
function Dashboard({status,hardware,cal,macros,play,go,stop}:{status:Status,hardware:HardwareStatus|null,cal:Calibration[],macros:Macro[],play:(m:Macro)=>void,go:(p:Page)=>void,stop:()=>void}){
 const hardwareConnected=hardware?.connected===true
 const [source,setSource]=useState<'sim'|'physical'>(()=>{try{return localStorage.getItem('handoff-hand-source')==='physical'?'physical':'sim'}catch{return 'sim'}})
 useEffect(()=>{try{localStorage.setItem('handoff-hand-source',source)}catch{}},[source])
 const physical=source==='physical',live=status.physical?.connected===true
 const shown:Record<number,number|null>=physical?physicalCurls(status.physical,cal):status.positions
 const unverified=physical&&live&&cal.some(c=>!c.verified)
 return <div className="overview-stack">
 <section className="overview-panel control-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={22}/></span><div className="panel-head-copy"><h1>Control your hand</h1><p>Preview your glove's movements.</p></div><button className="stop-button panel-stop" onClick={stop}><Square size={12} fill="currentColor"/> Stop</button></div>
  <div className="hardware-connection" role="status" aria-label={hardwareConnected?'Hardware connected':'Hardware disconnected'}><span className={`hardware-indicator ${hardwareConnected?'on':''}`} aria-hidden="true"/>Hardware connected</div>
  <div className="control-workspace"><div className="hand-stage"><div className="stage-head"><b>YOUR GLOVE</b><div className="hand-source" role="group" aria-label="Hand data source"><button className={physical?'':'on'} aria-pressed={!physical} onClick={()=>setSource('sim')}>Simulated</button><button className={physical?'on':''} aria-pressed={physical} onClick={()=>setSource('physical')}>Physical</button></div><span>{physical?(live?'LIVE':'OFFLINE'):status.playing?'MOVING':'READY'}</span></div><div className="hand-diagram hand-photo-stage"><Hand3D values={shown} dim={physical&&!live} viewKey={source}/>{physical&&!live&&<div className="hand-overlay">{status.physical?.error||'Servo controller not connected'}</div>}{unverified&&<span className="hand-badge">Calibration unverified</span>}<span className="diagram-label">3D · DRAG TO ROTATE</span></div><div className="stage-foot">{physical?"Live servo positions, mapped with each servo's saved open and closed counts":'Simulated positions from saved gestures'}</div></div><div className="control-details"><div className="detail-heading"><div><span className="section-kicker">MOVEMENT</span><h2>Finger movement</h2></div><span className="detail-status"><i/> {physical?(live?'Physical · live':'Physical · offline'):'Simulation'}</span></div><div className="channel-list">{servos.map((name,i)=>{const value=shown[i+1];return <div className="channel-row" key={name}><span className="channel-index">{String(i+1).padStart(2,'0')}</span><span className="channel-name">{name}</span><span className="channel-meter"><i style={{width:`${value??0}%`}}/></span><b>{value==null?'—':`${value}%`}</b></div>})}</div><button className="dark-action" onClick={()=>go('Manual Control')}>Open manual controls <span aria-hidden="true">↗</span></button></div></div>
  <div className="panel-note"><span className="status-dot online"/><b>Simulation active</b><span>{hardwareConnected?'Six servos responding. Stage targets and confirm them in Manual Control.':'Saved gestures use simulation; direct motor moves are in Manual Control.'}</span></div>
 </section>
 <section className="overview-panel gestures-panel"><div className="overview-panel-head"><span className="panel-icon"><Hand size={21}/></span><div className="panel-head-copy"><div className="section-kicker">SAVED MOVEMENTS</div><h2>Gestures, ready to test.</h2><p>Choose a macro to preview it in the simulated hand.</p></div><button className="plain-link" onClick={()=>go('Macro Studio')}>Open Macro Studio <span aria-hidden="true">↗</span></button></div><div className="overview-gesture-grid">{macros.slice(0,4).map((m,i)=><button key={m.id} disabled={status.stopped} onClick={()=>play(m)} className="overview-gesture"><span className="gesture-index">{String(i+1).padStart(2,'0')}</span><span><b>{m.name}</b><small>{m.description||'Custom gesture'}</small></span><Play size={15} fill="currentColor"/></button>)}</div><div className="panel-note"><span className="status-dot"/><span>{macros.length} saved macros</span></div></section>
 </div>}
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
 useEffect(()=>{if(!hardware)return;setTargets(current=>{const next={...current};for(const servo of hardware.servos){if(servo.position!=null&&next[servo.id]===undefined)next[servo.id]=servo.position}return next})},[hardware])
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
   <div className="flex justify-between gap-2 items-center"><div className="min-w-0"><div className="text-[9px] font-extrabold tracking-[.1em] text-[#8d9d90]">SERVO 0{id} · {servoMounts.find(m=>m.id===id)?.location.toUpperCase()}</div><h3 className="font-bold text-sm mt-1 mb-0 leading-tight">{name}</h3></div><span className="text-[10px] text-[#607669] text-right shrink-0">Now <b>{live?.position??'—'}</b><br/>{movingIds.includes(id)?'Sending':pending[id]!==undefined?'Pending':live?.moving?'Moving':'Stopped'}</span></div>
   <div className="grid grid-cols-[1fr_76px] gap-2 items-center mt-3"><input aria-label={`${name} target count slider`} type="range" min={minimum} max={maximum} value={Math.max(minimum,Math.min(maximum,target))} onChange={e=>stage(id,Number(e.target.value))} className="w-full" disabled={!editable}/><input aria-label={`${name} target count`} className="field !py-1.5 !px-2 text-xs" type="number" min={minimum} max={maximum} step="1" value={target} disabled={!editable} onChange={e=>stage(id,Number(e.target.value))}/></div>
   <div className="flex justify-between text-[9px] text-[#94a39a] mt-0.5"><span>0</span><span>Target count</span><span>4000</span></div>
   <div className="grid grid-cols-[max-content_1fr] gap-2 items-center mt-2"><span className="text-[10px] font-bold text-[#6b7f72] whitespace-nowrap">Speed {speed}</span><input aria-label={`${name} speed`} type="range" min={control?.position_speed_min??50} max={control?.position_speed_max??2400} step="25" value={speed} onChange={e=>setSpeeds(current=>({...current,[id]:Number(e.target.value)}))} className="w-full" disabled={!editable}/></div>
  </div>
 }
 return <section className="card p-5 md:p-7 mb-5">
  <div className="flex flex-wrap items-start justify-between gap-3"><div><Eyebrow>Physical glove</Eyebrow><h2 className="text-xl font-bold mt-1">Live servo positions</h2><p className="text-sm text-[#63796a] mt-2 max-w-2xl">Stage raw targets from 0–4000, then click Confirm movement. Multiple targets are sent together in one synchronized packet.</p></div><Pill tone={control?.writes_enabled?'green':'amber'}>{control?.writes_enabled?'Control ready':'Motor control locked'}</Pill></div>
  {!!hardware?.connected&&torqueOffIds.length>0&&<div role="status" className="message message-error mt-4"><b>Torque is off on servo{torqueOffIds.length===1?'':'s'} {torqueOffIds.join(', ')}.</b><p className="mt-1">When you enable torque, the site first aligns each selected servo’s stored target to its current position while torque is off, then enables torque. It checks that each servo is stationary and healthy and sends no movement command.</p><button className="btn btn-secondary mt-3" onClick={enableTorque} disabled={!available||torqueBusy}>{torqueBusy?'Aligning and enabling torque…':`Align and enable torque for ${torqueOffIds.join(', ')}`}</button></div>}
  {localError&&<div role="alert" className="message message-error mt-4">{localError}<button onClick={()=>setLocalError('')} aria-label="Dismiss warning"><X size={16}/></button></div>}
  {localNotice&&<div role="status" className="message message-success mt-4">{localNotice}</div>}
  <div className="mt-5 rounded-xl border border-[#d9e6dc] bg-[#f5faf6] p-3 md:px-4"><div className="flex items-center justify-between gap-3"><h3 className="font-bold text-sm">All servos</h3><b className="text-sm text-[#1e4937] whitespace-nowrap">{allServoTarget} counts</b></div><input aria-label="All servos target count slider" type="range" min="0" max={maxServoCount} step="1" value={allServoTarget} onChange={e=>stageAllServos(Number(e.target.value))} disabled={!allFingersAvailable} className="w-full mt-2"/><div className="flex justify-between text-[10px] text-[#94a39a] mt-0.5"><span>0</span><span>2000</span><span>4000</span></div></div>
  <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(290px,0.9fr)_minmax(0,1fr)] gap-3 mt-4 items-center">
   <div className="flex flex-col gap-3 xl:col-start-1 xl:row-start-1">{[1,2,3].map(servoControl)}</div>
   <div className="rounded-xl border border-[#e5ebe5] bg-[#fcfdfc] p-2 xl:col-start-2 xl:row-start-1"><div className="hand-diagram hand-photo-stage rounded-lg" style={{height:420}}><Hand3D values={previewValues} viewKey="manual-target-preview"/><span className="diagram-label">3D · DRAG TO ROTATE</span></div></div>
   <div className="flex flex-col gap-3 xl:col-start-3 xl:row-start-1">{[4,5,6].map(servoControl)}</div>
  </div>
  <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-[#d9e6dc] bg-[#f5faf6] p-3"><div className="flex-1 text-sm text-[#4f6658]">{pendingCount?`${pendingCount} servo ${pendingCount===1?'target':'targets'} pending. Confirm sends them together.`:'No pending movement.'}</div><button className="btn btn-secondary" onClick={discard} disabled={!pendingCount||!!movingIds.length}>Discard changes</button><button className="btn btn-primary" onClick={confirm} disabled={!pendingCount||!available||!!movingIds.length}>{movingIds.length?'Sending / waiting for settle…':'Confirm movement'}</button></div>
  <p className="text-xs text-[#839187] mt-5">Only Confirm movement sends physical targets. Manual targets use raw servo counts and may exceed calibrated travel; inspect the glove path before moving. Device health checks and Stop remain active. Keep the physical power cutoff within reach.</p>
 </section>
}
function Manual({status,hardware,onReading}:{status:Status,hardware:HardwareStatus|null,onReading:(reading:HardwareStatus)=>void}){
 return <HardwareManual status={status} hardware={hardware} onReading={onReading}/>
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
function Devices({status,hardware,scan}:{status:Status,hardware:HardwareStatus|null,scan:()=>Promise<void>}){
 const controllerState=hardware?.connected?'Connected':hardware?.port?(hardware.servos.length?`${hardware.servos.length}/6 responding`:'USB detected'):'Not connected'
 const items=[
  {name:'Servo controller',state:controllerState,detail:'USB connection and live position',Icon:Cpu},
  {name:'Smart glasses',state:status.devices.glasses??'Not connected',detail:'Glasses camera connection',Icon:Camera},
 ]
 return <div className="space-y-5">
  <div className="device-list">{items.map(({name,state,detail,Icon})=><div key={name} className="card p-5 flex items-center gap-4"><div className="p-3 rounded-xl bg-[#edf6ef] text-teal"><Icon size={22}/></div><div className="flex-1"><div className="font-bold">{name}</div><div className="text-xs text-[#839187] mt-1">{detail}</div></div><Pill tone={state.trim().toLowerCase()==='connected'?'green':hardware?.port&&name==='Servo controller'?'amber':'gray'}>{state}</Pill></div>)}</div>
  <div className="card p-5 max-w-[1040px]">
   <div className="flex flex-wrap justify-between gap-3 items-start"><div><Eyebrow>Servo bus</Eyebrow><h2 className="text-xl font-bold mt-1">Live status</h2><p className="text-xs text-[#839187] mt-1">Arranged like your photo, with the glove above the servo mount. Positions are raw counts; saved travel limits remain active.</p></div><button className="btn btn-secondary" onClick={scan}>Refresh status</button></div>
   {hardware?.error&&<p className="text-xs text-[#9b6633] mt-4">{hardware.error}</p>}
   {hardware?.port&&<p className="text-xs text-[#839187] mt-4">Controller: {hardware.port}</p>}
   <div className="grid md:grid-cols-2 gap-2 mt-4">{servoMounts.map(({id,location})=>{
    const servo=hardware?.servos.find(item=>item.id===id)
    return <div key={id} className="rounded-xl border border-[#e8ece8] px-4 py-3 flex justify-between gap-3"><div><div className="text-[10px] uppercase tracking-[.12em] font-bold text-[#839187]">{location}</div><b className="text-sm">ID {id}</b><span className="text-xs text-[#839187] ml-2">{servos[id-1]}</span></div><div className="text-right text-xs text-[#63796a]">{servo?<><b className="text-[#1e4937]">{servo.position??'—'} counts</b><div>{servo.voltage_v?.toFixed(1)??'—'} V · {servo.temperature_c??'—'}°C</div><div>{servo.mode===0?'Position mode':servo.mode==null?'Mode unknown':`Mode ${servo.mode}`} · {servo.torque_enabled==null?'Torque unknown':servo.torque_enabled?'Torque on':'Torque off'}</div></>:<span>No response</span>}</div></div>
   })}</div>
   <p className="text-xs text-[#839187] mt-4">Status reads do not move the glove. Direct position controls are on Manual Control; saved gestures still run in simulation.</p>
  </div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
