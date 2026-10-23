"""One new-App request in an isolated Gateway child; no rollout/config writes."""

import argparse
import getpass
import hashlib
import json
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import sys
import uuid

BUNDLE = None  # Filled by package-data-qa-candidate.py.
EXPECTED = {"uid": "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1", "generation": 381, "spec_sha256": "5a84f74566171e588433779a59b9adec1ae752c2dc3c42706ff55db1e295fbda"}
BASE = ["/usr/bin/kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=20s", "-n", "deer-flow"]
REMOTE = r"""
import contextlib,hashlib,io,json,logging,pathlib,signal,sys,types
data=json.loads(sys.stdin.buffer.read(262145))
logging.disable(logging.CRITICAL)
result={'status':'INCOMPLETE','provider_requests_attempted':0,'candidate_only_in_child':True,'credentials_exported':False}
def fail_timeout(signum,frame):raise TimeoutError('CANARY_DEADLINE')
signal.signal(signal.SIGALRM,fail_timeout);signal.alarm(195)
try:
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  root=pathlib.Path('/app/backend/packages/harness/deerflow')
  for name,expected in data['bundle']['base_sources'].items():
   raw=(root/name).read_bytes()
   assert len(raw)<=524288 and hashlib.sha256(raw).hexdigest()==expected,'SOURCE_MISMATCH'
  import deerflow.community.appforge
  for name,entry in data['bundle']['modules'].items():
   raw=entry['source'].encode();assert hashlib.sha256(raw).hexdigest()==entry['sha256'],'BUNDLE_MISMATCH'
   module=types.ModuleType(name);module.__package__=name.rpartition('.')[0];sys.modules[name]=module
   exec(compile(raw,'<data-qa-candidate>','exec'),module.__dict__)
  module=sys.modules['deerflow.community.appforge._candidate_tools']
  parser=sys.modules['deerflow.community.appforge.data_qa_stream']
  result['status']='PREFLIGHT_PASS'
  if data.get('run'):
   key=data['key'];assert isinstance(key,str) and 8<=len(key)<=8192 and '\n' not in key and '\r' not in key,'KEY_INVALID'
   def extra(name):
    assert name=='datalake_data_qa','UNEXPECTED_TOOL'
    return {'api_key':key,'api_url':'http://10.77.124.142:9080','app_id':'app-v16p2syh','conversation_prefix':'SquareWorkflow_','timeout_seconds':180,'trust_env':False}
   module._tool_extra=extra
   frames=[];original=parser.iter_data_qa_events
   known={'answer','knowledge','thinking','done','workflow_finished','workflow_interrupt','workflow_failed','workflow_error','error','end','progress','success','succeeded','completed','finished','failed','failure','interrupted','running','cancelled','canceled','aborted'}
   keys={'type','event','data','content','text','answer','status','node_type','outputs','output','choices','xx_ceai_extra_data','inputToken','outputToken','totalToken','error','message','transport_done','chunk_id','id','knowledge_filename','document_name'}
   def redacted(value,field='',depth=0):
    if depth>6:return '<depth-limit>'
    if isinstance(value,dict):return {k:redacted(v,k,depth+1) for k,v in value.items() if k in keys}
    if isinstance(value,list):return [redacted(v,field,depth+1) for v in value[:3]]
    if field in ('type','event','status','node_type'):return value if isinstance(value,str) and value in known else '<unrecognized>'
    if field in ('inputToken','outputToken','totalToken') and type(value) in (int,float):return value if 0<=value<=10**12 else None
    if value is None or isinstance(value,bool):return value
    return '<redacted-'+type(value).__name__+'>'
   def observe(response):
    for event in original(response):
     if len(frames)<128:frames.append(redacted(event))
     yield event
   parser.iter_data_qa_events=observe
   runtime=types.SimpleNamespace(context={'user_id':'data-qa-field-canary','thread_id':data['operation'],'datalake_enabled':True})
   result['provider_requests_attempted']=1
   value=json.loads(module._invoke_tool('datalake_data_qa','查询作业井基本信息表中设计井深最大的2口井',runtime))
   result.update(status='NEW_APP_RESPONSE_OBSERVED_NOT_BUSINESS_ACCEPTED',result_status=value.get('status'),error_code=value.get('code'),
    answer_present=bool(value.get('answer')),answer_characters=len(value.get('answer') or ''),knowledge_count=len(value.get('knowledge') or []),
    interrupt_present=bool(value.get('interrupt')),truncated=value.get('truncated'),protocol_sample=frames,protocol_sample_is_redacted=True,
    metric_question_available=False,business_values_verified=False)
except Exception as error:
 result.update(status='INCOMPLETE',error_class=type(error).__name__,error_code='CANDIDATE_OR_TRANSPORT_FAILED')
finally:signal.alarm(0)
print(json.dumps(result,sort_keys=True))
"""


def query(args, payload=None, timeout=30):
    command = ["--request-timeout=" + str(timeout) + "s" if item.startswith("--request-timeout=") else item for item in BASE]
    proc = subprocess.run(command + args, input=payload, capture_output=True, timeout=timeout)
    if proc.returncode or len(proc.stdout) > 8388608:
        raise ValueError("CLUSTER_QUERY_FAILED")
    return json.loads(proc.stdout)


def binding(value):
    return {"uid": value["metadata"]["uid"], "generation": value["metadata"]["generation"], "spec_sha256": hashlib.sha256(json.dumps(value["spec"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-new-data-qa-once", required=True, action="store_true")
    parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "datax-01":
        raise ValueError("WRONG_HOST_OR_USER")
    binary = shutil.which("kubectl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    if binary not in ("/usr/bin/kubectl", "/usr/local/bin/kubectl"):
        raise ValueError("KUBECTL_MISSING")
    BASE[0] = binary
    for path in ("/var/lib", os.path.dirname(binary), binary):
        info = os.lstat(path)
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError("UNTRUSTED_PATH")
    gateway = query(["get", "deployment", "gateway", "-o", "json"])
    if binding(gateway) != EXPECTED:
        raise ValueError("GATEWAY_BASELINE_CHANGED")
    status = gateway.get("status", {})
    if status.get("observedGeneration") != EXPECTED["generation"] or any(status.get(k) != 70 for k in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")) or status.get("terminatingReplicas"):
        raise ValueError("GATEWAY_ROLLOUT_IN_PROGRESS")
    pod_name = "gateway-8649cdbc5b-26q9l"
    pod_uid = "b5451481-1577-4656-a1ed-e28decdabc7a"

    def identity():
        pod = query(["get", "pod", pod_name, "-o", "json"])
        containers = [c for c in pod.get("status", {}).get("containerStatuses", []) if c["name"] == "gateway"]
        if pod["metadata"]["uid"] != pod_uid or pod["metadata"].get("deletionTimestamp") or not containers or not containers[0].get("ready"):
            raise ValueError("POD_CHANGED")

    identity()
    operation = uuid.uuid4().hex
    payload = {"bundle": BUNDLE, "operation": operation, "run": False}
    command = ["exec", "-i", pod_name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", REMOTE]
    preflight = query(command, json.dumps(payload).encode(), 40)
    if preflight.get("status") != "PREFLIGHT_PASS":
        raise ValueError("SOURCE_PREFLIGHT_FAILED")
    if not sys.stdin.isatty():
        raise ValueError("INTERACTIVE_HIDDEN_INPUT_REQUIRED")
    key = getpass.getpass("New Data QA API key (hidden; dedicated key only): ").strip()
    if not 8 <= len(key) <= 8192 or any(c in key for c in "\r\n"):
        raise ValueError("KEY_INVALID")
    once = "/var/lib/portal-data-qa-canary-20261021-r1.once"
    fd = os.open(once, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump({"operation": operation, "provider_requests_maximum": 1}, output)
    report = {
        "schema": "portal-data-qa-field-canary.v1",
        "operation": operation,
        "gateway": EXPECTED,
        "automatic_retry": False,
        "workload_modified": False,
        "configuration_modified": False,
        "secret_created": False,
        "credentials_exported": False,
        "ready_for_rollout": False,
        "maximum_provider_requests": 1,
        "status": "INCOMPLETE",
    }
    try:
        identity()
        if binding(query(["get", "deployment", "gateway", "-o", "json"])) != EXPECTED:
            raise ValueError("GATEWAY_BASELINE_CHANGED")
        payload.update(key=key, run=True)
        print(json.dumps({"phase": "NEW_DATA_QA_INTERFACE_CANARY", "maximum_provider_requests": 1}), flush=True)
        report["provider"] = query(command, json.dumps(payload).encode(), 215)
        identity()
        if binding(query(["get", "deployment", "gateway", "-o", "json"])) != EXPECTED:
            raise ValueError("GATEWAY_BASELINE_CHANGED")
        report["status"] = report["provider"].get("status", "INCOMPLETE")
    except Exception as error:
        report.update(error_class=type(error).__name__, error_code="CANARY_INCOMPLETE", provider_outcome_may_be_uncertain=True)
    finally:
        payload.pop("key", None)
        key = None
    path = pathlib.Path("/var/lib/portal-data-qa-canary-" + operation + ".json")
    raw = json.dumps(report, sort_keys=True).encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
    report.update(report_path=str(path), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "NEW_APP_RESPONSE_OBSERVED_NOT_BUSINESS_ACCEPTED" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "INCOMPLETE", "error_class": type(error).__name__, "error_code": "PREFLIGHT_OR_LOCAL_FAILURE", "credentials_exported": False, "workload_modified": False, "automatic_retry": False}))
        raise SystemExit(1)
