"""Two-node direct application/HTTP comparison. Python 3.7 host controller."""

# ruff: noqa: F821 -- pinned helpers and payloads supplied by package builder.


def stable(d):
    require(d["metadata"]["uid"] == GATEWAY_UID and d["spec"].get("replicas") == 70, "gateway_identity_changed")
    s = d.get("status", {})
    require(s.get("observedGeneration") == d["metadata"]["generation"] and all(s.get(k) == 70 for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")) and not s.get("unavailableReplicas") and not s.get("terminatingReplicas"), "gateway_rollout_in_progress")


def identity(p):
    state = next(c for c in p["status"]["containerStatuses"] if c["name"] == "gateway")
    require(not p["metadata"].get("deletionTimestamp") and state.get("ready") is True, "sample_not_ready")
    return dict(uid=p["metadata"]["uid"], container_id=state.get("containerID"), image_id=state.get("imageID"), restart_count=state.get("restartCount"))


def main():
    import datetime
    report = dict(schema="portal-deepseek-v41-transport-diagnosis.v1", operation=uuid.uuid4().hex, status="INCOMPLETE", samples=[], maximum_provider_requests=12, automatic_retry=False, workload_modified=False, configuration_modified=False, secret_created=False, credentials_exported=False, ready_for_rollout=False)
    report['historical_transport_audit'] = HISTORICAL_TRANSPORT_AUDIT
    directory = None
    try:
        require(sys.argv[1:] == ["--diagnose-and-test-once"] and os.name == "posix" and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode and socket.gethostname().split(".")[0] == "datax-01", "isolated_root_datax01_required")
        parent = pathlib.Path("/var/lib")
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode) and not stat.S_ISLNK(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022, "report_parent_permissions")
        directory = parent / ("portal-deepseek-v41-transport-" + report["operation"])
        directory.mkdir(mode=0o700)
        d = call(["get", "deployment", "gateway", "-o", "json"])
        stable(d)
        report["gateway_before"] = binding(d)
        target = next(c["image"] for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
        pods = call(["get", "pods", "-l", "app=gateway", "-o", "json"])["items"]
        samples, nodes = [], set()
        for p in sorted(pods, key=lambda p: p["metadata"]["name"]):
            if p["metadata"].get("deletionTimestamp") or p.get("status", {}).get("phase") != "Running" or p["spec"].get("nodeName") in nodes:
                continue
            if not any(c["name"] == "gateway" and c["image"] == target for c in p["spec"]["containers"]):
                continue
            try:
                identity(p)
            except Refused:
                continue
            samples.append(p)
            nodes.add(p["spec"]["nodeName"])
            if len(samples) == 2:
                break
        require(len(samples) == 2, "two_ready_nodes_required")
        require(not os.path.lexists(str(parent / DIAGNOSTIC_ONCE)), "diagnostic_already_claimed")
        require(sys.stdin.isatty(), "hidden_key_input_required")
        key = getpass.getpass("DeepSeek V4.1 API Key (hidden; key only): ")
        require(20 <= len(key) <= 4096 and key.strip() == key and not any(c.isspace() for c in key), "key_format")
        current = call(["get", "deployment", "gateway", "-o", "json"])
        stable(current)
        require(binding(current) == binding(d), "gateway_changed_before_probe")
        claim_once(parent, DIAGNOSTIC_ONCE, report)
        report["started_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for i, p in enumerate(samples):
            name = p["metadata"]["name"]
            require(identity(call(["get", "pod", name, "-o", "json"])) == identity(p), "sample_changed_before_probe")
            require(binding(call(["get", "deployment", "gateway", "-o", "json"])) == binding(d), "gateway_changed_before_probe")
            print(json.dumps(dict(phase="DEEPSEEK_V41_TRANSPORT_COMPARISON", sample=i + 1, total=2, maximum_requests_per_sample=6)), flush=True)
            report["provider_outcome_may_be_uncertain"] = True
            observation = call(["exec", "-i", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", DIAGNOSTIC_REMOTE], canonical(dict(key=key, source=PROBE_SOURCE, evidence=EVIDENCE_SOURCE, profile=PROFILE, operation=report["operation"] + "-" + str(i + 1))), timeout=630)
            report["samples"].append(dict(pod=name, node=p["spec"]["nodeName"], pod_ip=p["status"].get("podIP"), identity=identity(p), observation=observation))
            report["provider_outcome_may_be_uncertain"] = False
            require(identity(call(["get", "pod", name, "-o", "json"])) == identity(p), "sample_changed_after_probe")
        after = call(["get", "deployment", "gateway", "-o", "json"])
        stable(after)
        report["gateway_after"] = binding(after)
        require(binding(after) == binding(d), "gateway_changed_during_probe")
        report["provider_requests_attempted"] = sum(x["observation"].get("provider_requests_attempted", 0) for x in report["samples"])
        require(all(x["observation"].get("status") == "SAMPLE_ACCEPTED_NOT_DEPLOYED" for x in report["samples"]), "provider_or_transport_not_accepted")
        report["status"] = "TWO_NODE_PROVIDER_ACCEPTED_NOT_DEPLOYED"
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else "DIAGNOSIS_FAILED")
    report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["exitcode"] = 0 if report["status"] == "TWO_NODE_PROVIDER_ACCEPTED_NOT_DEPLOYED" else 1
    if directory is not None:
        raw = canonical(report) + b"\n"
        fd = os.open(str(directory / "REPORT.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        report.update(report_path=str(directory / "REPORT.json"), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(report, sort_keys=True))
    return report["exitcode"]


if __name__ == "__main__":
    sys.exit(main())
