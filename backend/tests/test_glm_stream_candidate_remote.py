"""Execute the packaged child loader with real SDK and offline transport."""

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("done", [True, False])
def test_remote_candidate_uses_guard_for_all_four_synthetic_calls(done):
    spec = importlib.util.spec_from_file_location("controller", ROOT / "deploy/enterprise/probe-glm-stream-completion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sources = {}
    for name in ("output_safety", "glm_stream_guard", "robust_openai", "enterprise_glm"):
        source = (ROOT / ("backend/packages/harness/deerflow/models/" + name + ".py")).read_text()
        sources[name] = {"source": source, "sha256": hashlib.sha256(source.encode()).hexdigest()}
    probe = (ROOT / "backend/app/gateway/enterprise_glm_probe.py").read_text()
    bundle = {"base_hashes": {k: sources[k]["sha256"] for k in ("output_safety", "robust_openai", "enterprise_glm")}, "sources": sources, "probe": {"source": probe, "sha256": hashlib.sha256(probe.encode()).hexdigest()}}
    prelude = r"""
import pathlib,os,httpx,json
os.environ['GLM_5_3_FLASH_API_KEY']='offline-fixture'
original_read=pathlib.Path.read_bytes
fixture_root=pathlib.Path(__ROOT__)
def read(path):
 if str(path).startswith('/app/backend/packages/harness/deerflow/models/'):
  return (fixture_root/'backend/packages/harness/deerflow/models'/path.name).read_text().encode()
 return original_read(path)
pathlib.Path.read_bytes=read
counter=0
def handler(request):
 global counter
 counter+=1
 assert str(request.url)=='https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1/chat/completions'
 body=json.loads(request.content)
 assert body['thinking']=={'type':'enabled'}
 if counter==2:
  delta={'role':'assistant','tool_calls':[{'index':0,'id':'fixture-call','type':'function','function':{'name':'probe_echo','arguments':'{"value":"GLM_PROBE_OK"}'}}]}; finish='tool_calls'
 else:
  delta={'role':'assistant','content':'红色' if counter==4 else 'GLM_PROBE_OK'}; finish='stop'
 value={'id':'fixture','object':'chat.completion.chunk','model':'GLM-5.3-Flash','created':1,'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
 return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(value)+'\n\n'+('data: [DONE]\n\n' if __DONE__ else ''))
Original=httpx.AsyncClient
class Offline(Original):
 def __init__(self,*a,**kw):
  kw['transport']=httpx.MockTransport(handler)
  super().__init__(*a,**kw)
httpx.AsyncClient=Offline
""".replace("__ROOT__", repr(str(ROOT))).replace("__DONE__", repr(done))
    # Child stdout is the actual sanitized remote receipt; never contacts network.
    p = subprocess.run([sys.executable, "-B", "-c", prelude + "\n" + module.REMOTE], input=json.dumps({"bundle": bundle, "run": True}).encode(), capture_output=True, timeout=45)
    assert p.returncode == 0, p.stderr.decode()
    receipt = json.loads(p.stdout)
    assert receipt.get("status") == ("CANDIDATE_ACCEPTED" if done else "INCOMPLETE"), receipt
    assert receipt["provider_requests_attempted"] == (4 if done else 1), receipt
    if done:
        assert set(receipt["provider"]["stage_results"].values()) == {"PASS"}
    else:
        assert receipt["provider"]["error_type"] == "ModelOutputSafetyError"
