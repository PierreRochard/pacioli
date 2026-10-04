const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const events = {}, replies = [], state = {tab:{id:1,windowId:2,active:true,url:'https://secure.chase.com/accounts'},
    observation:{kind:'form',token:'one',controls:[]}, captures:0, native:0, download:null};
  const event = name => ({addListener:callback => { events[name] = callback; }});
  const port = {onDisconnect:event('disconnect'),onMessage:event('command'),postMessage:r => replies.push(r)};
  const browser = {
    tabs:{onActivated:event('activated'),onUpdated:event('updated'),onRemoved:event('removed'),
      get:async()=>state.tab,
      executeScript:async(id,args)=> {
        if (args.file) return [];
        if (args.code.includes('["downloadSource"]')) return [{matched:state.domMatched===true}];
        return args.code.includes('["observe"]') ? [structuredClone(state.observation)] : [{acted:true}];
      },
      captureVisibleTab:async()=>{state.captures++;if(state.captureDenied)throw new Error('Permission denied');if(state.switchDuringCapture)events.activated();return 'data:image/jpeg;base64,eA==';}},
    downloads:{onCreated:event('download'),search:async()=>[state.download]},
    webRequest:{onHeadersReceived:event('headers')},
    windows:{create:async args=>{assert.equal(args.tabId,state.tab.id);assert.equal(args.focused,false);state.tab.active=true;state.moved=true;}},
    runtime:{connectNative:name=>{assert.equal(name,'lan.pacioli.chase');state.native++;return port;}},
    browserAction:{onClicked:event('clicked'),setBadgeText:()=>{}}
  };
  const context=vm.createContext({browser,URL,Date,Set,Error,JSON,Promise,
    Image:class{constructor(){this.width=100;this.height=100;}async decode(){}},
    document:{createElement:()=>({getContext:()=>({drawImage(){}}),toDataURL:()=> 'data:image/jpeg;base64,eA=='})}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../extensions/chase-local/background.js'),'utf8'),context);
  return {state,events,replies,connect(){events.clicked(state.tab);},
    async command(op,extra={}){await events.command({id:'test',op,...extra});return replies.at(-1);}};
}

test('toolbar refuses non-Chase pages and cannot connect a model to another tab',()=>{
  const f=fixture();f.events.clicked({...f.state.tab,url:'https://evil.example'});assert.equal(f.state.native,0);
  f.connect();assert.equal(f.state.native,1);
});
test('authentication screens are never captured',async()=>{
  const f=fixture();f.connect();f.state.observation.kind='authentication';
  const r=await f.command('observe');assert.equal(r.result.kind,'authentication');assert.equal(f.state.captures,0);
});
test('changing tabs during capture discards the screenshot',async()=>{
  const f=fixture();f.connect();f.state.switchDuringCapture=true;
  const r=await f.command('observe');assert.equal(r.error,'chase_tab_changed');assert.equal(r.result,undefined);
});
test('inactive or foreign tabs cannot be captured',async()=>{
  const f=fixture();f.connect();f.state.tab.active=false;
  assert.equal((await f.command('observe')).error,'chase_tab_not_active');assert.equal(f.state.captures,0);
  f.state.tab.active=true;f.state.tab.url='https://evil.example';
  assert.equal((await f.command('observe')).error,'chase_tab_not_active');assert.equal(f.state.captures,0);
});
test('sign-in origin change requires a fresh toolbar grant',async()=>{
  const f=fixture();f.connect();f.state.tab.url='https://www.chase.com/accounts';
  assert.equal((await f.command('observe')).error,'capture_permission_required');
  assert.equal(f.state.captures,0);
  f.connect();assert.equal((await f.command('observe')).result.kind,'form');
});
test('capture API denial gives an actionable permission error',async()=>{
  const f=fixture();f.connect();f.state.captureDenied=true;
  assert.equal((await f.command('observe')).error,'capture_permission_required');
});
test('concurrent downloads fail closed',async()=>{
  const f=fixture();f.connect();await f.command('act',{action:'download_qfx'});
  f.events.download({id:1,startTime:new Date().toISOString()});f.events.download({id:2,startTime:new Date().toISOString()});
  assert.equal((await f.command('download')).error,'ambiguous_download');
});
test('download needs a matching bank origin and referrer',async()=>{
  const f=fixture();f.connect();await f.command('act',{action:'download_qfx'});
  f.events.download({id:1,startTime:new Date().toISOString()});
  f.state.download={id:1,url:'https://evil.example/a.qfx',referrer:'https://secure.chase.com/accounts',state:'complete',filename:'/private/a.qfx',fileSize:500};
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.state.download.url='blob:https://secure.chase.com/opaque';
  const result=await f.command('download');assert.equal(result.result.path,'/private/a.qfx');
});
test('arbitrary commands are refused',async()=>{
  const f=fixture();f.connect();assert.equal((await f.command('eval',{code:'steal()'})).error,'unsupported_operation');
});

test('blob without referrer needs its exact download link in the selected tab',async()=>{
  const f=fixture();f.connect();await f.command('act',{action:'download_qfx'});
  const url='blob:https://secure.chase.com/opaque';
  f.events.download({id:1,startTime:new Date().toISOString(),url});
  f.state.download={id:1,url,state:'complete',filename:'/private/a.qfx',fileSize:500};
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.state.domMatched=true;
  f.state.download.url='blob:https://evil.example/opaque';
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.state.download.url=url;
  assert.equal((await f.command('download')).result.state,'complete');
  assert.equal((await f.command('diagnostics')).result.download.dom_correlated,true);
});

test('background preparation moves only the selected Chase tab into its own window',async()=>{
  const f=fixture();f.connect();f.state.tab.active=false;
  assert.equal((await f.command('prepare')).result.ready,true);assert.equal(f.state.moved,true);
  f.state.tab.url='https://evil.example';
  assert.equal((await f.command('prepare')).error,'select_chase_tab');
});

test('missing referrer requires an attachment response from the exact selected tab',async()=>{
  const f=fixture();f.connect();await f.command('act',{action:'download_qfx'});
  const url='https://secure.chase.com/export.qfx';
  f.events.download({id:1,startTime:new Date().toISOString(),url});
  f.state.download={id:1,url,state:'complete',filename:'/private/a.qfx',fileSize:500};
  const detail={tabId:99,timeStamp:Date.now()+1,url,statusCode:200,
    responseHeaders:[{name:'Content-Disposition',value:'attachment; filename="export.qfx"'}]};
  f.events.headers(detail);
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.events.headers({...detail,tabId:1,statusCode:403});
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.events.headers({...detail,tabId:1,url:'https://evil.example/export.qfx'});
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
  f.events.headers({...detail,tabId:1});
  assert.equal((await f.command('download')).result.state,'complete');
  const safe=(await f.command('diagnostics')).result.download;
  assert.equal(safe.network_correlated,true);assert.equal(safe.source_origin,'https://secure.chase.com');
  assert.equal(JSON.stringify(safe).includes('export.qfx'),false);
});
