#!/usr/bin/env python3
"""Python 3.7 host controller. Read-only diagnostics plus explicit opt-in probes."""

import argparse
import concurrent.futures
import contextlib
import getpass
import hashlib
import importlib.util
import json
import os
import queue
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("network_probe", str(ROOT / "portal-production-probe.py"))
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)
SOURCES = {
    "featured": "employee_featured_store.py",
    "channels": "runtime_config_store.py",
    "robust": "robust_openai.py",
    "glm": "enterprise_glm.py",
    "glm_probe": "enterprise_glm_probe.py",
}
PATHS = (
    "app/channels/runtime_config_store.py",
    "app/channels/service.py",
    "app/channels/feishu.py",
    "app/gateway/routers/agents.py",
    "app/gateway/routers/models.py",
    "app/gateway/routers/browser.py",
    "app/gateway/routers/artifacts.py",
    "app/gateway/app.py",
    "app/gateway/employee_featured_store.py",
    "app/gateway/routers/employee_directory.py",
    "app/gateway/ppt_runtime_broker.py",
    "app/gateway/codex_responses_adapter.py",
    "packages/harness/deerflow/models/robust_openai.py",
    "packages/harness/deerflow/models/enterprise_glm.py",
    "packages/harness/deerflow/models/factory.py",
    "packages/harness/deerflow/config/model_config.py",
    "packages/harness/deerflow/models/assistant_payload_replay.py",
    "packages/harness/deerflow/models/stream_admission.py",
    "packages/harness/deerflow/llm_timing_context.py",
    "packages/harness/deerflow/sandbox/path_patterns.py",
    "packages/harness/deerflow/community/browser_automation/tools.py",
    "packages/harness/deerflow/community/browser_automation/session.py",
)


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def failure(exc):
    result = {"result": "INCOMPLETE", "error_type": type(exc).__name__, "raw_error_printed": False, "automatic_retry": False}
    if isinstance(exc, CheckFailure):
        result["failed_check"] = str(exc)
    return result


def safe_parent(path):
    require(path.is_absolute() and path.resolve() == path, "unsafe_output_path")
    for parent in [path] + list(path.parents):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, "unsafe_output_ancestor")


def save(path, data):
    safe_parent(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def supervisor(source, deadline):
    # The key is on stdin only, never argv, environment or a saved file.
    return """
import json, os, signal, subprocess, sys
payload = sys.stdin.read(524289)
if len(payload) > 524288:
    print(json.dumps({"result":"FAIL", "error_class":"INPUT_TOO_LARGE"}))
    sys.exit(0)
child = subprocess.Popen([sys.executable,"-B","-c",%r], stdin=subprocess.PIPE,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, start_new_session=True)
try:
    out, _ = child.communicate(payload, timeout=%d)
    result = json.loads(out) if child.returncode == 0 else {"result":"FAIL","error_class":"CHILD_EXIT"}
except subprocess.TimeoutExpired:
    try: os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: child.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.communicate()
    result = {"result":"UNCONFIRMED","error_class":"DEADLINE","automatic_retry":False}
except (ValueError, TypeError):
    result = {"result":"UNCONFIRMED","error_class":"INVALID_OUTPUT","raw_output_printed":False}
print(json.dumps(result))
""" % (source, deadline)


def remote(pod, payload, deadline=45):
    code = (ROOT / "acceptance-remote.py").read_text(encoding="utf-8")
    result = subprocess.run(
        network.KUBE + ["--request-timeout=" + str(deadline + 12) + "s", "exec", "-i", pod, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-B", "-c", supervisor(code, deadline)],
        input=json.dumps(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=deadline + 18,
    )
    require(result.returncode == 0, "remote_command_failed_no_retry")
    require(len(result.stdout) < 262144, "oversize_remote_receipt")
    return json.loads(result.stdout)


def gated_command(pod, code):
    return network.KUBE + ["--request-timeout=55s", "exec", "-i", pod, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-B", "-c", code, "--gated"]


class GatedRemote:
    """Two bounded control frames over kubectl, never shared-file signalling."""

    def __init__(self, pod, payload, timeout=55):
        require(payload.get("action") in ("hold", "write") and "key" not in payload, "invalid_gated_payload")
        self.identity = {k: payload.get(k) for k in ("nonce", "action")}
        self.identity["writer"] = payload.get("index")
        self.deadline = time.monotonic() + timeout
        self.frames = queue.Queue(maxsize=3)
        self.events = []
        self.process = subprocess.Popen(gated_command(pod, (ROOT / "acceptance-remote.py").read_text(encoding="utf-8")), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, bufsize=1)

        def read_frames():
            try:
                for _ in range(3):
                    line = self.process.stdout.readline(16385)
                    if not line or len(line) > 16384:
                        self.frames.put_nowait(None)
                        return
                    self.frames.put_nowait(json.loads(line))
            except (ValueError, OSError, queue.Full):
                with contextlib.suppress(queue.Full):
                    self.frames.put_nowait(None)

        threading.Thread(target=read_frames, daemon=True).start()
        try:
            self._send(payload, limit=524288)
        except Exception:
            self.close()
            raise

    def remaining(self):
        value = self.deadline - time.monotonic()
        require(value > 0, "control_deadline")
        return value

    def _send(self, value, limit=1024):
        line = json.dumps(value) + "\n"
        require(len(line) <= limit, "control_frame_too_large")
        finished = queue.Queue(maxsize=1)

        def write():
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
                finished.put(True)
            except (OSError, ValueError):
                finished.put(False)

        threading.Thread(target=write, daemon=True).start()
        try:
            require(finished.get(timeout=self.remaining()), "control_send_failed")
        except queue.Empty:
            raise CheckFailure("control_send_deadline") from None

    def command(self, name):
        self._send(dict(self.identity, command=name))

    def receive(self, expected):
        try:
            frame = self.frames.get(timeout=self.remaining())
        except queue.Empty:
            raise CheckFailure("control_receive_deadline") from None
        require(isinstance(frame, dict) and all(frame.get(k) == v for k, v in self.identity.items()), "invalid_control_receipt")
        require(frame.get("event") in ("held", "ready", "result"), "invalid_control_event")
        self.events.append(frame)
        require(frame["event"] == expected, "control_expected_" + expected)
        if expected == "result":
            require(isinstance(frame.get("receipt"), dict), "missing_terminal_receipt")
            try:
                code = self.process.wait(timeout=min(3, self.remaining()))
            except subprocess.TimeoutExpired:
                raise CheckFailure("control_exit_deadline") from None
            require(code == 0, "control_child_exit")
            return frame["receipt"]
        return {"result": "PASS", "event": expected}

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            with contextlib.suppress(OSError, ValueError):
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def owned_ready(deployment, replicasets, pods):
    ids = {r["metadata"]["uid"] for r in replicasets if any(o.get("controller") and o.get("uid") == deployment["metadata"]["uid"] for o in r["metadata"].get("ownerReferences", []))}
    return sorted(
        [
            p
            for p in pods
            if not p["metadata"].get("deletionTimestamp")
            and any(o.get("controller") and o.get("uid") in ids for o in p["metadata"].get("ownerReferences", []))
            and any(c.get("name") == "gateway" and c.get("ready") for c in p.get("status", {}).get("containerStatuses", []))
        ],
        key=lambda p: p["metadata"]["name"],
    )


def selected_mount(pod, parent):
    container = next(c for c in pod["spec"]["containers"] if c["name"] == "gateway")
    mounts = [m for m in container.get("volumeMounts", []) if parent == m["mountPath"] or parent.startswith(m["mountPath"].rstrip("/") + "/")]
    require(bool(mounts), "parent_not_on_volume")
    mount = max(mounts, key=lambda m: len(m["mountPath"]))
    return mount


def shared_mount(pod, parent):
    mount = selected_mount(pod, parent)
    require(not mount.get("readOnly") and not mount.get("subPathExpr"), "needs_static_writable_shared_mount")
    subpath = mount.get("subPath", "")
    require(isinstance(subpath, str) and (not subpath or (not PurePosixPath(subpath).is_absolute() and all(p not in ("", ".", "..") for p in subpath.split("/")) and "\\" not in subpath)), "unsafe_static_subpath")
    volume = next(v for v in pod["spec"]["volumes"] if v["name"] == mount["name"])
    if "persistentVolumeClaim" in volume:
        claim = volume["persistentVolumeClaim"]
        require(not claim.get("readOnly"), "read_only_volume")
        pvc = network.command(["get", "pvc", claim["claimName"], "-o", "json"])
        require(pvc.get("status", {}).get("phase") == "Bound" and "ReadWriteMany" in pvc["spec"].get("accessModes", []), "requires_bound_rwx_volume")
        identity = {"pvc_uid": pvc["metadata"]["uid"]}
    elif "nfs" in volume:
        require(not volume["nfs"].get("readOnly"), "read_only_nfs")
        identity = volume["nfs"]
    else:
        raise RuntimeError("not_a_shared_volume")
    # Do not export NFS addresses. Equality is only used inside the controller.
    # A directory subPath is valid only when identical on both replicas AND
    # the remote preflight proves it is a safe directory before any write.
    return json.dumps([identity, mount["mountPath"], subpath], sort_keys=True)


def storage_test(selected, inventories, sources, requested_parent):
    require(len(selected) == 2, "two_distinct_gateway_nodes_required")
    parent = requested_parent or inventories[selected[0]["metadata"]["name"]].get("runtime_home")
    require(parent and PurePosixPath(parent).is_absolute() and ".." not in PurePosixPath(parent).parts, "shared_parent_unavailable")
    if not requested_parent:
        require(parent == inventories[selected[1]["metadata"]["name"]].get("runtime_home"), "runtime_home_mismatch")
    require(shared_mount(selected[0], parent) == shared_mount(selected[1], parent), "shared_volume_mismatch")
    for pod in selected:
        value = remote(pod["metadata"]["name"], {"action": "shared_parent", "parent": parent})
        require(value.get("result") == "PASS", "shared_parent_preflight_failed_" + str(value.get("failed_check", "unconfirmed")))
    nonce = uuid.uuid4().hex
    fixture = parent.rstrip("/") + "/portal-acceptance-" + nonce
    print("shared_fixture=" + fixture + " retention=KEEP_FOR_EVIDENCE", flush=True)
    base = {"fixture": fixture, "nonce": nonce, "sources": {k: sources[k] for k in ("featured", "channels")}}
    names = [p["metadata"]["name"] for p in selected]
    receipts = []
    report = {
        "fixture": fixture,
        "receipts": receipts,
        "gated_sessions": [],
        "cleanup_performed": False,
        "production_config_touched": False,
        "scope": "NEW_SYNTHETIC_FILES_ONLY_NOT_HOT_STORE_INITIALIZATION",
        "synchronization": "CONTROL_STDIN_STDOUT",
        "automatic_retry": False,
    }

    def record(pod_index, action, value):
        receipts.append({"pod": names[pod_index], "action": action, "receipt": value})
        print(json.dumps({"shared_stage": action, "pod": names[pod_index], "receipt": value}), flush=True)
        if value.get("result") != "PASS":
            raise CheckFailure("shared_fixture_failed_" + action)
        return value

    def call(pod_index, action, **kwargs):
        try:
            value = remote(names[pod_index], dict(base, action=action, **kwargs))
        except Exception as exc:
            value = failure(exc)
        return record(pod_index, action, value)

    def gated(pod_index, action, **kwargs):
        worker = GatedRemote(names[pod_index], dict(base, action=action, **kwargs))
        report["gated_sessions"].append({"pod": names[pod_index], "action": action, "events": worker.events})
        return worker

    try:
        call(0, "initialize")
        with gated(0, "hold") as holder:
            record(0, "hold_ready", holder.receive("held"))
            try:
                call(1, "contend")
            finally:
                holder.command("release")
                record(0, "hold", holder.receive("result"))
        with contextlib.ExitStack() as stack:
            writers = [stack.enter_context(gated(i, "write", index=i)) for i in range(2)]
            for i, writer in enumerate(writers):
                record(i, "write_ready", writer.receive("ready"))
            # No GO is sent until BOTH workers acknowledge readiness. The
            # contenders operate on the real shared JSON/lock, without cache
            # flushing, retries or an eventual-consistency grace period.
            started = time.monotonic()
            for writer in writers:
                writer.command("go")
            report["go_dispatch_seconds"] = round(time.monotonic() - started, 3)
            outcomes = []
            for i, writer in enumerate(writers):
                try:
                    record(i, "write", writer.receive("result"))
                    outcomes.append(True)
                except Exception as exc:
                    if not receipts or receipts[-1]["action"] != "write" or receipts[-1]["pod"] != names[i]:
                        receipts.append({"pod": names[i], "action": "write", "receipt": failure(exc)})
                    outcomes.append(False)
            require(all(outcomes), "shared_fixture_failed_write")
        verifications = [call(i, "verify") for i in range(2)]
        require(verifications[0]["fixture_sha256"] == verifications[1]["fixture_sha256"], "cross_node_snapshot_mismatch")
        report["result"] = "PASS"
    except Exception as exc:
        report.update(failure(exc))
    return report


def hidden_key():
    # Never fall back to echoing a key when getpass cannot control a terminal.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        key = getpass.getpass("GLM API key (hidden; never saved): ")
    require(bool(key) and key.strip() == key and len(key) < 8192, "invalid_hidden_key")
    return key


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--glm", action="store_true", help="at most four real synthetic model calls")
    parser.add_argument("--shared-fixture", action="store_true", help="create a NEW private fixture on verified shared storage")
    parser.add_argument("--shared-parent", help="optional audited shared directory; default is runtime home only when verified RWX")
    parser.add_argument("--shared-only", action="store_true", help="two-pod storage retest; no model calls or website probes")
    args = parser.parse_args(argv)
    if args.shared_only and (args.glm or not args.shared_fixture or not args.shared_parent):
        parser.error("--shared-only requires --shared-fixture --shared-parent and forbids --glm")
    return args


def main(argv=None):
    args = parse_arguments(argv)
    require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01", "requires_root_on_datax01")
    os.environ.setdefault("KUBECONFIG", "/etc/kubernetes/admin.conf")
    output = Path(args.output)
    safe_parent(output.parent)
    require(not output.exists(), "report_exists_no_overwrite")
    sources = {k: (ROOT / v).read_text(encoding="utf-8") for k, v in SOURCES.items()}
    report = {
        "batch": "portal-glm-acceptance-20261014-r4",
        "production_workloads_mutated": False,
        "configuration_modified": False,
        "images_imported": False,
        "rollout_performed": False,
        "credentials_saved_or_printed": False,
        "ready_for_rollout": False,
        "stages": {},
        "candidate_sources": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sources.items()},
        "remaining_acceptance": [
            "CANDIDATE_IMAGES_NOT_BUILT",
            "ADMIN_USER_BROWSER_AND_RESTART_PERSISTENCE",
            "ACTUAL_GLM_SELECTOR_AND_DEPLOYMENT_ROUTING",
            "AUTHENTICATED_INTRANET_BROWSER_AND_EGRESS_POLICY",
            "PPT_BATCH_NOT_INTEGRATED_REQUIRES_OWN_MIGRATION_AND_END_TO_END_ACCEPTANCE",
        ],
    }
    try:
        deployments = {n: network.command(["get", "deployment", n, "-o", "json"]) for n in network.DEPLOYMENTS}
        before = {n: network.fingerprint(v) for n, v in deployments.items()}
        report["deployments_before"] = before
        report["deployment_convergence"] = {
            n: {
                "desired": v["spec"].get("replicas", 1),
                "updated": v.get("status", {}).get("updatedReplicas", 0),
                "available": v.get("status", {}).get("availableReplicas", 0),
                "observed_generation": v.get("status", {}).get("observedGeneration", 0),
            }
            for n, v in deployments.items()
        }
        rs = network.command(["get", "replicasets", "-o", "json"])["items"]
        pods = network.command(["get", "pods", "-o", "json"])["items"]
        gateways = owned_ready(deployments["gateway"], rs, pods)
        require(bool(gateways), "no_owned_ready_gateway")
        selected = []
        for pod in gateways:
            if pod["spec"].get("nodeName") not in [p["spec"].get("nodeName") for p in selected]:
                selected.append(pod)
            if len(selected) == 2:
                break
        audit_gateways = selected if args.shared_only else gateways
        report["source_audit_scope"] = "TWO_SELECTED_GATEWAYS" if args.shared_only else "ALL_OWNED_READY_GATEWAYS"
        report["all_gateway_sources_audited"] = not args.shared_only
        report["gateway_pods_sampled"] = [
            {"pod": p["metadata"]["name"], "uid": p["metadata"]["uid"], "node": p["spec"].get("nodeName"), "container": {k: c.get(k) for k in ("image", "imageID", "restartCount", "ready")}}
            for p in audit_gateways
            for c in p.get("status", {}).get("containerStatuses", [])
            if c["name"] == "gateway"
        ]
        report["gateway_source_audit"] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(remote, p["metadata"]["name"], {"action": "inventory", "paths": PATHS}): p for p in audit_gateways}
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                pod = futures[future]
                try:
                    value = future.result()
                except Exception as exc:
                    value = failure(exc)
                report["gateway_source_audit"][pod["metadata"]["name"]] = value
                if index % 10 == 0 or index == len(audit_gateways):
                    print("source_audit=" + str(index) + "/" + str(len(audit_gateways)), flush=True)
        inventories = report["gateway_source_audit"]
        report["source_audit_complete"] = all("source_hashes" in v for v in inventories.values())
        report["sampled_sources_uniform"] = report["source_audit_complete"] and len({json.dumps(v["source_hashes"], sort_keys=True) for v in inventories.values()}) == 1
        report["gateway_sources_uniform"] = None if args.shared_only else report["sampled_sources_uniform"]
        nodes = network.command(["get", "nodes", "-o", "json"])["items"]
        report["node_conditions"] = {n["metadata"]["name"]: {c["type"]: c["status"] for c in n.get("status", {}).get("conditions", []) if c["type"] in ("Ready", "DiskPressure", "MemoryPressure", "PIDPressure")} for n in nodes}
        if args.shared_only:
            report["stages"]["browser_diagnostics"] = {"result": "NOT_REQUESTED"}
        for pod in [] if args.shared_only else selected:
            name = pod["metadata"]["name"]
            print("network_diagnostic=RUNNING pod=" + name, flush=True)
            try:
                value = network.command(["--request-timeout=95s", "exec", "-i", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-B", "-"], network.bounded_probe_source(), timeout=100)
                report["stages"]["browser_" + name] = value
            except Exception as exc:
                report["stages"]["browser_" + name] = failure(exc)
        for label, enabled in (("shared_storage", args.shared_fixture), ("glm", args.glm)):
            if not enabled:
                report["stages"][label] = {"result": "NOT_REQUESTED"}
                continue
            print(label + "=RUNNING", flush=True)
            try:
                if label == "shared_storage":
                    parent = args.shared_parent or inventories[selected[0]["metadata"]["name"]].get("runtime_home")
                    report["shared_mount_preflight"] = (
                        [{"pod": p["metadata"]["name"], "parent": parent, "mount": {k: selected_mount(p, parent).get(k) for k in ("mountPath", "readOnly", "subPath", "subPathExpr")}} for p in selected] if parent else []
                    )
                    report["stages"][label] = storage_test(selected, inventories, sources, args.shared_parent)
                else:
                    key = hidden_key()
                    try:
                        report["stages"][label] = remote(selected[0]["metadata"]["name"], {"action": "glm", "key": key, "sources": {k: sources[k] for k in ("robust", "glm", "glm_probe")}}, deadline=510)
                    finally:
                        key = None
            except Exception as exc:
                report["stages"][label] = failure(exc)
        after = {n: network.fingerprint(network.command(["get", "deployment", n, "-o", "json"])) for n in network.DEPLOYMENTS}
        report["baseline_unchanged"] = before == after
        report["collection"] = "COMPLETE"
    except Exception as exc:
        report.update(collection="INCOMPLETE", error_type=type(exc).__name__, raw_error_printed=False)
    finally:
        save(output, report)
        print(json.dumps({"collection": report.get("collection"), "report": str(output), "report_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "ready_for_rollout": False, "note": "COLLECTION_COMPLETION_IS_NOT_ACCEPTANCE_PASS"}))
    return 0 if report.get("collection") == "COMPLETE" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"collection": "INCOMPLETE", "error_type": type(exc).__name__, "raw_error_printed": False, "automatic_retry": False}))
        sys.exit(1)
