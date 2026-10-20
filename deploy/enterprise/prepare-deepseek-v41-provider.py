"""Server preparation template; package with package-deepseek-v41.py before use.

Python 3.7 controller, isolated current-runtime canary, then a new immutable
Secret. Never patches a Deployment, edits configuration or restarts a workload.
"""

import base64
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

PROFILE = None  # Filled by the reproducible packager.
PROBE_SOURCE = None
NAMESPACE = "deer-flow"
SECRET_KEY = "DEEPSEEK_V4_1_FLASH_API_KEY"
GATEWAY_UID = "a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1"


class Refused(ValueError):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def binding(value):
    return dict(
        uid=value["metadata"]["uid"],
        generation=value["metadata"]["generation"],
        spec_sha256=hashlib.sha256(canonical(value["spec"])).hexdigest(),
    )


def call(args, payload=None, timeout=25):
    binary = shutil.which(
        "kubectl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )
    require(binary in ("/usr/bin/kubectl", "/usr/local/bin/kubectl"), "kubectl_missing")
    for path in (binary, os.path.dirname(binary)):
        info = os.lstat(path)
        require(
            info.st_uid == 0
            and not info.st_mode & 0o022
            and not stat.S_ISLNK(info.st_mode),
            "kubectl_permissions",
        )
    process = subprocess.run(
        [
            binary,
            "--kubeconfig=/etc/kubernetes/admin.conf",
            "--request-timeout=" + str(timeout) + "s",
            "-n",
            NAMESPACE,
        ]
        + args,
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    require(process.returncode == 0, "kubectl_command_failed")
    require(len(process.stdout) <= 8388608, "kubectl_output_limit")
    return json.loads(process.stdout)


REMOTE = r"""
import sys,json,os,io,contextlib,logging,asyncio,ssl,hashlib
data=json.loads(sys.stdin.buffer.read(262145))
assert set(data)=={'key','source','profile'}
logging.disable(logging.CRITICAL)
for name in ('LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2','LANGSMITH_TRACING'):
 os.environ[name]='false'
with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
 import httpx
 scope={'__name__':'isolated_deepseek_v41_probe'}
 exec(compile(data['source'],'<isolated-deepseek-v41-probe>','exec'),scope)
 class BoundedStream(httpx.AsyncByteStream):
  def __init__(self,inner):self.inner=inner
  async def __aiter__(self):
   size=0
   async for value in self.inner:
    size+=len(value)
    if size>2097152:raise RuntimeError('response_size_limit')
    yield value
  async def aclose(self):await self.inner.aclose()
 async def bound(response):response.stream=BoundedStream(response.stream)
 async def run():
  ctx=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None)
  async with httpx.AsyncClient(verify=ctx,trust_env=False,follow_redirects=False,timeout=180,event_hooks={'response':[bound]}) as client:
   return await scope['run_probe'](data['key'],client,data['profile'])
 try:
  result=asyncio.run(run())
 except Exception as e:
  result={'status':'INCOMPLETE','error_class':type(e).__name__,'raw_output_exported':False}
 import deerflow.models.factory as factory
 import deerflow.models.robust_openai as adapter
 import pathlib
 result['runtime_sources']={n:hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest() for n,m in [('factory',factory),('robust_openai',adapter)]}
print(json.dumps(result,sort_keys=True))
"""


def prepare(report):
    require(
        isinstance(PROFILE, dict) and isinstance(PROBE_SOURCE, str),
        "use_packaged_script",
    )
    initial = call(["get", "deployment", "gateway", "-o", "json"])
    require(initial["metadata"]["uid"] == GATEWAY_UID, "gateway_identity_changed")
    desired = initial["spec"]["replicas"]
    require(
        desired == 70
        and initial.get("status", {}).get("updatedReplicas") == desired
        and initial["status"].get("readyReplicas") == desired,
        "gateway_rollout_in_progress",
    )
    report["gateway_before"] = binding(initial)
    images = {
        c["image"]
        for c in initial["spec"]["template"]["spec"]["containers"]
        if c["name"] == "gateway"
    }
    require(len(images) == 1, "gateway_container_shape")
    pods = call(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    eligible = [
        p
        for p in pods
        if not p["metadata"].get("deletionTimestamp")
        and p.get("status", {}).get("phase") == "Running"
        and any(
            c.get("name") == "gateway" and c.get("ready")
            for c in p["status"].get("containerStatuses", [])
        )
        and any(
            c["name"] == "gateway" and c["image"] in images
            for c in p["spec"]["containers"]
        )
    ]
    require(bool(eligible), "no_ready_gateway")
    pod = sorted(eligible, key=lambda p: p["metadata"]["name"])[0]
    name, uid = pod["metadata"]["name"], pod["metadata"]["uid"]
    report["sample"] = dict(pod=name, uid=uid, node=pod["spec"]["nodeName"])
    require(sys.stdin.isatty(), "interactive_hidden_key_input_required")
    key = getpass.getpass("DeepSeek V4.1 API Key (hidden; key only): ")
    require(
        20 <= len(key) <= 4096
        and key.strip() == key
        and not any(c.isspace() for c in key),
        "key_format",
    )
    report["provider_probe_requested"] = True
    print(
        json.dumps(
            dict(
                phase="DEEPSEEK_V41_PROVIDER_CANARY",
                maximum_provider_calls=5,
                gateway_restart=False,
            )
        ),
        flush=True,
    )
    result = call(
        [
            "exec",
            "-i",
            name,
            "-c",
            "gateway",
            "--",
            "/app/backend/.venv/bin/python",
            "-I",
            "-B",
            "-c",
            REMOTE,
        ],
        canonical(dict(key=key, source=PROBE_SOURCE, profile=PROFILE)),
        timeout=960,
    )
    report["provider"] = result
    require(
        result.get("status") == "PROVIDER_ACCEPTED_NOT_DEPLOYED"
        and result.get("provider_requests_attempted") == 5,
        "provider_acceptance_failed",
    )
    require(
        binding(call(["get", "deployment", "gateway", "-o", "json"]))
        == report["gateway_before"],
        "gateway_changed_during_probe",
    )
    current = call(["get", "pod", name, "-o", "json"])
    require(
        current["metadata"]["uid"] == uid
        and current["status"].get("containerStatuses")
        == pod["status"].get("containerStatuses"),
        "probe_container_changed",
    )
    secret_name = "deer-flow-deepseek-v41-" + report["operation"][:16]
    report["secret_name"] = secret_name
    secret = dict(
        apiVersion="v1",
        kind="Secret",
        metadata=dict(name=secret_name, namespace=NAMESPACE),
        immutable=True,
        type="Opaque",
        data={SECRET_KEY: base64.b64encode(key.encode("utf-8")).decode("ascii")},
    )
    report["secret_creation_requested"] = True
    report["secret_creation_may_have_applied"] = True
    result = call(
        [
            "create",
            "-f",
            "-",
            "-o",
            'go-template={"uid":"{{.metadata.uid}}","immutable":{{.immutable}}}',
        ],
        canonical(secret),
    )
    require(
        isinstance(result.get("uid"), str) and result.get("immutable") is True,
        "secret_creation_unconfirmed",
    )
    report["secret"] = dict(
        name=secret_name,
        namespace=NAMESPACE,
        key=SECRET_KEY,
        uid=result["uid"],
        immutable=True,
    )
    report.update(
        status="DEEPSEEK_V41_PROVIDER_ACCEPTED_SECRET_PREPARED_GATEWAY_NOT_CHANGED"
    )


def main():
    report = dict(
        schema="portal-deepseek-v41-preparation.v1",
        status="INCOMPLETE",
        operation=uuid.uuid4().hex,
        automatic_retry=False,
        gateway_restarted=False,
        workload_modified=False,
        configuration_modified=False,
        provider_probe_requested=False,
        secret_creation_requested=False,
        secret_creation_may_have_applied=False,
        credentials_exported=False,
        ready_for_rollout=False,
    )
    directory = None
    try:
        require(
            sys.argv[1:] == ["--prepare-deepseek-v41-once"],
            "explicit_invocation_required",
        )
        require(
            os.name == "posix"
            and os.geteuid() == 0
            and sys.flags.isolated
            and sys.flags.dont_write_bytecode
            and socket.gethostname().split(".")[0] == "datax-01",
            "isolated_root_datax01_required",
        )
        parent = pathlib.Path("/var/lib")
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o022,
            "report_parent_permissions",
        )
        directory = parent / ("portal-deepseek-v41-preparation-" + report["operation"])
        directory.mkdir(mode=0o700)
        # Shared once marker prevents uncertain model/Secret creation from automatic resubmission.
        fd = os.open(
            str(parent / "portal-deepseek-v41-preparation-20261020-r1.once"),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(dict(operation=report["operation"])))
            stream.flush()
            os.fsync(stream.fileno())
        report["profile_sha256"] = hashlib.sha256(canonical(PROFILE)).hexdigest()
        prepare(report)
    except BaseException as exc:
        report["error_class"] = type(exc).__name__
        report["error_code"] = (
            str(exc) if isinstance(exc, Refused) else "LOCAL_OR_TRANSPORT_FAILURE"
        )
    report["exitcode"] = (
        0 if report["status"].startswith("DEEPSEEK_V41_PROVIDER_ACCEPTED_SECRET") else 1
    )
    if directory is not None:
        raw = canonical(report) + b"\n"
        path = directory / "REPORT.json"
        fd = os.open(
            str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        report.update(
            report_path=str(path),
            report_bytes=len(raw),
            report_sha256=hashlib.sha256(raw).hexdigest(),
        )
    print(json.dumps(report, sort_keys=True))
    return report["exitcode"]


if __name__ == "__main__":
    sys.exit(main())
