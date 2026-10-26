"""Read-only company KB preparation inventory; host Python 3.7+.

No login, membership grant, Kubernetes mutation or model request. The child
uses the Gateway's existing DB connection configuration only in a read-only
transaction. It exports catalog fields and IDs of the two explicitly named
resources, never credentials, other resource names, documents or user records.
"""
import argparse
import hashlib
import json
import subprocess

BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]
GATEWAY_UID = "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"
GATEWAY_IMAGE = "deerflow.local/deer-flow-gateway@sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c"
CHILD = r'''
import asyncio,contextlib,hashlib,io,json,pathlib,sys
source=pathlib.Path('/app/backend/app/gateway/weknora_identity.py')
actual=hashlib.sha256(source.read_bytes()).hexdigest()
if actual != 'af64e8d928d4d2902b9f189c4e890e0dd7f46ba816b31bfad0197534df55f256':
 print(json.dumps({'status':'SOURCE_CHANGED','identity_sha256':actual,'database_access':False})); raise SystemExit(0)
sys.path.insert(0,'/app/backend')
allowed={
 'organizations':['id','name','searchable','require_approval','member_limit','owner_tenant_id','deleted_at'],
 'organization_members':['organization_id','user_id','tenant_id','role'],
 'organization_tenant_members':['organization_id','tenant_id','role'],
 'kb_shares':['knowledge_base_id','organization_id','source_tenant_id','permission'],
 'knowledge_bases':['id','name','tenant_id','deleted_at'],
}
async def inspect():
 from app.gateway.weknora_identity import _connect_weknora_database
 conn=await _connect_weknora_database()
 try:
  async with conn.transaction(isolation='repeatable_read',readonly=True):
   await conn.execute("SET LOCAL statement_timeout = '5000ms'")
   readonly=await conn.fetchval('SHOW transaction_read_only')
   if readonly != 'on': raise RuntimeError('READONLY_REQUIRED')
   rows=await conn.fetch('SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=ANY($1::text[])',list(allowed))
   tables={name:sorted(r['column_name'] for r in rows if r['table_name']==name and r['column_name'] in fields) for name,fields in allowed.items()}
   resources={}
   for table,name in [('knowledge_bases','中国海油制度知识库'),('organizations','中国海油全员制度共享空间')]:
    fields=tables[table]
    if 'id' not in fields or 'name' not in fields: continue
    selected=[f for f in fields if f not in {'name','deleted_at','owner_tenant_id'}]
    sql='SELECT '+','.join(selected)+' FROM '+table+' WHERE name=$1'
    if 'deleted_at' in fields: sql+=' AND deleted_at IS NULL'
    sql+=' ORDER BY id LIMIT 11'
    matched=await conn.fetch(sql,name)
    if len(matched)>10: raise RuntimeError('TOO_MANY_NAMED_RESOURCES')
    resources[table]=[dict(r) for r in matched]
   return {'status':'CATALOG_AND_NAMED_RESOURCES_OBSERVED','identity_sha256':actual,'database_readonly':True,'catalog':tables,'named_resources':resources,'documents_read':False,'user_records_read':False,'credentials_exported':False}
 finally:
  await conn.close()
sink=io.StringIO()
try:
 with contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
  result=asyncio.run(asyncio.wait_for(inspect(),20))
except Exception as exc:
 result={'status':'INCOMPLETE','error_class':type(exc).__name__,'raw_errors_exported':False}
print(json.dumps(result,default=str,sort_keys=True))
'''


class Refused(Exception):
    pass


def query(args):
    result = subprocess.run(BASE + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:
        raise Refused("READ_QUERY_FAILED")
    return json.loads(result.stdout)


def summary(deployment):
    return {
        "uid": deployment["metadata"]["uid"],
        "generation": deployment["metadata"]["generation"],
        "spec_sha256": hashlib.sha256(json.dumps(deployment["spec"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest(),
        "images": [c["image"] for c in deployment["spec"]["template"]["spec"]["containers"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-readonly", action="store_true", required=True)
    parser.parse_args()
    deployments = {}
    originals = {}
    for name in ("gateway", "weknora-app", "weknora-frontend"):
        obj = query(["get", "deployment", name, "-o", "json"])
        originals[name] = obj
        deployments[name] = summary(obj)
    gateway = originals["gateway"]
    if gateway["metadata"]["uid"] != GATEWAY_UID or deployments["gateway"]["images"] != [GATEWAY_IMAGE]:
        raise Refused("GATEWAY_BASELINE_CHANGED")
    desired = gateway["spec"]["replicas"]
    state = gateway.get("status", {})
    if gateway["spec"].get("paused") or state.get("observedGeneration") != gateway["metadata"]["generation"] or any(state.get(k, 0) != desired for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")):
        raise Refused("GATEWAY_NOT_STABLE")
    pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    ready = [p for p in pods if not p["metadata"].get("deletionTimestamp") and any(c.get("type") == "Ready" and c.get("status") == "True" for c in p.get("status", {}).get("conditions", [])) and any(c.get("name") == "gateway" and c.get("image") == GATEWAY_IMAGE for c in p["spec"]["containers"])]
    if not ready:
        raise Refused("NO_READY_GATEWAY")
    pod = sorted(ready, key=lambda p: p["metadata"]["name"])[0]
    name = pod["metadata"]["name"]
    before_pod = query(["get", "pod", name, "-o", "json"])
    observation = query(["exec", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", CHILD])
    after_pod = query(["get", "pod", name, "-o", "json"])
    if before_pod["metadata"]["uid"] != after_pod["metadata"]["uid"] or before_pod.get("status", {}).get("containerStatuses") != after_pod.get("status", {}).get("containerStatuses"):
        raise Refused("SAMPLED_CONTAINER_CHANGED")
    for deployment_name, original in originals.items():
        after = query(["get", "deployment", deployment_name, "-o", "json"])
        if summary(after) != summary(original):
            raise Refused("DEPLOYMENT_CHANGED")
    accepted = observation.get("status") == "CATALOG_AND_NAMED_RESOURCES_OBSERVED"
    print(json.dumps({
        "schema": "portal-weknora-company-knowledge-baseline.v1",
        "status": "READONLY_BASELINE_COLLECTED_NOT_ENABLED" if accepted else "INCOMPLETE",
        "deployments": deployments,
        "sample": {"pod": name, "uid": before_pod["metadata"]["uid"]},
        "observation": observation,
        "api_contract_acceptance_pending": True,
        "workload_modified": False, "database_modified": False, "files_modified": False,
        "credentials_exported": False, "provider_requests": 0, "membership_grants": 0,
    }, sort_keys=True))
    return 0 if accepted else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "INCOMPLETE", "error_class": type(exc).__name__, "error_code": str(exc) if isinstance(exc, Refused) else "READONLY_QUERY_FAILED", "raw_errors_exported": False, "workload_modified": False, "database_modified": False, "files_modified": False}))
        raise SystemExit(1)
