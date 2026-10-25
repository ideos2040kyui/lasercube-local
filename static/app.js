'use strict';
const $ = id => document.getElementById(id);
const labels = {density:'わらわら度',brightness:'明るさ',scale:'大きさ',speed:'動く速さ',next:'図形切替',start:'開始',stop:'停止'};
for(let i=0;i<2;i++)for(const [key,label]of Object.entries({brightness:'明るさ',scale:'大きさ',speed:'速度',density:'手の数',x:'横位置',y:'縦位置',enabled:'表示切替'}))labels['layer'+i+'_'+key]='レイヤー'+['A','B'][i]+'：'+label;
Object.assign(labels,{gauge_value:'どきどきメーター：線の長さ',gauge_brightness:'どきどきメーター：明るさ',gauge_enabled:'どきどきメーター：表示切替'});
let state = null, token = '', learning = null, midi = null, selectedInput = '', timer = null;
let settings = {gauge:{enabled:false,value:50,brightness:100,reactive:false},shape:'circle',brightness:1,scale:12,speed:0.15,colour:'#57e5dc',duration:0,density:4,layers:[{enabled:true,shape:'hands',brightness:100,scale:100,speed:.15,density:3,colour:'#ed1c24',x:0,y:0},{enabled:true,shape:'svg',brightness:100,scale:100,speed:1,density:3,colour:'#ed1c24',x:0,y:0}]};
let handData=null,handOutline=[],gaugeData=null,horrorData=null;
let svgAsset=null, svgLoading='', svgSyncNow=0;
let layerAssets=[null,null], layerLoading=['',''];
let effectAssets={blood:null,fan:null},effectLoading='';
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
  const gauge=settings.gauge||{enabled:false,value:50,brightness:100};$('gauge-enabled').checked=gauge.enabled;$('gauge-value').value=gauge.value;$('gauge-value-label').textContent=Math.round(gauge.value)+'%';$('gauge-brightness').value=gauge.brightness;$('gauge-brightness-label').textContent=Math.round(gauge.brightness)+'%';
  $('gauge-reactive').checked=!!gauge.reactive;
  const plan=horrorPlan(gauge.value);$('horror-status').textContent=gauge.reactive?((plan.hearts?'ピンクのハートだけ：'+plan.hearts+'個':'手形 '+plan.hands+'体 ／ 周囲の血糊 '+plan.small_blood+'個 ／ 中央の血糊 '+plan.large_blood+'個')+(plan.fans?' ／ 中央下の扇子 '+plan.fans+'個（0%限定）':'')):'メーター連動OFF：手動レイヤーを表示';
  $('density-value').textContent=Math.round(settings.density)+'体';
  $('brightness-value').textContent=Number(settings.brightness).toFixed(1)+'%';
  $('scale-value').textContent=Math.round(settings.scale)+'%';
  const animatedSVG=settings.shape==='svg'&&state?.svg?.duration;
  $('brightness-caption').textContent=settings.shape==='layers'?'全体の明るさ':'明るさ';
  $('scale-caption').textContent=settings.shape==='layers'?'全体の大きさ':'大きさ';
  $('colour').disabled=settings.shape==='layers';$('density').disabled=settings.shape==='layers';
  $('speed-caption').textContent=settings.shape==='layers'?'全体の再生速度':animatedSVG?'再生速度':settings.shape==='hands'?'うごめく速さ':'回転速度';
  $('speed-value').textContent=Number(settings.speed).toFixed(2)+(animatedSVG||settings.shape==='layers'?' 倍':settings.shape==='hands'?'':' 回転/秒');
  document.querySelectorAll('[data-shape]').forEach(button=>button.classList.toggle('selected',button.dataset.shape===settings.shape));
  $('preview-label').textContent={circle:'CIRCLE',eight:'FIGURE EIGHT',triangle:'TRIANGLE',hands:'RED HANDS / '+settings.density,svg:'SVG / '+(state?.svg?.name||''),layers:'LAYER A + LAYER B'}[settings.shape];
  if(gauge.reactive)$('preview-label').textContent=plan.hearts?'PINK HEARTS':plan.large_blood?'HORROR / CRITICAL':'HORROR / '+plan.hands+' HANDS';
  document.querySelectorAll('[data-layer][data-field]').forEach(input=>{const layer=settings.layers?.[Number(input.dataset.layer)];if(!layer)return;const key=input.dataset.field;if(key==='enabled')input.checked=layer[key];else input.value=layer[key];});
  for(let i=0;i<2;i++)if(settings.layers?.[i])for(const key of ['brightness','scale','speed','density','x','y']){const v=settings.layers[i][key];$('layer-'+i+'-'+key+'-value').textContent=key==='speed'?Number(v).toFixed(2):Math.round(v)+(key==='density'?'体':'%');}

}
let paramsTimer;
function change(values) { if(values.shape==='layers'&&settings.shape!=='layers'&&!('speed'in values))values.speed=1;if(values.shape==='hands'&&!('colour'in values))values.colour='#ed1c24';Object.assign(settings,values); updateControls(); clearTimeout(paramsTimer); paramsTimer=setTimeout(()=>act('/api/params',settings),50); }
function changeLayer(index,values){const layers=settings.layers.map((layer,i)=>i===index?{...layer,...values}:{...layer});change({layers});}
function render() {
  if(!state) return;
  svgSyncNow=performance.now();
  $('svg-preset').disabled=!state.svg;
  $('svg-info').textContent=state.svg?state.svg.name+' · '+state.svg.frame_count+'コマ'+(state.svg.duration?' / '+state.svg.duration.toFixed(2)+'秒':' / 静止画'): 'SVGを選ぶと輪郭のプレビューに切り替わります。';
  $('svg-notes').textContent=state.svg?state.svg.warnings.join(' '):'文字はパス化してください。画像・グラデーション・一般的なCSS/SMILアニメーションには未対応です。';
  if(state.svg&&state.svg.revision!==svgAsset?.revision&&svgLoading!==state.svg.revision){
    const revision=state.svg.revision;svgLoading=revision;
    fetch('/api/svg',{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('SVGの読み込みに失敗しました');return response.json();}).then(asset=>{if(asset?.revision===state?.svg?.revision)svgAsset=asset;}).catch(error=>notice(error.message)).finally(()=>{svgLoading='';});
  }

  for(let i=0;i<2;i++){
    const info=state.layer_svgs?.[i];$('layer-'+i+'-file-info').textContent=info?info.name+' · '+info.frame_count+'コマ':'SVG未読込';
    if(info&&info.revision!==layerAssets[i]?.revision&&layerLoading[i]!==info.revision){
      layerLoading[i]=info.revision;
      fetch('/api/svg?layer='+i,{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('レイヤーSVGの読み込みに失敗しました');return response.json();}).then(asset=>{if(asset?.revision===state?.layer_svgs?.[i]?.revision)layerAssets[i]=asset;}).catch(error=>notice(error.message)).finally(()=>{layerLoading[i]='';});
    }
  }
  const effectKey=['blood','fan'].map(role=>state.effects?.[role]?.revision||'').join(':');
  const loadedKey=['blood','fan'].map(role=>effectAssets[role]?.revision||'').join(':');
  $('effect-file-info').textContent='血糊：'+(state.effects?.blood?.name||'レイヤーBのSVG')+' ／ 扇子：'+(state.effects?.fan?.name||'GIF未設定');
  if(effectKey!==':'&&effectKey!==loadedKey&&effectLoading!==effectKey){effectLoading=effectKey;fetch('/api/effects',{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error('GIF輪郭データを読み込めませんでした');return response.json();}).then(assets=>{effectAssets=assets;}).catch(error=>notice(error.message)).finally(()=>{effectLoading='';});}
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
$('svg-load').onclick=()=>$('svg-file').click();
$('svg-file').onchange=async()=>{
  const file=$('svg-file').files?.[0];if(!file)return;
  if(file.size>2*1024*1024){notice('SVGは2MB以内のファイルを選んでください');$('svg-file').value='';return;}
  $('svg-load').disabled=true;clearTimeout(paramsTimer);
  try{
    if(!token)throw new Error('サーバーへの接続を待ってから読み込んでください');
    const result=await api('/api/svg',{source:await file.text(),name:file.name,ignore_pale:$('svg-ignore-pale').checked});
    settings={...result.params};updateControls();notice('SVGを読み込みました。プレビューを確認してから開始してください。');
  }catch(error){notice(error.message);}finally{$('svg-load').disabled=false;$('svg-file').value='';}
};
document.querySelectorAll('[data-layer][data-field]').forEach(input=>input.addEventListener('input',()=>{const key=input.dataset.field,value=key==='enabled'?input.checked:['shape','colour'].includes(key)?input.value:Number(input.value);changeLayer(Number(input.dataset.layer),{[key]:value});}));
for(let i=0;i<2;i++){
  $('layer-'+i+'-load').onclick=()=>$('layer-'+i+'-file').click();
  $('layer-'+i+'-file').onchange=async()=>{
    const input=$('layer-'+i+'-file'),file=input.files?.[0];if(!file)return;
    if(file.size>2*1024*1024){notice('SVGは2MB以内のファイルを選んでください');input.value='';return;}
    $('layer-'+i+'-load').disabled=true;clearTimeout(paramsTimer);
    try{if(!token)throw new Error('サーバーへの接続を待ってください');const result=await api('/api/svg',{source:await file.text(),name:file.name,ignore_pale:true,layer:i});settings={...result.params};updateControls();notice('レイヤー'+['A','B'][i]+'にSVGを読み込みました。');}
    catch(error){notice(error.message);}finally{$('layer-'+i+'-load').disabled=false;input.value='';}
  };
}
$('gauge-reactive').onchange=()=>change({gauge:{...settings.gauge,reactive:$('gauge-reactive').checked,enabled:true}});
$('gauge-enabled').onchange=()=>change({gauge:{...settings.gauge,enabled:$('gauge-enabled').checked}});
for(const key of ['value','brightness'])$('gauge-'+key).addEventListener('input',()=>change({gauge:{...settings.gauge,[key]:Number($('gauge-'+key).value),enabled:true}}));
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
    const target=/^layer([01])_(brightness|scale|speed|density|x|y|enabled)$/.exec(action);
    if(action.startsWith('gauge_')){const key=action.slice(6);if(key==='enabled'){if(kind===0xb0)change({gauge:{...settings.gauge,enabled:value>=64}});else if(value>0)change({gauge:{...settings.gauge,enabled:!settings.gauge.enabled}});}else if(['value','brightness'].includes(key))change({gauge:{...settings.gauge,[key]:value/127*100}});}
    else if(target){const i=Number(target[1]),key=target[2];if(key==='enabled'){if(kind===0xb0)changeLayer(i,{enabled:value>=64});else if(value>0)changeLayer(i,{enabled:!settings.layers[i].enabled});}else{const [min,max]={brightness:[0,100],scale:[10,100],speed:[0,1],density:[1,8],x:[-100,100],y:[-100,100]}[key];let mapped=min+(max-min)*value/127;if(key==='density')mapped=Math.round(mapped);changeLayer(i,{[key]:mapped});}}
    else if(['brightness','scale','speed','density'].includes(action)){const ranges={brightness:[0,5],scale:[5,30],speed:[0,1],density:[1,8]},[min,max]=ranges[action];const mapped=min+(max-min)*value/127;change({[action]:action==='density'?Math.round(mapped):mapped});}
    else if(value>0&&action==='next'){const shapes=['circle','eight','triangle','hands','layers',...(state?.svg?['svg']:[])];change({shape:shapes[(shapes.indexOf(settings.shape)+1)%shapes.length]});}
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
function installGauge(data){gaugeData=data;}
function installHorror(data){horrorData=data;}
fetch('/horror-scene.json').then(response=>{if(!response.ok)throw new Error('連動演出データを読み込めませんでした');return response.json();}).then(installHorror).catch(error=>notice(error.message));
fetch('/gauge.json').then(response=>{if(!response.ok)throw new Error('メーターデータを読み込めませんでした');return response.json();}).then(installGauge).catch(error=>notice(error.message));
fetch('/hand.json').then(response=>{if(!response.ok)throw new Error('hand asset');return response.json();}).then(installHand).catch(()=>notice('手形データを読み込めませんでした。画面を再読み込みしてください。'));
function drawHands(w,h,now,params=settings){
  if(!handData)return;
  const cycle=now/1000*params.speed*Math.PI*2,radius=Math.min(w,h)*.34*params.scale/20,dpr=Math.min(window.devicePixelRatio||1,2);
  for(let index=0;index<params.density;index++){
    const base=(params._hand_centres||handData.centres)[index],cx=base[0]+.045*Math.sin(cycle*.73+index*1.7),cy=base[1]+.045*Math.cos(cycle*.57+index*2.1);
    const angle=-.20+.20*Math.sin(cycle*.43+index*1.3),size=(params._hand_size??.27)+.025*Math.sin(cycle*.61+index*.9),c=Math.cos(angle),s=Math.sin(angle),mirror=index%2?-1:1;
    ctx.beginPath();handOutline.forEach((point,i)=>{const x=point[0]*mirror,y=point[1],px=w/2+(cx+(x*c-y*s)*size)*radius,py=h/2+(cy+(x*s+y*c)*size)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.closePath();
    const floor=params._hand_visibility_floor??.15,visibility=floor+(1-floor)*((1+Math.sin(cycle*.23+index*1.9))/2)**1.6;
    ctx.strokeStyle=params.colour;ctx.globalAlpha=(params.brightness/5)*visibility;ctx.shadowColor=params.colour;ctx.shadowBlur=14*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=3*dpr;ctx.lineWidth=.7*dpr;ctx.stroke();ctx.globalAlpha=1;ctx.shadowBlur=0;
  }
}
function drawSVG(w,h,now,params=settings,asset=svgAsset,info=state?.svg,seconds=null){
  if(!asset||asset.revision!==info?.revision)return;
  const elapsed=seconds??(((state.svg_elapsed||0)+Math.max(0,now-svgSyncNow)/1000)*params.speed);
  let index=0;
  if(asset.duration){const fraction=(elapsed/asset.duration)%1;asset.starts.forEach((start,i)=>{if(fraction>=start)index=i;});}
  const angle=asset.duration?0:elapsed*Math.PI*2,c=Math.cos(angle),s=Math.sin(angle),radius=Math.min(w,h)*.34*params.scale/20,dpr=Math.min(window.devicePixelRatio||1,2);
  for(const path of asset.frames[index].slice(0,asset._contour_limit??asset.frames[index].length)){
    ctx.beginPath();path.forEach(([x,y],i)=>{const px=w/2+(x*c-y*s)*radius,py=h/2+(x*s+y*c)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});
    ctx.strokeStyle=params.colour;ctx.shadowColor=params.colour;ctx.globalAlpha=params.brightness/5*(asset.frame_opacity?.[index]??1);ctx.shadowBlur=12*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=2*dpr;ctx.lineWidth=.7*dpr;ctx.stroke();
  }
  ctx.globalAlpha=1;ctx.shadowBlur=0;
}
function drawBasic(w,h,now,params=settings){
  const dpr=Math.min(window.devicePixelRatio||1,2);
  const phase=now/1000*params.speed*Math.PI*2,c=Math.cos(phase),s=Math.sin(phase),radius=Math.min(w,h)*.34*params.scale/20;
  ctx.beginPath();
  for(let i=0;i<=300;i++){
    const t=i/300*Math.PI*2;let x=Math.cos(t),y=Math.sin(t);
    if(params.shape==='eight'){x=Math.sin(t);y=Math.sin(t*2)*.65;}
    if(params.shape==='triangle'){const vs=[[0,-1],[.866,.5],[-.866,.5]],edge=Math.min(Math.floor(i/100),2),f=i/100-edge,a=vs[edge],b=vs[(edge+1)%3];x=a[0]+(b[0]-a[0])*f;y=a[1]+(b[1]-a[1])*f;}
    const px=w/2+(x*c-y*s)*radius,py=h/2+(x*s+y*c)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);
  }
  ctx.strokeStyle=params.colour;ctx.globalAlpha=params.brightness/5;ctx.shadowColor=params.colour;ctx.shadowBlur=16*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=3*dpr;ctx.lineWidth=.75*dpr;ctx.stroke();ctx.globalAlpha=1;ctx.shadowBlur=0;
}
function drawLayers(w,h,now){
  const seconds=((state?.svg_elapsed||0)+Math.max(0,now-svgSyncNow)/1000)*settings.speed;
  const radius=Math.min(w,h)*.34*settings.scale/20;
  for(let i=0;i<2;i++){
    const layer=settings.layers?.[i];if(!layer?.enabled||!layer.brightness||!settings.brightness)continue;
    const params={...settings,...layer,brightness:settings.brightness*layer.brightness/100,scale:settings.scale*layer.scale/100};
    ctx.save();ctx.translate(radius*layer.x/100,radius*layer.y/100);
    if(layer.shape==='hands')drawHands(w,h,seconds*1000,params);
    else if(layer.shape==='svg')drawSVG(w,h,now,params,layerAssets[i],state?.layer_svgs?.[i],seconds*layer.speed);
    else drawBasic(w,h,seconds*1000,params);
    ctx.restore();
  }
}
function horrorPlan(value){
  const threshold=horrorData?.horror_at??90,centralBelow=horrorData?.central_below??50;
  if(value>=threshold){const progress=Math.min(1,Math.max(0,(value-threshold)/((horrorData?.heart_fade_from??95)-threshold))),floor=horrorData?.heart_min_opacity??.1;return {stage:'hearts',severity:0,hands:0,small_blood:0,large_blood:0,hearts:horrorData?.heart_centres.length??5,fans:0,heart_opacity:floor+(1-floor)*progress};}
  const severity=(threshold-value)/threshold,central=Math.max(0,(centralBelow-value)/centralBelow);
  return {stage:value>=centralBelow?'horror':'critical',severity,central,hands:1+Math.floor(((horrorData?.max_hands??24)-1)*severity**1.5),small_blood:1+Math.floor(((horrorData?.blood_centres.length??8)-1)*severity),large_blood:value<centralBelow?1:0,hearts:0,fans:value<=0?(horrorData?.fan_centres?.length??3):0};
}
function drawHorror(w,h,now){
  if(!horrorData)return;
  const elapsed=((state?.svg_elapsed||0)+Math.max(0,now-svgSyncNow)/1000)*settings.speed;
  const plan=horrorPlan(settings.gauge.value),radius=Math.min(w,h)*.34*settings.scale/20,dpr=Math.min(window.devicePixelRatio||1,2);
  if(plan.hearts){
    const cycle=elapsed*Math.PI*2;
    horrorData.heart_centres.forEach(([x,y],index)=>{
      const cx=x+.02*Math.cos(cycle*.11+index),cy=y+.035*Math.sin(cycle*.09+index*1.8),size=.11+.012*Math.sin(cycle*.07+index),angle=.12*Math.sin(cycle*.08+index),c=Math.cos(angle),s=Math.sin(angle);
      ctx.beginPath();horrorData.heart_outline.forEach(([px,py],i)=>{const dx=w/2+(cx+(px*c-py*s)*size)*radius,dy=h/2+(cy+(px*s+py*c)*size)*radius;i?ctx.lineTo(dx,dy):ctx.moveTo(dx,dy);});
      ctx.strokeStyle='#ff69b4';ctx.shadowColor='#ff69b4';ctx.globalAlpha=settings.brightness/5*plan.heart_opacity;ctx.shadowBlur=12*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=2*dpr;ctx.lineWidth=.7*dpr;ctx.stroke();
    });
    ctx.globalAlpha=1;ctx.shadowBlur=0;return;
  }
  const severity=plan.severity;
  drawHands(w,h,elapsed*1000,{...settings,shape:'hands',colour:'#ed1c24',density:plan.hands,speed:.1+.65*severity,brightness:settings.brightness*(.35+.65*severity),_hand_centres:horrorData.hand_centres,_hand_size:.29-.12*severity,_hand_visibility_floor:.35+.3*severity});
  drawSceneFans(w,h,now,elapsed,plan,radius);
  const asset=effectAssets.blood||layerAssets[1]||svgAsset,info=state?.effects?.blood||state?.layer_svgs?.[1]||state?.svg;if(!asset||!asset.frames.length)return;
  const instances=[];
  for(let i=0;i<plan.small_blood;i++){const [x,y]=horrorData.blood_centres[i];instances.push({x,y,scale:.14+.10*severity,brightness:.25+.65*severity,contours:1,phase:i*.19});}
  for(let i=0;i<plan.large_blood;i++)instances.push({x:0,y:plan.fans?-.25:0,scale:(.72+.28*plan.central)*(plan.fans ? .8 : 1),brightness:.45+.55*plan.central,contours:2,phase:0});
  for(const [index,item] of instances.entries()){
    const animated=asset.kind==='gif';
    const still=animated?{...asset,_contour_limit:item.contours}:{...asset,frames:[asset.frames[asset.frames.length-1].slice(0,item.contours)],duration:0,starts:[]};
    ctx.save();ctx.translate(item.x*radius,item.y*radius);
    drawSVG(w,h,now,{...settings,shape:'svg',colour:'#ed1c24',scale:settings.scale*item.scale,brightness:settings.brightness*item.brightness,speed:0},still,info,animated?elapsed+item.phase:0);
    ctx.restore();
  }
}
function drawSceneFans(w,h,now,elapsed,plan,radius){
  if(!plan.fans||!effectAssets.fan)return;
  const fan=effectAssets.fan,info=state?.effects?.fan,fanTime=((state?.fan_elapsed??state?.svg_elapsed??0)+Math.max(0,now-svgSyncNow)/1000)*settings.speed;
  horrorData.fan_centres.forEach(([x,y,size],index)=>{const age=fanTime-index*.12;if(age<0)return;const asset={...fan,_contour_limit:6};ctx.save();ctx.translate((x+.025*Math.sin(elapsed*.5+index))*radius,(y+.035*Math.cos(elapsed*.4+index))*radius);drawSVG(w,h,now,{...settings,colour:'#ed1c24',scale:settings.scale*size,brightness:settings.brightness*Math.min(1,age/.3),speed:0},asset,info,age);ctx.restore();});
}
function gaugePaths(value){
  if(!gaugeData)return [];
  const [cx,cy]=gaugeData.centre,[sx,sy]=gaugeData.size,scale=path=>path.map(([x,y])=>[cx+x*sx,cy+y*sy]);
  const paths=[scale(gaugeData.outline)];if(value<=0)return paths;
  const full=scale(gaugeData.fill_path);if(value>=100){paths.push(full);return paths;}
  const lengths=full.slice(1).map((b,i)=>Math.hypot(b[0]-full[i][0],b[1]-full[i][1]));
  let remaining=lengths.reduce((a,b)=>a+b,0)*value/100;const shortened=[full[0]];
  for(let i=0;i<lengths.length;i++){const a=full[i],b=full[i+1],length=lengths[i];if(remaining>=length){shortened.push(b);remaining-=length;}else{if(remaining>1e-9){const t=remaining/Math.max(length,1e-12);shortened.push([a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]);}break;}}
  paths.push(shortened);return paths;
}
function drawGauge(w,h){
  const gauge=settings.gauge;if(!gaugeData||!gauge?.enabled||!gauge.brightness||!settings.brightness)return;
  const factor=Math.min(w,h)*.34/(.2*2047),dpr=Math.min(window.devicePixelRatio||1,2);
  const colour=settings.gauge.reactive&&horrorPlan(settings.gauge.value).hearts?'#ff69b4':'#ed1c24';
  for(const path of gaugePaths(gauge.value)){ctx.beginPath();path.forEach(([x,y],i)=>{const px=w/2+x*factor,py=h/2+y*factor;i?ctx.lineTo(px,py):ctx.moveTo(px,py);});ctx.strokeStyle=colour;ctx.shadowColor=colour;ctx.globalAlpha=settings.brightness/5*gauge.brightness/100;ctx.shadowBlur=10*dpr;ctx.lineWidth=2*dpr;ctx.stroke();ctx.shadowBlur=2*dpr;ctx.lineWidth=.7*dpr;ctx.stroke();}
  ctx.globalAlpha=1;ctx.shadowBlur=0;
}
function draw(now){
  const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
  if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);}
  const w=canvas.width,h=canvas.height;ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#ffffff07';ctx.lineWidth=dpr;
  for(let x=w/2% (32*dpr);x<w;x+=32*dpr){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}
  for(let y=h/2% (32*dpr);y<h;y+=32*dpr){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  if(settings.gauge?.reactive)drawHorror(w,h,now);
  else if(settings.shape==='layers')drawLayers(w,h,now);
  else if(settings.shape==='hands')drawHands(w,h,now);
  else if(settings.shape==='svg')drawSVG(w,h,now);
  else drawBasic(w,h,now);
  drawGauge(w,h);
  requestAnimationFrame(draw);
}
showBindings();updateControls();sync();setInterval(sync,900);requestAnimationFrame(draw);
