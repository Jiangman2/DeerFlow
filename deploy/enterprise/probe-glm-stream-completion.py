"""Python 3.7 controller: isolated candidate canary, never a rollout tool."""

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import uuid

BUNDLE = None  # Filled by package-glm-stream-completion.py.
UID = "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"
ONCE = "portal-glm-stream-completion-20261021-r1.once"


class Refused(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def binding(d):
    return {"uid": d["metadata"]["uid"], "generation": d["metadata"]["generation"], "spec_sha256": hashlib.sha256(canonical(d["spec"])).hexdigest()}


def ready(d):
    s = d.get("status", {})
    count = d["spec"]["replicas"]
    require(d["metadata"]["uid"] == UID and count > 0, "gateway_identity_or_scale")
    require(
        s.get("observedGeneration") == d["metadata"]["generation"]
        and all(s.get(k) == count for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))
        and not s.get("terminatingReplicas")
        and not s.get("unavailableReplicas"),
        "gateway_rollout_in_progress",
    )


def query(args, payload=None, timeout=25):
    binary = shutil.which("kubectl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    require(binary in ("/usr/bin/kubectl", "/usr/local/bin/kubectl"), "kubectl_missing")
    for path in (binary, os.path.dirname(binary)):
        info = os.lstat(path)
        require(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode), "kubectl_permissions")
    p = subprocess.run([binary, "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=" + str(timeout) + "s", "-n", "deer-flow"] + args, input=payload, capture_output=True, timeout=timeout)
    require(p.returncode == 0, "kubectl_command_failed")
    require(len(p.stdout) <= 8388608, "query_output_limit")
    return json.loads(p.stdout)


REMOTE = r"""
import sys,os,json,hashlib,pathlib,logging,contextlib,io,types,asyncio,ssl,importlib.metadata
data=json.loads(sys.stdin.buffer.read(524289))
logging.disable(logging.CRITICAL)
for k in ('LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2','LANGSMITH_TRACING'):
 os.environ[k]='false'
result={'status':'INCOMPLETE','provider_requests_attempted':0,'candidate_only_in_child':True}
try:
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  bundle=data['bundle']; root=pathlib.Path('/app/backend/packages/harness/deerflow/models')
  observed={}; result['runtime_sources']=observed
  for name,digest in bundle['base_hashes'].items():
   raw=(root/(name+'.py')).read_bytes()
   assert len(raw)<=262144,'SOURCE_LIMIT'
   observed[name]=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
   assert observed[name]==digest,'RUNTIME_SOURCE_MISMATCH'
  version=importlib.metadata.version('openai')
  assert version=='2.32.0','SDK_VERSION_UNQUALIFIED'
  assert os.environ.get('GLM_5_3_FLASH_API_KEY'),'GLM_KEY_NOT_CONFIGURED'
  result.update(status='PREFLIGHT_PASS',runtime_sources=observed,sdk_version=version)
  if data['run']:
   import deerflow.models
   for name in ('output_safety','glm_stream_guard','robust_openai','enterprise_glm'):
    entry=bundle['sources'][name]; source=entry['source']
    assert hashlib.sha256(source.encode()).hexdigest()==entry['sha256'],'CANDIDATE_HASH_MISMATCH'
    fullname='deerflow.models.'+name; module=types.ModuleType(fullname); module.__file__='<isolated-candidate-'+name+'>'
    sys.modules[fullname]=module; setattr(deerflow.models,name,module)
    exec(compile(source,module.__file__,'exec'),module.__dict__)
   entry=bundle['probe']; assert hashlib.sha256(entry['source'].encode()).hexdigest()==entry['sha256'],'PROBE_HASH_MISMATCH'
   scope={'__name__':'isolated_glm_probe'}; exec(compile(entry['source'],'<isolated-glm-probe>','exec'),scope)
   import httpx
   class Bounded(httpx.AsyncByteStream):
    def __init__(self,inner):self.inner=inner
    async def __aiter__(self):
     size=0
     async for value in self.inner:
      size+=len(value)
      if size>2097152:raise RuntimeError('RESPONSE_SIZE_LIMIT')
      yield value
    async def aclose(self):await self.inner.aclose()
   async def bound(response):response.stream=Bounded(response.stream)
   async def run():
    ca=os.environ.get('GLM_5_3_FLASH_CA_BUNDLE') or os.environ.get('SSL_CERT_FILE') or None
    ctx=ssl.create_default_context(cafile=ca)
    async with httpx.AsyncClient(verify=ctx,trust_env=False,follow_redirects=False,timeout=120,event_hooks={'response':[bound]}) as http:
     return await scope['run_probe'](os.environ['GLM_5_3_FLASH_API_KEY'],http,request_deadline=120)
   result['provider_outcome_may_be_uncertain']=True
   result['provider']=asyncio.run(run())
   result['provider_outcome_may_be_uncertain']=False
   result['provider_requests_attempted']=result['provider']['provider_requests_attempted']
   result['status']='CANDIDATE_ACCEPTED' if result['provider']['glm_canary']=='PASS' else 'INCOMPLETE'
   result['completion_validation']='TERMINAL_AND_DONE'
except Exception as exc:
 result.update(status='INCOMPLETE',error_class=type(exc).__name__)
 if isinstance(exc,AssertionError) and str(exc) in ('SOURCE_LIMIT','RUNTIME_SOURCE_MISMATCH','SDK_VERSION_UNQUALIFIED','GLM_KEY_NOT_CONFIGURED','CANDIDATE_HASH_MISMATCH','PROBE_HASH_MISMATCH'):
  result['error_code']=str(exc)
print(json.dumps(result,sort_keys=True))
"""


def execute(report, claim):
    d = query(["get", "deployment", "gateway", "-o", "json"])
    ready(d)
    report["gateway_before"] = binding(d)
    sets = query(["get", "replicasets", "-l", "app=gateway", "-o", "json"])
    owners = {r["metadata"]["uid"] for r in sets["items"] if any(o.get("uid") == UID and o.get("controller") for o in r["metadata"].get("ownerReferences", []))}
    pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])
    image = next(c["image"] for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
    candidates = [
        p
        for p in pods["items"]
        if not p["metadata"].get("deletionTimestamp")
        and p["status"].get("phase") == "Running"
        and any(o.get("uid") in owners and o.get("controller") for o in p["metadata"].get("ownerReferences", []))
        and any(c["name"] == "gateway" and c["image"] == image for c in p["spec"]["containers"])
        and any(c["name"] == "gateway" and c.get("ready") for c in p["status"].get("containerStatuses", []))
    ]
    require(bool(candidates), "no_ready_gateway")
    pod = sorted(candidates, key=lambda p: p["metadata"]["name"])[0]
    name = pod["metadata"]["name"]
    report["sample"] = {"pod": name, "uid": pod["metadata"]["uid"], "node": pod["spec"]["nodeName"], "image": image}
    args = ["exec", "-i", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", REMOTE]
    preflight = query(args, canonical({"bundle": BUNDLE, "run": False}), timeout=45)
    report["runtime_preflight"] = preflight
    require(preflight.get("status") == "PREFLIGHT_PASS", "runtime_preflight_failed")
    fresh = query(["get", "pod", name, "-o", "json"])
    require(fresh["metadata"]["uid"] == pod["metadata"]["uid"] and fresh["status"].get("containerStatuses") == pod["status"].get("containerStatuses") and not fresh["metadata"].get("deletionTimestamp"), "pod_changed")
    current = query(["get", "deployment", "gateway", "-o", "json"])
    ready(current)
    require(binding(current) == binding(d), "gateway_changed")
    claim()
    report["provider_probe_requested"] = True
    report["provider_outcome_may_be_uncertain"] = True
    print(json.dumps({"phase": "GLM_STREAM_COMPLETION_CANARY", "maximum_provider_calls": 4, "gateway_restart": False}), flush=True)
    report["candidate"] = query(args, canonical({"bundle": BUNDLE, "run": True}), timeout=540)
    report["provider_outcome_may_be_uncertain"] = report["candidate"].get("provider_outcome_may_be_uncertain", False)
    after = query(["get", "deployment", "gateway", "-o", "json"])
    report["gateway_after"] = binding(after)
    require(binding(after) == binding(d), "gateway_changed_during_probe")
    ready(after)
    fresh = query(["get", "pod", name, "-o", "json"])
    require(fresh["metadata"]["uid"] == pod["metadata"]["uid"] and fresh["status"].get("containerStatuses") == pod["status"].get("containerStatuses") and not fresh["metadata"].get("deletionTimestamp"), "pod_changed_during_probe")
    require(report["candidate"].get("status") == "CANDIDATE_ACCEPTED", "provider_acceptance_failed")
    report["status"] = "GLM_STREAM_CANDIDATE_ACCEPTED_NOT_DEPLOYED"


def main():
    report = {
        "schema": "portal-glm-stream-candidate.v1",
        "operation": uuid.uuid4().hex,
        "status": "INCOMPLETE",
        "automatic_retry": False,
        "configuration_modified": False,
        "workload_modified": False,
        "gateway_restarted": False,
        "credentials_exported": False,
        "provider_probe_requested": False,
        "ready_for_rollout": False,
    }
    parent = None
    try:
        require(sys.argv[1:] == ["--verify-glm-stream-once"], "explicit_flag_required")
        require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01", "controller_host_or_user")
        require(isinstance(BUNDLE, dict), "unpackaged_template")
        report["candidate_bundle_sha256"] = hashlib.sha256(canonical(BUNDLE)).hexdigest()
        parent = os.open("/var/lib", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(parent)
        require(info.st_uid == 0 and not info.st_mode & 0o022, "report_parent_permissions")

        def claim():
            fd = os.open(ONCE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical({"operation": report["operation"]}))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent)
            report["once_claimed"] = True

        require(not os.path.lexists("/var/lib/" + ONCE), "canary_already_claimed")
        execute(report, claim)
    except Exception as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "LOCAL_OR_TRANSPORT_FAILURE")
    report["exitcode"] = 0 if report["status"] == "GLM_STREAM_CANDIDATE_ACCEPTED_NOT_DEPLOYED" else 1
    if parent is not None:
        try:
            name = "portal-glm-stream-candidate-" + report["operation"] + ".json"
            raw = canonical(report)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent)
            report.update(report_path="/var/lib/" + name, report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
        finally:
            os.close(parent)
    print(json.dumps(report, sort_keys=True))
    return report["exitcode"]


if __name__ == "__main__":
    raise SystemExit(main())
