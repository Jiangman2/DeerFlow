"""Resume only the two isolated Jobs, using the exact already prepared resources.

Packaged with the reviewed controller, failure receipt and pure reuse validator.
No Secret input, Secret creation, ConfigMap creation or Deployment patch exists
in this execution path. The original once marker is retained.
"""

# ruff: noqa: F821 -- populated by the checked package builder.

TOLERATIONS = [dict(key="dedicated", operator="Equal", value="gateway", effect="NoSchedule")]
REUSE_NAMESPACE = {"__name__": "prepared_resource_reuse"}
exec(compile(REUSE_SOURCE, "<prepared-resource-reuse>", "exec"), REUSE_NAMESPACE)


class ResumptionController(Controller):
    def create(self, kind, obj):
        require(kind == "job" and obj.get("kind") == "Job" and obj["metadata"]["name"].startswith("portal-data-qa-recheck-" + self.report["operation"][:12] + "-"), "resumption_only_creates_owned_jobs")
        return super().create(kind, obj)

    def claim_once(self):
        marker = self.root.parent / "portal-data-qa-glm-resume-20261021-r2.once"
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(dict(operation=self.report["operation"], report_path=str(self.root / "REPORT.json"), resumed_preparation=FAILURE["operation"])))
            stream.flush()
            os.fsync(stream.fileno())
        self.report["once_claimed"] = True


def read_failed_preparation():
    path = FAILURE["report_path"]
    require(path == "/var/lib/portal-data-qa-glm-preparation-2e0fd403b23a4cf5b880daaee1b8e504/REPORT.json", "failure_report_path")
    parent = pathlib.Path(path).parent
    require(not parent.is_symlink() and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o077, "failure_report_directory")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not stat.S_IMODE(st.st_mode) & 0o077 and st.st_nlink == 1 and st.st_size == FAILURE["report_bytes"], "failure_report_identity")
        raw = stream.read(FAILURE["report_bytes"] + 1)
    require(len(raw) == FAILURE["report_bytes"] and hashlib.sha256(raw).hexdigest() == FAILURE["report_sha256"], "failure_report_hash")
    return json.loads(raw)


def existing_resources(c, failure):
    config = c.get("configmap", FAILURE["prepared_config"]["name"])
    secret = FAILURE["prepared_secret"]
    template = '{{.metadata.name}}{{"\\n"}}{{.metadata.namespace}}{{"\\n"}}{{.metadata.uid}}{{"\\n"}}{{.immutable}}{{"\\n"}}{{range $k,$v := .data}}{{$k}}{{"\\n"}}{{end}}'
    rows = c.call("existing_secret_metadata", ["get", "secret", secret["name"], "-o", "go-template=" + template], raw=True).decode("ascii").splitlines()
    require(len(rows) == 5, "existing_secret_metadata_shape")
    metadata = dict(name=rows[0], namespace=rows[1], uid=rows[2], immutable=rows[3] == "true", keys=rows[4:])
    validated = REUSE_NAMESPACE["validate_reuse"](failure, config, metadata)
    return config, validated


def node_for_probe(c, name):
    node = c.get("node", name)
    expected = RELEASE["profile"]["nodes"][name]["expected_node_uid"]
    require(node["metadata"]["uid"] == expected and node["metadata"]["labels"].get("workload") == "gateway" and not node["spec"].get("unschedulable") and any(x.get("type") == "Ready" and x.get("status") == "True" for x in node["status"]["conditions"]), "probe_node_changed")
    # No broad toleration and no disabling the scheduler. Unexpected taints
    # require review instead of treating every dedicated node as usable.
    require(node["spec"].get("taints", []) == [dict(key="dedicated", value="gateway", effect="NoSchedule")], "probe_node_taints_changed")
    return node["metadata"]["labels"]["kubernetes.io/hostname"]


def resume(c, read_failure=read_failed_preparation):
    preload_gate()
    sealed_evidence()
    failure = read_failure()
    d = c.get("deployment", "gateway")
    stable_gateway(d)
    pod = d["spec"]["template"]["spec"]
    require(pod.get("tolerations") == TOLERATIONS and pod.get("schedulerName", "default-scheduler") == "default-scheduler" and pod.get("nodeSelector") == {"workload": "gateway"}, "reviewed_gateway_scheduling_changed")
    require(binding(c.get("deployment", "frontend")) == FRONT, "frontend_changed")
    old = c.get("configmap", CONFIG_NAME)
    require(old["metadata"]["uid"] == CONFIG_UID and old.get("immutable") is True and digest(old.get("data")) == CONFIG_SHA, "active_config_changed")
    config, reused = existing_resources(c, failure)
    c.report.update(prepared_config=reused["config"], prepared_secret=reused["secret"], gateway_before=binding(d))
    old_job = REUSE_NAMESPACE["JOB"]
    raw = c.call("previous_job_absence", ["get", "job", old_job["name"], "--ignore-not-found", "-o", "json"], raw=True)
    require(not raw.strip(), "previous_job_still_present")
    previous_pods = c.call("previous_pods_absence", ["get", "pods", "-l", "job-name=" + old_job["name"], "-o", "json"])
    require(previous_pods.get("items") == [], "previous_probe_pods_still_present")
    nodes = {name: node_for_probe(c, name) for name in sorted(PRELOADS)}
    stable_gateway(c.get("deployment", "gateway"))
    c.claim_once()
    code = CANDIDATE + "\ncandidate_entry(" + repr(RELEASE["profile"]["tail_files"]["gateway-forward"]["files"]) + ", " + repr(hashlib.sha256(config["data"]["config.yaml"].encode()).hexdigest()) + ")\n"
    for index, host in enumerate(sorted(nodes)[:2]):
        stable_gateway(c.get("deployment", "gateway"))
        hostname = node_for_probe(c, host)
        name = "portal-data-qa-recheck-" + c.report["operation"][:12] + "-" + str(index)
        job = candidate_job(d, name, hostname, reused["config"]["name"], reused["secret"]["name"], code)
        job["spec"]["template"]["spec"]["nodeSelector"]["workload"] = "gateway"
        uid = c.create("job", job)
        inspect_job(c, name, uid)
        c.save()
        print(json.dumps(dict(phase="ISOLATED_CANDIDATE_RUNTIME", accepted=index + 1, total=2)), flush=True)
    stable_gateway(c.get("deployment", "gateway"))
    require(binding(c.get("deployment", "frontend")) == FRONT, "frontend_changed")
    existing_resources(c, failure)
    require(c.get("configmap", CONFIG_NAME)["data"] == old["data"], "active_config_changed")
    c.report.update(status="DATA_QA_GLM_PREPARED_CANDIDATE_ACCEPTED_NOT_DEPLOYED", exitcode=0, candidate_accepted=2, gateway_after=binding(d), active_configuration_modified=False)


def main():
    require(sys.argv[1:] == ["--resume-existing-candidate-once"], "explicit_resume_flag_required")
    require(sys.version_info >= (3, 7) and sys.flags.isolated and sys.dont_write_bytecode and os.geteuid() == 0 and socket.gethostname() == "datax-01", "root_datax01_isolated_python_required")
    os.umask(0o077)
    parent = pathlib.Path("/var/lib")
    require(not parent.is_symlink() and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022, "private_parent_required")
    binary = shutil.which("kubectl")
    require(binary is not None and pathlib.Path(binary).resolve().stat().st_uid == 0 and not pathlib.Path(binary).resolve().stat().st_mode & 0o022, "trusted_kubectl_required")
    operation = uuid.uuid4().hex
    root = parent / ("portal-data-qa-glm-resumption-" + operation)
    root.mkdir(mode=0o700)
    report = dict(schema="portal-data-qa-glm-resumption.v2", operation=operation, resumed_preparation=FAILURE["operation"], status="INCOMPLETE", exitcode=1, automatic_retry=False, provider_requests=0, gateway_restarted=False, deployment_patch_requested=False, ready_for_rollout=False, credentials_exported=False, secret_creation_requested=False, configmap_creation_requested=False, business_values_verified=False, candidate_checks=[], queries=[], resource_intents=[], candidate_jobs_ttl_seconds=600, candidate_job_cleanup_confirmed=False, persisted_custom_agent_reference_inventory_pending=True, secret_matches_canary_key_not_independently_proven=True)
    c = ResumptionController(root, report, binary)
    try:
        marker = parent / "portal-data-qa-glm-resume-20261021-r2.once"
        require(not marker.exists() and not marker.is_symlink(), "previous_resumption_requires_review")
        resume(c)
    except Exception as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, (Refused, ValueError)) and re.fullmatch(r"[A-Za-z0-9_]{1,100}", str(exc)) else "DETAILS_WITHHELD")
    except KeyboardInterrupt:
        report.update(error_class="KeyboardInterrupt", error_code="OPERATOR_INTERRUPTED_REVIEW_RESOURCE_INTENTS")
    report["elapsed_seconds"] = round(time.monotonic() - c.started, 3)
    meta = c.save()
    print(json.dumps(dict(report, **meta), sort_keys=True), flush=True)
    return report["exitcode"]


if __name__ == "__main__":
    raise SystemExit(main())
