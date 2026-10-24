// Offline logic test for MIDI learning. Runs with macOS JavaScript for Automation.
ObjC.import('Foundation');
var projectRoot=ObjC.unwrap($.NSFileManager.defaultManager.currentDirectoryPath);
var source = $.NSString.stringWithContentsOfFileEncodingError(projectRoot+'/static/app.js', $.NSUTF8StringEncoding, null).js;
var elements = {}, storage = {}, clicks = {};
var drawing = {strokes:0,beginPath:function(){},moveTo:function(){},lineTo:function(){},closePath:function(){},stroke:function(){this.strokes++;}};
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
  source + '\nreturn {onMidi, installHand, drawHands, read:()=>({settings,bindings}), setMidi:value=>{midi=value;}};');
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
'MIDI and hand preview: '+assertions+' assertions passed';
