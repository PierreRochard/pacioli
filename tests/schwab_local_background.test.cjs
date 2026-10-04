const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const events = {}, replies = [], state = {tab:{id:1,windowId:2,active:true,url:'https://client.schwab.com/app/accounts/history'},
    captures:0, native:0, download:null, calls:[]};
  const event = name => ({addListener:callback => { events[name] = callback; }});
  const port = {onDisconnect:event('disconnect'),onMessage:event('command'),postMessage:r => replies.push(r)};
  const browser = {
    tabs:{onActivated:event('activated'),onUpdated:event('updated'),onRemoved:event('removed'),
      get:async()=>state.tab,
      executeScript:async(id,args)=> {
        if (args.file) return [];
        state.calls.push(args.code);
        if (args.code.includes('["downloadSource"]')) return [{matched:state.domMatched===true}];
        if (args.code.includes('["probe"]')) return [{kind:'structure',controls:[]}];
        return [{acted:true}];
      },
      captureVisibleTab:async()=>{state.captures++;return 'data:image/jpeg;base64,eA==';}},
    downloads:{onCreated:event('download'),search:async()=>[state.download]},
    webRequest:{onHeadersReceived:event('headers')},
    windows:{create:async()=>{state.tab.active=true;}},
    runtime:{connectNative:name=>{assert.equal(name,'lan.pacioli.schwab');state.native++;return port;}},
    browserAction:{onClicked:event('clicked'),setBadgeText:()=>{}}
  };
  const context=vm.createContext({browser,URL,Date,Set,Error,JSON,Promise,
    Image:class{constructor(){this.width=100;this.height=100;}async decode(){}},
    document:{createElement:()=>({getContext:()=>({drawImage(){}}),toDataURL:()=> 'data:image/jpeg;base64,eA=='})}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../extensions/schwab-local/background.js'),'utf8'),context);
  return {state,events,replies,connect(){events.clicked(state.tab);},
    async command(op,extra={}){await events.command({id:'test',op,...extra});return replies.at(-1);}};
}

test('toolbar refuses other sites, including Chase',()=>{
  const f=fixture();
  f.events.clicked({...f.state.tab,url:'https://secure.chase.com/x'});assert.equal(f.state.native,0);
  f.connect();assert.equal(f.state.native,1);
});
test('probe needs the selected active tab and never captures',async()=>{
  const f=fixture();
  f.connect();
  assert.deepEqual((await f.command('probe')).result,{kind:'structure',controls:[]});
  assert.equal(f.state.captures,0);
  f.state.tab.active=false;
  assert.equal((await f.command('probe')).error,'schwab_tab_not_active');
});
test('downloads must be verified JSON, CSV or XML exports',async()=>{
  const f=fixture();f.connect();
  await f.command('act',{action:'download_export'});
  f.events.download({id:7,startTime:new Date().toISOString(),url:'blob:https://client.schwab.com/abc',referrer:'',state:'complete'});
  f.state.domMatched=true;
  f.state.download={id:7,url:'blob:https://client.schwab.com/abc',referrer:'',state:'complete',filename:'/tmp/x.qfx',fileSize:10};
  assert.equal((await f.command('download')).error,'invalid_export_download');
  assert.equal((await f.command('act',{action:'download_export'})).error,'download_already_pending');
  await f.command('release');
  await f.command('act',{action:'download_export'});
  f.events.download({id:8,startTime:new Date().toISOString(),url:'blob:https://client.schwab.com/def',referrer:'',state:'complete'});
  f.state.download={id:8,url:'blob:https://client.schwab.com/def',referrer:'',state:'complete',filename:'/tmp/History.json',fileSize:10};
  assert.deepEqual(JSON.parse(JSON.stringify((await f.command('download')).result)),{state:'complete',id:8,path:'/tmp/History.json'});
});
test('unobserved blobs from the page are rejected',async()=>{
  const f=fixture();f.connect();
  await f.command('act',{action:'download_export'});
  f.events.download({id:9,startTime:new Date().toISOString(),url:'blob:https://client.schwab.com/zzz',referrer:'',state:'complete'});
  f.state.domMatched=false;
  f.state.download={id:9,url:'blob:https://client.schwab.com/zzz',referrer:'',state:'complete',filename:'/tmp/a.json',fileSize:10};
  assert.equal((await f.command('download')).error,'download_provenance_unavailable');
});
