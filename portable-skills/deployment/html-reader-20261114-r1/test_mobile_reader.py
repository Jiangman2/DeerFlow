"""Local mobile browser check; BrowserAct has no configured browser/API key.

Use the existing Playwright installation and an internal-only Docker network.
No production account, session, provider endpoint, or external resource is used.
"""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from playwright.sync_api import sync_playwright
import nginx_reader
from test_nginx_http import command, DOCKER, NGINX, MOCK

REPO=Path(__file__).resolve().parents[3]
RESULT=REPO/'portable-skills/validation/energy-html-reader-20261114-r1'


def main():
    name='energy-reader-mobile-'+uuid.uuid4().hex[:10]
    RESULT.mkdir(exist_ok=True,parents=True)
    with tempfile.TemporaryDirectory(prefix='energy-reader-mobile-') as temp:
        directory=Path(temp)
        html=(REPO/'portable-skills/validation/energy-market-daily-2.1.4-20261114/complete.html').read_text(encoding='utf-8')
        html=html.replace('</body>','<script>window.__UNEXPECTED_SCRIPT_EXECUTION=true</script></body>')
        (directory/'report.html').write_text(html,encoding='utf-8')
        mock=MOCK.replace("self.wfile.write('<!doctype html><html><meta charset=\"utf-8\"><body>TEST_REPORT_日报<svg viewBox=\"0 0 1 1\"></svg></body></html>'.encode())", "self.wfile.write(open('/fixture/report.html','rb').read())")
        assert "open('/fixture/report.html'" in mock
        (directory/'mock.py').write_text(mock,encoding='utf-8')
        config='events {}\nhttp { access_log off; server { listen 18082; set $gateway_upstream 127.0.0.1:18081;\n'+nginx_reader.reader_block('http://$gateway_upstream','$scheme')+'\nlocation /api/ { proxy_pass http://$gateway_upstream; }\n} }\n'
        (directory/'nginx.conf').write_text(config,encoding='utf-8')
        mount='type=bind,source='+str(directory)+',target=/fixture,readonly'
        try:
            command(['run','-d','--name',name,'--network','none','--read-only','--mount',mount,'python:3.10-slim','python','-I','-B','/fixture/mock.py'])
            command(['run','-d','--name',name+'-nginx','--network','container:'+name,'--read-only','--tmpfs','/var/cache/nginx:rw','--tmpfs','/var/run:rw','--mount',mount,'--entrypoint','nginx',NGINX,'-c','/fixture/nginx.conf','-g','daemon off;'])
            origin='http://energy-reader.test'
            url=origin+'/reports/energy-daily/11111111-2222-4333-8444-555555555555/2026-11-14.html'
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True)
                try:
                    context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,device_scale_factor=1)
                    context.add_cookies([{'name':'session','value':'owner','url':origin}])
                    page=context.new_page();requests=[];page.on('request',lambda r:requests.append(r.url))
                    def fetch_nginx(route,request):
                        assert request.url==url
                        code="import http.client,json,base64; c=http.client.HTTPConnection('127.0.0.1',18082,timeout=5); c.request('GET',"+repr(request.url[len(origin):])+",headers="+repr({'Cookie':request.all_headers().get('cookie',''),'Host':'energy-reader.test'})+"); r=c.getresponse(); print(json.dumps({'status':r.status,'headers':r.getheaders(),'body':base64.b64encode(r.read()).decode()})); c.close()"
                        response=json.loads(command(['exec',name,'python','-I','-B','-c',code]).stdout)
                        route.fulfill(status=response['status'],headers=dict(response['headers']),body=base64.b64decode(response['body']))
                    # Feed the real, isolated Nginx response to the browser without
                    # a public Docker port or any DNS/external-network request.
                    page.route('**/*',fetch_nginx)
                    response=page.goto(url,wait_until='networkidle')
                    assert response.status==200
                    metrics=page.evaluate('''() => ({title:document.querySelector('h1').innerText,svg:document.querySelectorAll('svg').length,links:document.querySelectorAll('a[href]').length,viewport:innerWidth,width:document.documentElement.scrollWidth,scriptRan:!!window.__UNEXPECTED_SCRIPT_EXECUTION,reportWidth:document.querySelector('main.report').getBoundingClientRect().width})''')
                    assert metrics['title']=='油气与金融市场日报' and metrics['svg']>=3 and metrics['links']>=8,metrics
                    assert metrics['width']<=metrics['viewport'] and metrics['reportWidth']>250,metrics
                    assert not metrics['scriptRan'],metrics
                    assert requests==[url],requests
                    page.screenshot(path=str(RESULT/'mobile-390.png'),full_page=True)
                    report={'schema':'energy-html-reader-mobile-test.v1','status':'MOBILE_INLINE_HTML_AND_CSP_QUALIFIED','metrics':metrics,
                            'provider_requests':0,'production_access':False,'synthetic_session':True,'external_network_requests':0,
                            'browseract_fallback_reason':'NO_CONFIGURED_BROWSER_OR_API_KEY','browser':'existing local Playwright Chromium',
                            'transport':'Real isolated Nginx response supplied through local Playwright routing; no public port'}
                    (RESULT/'BROWSER.json').write_text(json.dumps(report,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')
                    print(json.dumps(report))
                finally:browser.close()
        finally:
            command(['rm','-f',name+'-nginx',name],check=False)


if __name__=='__main__':main()
