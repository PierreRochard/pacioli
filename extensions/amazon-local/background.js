/* Read-only order history/receipt navigation. No credentials, forms or purchases. */
let port=null, selected=null, busy=false, links=new Set(), previous=null;
function permitted(url) {
  try {
    const u=new URL(url);
    return u.origin==='https://www.amazon.com' && !u.username && !u.password &&
      /^\/(?:gp\/(?:your-account\/(?:order-history|order-details)(?:\.html)?|css\/(?:order-history|summary\/print\.html))|your-orders\/orders)(?:\/ref=[A-Za-z0-9_-]+)?\/?$/.test(u.pathname);
  } catch (_) { return false; }
}
async function execute(cmd) {
  if(cmd.op==='ping')return {ready:!!selected,version:1};
  if(!selected)throw new Error('select_amazon_orders_tab');
  const tab=await browser.tabs.get(selected);
  if(!permitted(tab.url))throw new Error('amazon_sign_in_or_orders_needed');
  if(cmd.op==='amazon_observe') {
    if(tab.status==='loading')return {state:'loading'};
    const [data]=await browser.tabs.executeScript(tab.id,{code:`(() => {
      const root=document.querySelector('#yourOrders, #orderDetails, #ordersContainer, #a-page') || document.body;
      const text=root.innerText;
      if(text.length>200000)throw new Error('receipt_too_large');
      return {text,links:Array.from(root.querySelectorAll('a[href]')).map(a=>({url:a.href,label:a.innerText.trim()})).slice(0,3000)};
    })()`});
    const safe=data.links.filter(l=>permitted(l.url));
    links=new Set(safe.map(l=>l.url));
    return {state:'observed',url:tab.url,text:data.text,links:safe};
  }
  if(cmd.op==='amazon_navigate') {
    if(!permitted(cmd.url) || !links.has(cmd.url))throw new Error('unobserved_amazon_link');
    previous=tab.url;links.clear();await browser.tabs.update(tab.id,{url:cmd.url});return {state:'loading'};
  }
  if(cmd.op==='amazon_back') {
    if(!previous || !permitted(previous))throw new Error('no_order_history_return');
    const url=previous;previous=null;links.clear();await browser.tabs.update(tab.id,{url});return {state:'loading'};
  }
  throw new Error('unsupported_operation');
}
browser.browserAction.onClicked.addListener(tab=>{
  if(busy || !permitted(tab.url)){browser.browserAction.setBadgeText({text:'!'});return;}
  selected=tab.id;links.clear();
  if(!port){
    port=browser.runtime.connectNative('lan.pacioli.amazon');
    port.onDisconnect.addListener(()=>{port=null;browser.browserAction.setBadgeText({text:'!'});});
    port.onMessage.addListener(async cmd=>{
      if(busy){port?.postMessage({id:cmd.id,error:'bridge_busy'});return;}
      busy=true;
      try{port?.postMessage({id:cmd.id,result:await execute(cmd)});}
      catch(e){port?.postMessage({id:cmd.id,error:/^[a-z_]{3,48}$/.test(e.message)?e.message:'amazon_reader_failed'});}
      finally{busy=false;}
    });
  }
  browser.browserAction.setBadgeText({text:'ON'});
});
browser.tabs.onRemoved.addListener(id=>{if(id===selected){selected=null;links.clear();}});
