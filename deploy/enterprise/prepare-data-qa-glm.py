"""Standalone Python 3.7 controller, populated by the pinned package builder.

Creates new immutable resources and two bounded candidate Jobs. Never patches
Deployments or reuses a production browser/session. Unknown writes are not retried.
"""

# ruff: noqa: F821 -- constants are supplied by the checked package builder.

import copy
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid

NAMESPACE = "deer-flow"
CONFIG_NAME = "deer-flow-config-hot-glm-20261019-f003c309f97d"
CONFIG_UID = "652e25a9-eb30-4454-8f1e-1d0d036b5253"
CONFIG_SHA = "0aa1f67942bdbe15f33dd80f99bd7ab7d01a88020afa4ab863c3baec98bdc3b3"
KEY = "APPFORGE_DATA_QA_API_KEY"
FRONT = dict(
    uid="bb0b3f6e-31f7-4073-8b76-7650ea5b96ee",
    generation=225,
    spec_sha256="1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123",
)


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


def binding(d):
    return dict(
        uid=d["metadata"]["uid"],
        generation=d["metadata"]["generation"],
        spec_sha256=digest(d["spec"]),
    )


def stable_gateway(d):
    require(
        binding(d) == RELEASE["field_acceptance"]["gateway"], "gateway_baseline_changed"
    )
    s = d.get("status", {})
    require(
        d["spec"]["replicas"] == 70
        and s.get("observedGeneration") == d["metadata"]["generation"]
        and all(
            s.get(k) == 70
            for k in (
                "replicas",
                "updatedReplicas",
                "readyReplicas",
                "availableReplicas",
            )
        )
        and not s.get("unavailableReplicas")
        and not s.get("terminatingReplicas"),
        "gateway_rollout_in_progress",
    )


def preload_gate():
    role = RELEASE["profile"]["roles"][0]
    require(
        len(PRELOADS) == 5 and set(PRELOADS) == set(RELEASE["profile"]["nodes"]),
        "five_preloads_required",
    )
    for host, receipt in PRELOADS.items():
        require(
            receipt["expected_node_uid"]
            == RELEASE["profile"]["nodes"][host]["expected_node_uid"],
            "preload_node_uid",
        )
        require(
            receipt["hostname"] == host
            and receipt["schema"] == "portal-gateway-data-qa-glm-preload.v1"
            and receipt["status"]
            == "COMPLETE_GATEWAY_DATA_QA_GLM_PRELOAD_REVIEW_REQUIRED"
            and receipt["exitcode"] == 0
            and receipt["node_preload_passed"] is True
            and receipt["old_references_preserved"] is True
            and receipt["image_import_may_be_partial"] is False,
            "preload_not_accepted",
        )
        require(len(receipt["roles"]) == 1, "preload_role_count")
        observed = receipt["roles"][0]
        require(
            all(
                observed[k] == role[k]
                for k in ("manifest_digest", "config_digest", "tag", "role")
            )
            and all(
                observed[k] == "PASS"
                for k in ("phase_a", "phase_b", "content_and_unpack", "cri_visibility")
            ),
            "preload_image_mismatch",
        )


def sealed_evidence():
    field = RELEASE["field_acceptance"]
    for name, path in (
        ("data_qa", field["data_qa"]["report_path"]),
        (
            "glm",
            "/var/lib/portal-glm-stream-candidate-"
            + field["glm"]["operation"]
            + ".json",
        ),
    ):
        expected = field[name]
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            st = os.fstat(stream.fileno())
            require(
                stat.S_ISREG(st.st_mode)
                and st.st_uid == 0
                and not stat.S_IMODE(st.st_mode) & 0o077
                and st.st_nlink == 1
                and st.st_size == expected["report_bytes"],
                "field_report_identity",
            )
            raw = stream.read(expected["report_bytes"] + 1)
        require(
            len(raw) == expected["report_bytes"]
            and hashlib.sha256(raw).hexdigest() == expected["report_sha256"],
            "field_report_digest",
        )


class Controller:
    def __init__(self, root, report, binary):
        self.root, self.report, self.binary = root, report, binary
        self.started = time.monotonic()
        self.calls = 0

    def call(self, phase, args, value=None, raw=False, timeout=25):
        self.calls += 1
        require(
            self.calls <= 160 and time.monotonic() - self.started < 900,
            "controller_budget",
        )
        command = [
            self.binary,
            "--kubeconfig=/etc/kubernetes/admin.conf",
            "--request-timeout=15s",
            "-n",
            NAMESPACE,
        ] + args
        request = canonical(value) if value is not None else None
        # Files are unlinked immediately. Raw config/Secret material is never
        # included in a report or written to a named diagnostic log.
        with tempfile.TemporaryFile(dir=str(self.root)) as out, tempfile.TemporaryFile(dir=str(self.root)) as err:  # fmt: skip -- Python 3.7 host
            p = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            try:
                p.communicate(request, timeout=timeout)
            except subprocess.TimeoutExpired:
                import signal

                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=5)
                raise Refused(phase + "_timeout")
            sizes = [os.fstat(f.fileno()).st_size for f in (out, err)]
            self.report["queries"].append(
                dict(
                    phase=phase,
                    returncode=p.returncode,
                    stdout_bytes=sizes[0],
                    stderr_bytes=sizes[1],
                )
            )
            require(max(sizes) <= 8388608, "command_output_limit")
            require(p.returncode == 0, phase + "_failed")
            out.seek(0)
            data = out.read()
        return data if raw else json.loads(data)

    def get(self, kind, name):
        return self.call("get_" + kind, ["get", kind, name, "-o", "json"])

    def save(self):
        path = self.root / "REPORT.json"
        raw = canonical(self.report) + b"\n"
        temporary = self.root / "REPORT.pending"
        fd = os.open(
            str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
        return dict(
            report_path=str(path),
            report_bytes=len(raw),
            report_sha256=hashlib.sha256(raw).hexdigest(),
        )

    def create(self, kind, obj):
        # Persist intent before any write; never retry a failed/uncertain create.
        self.report["resource_intents"].append(
            dict(
                kind=kind, name=obj["metadata"]["name"], creation_may_have_applied=True
            )
        )
        self.save()
        data = self.call(
            "create_" + kind,
            ["create", "-f", "-", "-o", "jsonpath={.metadata.uid}"],
            value=obj,
            raw=True,
        )
        uid = data.decode("ascii").strip()
        require(re.fullmatch(r"[a-f0-9-]{36}", uid), "created_resource_uid")
        self.report["resource_intents"][-1].update(uid=uid, creation_confirmed=True)
        self.save()
        return uid

    def claim_once(self):
        marker = self.root.parent / "portal-data-qa-glm-prepare-20261021-r1.once"
        fd = os.open(
            str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(
                canonical(
                    dict(
                        operation=self.report["operation"],
                        report_path=str(self.root / "REPORT.json"),
                    )
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        self.report["once_claimed"] = True


def candidate_job(deployment, name, node, config_name, secret_name, code):
    container = next(
        c
        for c in deployment["spec"]["template"]["spec"]["containers"]
        if c["name"] == "gateway"
    )
    require(
        not any(e["name"] == KEY for e in container.get("env", [])),
        "new_key_already_registered",
    )
    # No production volumes, ports, probes, sidecars or app=gateway label.
    env = copy.deepcopy(container.get("env", []))
    for entry in env:
        resource_ref = entry.get("valueFrom", {}).get("resourceFieldRef")
        if resource_ref and resource_ref.get("containerName"):
            require(resource_ref["containerName"] == "gateway", "unsupported_candidate_resource_reference")
            resource_ref["containerName"] = "candidate"
    env.append(
        dict(name=KEY, valueFrom=dict(secretKeyRef=dict(name=secret_name, key=KEY)))
    )
    candidate = dict(
        name="candidate",
        image="deerflow.local/deer-flow-gateway@"
        + RELEASE["profile"]["roles"][0]["manifest_digest"],
        imagePullPolicy="Never",
        command=["/app/backend/.venv/bin/python", "-I", "-B", "-u", "-c", code],
        env=env,
        envFrom=copy.deepcopy(container.get("envFrom", [])),
        resources=dict(
            requests=dict(cpu="100m", memory="256Mi"),
            limits=dict(cpu="1", memory="1Gi"),
        ),
        securityContext=dict(
            allowPrivilegeEscalation=False, capabilities=dict(drop=["ALL"])
        ),
        volumeMounts=[
            dict(name="candidate-config", mountPath="/candidate", readOnly=True)
        ],
    )
    spec = dict(
        restartPolicy="Never",
        automountServiceAccountToken=False,
        nodeSelector={"kubernetes.io/hostname": node},
        containers=[candidate],
        volumes=[
            dict(
                name="candidate-config",
                configMap=dict(
                    name=config_name,
                    items=[dict(key="config.yaml", path="config.yaml")],
                ),
            )
        ],
    )
    return dict(
        apiVersion="batch/v1",
        kind="Job",
        metadata=dict(
            name=name, namespace=NAMESPACE, labels={"portal-candidate": "data-qa-glm"}
        ),
        spec=dict(
            backoffLimit=0,
            activeDeadlineSeconds=180,
            ttlSecondsAfterFinished=600,
            completions=1,
            parallelism=1,
            template=dict(
                metadata=dict(labels={"portal-candidate": "data-qa-glm"}), spec=spec
            ),
        ),
    )


def job_diagnostics(c, job, uid, status):
    """Export bounded control-plane reasons, never raw messages or manifests."""
    def reason(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", value) else "OTHER"

    observation = dict(job=job, uid=uid, counts={k: status.get(k) for k in ("active", "failed", "succeeded")}, conditions=[dict(type=reason(x.get("type")), status=reason(x.get("status")), reason=reason(x.get("reason"))) for x in status.get("conditions", [])][:12])
    try:
        events = c.call("candidate_job_events", ["get", "events", "--field-selector", "involvedObject.uid=" + uid, "-o", "json"])
        observation["events"] = []
        for event in events.get("items", [])[-20:]:
            text = str(event.get("message", ""))[:16384]
            low = text.lower()
            observation["events"].append(dict(reason=reason(event.get("reason")), categories=[word for word in ("exceeded quota", "must specify", "forbidden", "admission", "podsecurity", "serviceaccount", "not found", "deadline", "insufficient", "taint", "imagepull") if word in low], resource_fields=sorted(set(re.findall(r"(?:requests|limits)\.[a-zA-Z_.]+", text))), resource_quantities=re.findall(r"(?:requests|limits)\.[a-zA-Z_.]+=[0-9.]+[A-Za-z]*", text)[:24]))
    except (Refused, ValueError, TypeError, AttributeError):
        observation["event_query_failed"] = True
    c.report.setdefault("candidate_job_diagnostics", []).append(observation)
    c.save()


def inspect_job(c, job, uid, now=time.monotonic, sleep=time.sleep):
    started = now()
    status = {}
    while now() - started < 240:
        value = c.get("job", job)
        require(value["metadata"]["uid"] == uid, "candidate_job_replaced")
        status = value.get("status", {})
        terminal_condition = any(x.get("type") in ("Failed", "Complete") and x.get("status") == "True" for x in status.get("conditions", []))
        if status.get("failed") or status.get("succeeded") or terminal_condition:
            job_diagnostics(c, job, uid, status)
            pods = c.call(
                "candidate_pods", ["get", "pods", "-l", "job-name=" + job, "-o", "json"]
            )["items"]
            require(bool(pods), "candidate_pod_absent_review_job_events")
            require(len(pods) == 1, "candidate_pod_count")
            require(
                any(
                    o.get("uid") == uid and o.get("controller")
                    for o in pods[0]["metadata"].get("ownerReferences", [])
                ),
                "candidate_pod_owner",
            )
            pod = pods[0]
            states = pod["status"].get("containerStatuses", [])
            require(
                len(states) == 1
                and states[0]
                .get("imageID", "")
                .endswith(RELEASE["profile"]["roles"][0]["manifest_digest"]),
                "candidate_image_identity",
            )
            raw = c.call(
                "candidate_result",
                [
                    "logs",
                    pod["metadata"]["name"],
                    "-c",
                    "candidate",
                    "--limit-bytes=16384",
                    "--tail=20",
                ],
                raw=True,
            )
            result = json.loads(raw)
            # Only controlled small result keys reach operator output.
            require(
                set(result)
                <= {
                    "status",
                    "error_class",
                    "error_code",
                    "provider_requests",
                    "source_files",
                    "data_tools",
                    "dedicated_key_resolved",
                    "disabled_button_hint",
                    "external_paths_denied",
                    "empty_error_and_delegate_scope",
                    "stream_completion",
                    "business_values_verified",
                },
                "candidate_result_shape",
            )
            c.report["candidate_checks"].append(
                dict(job=job, uid=uid, pod=pod["metadata"]["name"], observation=result)
            )
            require(
                status.get("succeeded") == 1
                and result.get("status") == "PASS"
                and result.get("provider_requests") == 0,
                "candidate_runtime_failed",
            )
            return
        sleep(4)
    job_diagnostics(c, job, uid, status)
    raise Refused("candidate_job_deadline")


def prepare(c, read_key=getpass.getpass):
    preload_gate()
    sealed_evidence()
    d = c.get("deployment", "gateway")
    stable_gateway(d)
    require(binding(c.get("deployment", "frontend")) == FRONT, "frontend_changed")
    nodes = {}
    for host, receipt in sorted(PRELOADS.items()):
        node = c.get("node", host)
        require(
            node["metadata"]["uid"] == receipt["expected_node_uid"]
            and not node["spec"].get("unschedulable")
            and any(
                x.get("type") == "Ready" and x.get("status") == "True"
                for x in node["status"]["conditions"]
            ),
            "preloaded_node_changed",
        )
        nodes[host] = node["metadata"]["labels"]["kubernetes.io/hostname"]
    cm = c.get("configmap", CONFIG_NAME)
    require(
        cm["metadata"]["uid"] == CONFIG_UID
        and cm.get("immutable") is True
        and digest(cm.get("data")) == CONFIG_SHA
        and not cm.get("binaryData"),
        "source_config_changed",
    )
    require("config.yaml" in cm["data"], "source_config_key")
    # Safe source migration executes only YAML parsing and the pure transform
    # in a short-lived process, not in the running server interpreter.
    pods = c.call("gateway_pods", ["get", "pods", "-l", "app=gateway", "-o", "json"])[
        "items"
    ]
    eligible = [
        p
        for p in pods
        if not p["metadata"].get("deletionTimestamp")
        and p.get("status", {}).get("phase") == "Running"
        and any(
            s["name"] == "gateway"
            and s.get("ready")
            and s.get("imageID", "").endswith(
                RELEASE["field_acceptance"]["base_manifest"]
            )
            for s in p.get("status", {}).get("containerStatuses", [])
        )
    ]
    require(len(eligible) == 70, "gateway_pod_inventory_changed")
    sample = sorted(eligible, key=lambda p: p["metadata"]["name"])[0]
    transform = (
        MIGRATION
        + '\nimport sys,json,yaml\ntry:\n source=json.load(sys.stdin); parsed=yaml.safe_load(source["config.yaml"]); changed=migrate_configuration(parsed); source["config.yaml"]=yaml.safe_dump(changed,allow_unicode=True,sort_keys=False); assert yaml.safe_load(source["config.yaml"])==changed; print(json.dumps({"data":source},ensure_ascii=True))\nexcept Exception:\n print(json.dumps({"error":"CONFIG_MIGRATION_REFUSED"}));sys.exit(1)\n'
    )
    changed = c.call(
        "transform_config",
        [
            "exec",
            "-i",
            sample["metadata"]["name"],
            "-c",
            "gateway",
            "--",
            "/app/backend/.venv/bin/python",
            "-I",
            "-B",
            "-c",
            transform,
        ],
        value=cm["data"],
    )["data"]
    require(
        set(changed) == set(cm["data"])
        and all(changed[k] == v for k, v in cm["data"].items() if k != "config.yaml"),
        "unrelated_config_key_changed",
    )
    stable_gateway(c.get("deployment", "gateway"))
    key = read_key(
        "New Data QA API key (hidden; same dedicated key used in the successful canary): "
    )
    require(
        isinstance(key, str)
        and 8 <= len(key) <= 4096
        and key.strip() == key
        and not any(ord(ch) < 33 or ord(ch) > 126 for ch in key),
        "invalid_key_shape",
    )
    # Stable checks are repeated after human input, before resources are created.
    stable_gateway(c.get("deployment", "gateway"))
    require(binding(c.get("deployment", "frontend")) == FRONT, "frontend_changed")
    require(
        c.get("configmap", CONFIG_NAME)["metadata"]["uid"] == CONFIG_UID,
        "source_config_replaced",
    )
    c.claim_once()
    suffix = c.report["operation"][:12]
    sn, cn = "deer-flow-data-qa-" + suffix, "deer-flow-config-data-qa-glm-" + suffix
    secret = dict(
        apiVersion="v1",
        kind="Secret",
        metadata=dict(name=sn, namespace=NAMESPACE),
        immutable=True,
        type="Opaque",
        stringData={KEY: key},
    )
    suid = c.create("secret", secret)
    secret.clear()
    # Metadata and key names only; never fetch Secret data for verification.
    template = '{{.metadata.uid}}{{"\\n"}}{{.immutable}}{{"\\n"}}{{range $k,$v := .data}}{{$k}}{{"\\n"}}{{end}}'
    metadata = (
        c.call(
            "verify_secret_metadata",
            ["get", "secret", sn, "-o", "go-template=" + template],
            raw=True,
        )
        .decode()
        .splitlines()
    )
    require(metadata == [suid, "true", KEY], "secret_metadata_mismatch")
    key = None
    cmuid = c.create(
        "configmap",
        dict(
            apiVersion="v1",
            kind="ConfigMap",
            metadata=dict(name=cn, namespace=NAMESPACE),
            immutable=True,
            data=changed,
        ),
    )
    observed = c.get("configmap", cn)
    require(
        observed["metadata"]["uid"] == cmuid
        and observed.get("immutable") is True
        and observed.get("data") == changed,
        "prepared_config_readback",
    )
    c.report["prepared_secret"] = dict(
        name=sn, namespace=NAMESPACE, uid=suid, immutable=True, keys=[KEY]
    )
    c.report["prepared_config"] = dict(
        name=cn,
        namespace=NAMESPACE,
        uid=cmuid,
        immutable=True,
        data_sha256=digest(changed),
    )
    code = (
        CANDIDATE
        + "\ncandidate_entry("
        + repr(RELEASE["profile"]["tail_files"]["gateway-forward"]["files"])
        + ", "
        + repr(hashlib.sha256(changed["config.yaml"].encode()).hexdigest())
        + ")\n"
    )
    for index, host in enumerate(sorted(nodes)[:2]):
        stable_gateway(c.get("deployment", "gateway"))
        name = "portal-data-qa-check-" + suffix + "-" + str(index)
        job = candidate_job(d, name, nodes[host], cn, sn, code)
        uid = c.create("job", job)
        inspect_job(c, name, uid)
        c.save()
        print(
            json.dumps(
                dict(phase="ISOLATED_CANDIDATE_RUNTIME", accepted=index + 1, total=2)
            ),
            flush=True,
        )
    stable_gateway(c.get("deployment", "gateway"))
    require(binding(c.get("deployment", "frontend")) == FRONT, "frontend_changed")
    require(
        c.get("configmap", CONFIG_NAME)["data"] == cm["data"],
        "source_config_changed_after_prepare",
    )
    c.report.update(
        status="DATA_QA_GLM_PREPARED_CANDIDATE_ACCEPTED_NOT_DEPLOYED",
        exitcode=0,
        candidate_accepted=2,
        gateway_before=binding(d),
        gateway_after=binding(d),
        configuration_resources_created=True,
        active_configuration_modified=False,
    )


def main():
    require(
        sys.argv[1:] == ["--prepare-data-qa-glm-once"], "explicit_prepare_flag_required"
    )
    require(
        sys.version_info >= (3, 7)
        and sys.flags.isolated
        and sys.dont_write_bytecode
        and os.geteuid() == 0
        and socket.gethostname() == "datax-01",
        "root_datax01_isolated_python_required",
    )
    os.umask(0o077)
    require(sys.stdin.isatty(), "interactive_hidden_key_input_required")
    parent = pathlib.Path("/var/lib")
    require(
        not parent.is_symlink()
        and parent.stat().st_uid == 0
        and not parent.stat().st_mode & 0o022,
        "private_parent_required",
    )
    binary = shutil.which("kubectl")
    require(
        binary is not None
        and pathlib.Path(binary).resolve().stat().st_uid == 0
        and not pathlib.Path(binary).resolve().stat().st_mode & 0o022,
        "trusted_kubectl_required",
    )
    operation = uuid.uuid4().hex
    root = parent / ("portal-data-qa-glm-preparation-" + operation)
    root.mkdir(mode=0o700)
    report = dict(
        schema="portal-data-qa-glm-preparation.v1",
        operation=operation,
        status="INCOMPLETE",
        exitcode=1,
        automatic_retry=False,
        provider_requests=0,
        gateway_restarted=False,
        deployment_patch_requested=False,
        ready_for_rollout=False,
        credentials_exported=False,
        business_values_verified=False,
        candidate_checks=[],
        queries=[],
        resource_intents=[],
        candidate_jobs_ttl_seconds=600,
        candidate_job_cleanup_confirmed=False,
        persisted_custom_agent_reference_inventory_pending=True,
        secret_matches_canary_key_not_independently_proven=True,
    )
    c = Controller(root, report, binary)
    try:
        # A duplicate invocation is explicit recovery work, never an automatic
        # second Secret creation or second pair of Jobs.
        marker = parent / "portal-data-qa-glm-prepare-20261021-r1.once"
        require(
            not marker.exists() and not marker.is_symlink(),
            "previous_preparation_requires_review",
        )
        prepare(c)
    except Exception as exc:
        report.update(
            error_class=type(exc).__name__,
            error_code=str(exc)
            if isinstance(exc, Refused)
            and re.fullmatch(r"[A-Za-z0-9_]{1,100}", str(exc))
            else "DETAILS_WITHHELD",
        )
    except KeyboardInterrupt:
        report.update(error_class="KeyboardInterrupt", error_code="OPERATOR_INTERRUPTED_REVIEW_RESOURCE_INTENTS")
    report["elapsed_seconds"] = round(time.monotonic() - c.started, 3)
    meta = c.save()
    print(json.dumps(dict(report, **meta), sort_keys=True), flush=True)
    return report["exitcode"]


if __name__ == "__main__":
    raise SystemExit(main())
