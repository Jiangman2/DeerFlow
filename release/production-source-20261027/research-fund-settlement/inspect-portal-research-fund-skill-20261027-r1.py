#!/usr/bin/env python3
"""Read-only Skill/storage baseline. Host Python 3.7+, no application imports."""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time

BASE = [
    "kubectl",
    "--kubeconfig=/etc/kubernetes/admin.conf",
    "--request-timeout=15s",
    "-n",
    "deer-flow",
]
NAME = "research-fund-settlement"
DEADLINE = 240
MAX_COMMANDS = 45
COUNTER = 0
STARTED = 0

CHILD = r"""
import ast,hashlib,json,os,pathlib,stat,sys,time
started=time.monotonic()
def sha(b): return hashlib.sha256(b).hexdigest()
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def read(p,limit=2097152):
 p=pathlib.Path(p)
 for q in (p,)+tuple(p.parents):
  if q.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')
 a=p.lstat()
 if not stat.S_ISREG(a.st_mode) or a.st_size>limit: raise RuntimeError('FILE_LIMIT')
 b=p.read_bytes(); z=p.lstat()
 if (a.st_ino,a.st_dev,a.st_size,a.st_mtime_ns)!=(z.st_ino,z.st_dev,z.st_size,z.st_mtime_ns) or len(b)!=a.st_size: raise RuntimeError('FILE_CHANGED')
 return b,a
def tree(root):
 root=pathlib.Path(root)
 if not root.exists(): return {'present':False}
 for p in (root,)+tuple(root.parents):
  if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')
 if not root.is_dir(): raise RuntimeError('NOT_DIRECTORY')
 rows=[]; total=0; cache=0
 for parent,dirs,files in os.walk(str(root),followlinks=False):
  if time.monotonic()-started>45: raise RuntimeError('SCAN_DEADLINE')
  for n in list(dirs):
   p=pathlib.Path(parent)/n
   if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')
   if n=='__pycache__': dirs.remove(n); cache+=1
  for n in sorted(files):
   p=pathlib.Path(parent)/n
   if n.endswith(('.pyc','.pyo')):
    if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')
    cache+=1; continue
   b,s=read(p); total+=len(b)
   if len(rows)>=512 or total>8388608: raise RuntimeError('PACKAGE_LIMIT')
   rows.append({'path':p.relative_to(root).as_posix(),'bytes':len(b),'sha256':sha(b)})
 rows.sort(key=lambda x:x['path'])
 if not any(r['path']=='SKILL.md' for r in rows): raise RuntimeError('SKILL_MD_MISSING')
 b,_=read(root/'SKILL.md')
 front=b.split(b'---',2)[1] if b.startswith(b'---') and len(b.split(b'---',2))==3 else b''
 s=root.stat()
 return {'present':True,'files':rows,'tree_sha256':sha(canonical(rows)),'bytes':total,'file_count':len(rows),
         'frontmatter_sha256':sha(front),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'ignored_cache_entries':cache}
mode=sys.argv[1]
if mode=='gateway':
 out={'public':tree('/app/skills/public/research-fund-settlement'),'custom':tree('/app/skills/custom/research-fund-settlement'),'runtime_sources':{}}
 paths={
  'activation':'/app/backend/packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py',
  'types':'/app/backend/packages/harness/deerflow/skills/types.py',
  'projection':'/app/backend/packages/harness/deerflow/skills/projection.py',
  'local_storage':'/app/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py',
  'catalog':'/app/backend/packages/harness/deerflow/skills/catalog.py',
  'router':'/app/backend/app/gateway/routers/skills.py'}
 for key,path in sorted(paths.items()):
  if not pathlib.Path(path).exists(): out['runtime_sources'][key]={'present':False}; continue
  b,_=read(path); t=b.decode('utf8'); parsed=ast.parse(t)
  functions={n.name for n in ast.walk(parsed) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
  out['runtime_sources'][key]={'present':True,'sha256':sha(b),'bytes':len(b)}
  if key=='activation':
   out['runtime_sources'][key]['read_skill_content_function_present']='_read_skill_content' in functions
   out['runtime_sources'][key]['activation_from_workflow_present']='_activation_from_workflow' in functions
   out['runtime_sources'][key]['body_read_text_present']='.read_text(' in t
 config=pathlib.Path('/app/backend/config.yaml')
 if config.exists():
  b,_=read(config)
  try:
   import yaml
   d=yaml.safe_load(b) or {}; sk=d.get('skills') or {}
   out['skills_configuration']={}
   for k in ('path','use','container_path'):
    v=sk.get(k)
    if v is None: out['skills_configuration'][k]=None
    elif isinstance(v,str) and len(v)<256 and (v.startswith(('/app/skills','/mnt/skills','deerflow.skills.')) or v in ('local','filesystem')): out['skills_configuration'][k]=v
    else: out['skills_configuration'][k]={'value_sha256':sha(canonical(v))}
  except ImportError: out['skills_configuration']={'status':'YAML_READER_UNAVAILABLE'}
elif mode=='sandbox':
 root=pathlib.Path('/mnt/skills/public/research-fund-settlement'); out={'present':root.exists(),'files':[]}
 for n in ('SKILL.md','scripts/report_cli.py','scripts/group_template.py','scripts/standard_input.py','scripts/text_color.py'):
  p=root/n
  if p.exists():
   b,_=read(p); out['files'].append({'path':n,'sha256':sha(b),'bytes':len(b)})
else: raise RuntimeError('INVALID_MODE')
out.update({'application_imported':False,'files_modified':False,'credentials_exported':False})
print(json.dumps(out,sort_keys=True))
"""


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def run(args, input_bytes=None):
    global COUNTER
    require(time.monotonic() - STARTED < DEADLINE, "HOST_DEADLINE")
    COUNTER += 1
    require(COUNTER <= MAX_COMMANDS, "COMMAND_BUDGET")
    require(args[0] in ("get", "exec"), "READONLY_COMMAND_REQUIRED")
    proc = subprocess.run(
        BASE + args,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    require(proc.returncode == 0, "READ_COMMAND_FAILED")
    require(len(proc.stdout) <= 8388608, "OUTPUT_LIMIT")
    return json.loads(proc.stdout)


def get(kind, name=None, selector=None):
    args = ["get", kind]
    if name:
        require(
            re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name) is not None,
            "INVALID_RESOURCE_NAME",
        )
        args.append(name)
    if selector:
        args += ["-l", selector]
    return run(args + ["-o", "json"])


def fingerprint(obj):
    m = obj["metadata"]
    return {
        "name": m["name"],
        "uid": m["uid"],
        "generation": m.get("generation"),
        "spec_sha256": digest(obj["spec"]),
    }


def controlled_by(obj, kind, uid):
    return any(
        o.get("controller") is True and o.get("kind") == kind and o.get("uid") == uid
        for o in obj["metadata"].get("ownerReferences", [])
    )


def ready(pod, container):
    statuses = [
        s
        for s in pod.get("status", {}).get("containerStatuses", [])
        if s.get("name") == container
    ]
    return (
        not pod["metadata"].get("deletionTimestamp")
        and pod.get("status", {}).get("phase") == "Running"
        and len(statuses) == 1
        and statuses[0].get("ready") is True
        and "running" in statuses[0].get("state", {})
    )


def mounted_claims(spec, mount_path):
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    rows = []
    for c in spec.get("containers", []):
        for m in c.get("volumeMounts", []):
            if m.get("mountPath") == mount_path:
                v = volumes.get(m["name"], {})
                rows.append(
                    {
                        "container": c["name"],
                        "claim": v.get("persistentVolumeClaim", {}).get("claimName"),
                        "read_only": m.get("readOnly", False),
                        "subpath_present": bool(m.get("subPath")),
                    }
                )
    return rows


def sandbox_kind(spec):
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    matches = []
    for c in spec.get("containers", []):
        if c["name"] != "sandbox":
            continue
        for m in c.get("volumeMounts", []):
            path = m.get("mountPath")
            if path not in ("/mnt/skills", "/mnt/skills/public"):
                continue
            sub = m.get("subPath", "")
            kind = (
                "runtime_projection"
                if "/skills_view/policies/" in sub
                else "published_employee_snapshot"
                if "/agent-skills/" in sub
                else "direct_public_pvc"
                if path == "/mnt/skills/public" and sub in ("", "public")
                else "other"
            )
            matches.append(
                {
                    "kind": kind,
                    "mount_path": path,
                    "claim": volumes.get(m["name"], {})
                    .get("persistentVolumeClaim", {})
                    .get("claimName"),
                    "read_only": m.get("readOnly", False),
                    "subpath_sha256": digest(sub),
                }
            )
    return matches


def observe(pod, container, mode):
    m = pod["metadata"]
    return run(
        [
            "exec",
            "-i",
            m["name"],
            "-c",
            container,
            "--",
            "python3",
            "-I",
            "-B",
            "-",
            mode,
        ],
        CHILD.encode(),
    )


def main():
    global STARTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-readonly", action="store_true", required=True)
    parser.parse_args()
    STARTED = time.monotonic()
    report = {
        "schema": "portal-research-fund-skill-baseline.v1",
        "status": "INCOMPLETE",
        "workload_modified": False,
        "files_modified": False,
        "database_access": False,
        "secret_api_reads": 0,
        "credentials_exported": False,
        "provider_requests": 0,
        "skill_update_requested": False,
        "production_update_pending": True,
    }
    try:
        require(socket.gethostname().split(".")[0] == "datax-01", "WRONG_HOST")
        require(os.geteuid() == 0, "ROOT_REQUIRED")
        deployments = {
            n: get("deployment", n)
            for n in ("gateway", "ppt-master-runner", "provisioner")
        }
        report["deployments"] = {n: fingerprint(d) for n, d in deployments.items()}
        report["skills_mounts"] = {
            n: mounted_claims(d["spec"]["template"]["spec"], "/app/skills")
            for n, d in deployments.items()
        }
        claims = {
            m["claim"]
            for n in ("gateway", "ppt-master-runner")
            for m in report["skills_mounts"][n]
        }
        require(len(claims) == 1 and None not in claims, "SKILLS_PVC_AMBIGUOUS")
        claim = next(iter(claims))
        report["provisioner_skills_pvc_values"] = [
            e["value"]
            for c in deployments["provisioner"]["spec"]["template"]["spec"].get(
                "containers", []
            )
            for e in c.get("env", [])
            if e.get("name") == "SKILLS_PVC_NAME" and "value" in e
        ]
        pvc = get("pvc", claim)
        report["skills_pvc"] = {
            "name": claim,
            "uid": pvc["metadata"]["uid"],
            "phase": pvc.get("status", {}).get("phase"),
            "access_modes": pvc["spec"].get("accessModes"),
            "volume_name": pvc["spec"].get("volumeName"),
            "storage_class": pvc["spec"].get("storageClassName"),
        }
        require(report["skills_pvc"]["phase"] == "Bound", "SKILLS_PVC_NOT_BOUND")
        pv = get("pv", pvc["spec"]["volumeName"])
        kinds = [
            k
            for k in ("nfs", "csi", "local", "hostPath", "cephfs", "glusterfs")
            if k in pv["spec"]
        ]
        report["skills_pv"] = {
            "name": pv["metadata"]["name"],
            "uid": pv["metadata"]["uid"],
            "storage_kind": kinds,
            "source_fingerprint": digest({k: pv["spec"][k] for k in kinds}),
        }
        gateway = deployments["gateway"]
        labels = gateway["spec"]["selector"].get("matchLabels", {})
        require(
            labels and not gateway["spec"]["selector"].get("matchExpressions"),
            "SELECTOR_UNSUPPORTED",
        )
        selector = ",".join(k + "=" + v for k, v in sorted(labels.items()))
        sets = get("replicasets", selector=selector)["items"]
        owned = {
            r["metadata"]["uid"]
            for r in sets
            if controlled_by(r, "Deployment", gateway["metadata"]["uid"])
        }
        pods = get("pods", selector=selector)["items"]
        report["gateway_counts"] = {
            "desired": gateway["spec"].get("replicas"),
            "status": {
                k: gateway.get("status", {}).get(k)
                for k in (
                    "observedGeneration",
                    "replicas",
                    "updatedReplicas",
                    "readyReplicas",
                    "availableReplicas",
                    "terminatingReplicas",
                )
            },
        }
        names = {c["name"] for c in gateway["spec"]["template"]["spec"]["containers"]}
        require("gateway" in names, "GATEWAY_CONTAINER_CHANGED")
        bynode = {}
        for pod in sorted(pods, key=lambda p: p["metadata"]["name"]):
            if ready(pod, "gateway") and any(
                controlled_by(pod, "ReplicaSet", u) for u in owned
            ):
                bynode.setdefault(pod["spec"]["nodeName"], pod)
        require(0 < len(bynode) <= 8, "GATEWAY_NODE_SAMPLES_CHANGED")
        report["samples"] = []
        report["sample_scope"] = "ONE_READY_OWNED_GATEWAY_PER_NODE_NOT_ALL_WORKERS"
        tree_hashes = set()
        runtime_hashes = set()
        for node, pod in sorted(bynode.items()):
            observed = observe(pod, "gateway", "gateway")
            require(observed["public"].get("present"), "PUBLIC_SKILL_MISSING")
            tree_hashes.add(observed["public"]["tree_sha256"])
            runtime_hashes.add(digest(observed["runtime_sources"]))
            if report["samples"]:
                for category in ("public", "custom"):
                    observed[category].pop("files", None)
            report["samples"].append(
                {
                    "node": node,
                    "pod": pod["metadata"]["name"],
                    "uid": pod["metadata"]["uid"],
                    "image": [
                        c["image"]
                        for c in pod["spec"]["containers"]
                        if c["name"] == "gateway"
                    ][0],
                    "observation": observed,
                }
            )
            print(
                json.dumps(
                    {
                        "phase": "READONLY_SKILL_SAMPLE",
                        "checked": len(report["samples"]),
                        "total": len(bynode),
                    }
                ),
                flush=True,
            )
        report["sampled_trees_consistent"] = len(tree_hashes) == 1
        report["sampled_runtime_sources_consistent"] = len(runtime_hashes) == 1
        sandboxes = get("pods", selector="app=deer-flow-sandbox")["items"]
        report["sandbox_mount_kinds"] = {}
        report["sandbox_samples"] = []
        sampled = set()
        for pod in sorted(sandboxes, key=lambda p: p["metadata"]["name"]):
            if not ready(pod, "sandbox"):
                continue
            for mount in sandbox_kind(pod["spec"]):
                kind = mount["kind"]
                report["sandbox_mount_kinds"][kind] = (
                    report["sandbox_mount_kinds"].get(kind, 0) + 1
                )
                if kind not in sampled and len(sampled) < 3:
                    observed = observe(pod, "sandbox", "sandbox")
                    report["sandbox_samples"].append(
                        {
                            "identity_sha256": digest(pod["metadata"]["uid"]),
                            "mount": mount,
                            "observation": observed,
                        }
                    )
                    sampled.add(kind)
        for n, old in deployments.items():
            fresh = get("deployment", n)
            require(
                fingerprint(fresh) == fingerprint(old),
                "DEPLOYMENT_CHANGED_DURING_INSPECTION",
            )
        fresh_pvc = get("pvc", claim)
        require(
            fresh_pvc["metadata"]["uid"] == pvc["metadata"]["uid"]
            and fresh_pvc["spec"].get("volumeName") == pvc["spec"].get("volumeName"),
            "SKILLS_VOLUME_CHANGED",
        )
        require(
            len(tree_hashes) == 1 and len(runtime_hashes) == 1, "SAMPLED_SOURCES_DIFFER"
        )
        report["status"] = "READONLY_SKILL_BASELINE_COLLECTED_NOT_UPDATED"
        report["exitcode"] = 0
    except Exception as exc:
        report["error_class"] = type(exc).__name__
        report["error_code"] = (
            str(exc) if isinstance(exc, Refused) else "BOUNDED_READ_FAILED"
        )
        report["exitcode"] = 1
    report["read_commands"] = COUNTER
    report["elapsed_seconds"] = round(time.monotonic() - STARTED, 3)
    print(json.dumps(report, sort_keys=True))
    return report["exitcode"]


if __name__ == "__main__":
    sys.exit(main())
