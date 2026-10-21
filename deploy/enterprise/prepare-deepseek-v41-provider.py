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
PRIOR_ATTEMPT = None  # Optional pinned, no-provider/no-Secret failure receipt.
ONCE_NAME = "portal-deepseek-v41-preparation-20261020-r1.once"


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
        capture_output=True,
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


def read_private(parent, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077 and info.st_size <= limit, "prior_file_permissions_or_size")
        data = stream.read(limit + 1)
        require(len(data) <= limit, "prior_file_limit")
        return data


def verify_prior_attempt(parent, prior):
    require(isinstance(prior, dict), "prior_binding_required")
    operation = prior["operation"]
    require(len(operation) == 32 and all(c in "0123456789abcdef" for c in operation), "prior_operation_invalid")
    kind = prior.get("kind", "noop")
    require(kind in ("noop", "flash_http_503"), "prior_kind_invalid")
    marker_name = ONCE_NAME
    if kind == "flash_http_503":
        previous = prior.get("resumed_noop_operation", "")
        require(len(previous) == 32 and all(c in "0123456789abcdef" for c in previous), "prior_parent_operation_invalid")
        marker_name = "portal-deepseek-v41-resume-" + previous + ".once"
    rootfd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        marker = json.loads(read_private(rootfd, marker_name, 4096))
        require(marker == dict(operation=operation), "prior_once_identity_changed")
        folderfd = os.open("portal-deepseek-v41-preparation-" + operation, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
        try:
            info = os.fstat(folderfd)
            require(info.st_uid == 0 and not info.st_mode & 0o077, "prior_directory_permissions")
            raw = read_private(folderfd, "REPORT.json", 65536)
        finally:
            os.close(folderfd)
    finally:
        os.close(rootfd)
    require(len(raw) == prior["bytes"] and hashlib.sha256(raw).hexdigest() == prior["sha256"], "prior_report_mismatch")
    value = json.loads(raw)
    require(value.get("operation") == operation and value.get("status") == "INCOMPLETE", "prior_failure_mismatch")
    require(all(value.get(k) is False for k in ("secret_creation_requested", "secret_creation_may_have_applied", "workload_modified", "configuration_modified", "gateway_restarted")), "prior_attempt_may_have_side_effects")
    if kind == "noop":
        require(value.get("error_code") == "gateway_rollout_in_progress" and value.get("provider_probe_requested") is False, "prior_attempt_may_have_side_effects")
    else:
        require(value.get("error_code") == "provider_acceptance_failed" and value.get("provider_probe_requested") is True
                and value.get("resumed_noop_operation") == previous and value.get("profile_sha256") == hashlib.sha256(canonical(PROFILE)).hexdigest(), "prior_provider_binding_changed")
        provider = value.get("provider", {})
        require(provider.get("status") == "INCOMPLETE" and provider.get("stage") == "flash" and provider.get("http_status") == 503
                and provider.get("provider_requests_attempted") == 1 and provider.get("checks") == [] and provider.get("automatic_retry") is False
                and provider.get("business_tools_executed") == 0
                and provider.get("stage_results") == dict(flash="FAIL", thinking="NOT_ATTEMPTED", tool_call="NOT_ATTEMPTED", tool_resume="NOT_ATTEMPTED", multi_image="NOT_ATTEMPTED"),
                "prior_provider_outcome_not_safe_to_retry")
    return operation


def claim_once(parent, name, report):
    fd = os.open(str(parent / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(dict(operation=report["operation"])))
        stream.flush()
        os.fsync(stream.fileno())
    report["once_claimed"] = True


def prepare(report, before_provider=None):
    require(
        isinstance(PROFILE, dict) and isinstance(PROBE_SOURCE, str),
        "use_packaged_script",
    )
    initial = call(["get", "deployment", "gateway", "-o", "json"])
    require(initial["metadata"]["uid"] == GATEWAY_UID, "gateway_identity_changed")
    desired = initial["spec"]["replicas"]
    report["gateway_before"] = binding(initial)
    report["gateway_readiness"] = {k: initial.get("status", {}).get(k) for k in ("observedGeneration", "replicas", "updatedReplicas", "readyReplicas", "availableReplicas", "unavailableReplicas", "terminatingReplicas")}
    report["gateway_readiness"]["desired"] = desired
    require(desired == 70, "gateway_replica_count_changed")
    require(
        initial.get("status", {}).get("observedGeneration") == initial["metadata"]["generation"]
        and all(initial["status"].get(k) == desired for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))
        and not initial["status"].get("unavailableReplicas", 0)
        and not initial["status"].get("terminatingReplicas", 0),
        "gateway_rollout_in_progress",
    )
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
    if before_provider is not None:
        before_provider()
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
        once = ONCE_NAME
        if PRIOR_ATTEMPT is not None:
            prior_operation = verify_prior_attempt(parent, PRIOR_ATTEMPT)
            recovery_field = "resumed_provider_503_operation" if PRIOR_ATTEMPT.get("kind") == "flash_http_503" else "resumed_noop_operation"
            report[recovery_field] = prior_operation
            once = "portal-deepseek-v41-resume-" + prior_operation + ".once"
        require(not os.path.lexists(str(parent / once)), "preparation_already_claimed")
        report["profile_sha256"] = hashlib.sha256(canonical(PROFILE)).hexdigest()
        # Read-only readiness failures do not consume a new provider attempt.
        # The exclusive marker is still created before any request or Secret write.
        prepare(report, before_provider=lambda: claim_once(parent, once, report))
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
