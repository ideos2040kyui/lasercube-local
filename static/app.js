'use strict';
const $ = id => document.getElementById(id);
const labels = {density:'わらわら度',brightness:'明るさ',scale:'大きさ',speed:'動く速さ',next:'図形切替',start:'開始',stop:'停止'};
let state = null, token = '', learning = null, midi = null, selectedInput = '', timer = null;
let settings = {shape:'circle',brightness:1,scale:12,speed:0.15,colour:'#57e5dc',duration:0,density:4};
let handData=null,handOutline=[];
let oscEditing = false;
const pendingMidiOSC = new Map();
let bindings = {};
try { bindings = JSON.parse(localStorage.getItem('lasercube-midi-bindings') || '{}'); } catch (_) {}
function notice(text) { $('message').textContent=text; $('message').hidden=false; clearTimeout(timer); timer=setTimeout(()=>$('message').hidden=true,6500); }
async function api(path, body={}) {
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error || '通信エラー');
  state=data; render(); return data;
}
async function act(path, body={}) { try{return await api(path,body);}catch(e){notice(e.message);return null;} }
function updateControls() {
  for(const key of ['brightness','scale','speed','duration','colour','density']) $(key).value=settings[key];
  $('density-value').textContent=Math.round(settings.density)+'体';
  $('brightness-value').textContent=Number(settings.brightness).toFixed(1)+'%';
  $('scale-value').textContent=Math.round(settings.scale)+'%';
  $('speed-caption').textContent=settings.shape==='hands'?'うごめく速さ':'回転速度';
  $('speed-value').textContent=Number(settings.speed).toFixed(2)+(settings.shape==='hands'?'':' 回転/秒');
  document.querySelectorAll('[data-shape]').forEach(button=>button.classList.toggle('selected',button.dataset.shape===settings.shape));
  $('preview-label').textContent={circle:'CIRCLE',eight:'FIGURE EIGHT',triangle:'TRIANGLE',hands:'RED HANDS / '+settings.density}[settings.shape];
}
let paramsTimer;
function change(values) { if(values.shape==='hands'&&!('colour'in values))values.colour='#ed1c24';Object.assign(settings,values); updateControls(); clearTimeout(paramsTimer); paramsTimer=setTimeout(()=>act('/api/params',settings),50); }
function render() {
  if(!state) return;
  const real=state.mode==='real', connected=state.connected, device=connected?state.device:null;
  $('connection-dot').classList.toggle('online',connected);
  $('connection-title').textContent=connected?'LaserCube 接続済み':'LaserCube 未接続';
  $('device-address').textContent=state.host;
  $('mode-badge').textContent=real?'HARDWARE':'DEMO';
  $('mode-badge').classList.toggle('real',real);
  $('demo-mode').classList.toggle('selected',!real); $('real-mode').classList.toggle('selected',real);
  $('arm-panel').hidden=!real;
  $('mode-description').textContent=real?'本体の安全機能を有効にしてから、出力操作を許可します。':'デモではレーザーを出力しません。機器なしで試せます。';
  $('arm').textContent=state.armed?'出力操作を許可済み':'実機への出力操作を許可';
  $('arm').disabled=state.armed || !$('safety').checked || !connected;
  $('start').disabled=state.running || (real && (!connected || !state.armed));
  $('start').textContent=real?'▶ 実機出力を開始':'▶ デモを開始';
  $('transport-title').textContent=state.running?(real?'実機出力中':'デモ実行中'):'プレビュー';
  $('transport-detail').textContent=state.running?(state.remaining===null?'停止操作まで継続中':'あと '+Math.ceil(state.remaining)+' 秒で自動停止'):real?'開始ボタンで本体へ送信':'機器がなくても試せます';
  $('run-label').textContent=state.running?(state.remaining===null?'RUNNING / ∞':'RUNNING / '+Math.ceil(state.remaining)+'s'):'PREVIEW ONLY';
  $('device-pill').textContent=connected?'ONLINE':'OFFLINE';
  $('device-model').textContent=device?device.model:'未接続（デモは利用可能）';
  $('device-fw').textContent=device?device.firmware:'—';
  $('device-temp').textContent=device?device.temperature+' °C'+(device.over_temperature?' · 過熱停止':device.temperature_warning?' · 温度警告':''):'—';
  $('thermal-notice').hidden=!device || !(device.temperature_warning || device.over_temperature);
  $('thermal-notice').textContent=device?.over_temperature?'本体が過熱停止を返しています。実機出力を停止し、開始操作をブロックしています。':device?.temperature_warning?'本体から温度警告を受信しています。警告は表示し、過熱停止フラグがONになった場合は自動停止します。':'';
  $('device-lock').textContent=device?(device.interlock_enabled?'有効':'未有効'):'—';
  $('events').replaceChildren(...state.events.slice(0,8).map(item=>{const row=document.createElement('div'),time=document.createElement('time');time.textContent=item.time;row.append(time,document.createTextNode(item.message));return row;}));
  const osc=state.osc||{rx_enabled:false,tx_enabled:false,rx_port:9000,tx_port:9001,received:0,sent:0};
  if(!oscEditing){$('osc-rx').checked=osc.rx_enabled;$('osc-tx').checked=osc.tx_enabled;$('osc-rx-port').value=osc.rx_port;$('osc-tx-port').value=osc.tx_port;}
  $('osc-sent').textContent='送信 '+osc.sent;$('osc-received').textContent='受信 '+osc.received;
  $('osc-last-tx').textContent=osc.last_tx||'—';$('osc-last-rx').textContent=osc.last_rx||'—';
  $('osc-error').textContent=osc.error||'';$('osc-error').hidden=!osc.error;
}
async function sync() {
  try {
    const response=await fetch('/api/state',{cache:'no-store'}); if(!response.ok) throw new Error('server');
    state=await response.json(); token=state.token;
    if(!document.activeElement || !['INPUT','SELECT'].includes(document.activeElement.tagName)) { settings={...state.params};updateControls(); }
    render();
    await api('/api/heartbeat');
  } catch(e) { $('connection-title').textContent='ローカルサーバーに接続できません'; }
}
for(const key of ['brightness','scale','speed','duration','colour','density']) $(key).addEventListener('input',()=>change({[key]:key==='colour'?$(key).value:Number($(key).value)}));
document.querySelectorAll('[data-shape]').forEach(button=>button.addEventListener('click',()=>change({shape:button.dataset.shape})));
$('demo-mode').onclick=()=>act('/api/mode',{mode:'demo'});
$('real-mode').onclick=()=>act('/api/mode',{mode:'real'});
$('safety').onchange=render;
$('arm').onclick=()=>act('/api/arm',{acknowledge:$('safety').checked});
$('start').onclick=async()=>{clearTimeout(paramsTimer);if(await act('/api/params',settings)) await act('/api/start');};
$('stop').onclick=()=>act('/api/stop');
window.addEventListener('keydown',event=>{if(event.code==='Escape') act('/api/stop');});
window.addEventListener('pagehide',()=>{if(token&&state?.running) fetch('/api/stop',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:'{}',keepalive:true}).catch(()=>{});});
for(const id of ['osc-rx','osc-tx','osc-rx-port','osc-tx-port']) $(id).addEventListener('input',()=>{oscEditing=true;});
$('osc-save').onclick=async()=>{const result=await act('/api/osc/config',{rx_enabled:$('osc-rx').checked,rx_port:Number($('osc-rx-port').value),tx_enabled:$('osc-tx').checked,tx_host:'127.0.0.1',tx_port:Number($('osc-tx-port').value)});if(result){oscEditing=false;render();notice('OSCの設定を適用しました');}};

function forwardMidiOSC(status,index,value){
  if(!state?.osc?.tx_enabled)return;
  const send=message=>fetch('/api/osc/midi',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(message)}).then(async response=>{if(!response.ok)throw new Error((await response.json()).error);}).catch(error=>notice('OSC送信：'+error.message));
  const message={status,index,value};
  if((status&0xf0)!==0xb0){send(message);return;}
  const key=status+':'+index, pending=pendingMidiOSC.get(key);
  if(pending){pending.message=message;return;}
  const entry={message};pendingMidiOSC.set(key,entry);
  setTimeout(()=>{pendingMidiOSC.delete(key);send(entry.message);},16);
}

function saveBindings() { localStorage.setItem('lasercube-midi-bindings',JSON.stringify(bindings)); showBindings(); }
function showBindings() {
  $('bindings').replaceChildren(...Object.entries(bindings).filter(([action,b])=>labels[action]&&b&&['cc','note'].includes(b.type)&&Number.isInteger(b.index)).map(([action,b])=>{
    const row=document.createElement('div'),name=document.createElement('strong'),value=document.createElement('span'),remove=document.createElement('button');row.className='binding';
    name.textContent=labels[action];value.textContent=(b.type==='cc'?'CC ':'NOTE ')+b.index+' / CH '+(b.channel+1);remove.textContent='解除';remove.onclick=()=>{delete bindings[action];saveBindings();};row.append(name,value,remove);return row;
  }));
}
function inputList() {
  if(!midi)return;
  const inputs=[...midi.inputs.values()].filter(input=>input.state!=='disconnected');
  const preferred=inputs.find(input=>input.id===selectedInput)||inputs.find(input=>/APC.?40/i.test(input.name||''))||inputs[0];
  selectedInput=preferred?.id||'';
  $('midi-device').replaceChildren(...inputs.map(input=>{const option=document.createElement('option');option.value=input.id;option.textContent=input.name||'MIDI input';return option;}));
  if(!inputs.length){const option=document.createElement('option');option.textContent='MIDI機器が見つかりません';$('midi-device').append(option);}
  $('midi-device').value=selectedInput;
  for(const input of midi.inputs.values()) input.onmidimessage=input.id===selectedInput?onMidi:null;
  $('midi-status').textContent=preferred?'入力中：'+preferred.name:'USB接続後、この一覧に機器が表示されます。';
}
async function connectMidi() {
  if(!navigator.requestMIDIAccess){notice('MIDI入力はChromeまたはEdgeで、このlocalhostのURLを開いてください。デモ画面はそのまま使えます。');return;}
  try {midi=await navigator.requestMIDIAccess({sysex:false});midi.onstatechange=inputList;inputList();$('midi-connect').textContent='MIDI 接続済み';}catch(e){notice('MIDIアクセスを許可してください：'+e.message);}
}
$('midi-connect').onclick=connectMidi;
$('midi-device').onchange=()=>{selectedInput=$('midi-device').value;inputList();};
$('learn').onclick=()=>{if(!midi){notice('先に「MIDIを接続」を押してください');return;}if(learning){learning=null;$('learn').classList.remove('learning');$('learn').textContent='操作を覚える';return;}learning=$('learn-action').value;$('learn').classList.add('learning');$('learn').textContent='入力を待っています…';$('learn-help').textContent=labels[learning]+' に使うフェーダー・ノブ・ボタンを1つ動かしてください。';};
document.querySelectorAll('[data-learn]').forEach(button=>button.addEventListener('click',()=>{learning=null;$('learn-action').value=button.dataset.learn;$('learn').onclick();$('learn-help').scrollIntoView?.({behavior:'smooth',block:'center'});}));
function onMidi(event) {
  const [status,index,value]=event.data,kind=status&0xf0,channel=status&15;
  if(kind!==0xb0&&kind!==0x90&&kind!==0x80)return;
  forwardMidiOSC(status,index,value);
  const type=kind===0xb0?'cc':'note';
  $('midi-last').textContent=(type==='cc'?'CC ':'NOTE ')+index+' / CH '+(channel+1)+' / '+value;
  if(kind===0x80||(type==='note'&&value===0))return;
  if(learning){
    for(const [action,b]of Object.entries(bindings)) if(b.type===type&&b.channel===channel&&b.index===index)delete bindings[action];
    bindings[learning]={type,channel,index};learning=null;$('learn').classList.remove('learning');$('learn').textContent='操作を覚える';$('learn-help').textContent='割り当てを保存しました。このブラウザで次回も使えます。';saveBindings();return;
  }
  for(const [action,b]of Object.entries(bindings)){
    if(b.type!==type||b.channel!==channel||b.index!==index)continue;
    if(['brightness','scale','speed','density'].includes(action)){const ranges={brightness:[0,5],scale:[5,30],speed:[0,1],density:[1,8]},[min,max]=ranges[action];const mapped=min+(max-min)*value/127;change({[action]:action==='density'?Math.round(mapped):mapped});}
    else if(value>0&&action==='next'){const shapes=['circle','eight','triangle','hands'];change({shape:shapes[(shapes.indexOf(settings.shape)+1)%shapes.length]});}
    else if(value>0&&action==='start')$('start').click();
    else if(value>0&&action==='stop')$('stop').click();
  }
}

const canvas=$('preview'),ctx=canvas.getContext('2d');
function installHand(data){
  handData=data;let points=data.outline.map(point=>point.slice());
  for(let pass=0;pass<2;pass++){const next=[];points.forEach((point,index)=>{const following=points[(index+1)%points.length];next.push([point[0]*.75+following[0]*.25,point[1]*.75+following[1]*.25],[point[0]*.25+following[0]*.75,point[1]*.25+following[1]*.75]);});points=next;}
  handOutline=points.map(point=>[point[0]/1.35,point[1]/1.35]);
  $('hand-icon').setAttribute('d','M'+data.outline.map(point=>point.join(',')).join('L')+'Z');
}
fetch('/hand.json').then(response=>{if(!response.ok)throw new Error('hand asset');return response.json();}).then(installHand).catch(()=>notice('手形データを読み込めませんでした。画面を再読み込みしてください。'));
function drawHands(w,h,now){
  if(!handData)return;
  const cycle=now/1000*settings.speed*Math.PI*2,radius=Math.min(w,h)*.34*settings.scale/20,dpr=Math.min(window.devicePixelRatio||1,2);
  for(let index=0;index<settings.density;index++){
    const base=handData.centres[index],cx=base[0]+.045*Math.sin(cycle*.73+index*1.7),cy=base[1]+.045*Math.cos(cycle*.57+index*2.1);
    const angle=-.20+.20*Math.sin(cycle*.43+index*1.3),size=.27+.025*Math.sin(cycle*.61+index*.9),c=Math.cos(angle),s=Math.sin(angle),mirror=index%2?-1:1;
    ctx.beginPath();handOutline.forEach((point,i)=>{const x=point[0]*mirror,y=point[1],px=w/2+(cx+(x*c-y*s)*size)*radius,py=h/2+(cy+(x*s+y*c)*size)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.closePath();
    const visibility=.15+.85*((1+Math.sin(cycle*.23+index*1.9))/2)**1.6;
    ctx.strokeStyle=settings.colour;ctx.globalAlpha=(.35+.65*settings.brightness/5)*visibility;ctx.shadowColor=settings.colour;ctx.shadowBlur=14*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=3*dpr;ctx.lineWidth=.7*dpr;ctx.stroke();ctx.globalAlpha=1;ctx.shadowBlur=0;
  }
}
function draw(now){
  const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
  if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);}
  const w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#ffffff07';ctx.lineWidth=dpr;
  for(let x=w/2% (32*dpr);x<w;x+=32*dpr){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}
  for(let y=h/2% (32*dpr);y<h;y+=32*dpr){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  if(settings.shape==='hands'){drawHands(w,h,now);requestAnimationFrame(draw);return;}
  const phase=now/1000*settings.speed*Math.PI*2,c=Math.cos(phase),s=Math.sin(phase),radius=Math.min(w,h)*.34*settings.scale/20;
  ctx.beginPath();
  for(let i=0;i<=300;i++){
    const t=i/300*Math.PI*2;let x=Math.cos(t),y=Math.sin(t);
    if(settings.shape==='eight'){x=Math.sin(t);y=Math.sin(t*2)*.65;}
    if(settings.shape==='triangle'){const vs=[[0,-1],[.866,.5],[-.866,.5]],edge=Math.min(Math.floor(i/100),2),f=i/100-edge,a=vs[edge],b=vs[(edge+1)%3];x=a[0]+(b[0]-a[0])*f;y=a[1]+(b[1]-a[1])*f;}
    const px=w/2+(x*c-y*s)*radius,py=h/2+(x*s+y*c)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);
  }
  ctx.strokeStyle=settings.colour;ctx.globalAlpha=.3+.7*settings.brightness/5;ctx.shadowColor=settings.colour;ctx.shadowBlur=16*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=3*dpr;ctx.lineWidth=.75*dpr;ctx.stroke();ctx.globalAlpha=1;ctx.shadowBlur=0;
  requestAnimationFrame(draw);
}
showBindings();updateControls();sync();setInterval(sync,900);requestAnimationFrame(draw);
