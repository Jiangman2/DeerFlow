"""Package a new explicit diagnostic attempt; never contains a credential."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

REMOTE = r'''
import sys,json,os,io,contextlib,logging,asyncio,ssl,hashlib,socket,signal,pathlib,urllib.request
data=json.loads(sys.stdin.buffer.read(262145))
assert set(data)=={'key','source','evidence','profile','operation'}
logging.disable(logging.CRITICAL)
for name in ('LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2','LANGSMITH_TRACING'):
 os.environ[name]='false'
signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(TimeoutError()))
signal.alarm(600)
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 import httpx
 scope={'__name__':'isolated_probe'}
 exec(compile(data['source'],'<application-canary>','exec'),scope)
 evidence={'__name__':'transport_evidence'}
 exec(compile(data['evidence'],'<transport-evidence>','exec'),evidence)
 recorder=evidence['Recorder'](data['key'],data['operation'])
 async def run():
  ctx=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None)
  async with httpx.AsyncClient(verify=ctx,trust_env=False,follow_redirects=False,timeout=90,event_hooks={'request':[recorder.request],'response':[recorder.response]}) as client:
   return await evidence['diagnose'](data['key'],data['profile'],scope['run_probe'],client,recorder)
 try:
  dns=sorted(set(x[4][0] for x in socket.getaddrinfo('aiapi.cnooc.com.cn',443,type=socket.SOCK_STREAM)))
  result=asyncio.run(run())
  result['dns_addresses']=dns
 except BaseException as exc:
  result=dict(status='INCOMPLETE',error_class=type(exc).__name__,requests=recorder.rows,provider_requests_attempted=len(recorder.rows),credentials_exported=False)
 result['environment_proxy_present']=any(os.environ.get(k) for k in ('HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy'))
 result['urllib_environment_bypass_observed']=urllib.request.proxy_bypass_environment('aiapi.cnooc.com.cn')
 result['environment_bypass_is_not_httpx_route_proof']=True
 import deerflow.models.factory as factory
 import deerflow.models.robust_openai as adapter
 result['runtime_sources']={n:hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest() for n,m in [('factory',factory),('robust_openai',adapter)]}
 result['versions']={'httpx':httpx.__version__}
 import openai
 result['versions']['openai']=openai.__version__
signal.alarm(0)
print(json.dumps(result,sort_keys=True))
'''


def build(root, previous):
    # r4 was actually used in the incident. Reuse its exact application canary
    # and profile while replacing only transport instrumentation and controller.
    assert hashlib.sha256(previous.encode()).hexdigest() == '3716be98c073b890ef33e64b3adfffbbd7d3b5361cde1d1379c2a1689350aa17'
    n = {"__name__": "old_diagnostic"}
    exec(compile(previous, "<r4>", "exec"), n)
    assert n['PROFILE']['base_url'] == 'https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1'
    clients = [node for node in ast.walk(ast.parse(n['REMOTE'])) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'AsyncClient']
    assert len(clients) == 1
    keywords = {kw.arg: kw.value for kw in clients[0].keywords}
    assert ast.literal_eval(keywords['trust_env']) is False and ast.literal_eval(keywords['follow_redirects']) is False
    original = (root / 'deploy/enterprise/prepare-deepseek-v41-provider.py').read_text(encoding='utf-8')
    tree = ast.parse(original)
    names = {'Refused','require','canonical','binding','call','claim_once'}
    chunks = [ast.get_source_segment(original, node) for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in names]
    evidence = (root / 'deploy/enterprise/deepseek-v41-transport-evidence.py').read_text(encoding='utf-8')
    constants = dict(PROFILE=n['PROFILE'],PROBE_SOURCE=n['PROBE_SOURCE'],EVIDENCE_SOURCE=evidence,DIAGNOSTIC_REMOTE=REMOTE,NAMESPACE='deer-flow',GATEWAY_UID='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',DIAGNOSTIC_ONCE='portal-deepseek-v41-transport-20261021-r1.once')
    constants['HISTORICAL_TRANSPORT_AUDIT'] = dict(r4_script_sha256=hashlib.sha256(previous.encode()).hexdigest(), r4_trust_env=False, r4_follow_redirects=False, application_probe_sha256=hashlib.sha256(n['PROBE_SOURCE'].encode()).hexdigest())
    result = '\n'.join(chunks) + '\n' + '\n'.join(k+' = '+repr(v) for k,v in constants.items())+'\n'
    result += (root/'deploy/enterprise/diagnose-deepseek-v41-transport.py').read_text(encoding='utf-8')
    ast.parse(result,feature_version=(3,7))
    return result.encode()


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--previous',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    raw=build(Path(__file__).resolve().parents[2],args.previous.read_text(encoding='utf-8'))
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),path=str(args.output))))
