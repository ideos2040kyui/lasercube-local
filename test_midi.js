// Offline logic test for MIDI learning. Runs with macOS JavaScript for Automation.
ObjC.import('Foundation');
var projectRoot=ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
var source = $.NSString.stringWithContentsOfFileEncodingError(projectRoot+'/static/app.js', $.NSUTF8StringEncoding, null).js;
var elements = {}, storage = {}, clicks = {};
var drawing = {strokeAlphas:[],strokeColours:[],moves:[],strokes:0,save:function(){},restore:function(){},translate:function(){},beginPath:function(){},moveTo:function(x,y){this.moves.push([x,y]);},lineTo:function(){},closePath:function(){},stroke:function(){this.strokes++;this.strokeColours.push(this.strokeStyle);this.strokeAlphas.push(this.globalAlpha);}};
function element(id) {
  if (!elements[id]) elements[id] = {
    value: id === 'learn-action' ? 'brightness' : '',
    hidden: false, disabled: false, textContent: '', dataset: {},
    classList: {add: function(){}, remove: function(){}, toggle: function(){}},
    addEventListener: function(){}, replaceChildren: function(){}, append: function(){},
    getContext: function(){return drawing;},setAttribute:function(name,value){this[name]=value;},
    click: function(){clicks[id] = (clicks[id] || 0) + 1;}
  };
  return elements[id];
}
var documentStub = {
  getElementById: element, querySelectorAll: function(){return [];},
  createElement: function(){return element('generated-'+Math.random());},
  createTextNode: function(text){return text;}
};
var build = new Function('document','navigator','localStorage','fetch','requestAnimationFrame','setInterval','setTimeout','clearTimeout','window',
  source + '\nreturn {onMidi, installHand, drawHands, drawSVG, drawLayers, drawGauge, gaugePaths, installGauge, installHorror, horrorPlan, drawHorror, changeLayer, updateControls, read:()=>({settings,bindings}), setSVG:(asset,session)=>{svgAsset=asset;state=session;layerAssets=[asset,asset];}, setEffects:assets=>{effectAssets=assets;state={...state,effects:{blood:assets.blood,fan:assets.fan},fan_elapsed:2};}, setMidi:value=>{midi=value;}};');
var app = build(documentStub, {}, {
  getItem: function(key){return storage[key] || null;},
  setItem: function(key,value){storage[key]=value;}
}, function(){return new Promise(function(){});}, function(){}, function(){}, function(){}, function(){}, {addEventListener:function(){}});
var assertions=0;
function assert(value, message) { assertions++;if (!value) throw new Error(message); }
app.setMidi({});
element('learn').onclick();
app.onMidi({data:[0xB2,7,64]});
assert(app.read().bindings.brightness.channel === 2, 'MIDI learn must retain channel');
assert(app.read().bindings.brightness.index === 7, 'MIDI learn must retain CC');
app.onMidi({data:[0xB2,7,127]});
assert(app.read().settings.brightness === 5, 'Fader maximum must map to 5 percent');
app.onMidi({data:[0xB1,7,0]});
assert(app.read().settings.brightness === 5, 'Other channels must not trigger binding');
app.onMidi({data:[0xB2,7,0]});
assert(app.read().settings.brightness === 0, 'Fader minimum must map to zero');
element('learn-action').value='stop';element('learn').onclick();
app.onMidi({data:[0x90,53,127]});
assert(!clicks.stop, 'Learning a stop key must not execute the action');
app.onMidi({data:[0x90,53,0]});
assert(!clicks.stop, 'Note-off encoded as zero velocity must be ignored');
app.onMidi({data:[0x90,53,127]});
assert(clicks.stop === 1, 'Learned note must invoke stop exactly once');
assert(JSON.parse(storage['lasercube-midi-bindings']).stop.index === 53, 'Bindings must persist');
element('learn-action').value='density';element('learn').onclick();app.onMidi({data:[0xB0,7,64]});
app.onMidi({data:[0xB0,7,0]});assert(app.read().settings.density===1,'Density minimum must be one hand');
app.onMidi({data:[0xB0,7,127]});assert(app.read().settings.density===8,'Density maximum must be eight hands');
var hand=JSON.parse($.NSString.stringWithContentsOfFileEncodingError(projectRoot+'/static/hand.json',$.NSUTF8StringEncoding,null).js);
app.installHand(hand);app.read().settings.shape='hands';app.drawHands(800,600,1000);
assert(drawing.strokes===16,'Eight hands must each be rendered with two strokes');
assert(element('hand-icon').d.startsWith('M'),'Hand button must use the shared hand outline');
app.setSVG({revision:'test',duration:2,starts:[0,.25],frames:[[[[0,0],[1,0]]],[[[0,1],[1,1]]]]},{svg:{revision:'test',name:'test.svg',duration:2},svg_elapsed:0});
app.read().settings.shape='svg';app.read().settings.speed=1;
var before=drawing.strokes;app.drawSVG(800,600,1000);
assert(drawing.strokes===before+2,'SVG outlines must each be drawn separately');
app.updateControls();assert(element('speed-caption').textContent==='再生速度','Animated SVG speed must be labelled as playback speed');
assert(element('preview-label').textContent==='SVG / test.svg','SVG name must be rendered as plain text');
var beforeB=JSON.stringify(app.read().settings.layers[1]);
element('learn-action').value='layer0_brightness';element('learn').onclick();app.onMidi({data:[0xB0,18,64]});
app.onMidi({data:[0xB0,18,0]});assert(app.read().settings.layers[0].brightness===0,'Layer A fader must fade only A');
assert(JSON.stringify(app.read().settings.layers[1])===beforeB,'Layer B settings must remain independent');
app.onMidi({data:[0xB0,18,127]});assert(app.read().settings.layers[0].brightness===100,'Layer fader maximum is 100 percent');
element('learn-action').value='gauge_value';element('learn').onclick();app.onMidi({data:[0xB0,19,64]});
app.onMidi({data:[0xB0,19,127]});assert(app.read().settings.gauge.value===100,'Meter fader maximum must fill the gauge');
app.onMidi({data:[0xB0,19,0]});assert(app.read().settings.gauge.value===0,'Meter fader minimum must empty the gauge');
app.installGauge(JSON.parse($.NSString.stringWithContentsOfFileEncodingError(projectRoot+'/static/gauge.json',$.NSUTF8StringEncoding,null).js));
var fullSerpent=app.gaugePaths(100)[1];
function lineLength(path){return path.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p[0]-path[i][0],p[1]-path[i][1]),0);}
for(var fraction of [13,37,62,94]){
 var shortSerpent=app.gaugePaths(fraction)[1];
 assert(JSON.stringify(shortSerpent.slice(0,-1))===JSON.stringify(fullSerpent.slice(0,shortSerpent.length-1)),'Shortened meter must keep the same fixed bends');
 assert(Math.abs(lineLength(shortSerpent)/lineLength(fullSerpent)-fraction/100)<1e-9,'Slider must change the length of the line');
}
app.read().settings.gauge={enabled:true,value:0,brightness:100};app.read().settings.brightness=5;
drawing.moves=[];before=drawing.strokes;app.drawGauge(800,600);
assert(drawing.strokes===before+2,'Empty meter must keep its outline visible');
var meterPosition=JSON.stringify(drawing.moves);assert(drawing.moves.every(p=>p[0]>400&&p[1]<300),'Meter must be drawn in the upper right');
app.read().settings.scale=5;app.read().settings.speed=0;drawing.moves=[];app.drawGauge(800,600);
assert(JSON.stringify(drawing.moves)===meterPosition,'Main scale and speed must not move or resize the meter');
app.read().settings.gauge.value=100;before=drawing.strokes;app.drawGauge(800,600);
assert(drawing.strokes===before+4,'Full meter must draw both outline and fill');
app.read().settings.gauge.enabled=false;before=drawing.strokes;app.drawGauge(800,600);
assert(drawing.strokes===before,'Hidden meter must not draw');
app.installHorror(JSON.parse($.NSString.stringWithContentsOfFileEncodingError(projectRoot+'/static/horror-scene.json',$.NSUTF8StringEncoding,null).js));
assert(app.horrorPlan(90.0001).hearts===5,'Above 90 percent must show only five hearts');
assert(app.horrorPlan(90).hearts===5&&app.horrorPlan(90).hands===0,'Exactly 90 percent must show only hearts');
assert(app.horrorPlan(50).large_blood===0,'Exactly 50 percent must not show central blood');
assert(app.horrorPlan(49.999).large_blood===1,'Below 50 percent must show central blood');
assert(app.horrorPlan(0).hands===24&&app.horrorPlan(0).large_blood===1&&app.horrorPlan(0).small_blood===8,'Zero percent must be maximally crowded');
app.read().settings.gauge={enabled:true,reactive:true,value:100,brightness:100};
app.read().settings.scale=30;app.read().settings.speed=1;before=drawing.strokes;drawing.strokeColours=[];
app.drawHorror(800,600,1000);
assert(drawing.strokes===before+10,'Heart scene must draw only five hearts');
assert(drawing.strokeColours.every(c=>c==='#ff69b4'),'Heart scene must be pink without red hands or blood');
app.read().settings.gauge.value=89.999;before=drawing.strokes;drawing.strokeColours=[];app.drawHorror(800,600,1000);
assert(drawing.strokes===before+4,'Below 90 percent show one hand and one peripheral blood outline');
assert(drawing.strokeColours.every(c=>c==='#ed1c24'),'Horror must remove the pink hearts');
app.read().settings.gauge.value=0;before=drawing.strokes;app.drawHorror(800,600,1000);
assert(drawing.strokes===before+66,'At zero show 24 hands, eight small blood images, and one central image');
app.onMidi({data:[0xB0,19,127]});assert(app.read().settings.gauge.reactive&&app.horrorPlan(app.read().settings.gauge.value).hearts===5,'The existing meter fader must keep reactive mode and restore hearts');
app.onMidi({data:[0xB0,19,63]});assert(app.horrorPlan(app.read().settings.gauge.value).large_blood===1,'The existing meter fader must trigger central blood below 50 percent');
assert(app.horrorPlan(100).fans===0,'Full meter must not show a fan');
assert(app.horrorPlan(0).fans===3&&app.horrorPlan(.001).fans===0,'Three fans must appear only at exactly zero');
var gifFixture={revision:'gif-fixture',kind:'gif',duration:1,starts:[0],frames:[[[[0,0],[1,0],[0,1],[0,0]]]],frame_opacity:[1]};
var fanWithEyes={...gifFixture,frames:[[gifFixture.frames[0][0],gifFixture.frames[0][0],gifFixture.frames[0][0]]]};
app.setEffects({blood:gifFixture,fan:fanWithEyes});app.read().settings.gauge.value=100;before=drawing.strokes;
app.drawHorror(800,600,1000);assert(drawing.strokes===before+10,'At full meter draw only five hearts');
app.read().settings.gauge.value=99;before=drawing.strokes;app.drawHorror(800,600,1000);
assert(drawing.strokes===before+10,'Below full meter only the five hearts remain');
app.read().settings.gauge.value=0;before=drawing.strokes;app.drawHorror(800,600,1000);
assert(drawing.strokes===before+84,'At zero add three fans with an outline, eye and pupil each');
for(var meterLevel of [100,90,89.999]){
 app.read().settings.gauge.value=meterLevel;drawing.strokeColours=[];app.drawGauge(800,600);
 assert(drawing.strokeColours.every(c=>c===(meterLevel>=90?'#ff69b4':'#ed1c24')),'Meter colour must match the hearts or horror stage');
}
assert(app.horrorPlan(95).heart_opacity===1&&Math.abs(app.horrorPlan(92.5).heart_opacity-.55)<1e-9&&app.horrorPlan(90).heart_opacity===.1,'Hearts must fade gradually from 95 to 90 percent');
var fading={...gifFixture,frame_opacity:[.5]};before=drawing.strokeAlphas.length;
app.drawSVG(800,600,1000,{...app.read().settings,brightness:5},fading,{revision:'gif-fixture'},0);
assert(drawing.strokeAlphas[before]===.5,'GIF fading must change preview opacity');
'MIDI, layers, meter, horror and GIF preview: '+assertions+' assertions passed';
