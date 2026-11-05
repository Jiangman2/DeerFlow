"""Read-only deployment and Data QA source/configuration inventory (Python 3.7+)."""

import argparse
import hashlib
import json
import subprocess

BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]
PROBE = r"""
import contextlib,hashlib,io,json,pathlib,re
sink=io.StringIO()
with contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
 from deerflow.config import get_app_config
 config=get_app_config()
 names=[]
 for t in config.tools:
  if not t.name.startswith('datalake_'):continue
  extra=t.model_extra or {}
  use=t.use if re.fullmatch(r'[A-Za-z0-9_.:]+',t.use) else 'UNEXPECTED_PROVIDER'
  fields=('api_url', 'api_key', 'app_id', 'conversation_prefix', 'timeout_seconds', 'trust_env', 'inputs', 'hytoken', 'hyaccount', 'retrieve_api_url', 'project_id', 'document_store_ids')
  names.append({'name':t.name,'use':use,'configured_fields':sorted(k for k in fields if k in extra),'key_value_exported':False})
root=pathlib.Path('/app/backend/packages/harness/deerflow')
sources={}
for name in (
 'community/appforge/tools.py', 'community/appforge/__init__.py', 'config/tool_config.py', 'config/datalake_tools.py',
 'tools/tools.py', 'agents/middlewares/tool_error_handling_middleware.py', 'models/enterprise_glm.py', 'models/robust_openai.py',
 'models/output_safety.py', 'models/glm_stream_guard.py', 'subagents/executor.py', 'tools/builtins/task_tool.py', 'runtime/datalake_policy.py',
 'agents/middlewares/datalake_source_middleware.py', 'community/appforge/data_qa_stream.py'):
 p=root/name
 if p.is_file():
  b=p.read_bytes();assert len(b)<=524288,'SOURCE_LIMIT'
  sources[name]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
 else:sources[name]={'status':'ABSENT'}
print(json.dumps({'tools':names,'sources':sources,'credentials_exported':False},sort_keys=True))
"""


def query(args, limit=8388608):
    result = subprocess.run(BASE + args, capture_output=True, timeout=40)
    if result.returncode or len(result.stdout) > limit:
        raise ValueError("READ_QUERY_FAILED")
    return json.loads(result.stdout)


def binding(deployment):
    metadata = deployment["metadata"]
    spec = deployment["spec"]
    return {
        "uid": metadata["uid"],
        "generation": metadata["generation"],
        "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "images": [{"container": c["name"], "image": c["image"]} for c in spec["template"]["spec"]["containers"]],
        "desired": spec.get("replicas"),
        "ready": deployment.get("status", {}).get("readyReplicas"),
        "updated": deployment.get("status", {}).get("updatedReplicas"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-readonly", required=True, action="store_true")
    parser.parse_args()
    deployment = query(["get", "deployment", "gateway", "-o", "json"])
    before = binding(deployment)
    status = deployment.get("status", {})
    desired = deployment["spec"].get("replicas")
    if not desired or any(status.get(k) != desired for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")) or status.get("observedGeneration") != before["generation"]:
        raise ValueError("GATEWAY_NOT_STABLE")
    pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    target_image = next(c["image"] for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
    eligible = []
    for p in pods:
        cs = [c for c in p.get("status", {}).get("containerStatuses", []) if c["name"] == "gateway"]
        images = [c["image"] for c in p["spec"]["containers"] if c["name"] == "gateway"]
        if not p["metadata"].get("deletionTimestamp") and cs and cs[0].get("ready") and images == [target_image]:
            eligible.append(p)
    if not eligible:
        raise ValueError("NO_MATCHING_READY_POD")
    p = sorted(eligible, key=lambda v: v["metadata"]["name"])[0]
    name, uid = p["metadata"]["name"], p["metadata"]["uid"]
    observation = query(["exec", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", PROBE], 65536)
    if query(["get", "pod", name, "-o", "json"])["metadata"]["uid"] != uid:
        raise ValueError("POD_CHANGED")
    after = binding(query(["get", "deployment", "gateway", "-o", "json"]))
    if before != after:
        raise ValueError("GATEWAY_CHANGED")
    refs = [{"volume": v["name"], "configmap": v["configMap"]["name"]} for v in deployment["spec"]["template"]["spec"].get("volumes", []) if "configMap" in v]
    print(
        json.dumps(
            {
                "schema": "portal-data-qa-release-baseline.v2",
                "status": "READONLY_BASELINE_COLLECTED",
                "gateway": before,
                "configmap_references": refs,
                "sample": {"pod": name, "uid": uid, "node": p["spec"].get("nodeName")},
                "observation": observation,
                "scope": "ONE_READY_GATEWAY_SAMPLE",
                "workload_modified": False,
                "provider_requests": 0,
                "credentials_exported": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and str(error) in ("READ_QUERY_FAILED", "GATEWAY_NOT_STABLE", "NO_MATCHING_READY_POD", "POD_CHANGED", "GATEWAY_CHANGED") else "READONLY_INSPECTION_FAILED"
        print(json.dumps({"status": "INCOMPLETE", "error_code": code, "workload_modified": False, "credentials_exported": False, "provider_requests": 0}))
        raise SystemExit(1)
