"""Real Firefox WebExtension download provenance against a loopback fixture bank."""
import json
from pathlib import Path
import time
import pytest

from tests.test_chase_firefox import firefox, attach

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('source', ['network', 'blob', 'detached_blob', 'unobserved_blob'])
def test_real_extension_correlates_download_with_selected_tab_response(firefox,tmp_path,source):
    extension=tmp_path/'extension';extension.mkdir()
    production=ROOT/'extensions/chase-local'
    manifest=json.loads((production/'manifest.json').read_text())
    manifest['browser_specific_settings']['gecko']['id']='fixture-chase@pacioli.test'
    manifest['permissions']=['http://127.0.0.1/*','downloads','webRequest']
    (extension/'manifest.json').write_text(json.dumps(manifest))
    origins="['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']"
    content=(production/'content.js').read_text().replace(origins,json.dumps([firefox.origin]))
    (extension/'content.js').write_text(content)
    background=(production/'background.js').read_text().replace(origins,json.dumps([firefox.origin]))
    # Firefox match patterns omit ports; the exact origin check still uses the
    # fixture's ephemeral port, just as production checks the exact bank origin.
    background=background.replace("{urls: [...allowed].map(o => o + '/*')}",
                                  "{urls: ['http://127.0.0.1/*']}")
    # Test-only driver uses the actual production command/validation path.
    # No native host, real banking page, model, or user profile participates.
    background+='''
(async()=>{
  let chosen;
  try {
    chosen=(await browser.tabs.query({url:'http://127.0.0.1/*'})).find(t=>t.url.endsWith('/approved'));
    selected={id:chosen.id,windowId:chosen.windowId,origin:new URL(chosen.url).origin};
    await browser.tabs.update(chosen.id,{active:true});
    const s=await call('observe');
    await execute({op:'act',action:'download_qfx',token:s.token,
      target_id:s.controls.find(c=>c.kind==='download').id,
      task:{last4:'1234',start:'09/01/2026',end:'09/03/2026'}});
    let result;
    for(let n=0;n<100;n++) {
      await new Promise(resolve=>setTimeout(resolve,100));
      result=await execute({op:'download'});
      if(result.state==='complete')break;
    }
    const diagnostic=await execute({op:'diagnostics'});
    await execute({op:'release'});
    await browser.tabs.executeScript(chosen.id,{code:`document.body.dataset.fixtureResult=${JSON.stringify(JSON.stringify({result,diagnostic}))}`});
  } catch(error) {
    if(chosen)await browser.tabs.executeScript(chosen.id,{code:`document.body.dataset.fixtureResult=${JSON.stringify(JSON.stringify({error:error.message}))}`});
  }
})();
'''
    (extension/'background.js').write_text(background)
    with attach(firefox) as connection:
        connection.page.goto(firefox.origin+'/approved')
        connection.script(connection.tab,'''document.body.innerHTML='<fixture-export></fixture-export>';
          window.fixtureRoot=document.querySelector('fixture-export').attachShadow({mode:'closed'});window.fixtureRoot.innerHTML=`<style>select,input,button{display:block;margin:20px;width:400px;height:45px}</style>
            <select id="downloadActivityAccounts"><option>Checking (...1234)</option></select>
            <select id="fileType"><option>Quicken Web Connect (QFX)</option></select>
            <input id="fromDate" value="09/01/2026"><input id="toDate" value="09/03/2026">
            <button id="downloadButton" onclick="location='/statement'">Download</button>`;return true;''')
        if source != 'network':
            if source=='unobserved_blob':
                connection.script(connection.tab, "window.earlierBlob=URL.createObjectURL(new Blob(['not an authorized export']));return true;")
            connection.script(connection.tab, '''window.fixtureRoot.querySelector('button').onclick=async()=>{
                const blob=await (await fetch('/statement')).blob();
                const a=document.createElement('a');a.href=window.earlierBlob || URL.createObjectURL(blob);a.download='fixture.qfx';
                if(args.attached)document.body.append(a);
                a.click();a.remove();
              };return true;''', {'attached':source=='blob'})
        installed=connection.command('webExtension.install',{'extensionData':{'type':'path','path':str(extension)}})
        assert installed['extension']=='fixture-chase@pacioli.test'
        deadline=time.monotonic()+20
        result=None
        while time.monotonic()<deadline:
            result=connection.script(connection.tab,'return document.body.dataset.fixtureResult || null;')
            if result:break
            time.sleep(.1)
        assert result is not None, 'Fixture extension did not finish'
        result=json.loads(result)
        if source=='unobserved_blob':
            assert result.get('error')=='download_provenance_unavailable',result
            return
        assert 'error' not in result,result
        assert result['result']['state']=='complete'
        assert result['diagnostic']['download']['network_correlated' if source=='network' else 'dom_correlated'] is True,result
        assert firefox.bank.downloads==1
        assert Path(result['result']['path']).read_bytes()==firefox.bank.content
