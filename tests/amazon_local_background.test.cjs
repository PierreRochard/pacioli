const test=require('node:test'), assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function setup(){
  let events={},reads=0,updates=[],url='https://www.amazon.com/your-orders/orders';
  let browser={tabs:{get:async()=>({id:7,url,status:'complete'}),executeScript:async()=>{reads++;return [{text:'Order evidence',links:[{url:'https://www.amazon.com/gp/your-account/order-details?orderID=123-1234567-1234567',label:'Order details'},{url:'https://www.amazon.com/gp/buy/spc/handlers/display.html',label:'Buy'}]}];},update:async(id,args)=>{updates.push(args.url);url=args.url;},onRemoved:{addListener:f=>events.remove=f}},browserAction:{setBadgeText:()=>{},onClicked:{addListener:f=>events.click=f}},runtime:{connectNative:()=>({onDisconnect:{addListener:()=>{}},onMessage:{addListener:f=>events.message=f},postMessage:()=>{}})}};
  const context=vm.createContext({browser,URL,Set});vm.runInContext(fs.readFileSync(path.join(__dirname,'../extensions/amazon-local/background.js'),'utf8'),context);
  return {context,events,updates,reads:()=>reads,exec:cmd=>context.execute(cmd)};
}
test('reader requires selected orders tab and never allows purchase or external navigation',async()=>{
 const h=setup();await assert.rejects(h.exec({op:'amazon_observe'}),/select_amazon/);h.events.click({id:7,url:'https://www.amazon.com/your-orders/orders'});
 const data=await h.exec({op:'amazon_observe'});assert.equal(data.links.length,1);
 assert.equal(h.context.permitted('https://www.amazon.com/your-orders/orders/cancel'),false);
 assert.equal(h.context.permitted('https://www.amazon.com/gp/your-account/order-details/ref=ppx_yo_dt_b_details?orderID=123-1234567-1234567'),true);
 for(const url of ['https://evil.example/gp/your-account/order-details?orderID=x','https://www.amazon.com/gp/buy/spc/handlers/display.html','https://www.amazon.com/gp/your-account/order-details?orderID=999-1234567-1234567'])await assert.rejects(h.exec({op:'amazon_navigate',url}),/unobserved/);
 await h.exec({op:'amazon_navigate',url:data.links[0].url});assert.equal(h.updates.length,1);
 await h.exec({op:'amazon_back'});assert.equal(h.updates[1],'https://www.amazon.com/your-orders/orders');
 await assert.rejects(h.exec({op:'act',action:'buy'}),/unsupported/);
});
