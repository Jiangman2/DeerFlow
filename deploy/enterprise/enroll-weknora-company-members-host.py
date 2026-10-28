"""Add existing eligible workspaces as native viewers. Host Python 3.7+.

Runs only on datax-01; no deployment/configuration mutation. Keeps an exclusive
attempt directory and sanitized live progress. Never replay after an incomplete
attempt; re-read membership state before preparing a separate continuation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time

CHILD = None
GUARD = None
DIRECTORY = Path("/var/lib/portal-weknora-company-bulk-enrollment-20261024-r1")
COMPLETE = "BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED"


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def stream_child(command, report):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    events = queue.Queue(maxsize=16)

    def reader():
        total = 0
        try:
            while True:
                line = process.stdout.readline(65537)
                if not line:
                    break
                total += len(line)
                if len(line) > 65536 or total > 2 * 1024 * 1024:
                    events.put(("error", None))
                    return
                events.put(("line", line))
        finally:
            events.put(("end", None))

    threading.Thread(target=reader, daemon=True).start()
    started, next_ping, observation = time.monotonic(), 0.0, None
    try:
        with (DIRECTORY / "PROGRESS.jsonl").open("xb") as journal:
            while True:
                require(time.monotonic() - started < 7240, "CHILD_DEADLINE_DO_NOT_REPLAY")
                if time.monotonic() >= next_ping:
                    try:
                        process.stdin.write(b"PING\n")
                        process.stdin.flush()
                    except (BrokenPipeError, OSError):
                        pass  # Drain a final report from a child that just exited.
                    next_ping = time.monotonic() + 5
                try:
                    kind, raw = events.get(timeout=1)
                except queue.Empty:
                    continue
                require(kind != "error", "CHILD_OUTPUT_BOUND_EXCEEDED")
                if kind == "end":
                    break
                value = json.loads(raw)
                require(isinstance(value, dict), "CHILD_OUTPUT_INVALID")
                if value.get("schema") == "portal-weknora-company-bulk-enrollment.v1":
                    require(observation is None, "DUPLICATE_FINAL_RECEIPT")
                    observation = value
                    report["observation"] = value
                else:
                    phase = value.get("phase")
                    allowed = {"phase", "pass"} if phase == "FRESH_PAGED_INVENTORY" else {"phase", "batches", "posts", "confirmed_viewers"}
                    require(phase in {"FRESH_PAGED_INVENTORY", "BULK_VIEWER_BATCH_VERIFIED"} and set(value) <= allowed and all(type(v) is int and 0 <= v <= 10000 for k, v in value.items() if k != "phase"), "PROGRESS_PROTOCOL_INVALID")
                    journal.write(json.dumps(value, sort_keys=True).encode() + b"\n")
                    journal.flush()
                    os.fsync(journal.fileno())
                    report["last_progress"] = value
                    print(json.dumps(value, sort_keys=True), flush=True)
            process.wait(timeout=10)
        require(observation is not None, "CHILD_FINAL_RECEIPT_MISSING_DO_NOT_REPLAY")
        require(process.returncode == 0 and observation.get("status") == COMPLETE, "BULK_INCOMPLETE_SEE_OBSERVATION")
        return observation
    finally:
        # stdin EOF stops the remote heartbeat guard; closing kubectl is not
        # evidence that the final in-flight native POST did not commit.
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()


def execute(report):
    require(isinstance(CHILD, str) and isinstance(GUARD, str), "PACKAGED_SCRIPT_REQUIRED")
    require(socket.gethostname().split(".")[0] == "datax-01" and os.geteuid() == 0, "DATAX01_ROOT_REQUIRED")
    guard = {"__name__": "enrollment_readonly_guard"}
    exec(compile(GUARD, "<readonly-guard>", "exec"), guard)
    report["prior_receipts"] = guard["check_receipts"]()

    def check_deployments():
        for name in guard["PINS"]:
            guard["check_deployment"](guard["query"](["get", "deployment", name, "-o", "json"]), name)

    check_deployments()
    pods = guard["query"](["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
    pods = sorted([p for p in pods if guard["eligible"](p)], key=lambda p: p["metadata"]["name"])
    require(bool(pods), "NO_READY_GATEWAY")
    pod = guard["query"](["get", "pod", pods[0]["metadata"]["name"], "-o", "json"])
    require(guard["eligible"](pod) and pod["metadata"]["uid"] == pods[0]["metadata"]["uid"], "SAMPLED_POD_CHANGED")
    report["sample"] = {"pod": pod["metadata"]["name"], "uid": pod["metadata"]["uid"]}
    require(not DIRECTORY.exists() and not DIRECTORY.is_symlink(), "PRIOR_ATTEMPT_EXISTS_DO_NOT_REPLAY")
    DIRECTORY.mkdir(mode=0o700)
    report["once_claimed"] = True
    with (DIRECTORY / "STARTED.json").open("x") as output:
        json.dump({"child_sha256": hashlib.sha256(CHILD.encode()).hexdigest(), "automatic_retry": False, "maximum_membership_posts": 10000}, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    # The host's overall deadline/heartbeat bounds the streaming exec. Ordinary
    # kubectl metadata queries retain their 15-second request timeout.
    base = ["--request-timeout=0" if arg == "--request-timeout=15s" else arg for arg in guard["BASE"]]
    report["membership_mutation_may_have_applied"] = True
    stream_child(base + ["exec", "-i", pod["metadata"]["name"], "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-u", "-c", CHILD], report)
    check_deployments()
    after = guard["query"](["get", "pod", pod["metadata"]["name"], "-o", "json"])
    require(guard["eligible"](after) and after["metadata"]["uid"] == pod["metadata"]["uid"] and after.get("status", {}).get("containerStatuses") == pod.get("status", {}).get("containerStatuses"), "SAMPLED_CONTAINER_CHANGED")
    report["status"] = COMPLETE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enroll-existing-company-viewers-once", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    report = {"schema": "portal-weknora-company-bulk-enrollment-host.v1", "status": "INCOMPLETE", "once_claimed": False,
              "membership_mutation_may_have_applied": False, "automatic_retry": False, "credentials_exported": False,
              "user_list_exported": False, "database_direct_writes": 0, "provider_requests": 0, "production_workload_mutation": False,
              "auto_enrollment_deployed": False}
    started = time.monotonic()
    try:
        execute(report)
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "BULK_OPERATION_STOPPED_INSPECT_REPORT", raw_errors_exported=False)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if report["once_claimed"]:
        data = json.dumps(report, sort_keys=True, ensure_ascii=True).encode()
        path = DIRECTORY / "REPORT.json"
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        report.update(report_path=str(path), report_bytes=len(data), report_sha256=hashlib.sha256(data).hexdigest())
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == COMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
