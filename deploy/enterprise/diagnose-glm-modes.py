"""Explicit five-request candidate check; no production configuration writes.

Packaged with public adapter/probe sources. Operator host supports Python 3.7.
The child uses the existing Gateway environment credential without exporting it.
"""

import hashlib
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import uuid

ADAPTER_SOURCE = None
PROBE_SOURCE = None
PROFILE = None

REMOTE = r'''
import asyncio, contextlib, hashlib, io, json, logging, os, ssl, sys, types
logging.disable(logging.CRITICAL)
result = {"status":"INCOMPLETE", "credentials_exported":False, "configuration_modified":False}
try:
 with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
  data=json.load(sys.stdin)
  from deerflow.config import get_app_config
  config=get_app_config()
  current=config.get_model_config("glm-5.3-flash")
  if current is None: raise ValueError("model_absent")
  row=current.model_dump()
  expected=data["profile"]
  if row.get("model")!=expected["model"] or row.get("base_url")!=expected["base_url"] or row.get("use")!=expected["use"]:
   raise ValueError("profile_identity_changed")
  key=os.environ.get("GLM_5_3_FLASH_API_KEY", "")
  if not key or key.strip()!=key: raise ValueError("key_unavailable")
  result["current_capabilities"]={k:row.get(k) for k in ("supports_thinking","thinking_always_on","supports_vision","supports_reasoning_effort")}
  adapter=types.ModuleType("deerflow.models.enterprise_glm")
  sys.modules[adapter.__name__]=adapter
  exec(compile(data["adapter"], "<candidate-glm-adapter>", "exec"),adapter.__dict__)
  probe=types.ModuleType("portal_glm_modes_candidate")
  exec(compile(data["probe"], "<candidate-glm-probe>", "exec"),probe.__dict__)
  from deerflow.config.app_config import AppConfig
  from deerflow.models.factory import create_chat_model
  from langchain_core.messages import HumanMessage
  candidate=dict(expected);candidate["api_key"]=key
  candidate_config=AppConfig(models=[candidate],sandbox=config.sandbox)
  async def run():
   import httpx
   ca=os.environ.get("GLM_5_3_FLASH_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or None
   async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=ca),trust_env=False,follow_redirects=False,timeout=30) as http:
    for enabled in (False,True):
     m=create_chat_model("glm-5.3-flash",thinking_enabled=enabled,app_config=candidate_config,attach_tracing=False,http_async_client=http)
     payload=m._get_request_payload([HumanMessage("synthetic")])
     if payload["extra_body"]["thinking"]["type"]!=("enabled" if enabled else "disabled"): raise ValueError("factory_mode_mismatch")
    result["candidate_factory_modes_verified"]=True
    return await probe.run_probe(key,http,request_deadline=120,verify_modes=True)
  result["provider"]=asyncio.run(run())
  result["status"]="CANDIDATE_MODES_AND_VISION_ACCEPTED_NOT_DEPLOYED" if result["provider"]["glm_canary"]=="PASS" else "INCOMPLETE"
except Exception as e:
 result["error_class"]=type(e).__name__
print(json.dumps(result,sort_keys=True))
'''


def require(value):
    if not value:
        raise ValueError("precondition_failed")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def binding(deployment):
    return dict(uid=deployment["metadata"]["uid"], generation=deployment["metadata"]["generation"], spec_sha256=hashlib.sha256(canonical(deployment["spec"])).hexdigest())


def main():
    report = dict(schema="portal-glm-modes-diagnosis.v1", status="INCOMPLETE", workload_modified=False,
                  configuration_modified=False, credentials_exported=False, automatic_retry=False,
                  provider_probe_requested=False, maximum_provider_calls=5,
                  candidate_loaded_only_in_probe_process=True, ready_for_rollout=False)
    folder = None
    try:
        require(sys.argv[1:] == ["--diagnose-glm-modes-once"] and sys.platform == "linux" and os.geteuid() == 0)
        require(sys.flags.isolated and sys.dont_write_bytecode and socket.gethostname().split(".")[0] == "datax-01")
        require(isinstance(ADAPTER_SOURCE, str) and isinstance(PROBE_SOURCE, str) and isinstance(PROFILE, dict))
        report["candidate_sha256"] = dict(adapter=hashlib.sha256(ADAPTER_SOURCE.encode()).hexdigest(), probe=hashlib.sha256(PROBE_SOURCE.encode()).hexdigest(),
                                          profile=hashlib.sha256(canonical(PROFILE)).hexdigest())
        parent = pathlib.Path("/var/lib")
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        operation = uuid.uuid4().hex
        folder = parent / ("portal-glm-modes-diagnosis-" + operation)
        folder.mkdir(mode=0o700)
        report["operation"] = operation
        executable = next((p for p in ("/usr/bin/kubectl", "/usr/local/bin/kubectl") if os.path.isfile(p)), None)
        require(executable is not None)
        binary = pathlib.Path(executable).resolve().stat()
        require(binary.st_uid == 0 and not binary.st_mode & 0o022)

        def query(args, payload=None, timeout=25):
            process = subprocess.run([executable, "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"] + args,
                                     input=payload, capture_output=True, timeout=timeout)
            require(process.returncode == 0 and len(process.stdout) <= 8388608)
            return json.loads(process.stdout)

        deployment = query(["get", "deployment", "gateway", "-o", "json"])
        before = binding(deployment)
        require(before["uid"] == "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1")
        status = deployment["status"]
        count = deployment["spec"]["replicas"]
        require(count == 70 and status.get("observedGeneration") == before["generation"])
        require(all(status.get(k) == count for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")))
        require(not status.get("terminatingReplicas") and not status.get("unavailableReplicas"))
        report["gateway_before"] = before
        target = next(c["image"] for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
        pods = query(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
        eligible = [p for p in pods if not p["metadata"].get("deletionTimestamp") and p.get("status", {}).get("phase") == "Running"
                    and any(c["name"] == "gateway" and c["image"] == target for c in p["spec"]["containers"])
                    and any(c["name"] == "gateway" and c.get("ready") for c in p.get("status", {}).get("containerStatuses", []))]
        require(bool(eligible))
        pod = eligible[0]
        identity = dict(name=pod["metadata"]["name"], uid=pod["metadata"]["uid"], containers=pod["status"]["containerStatuses"])
        report["sample"] = dict(pod=identity["name"], uid=identity["uid"], node=pod["spec"]["nodeName"])
        marker = parent / "portal-glm-modes-diagnosis-20261021-r1.once"
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(dict(operation=operation)))
            stream.flush()
            os.fsync(stream.fileno())
        report["provider_probe_requested"] = True
        print(json.dumps(dict(phase="GLM_FLASH_THINKING_TOOL_VISION_CANARY", maximum_provider_calls=5)), flush=True)
        report["candidate"] = query(["exec", identity["name"], "-c", "gateway", "-i", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", REMOTE],
                                    canonical(dict(adapter=ADAPTER_SOURCE, probe=PROBE_SOURCE, profile=PROFILE)), timeout=660)
        fresh = query(["get", "pod", identity["name"], "-o", "json"])
        require(fresh["metadata"]["uid"] == identity["uid"] and not fresh["metadata"].get("deletionTimestamp"))
        require(fresh["status"]["containerStatuses"] == identity["containers"])
        require(binding(query(["get", "deployment", "gateway", "-o", "json"])) == before)
        report["gateway_binding_preserved"] = True
        report["status"] = report["candidate"]["status"]
    except Exception as exc:
        report["error_class"] = type(exc).__name__
    passed = report["status"] == "CANDIDATE_MODES_AND_VISION_ACCEPTED_NOT_DEPLOYED"
    report["exitcode"] = 0 if passed else 1
    if folder is not None:
        raw = canonical(report)
        path = folder / "REPORT.json"
        with os.fdopen(os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(raw)
        report.update(report_path=str(path), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(report, sort_keys=True))
    return report["exitcode"]


if __name__ == "__main__":
    raise SystemExit(main())
