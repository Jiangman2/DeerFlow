"""Packaged with pinned, field-qualified controller helpers. Python 3.7 host."""

# The packager supplies imports, unchanged controller functions, and constants.

ONCE = "portal-gateway-glm-stream-activation-20261021-r1.once"
FRONT_BINDING = dict(uid="bb0b3f6e-31f7-4073-8b76-7650ea5b96ee", generation=225, spec_sha256="1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123")


def preload_gate():
    require(set(PRELOADS) == set(NODES) and len(NODES) == 5, "five_preloads_required")
    for name, receipt in PRELOADS.items():
        require(
            receipt["hostname"] == name
            and receipt["expected_node_uid"] == NODES[name]
            and receipt["schema"] == "portal-gateway-glm-stream-preload.v1"
            and receipt["status"] == "COMPLETE_GATEWAY_GLM_STREAM_PRELOAD_REVIEW_REQUIRED"
            and receipt["exitcode"] == 0
            and receipt["node_preload_passed"] is True
            and receipt["old_references_preserved"] is True
            and receipt["image_import_may_be_partial"] is False,
            "preload_not_accepted",
        )
        require(re.fullmatch("[a-f0-9]{32}", receipt["operation"]) and re.fullmatch("[a-f0-9]{64}", receipt["report_sha256"]) and 0 < receipt["report_bytes"] < 1048576, "preload_receipt_binding")
        require(len(receipt["roles"]) == 1, "one_image_role_required")
        role = receipt["roles"][0]
        require(
            role["manifest_digest"] == MD and role["config_digest"] == CD and role["tag"] == TAG and role["role"] == "gateway-forward" and all(role[k] == "PASS" for k in ("phase_a", "phase_b", "content_and_unpack", "cri_visibility")),
            "preload_image_or_acceptance_changed",
        )


def read_canary(parent):
    name = "portal-glm-stream-candidate-" + FIELD["operation"] + ".json"
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size == FIELD["report_bytes"], "canary_file_identity")
        raw = stream.read(FIELD["report_bytes"] + 1)
        after = os.fstat(stream.fileno())
    require((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "canary_file_changed")
    require(pin(raw) == dict(bytes=FIELD["report_bytes"], sha256=FIELD["report_sha256"]), "canary_file_digest")
    value = json.loads(raw)
    require(
        value["status"] == "GLM_STREAM_CANDIDATE_ACCEPTED_NOT_DEPLOYED"
        and value["candidate_bundle_sha256"] == FIELD["candidate_bundle_sha256"]
        and value["gateway_before"] == value["gateway_after"] == CURRENT_BINDING
        and value["workload_modified"] is False,
        "canary_receipt_invalid",
    )
    candidate = value["candidate"]
    require(
        candidate["status"] == "CANDIDATE_ACCEPTED"
        and candidate["sdk_version"] == "2.32.0"
        and candidate["completion_validation"] == "TERMINAL_AND_DONE"
        and candidate["provider"]["stage_results"] == dict(text="PASS", tool_call="PASS", tool_resume="PASS", image="PASS"),
        "canary_stages_incomplete",
    )
    return value


def check_runtime(value, fixed):
    require(
        value.get("http", {}).get("status") == "PASS"
        and value.get("glm", {}).get("status") == "PASS"
        and value.get("typed_uuid_read", {}).get("status") == "PASS"
        and value["typed_uuid_read"].get("id_type") == "UUID"
        and value.get("brand", {}).get("status") == "PASS",
        "runtime_acceptance_failed",
    )
    stream = value.get("stream_completion", {})
    require(stream.get("status") == "PASS" and stream.get("fixed") is fixed and stream.get("sdk_version") == "2.32.0" and stream.get("provider_requests") == 0, "stream_runtime_acceptance_failed")


def execute(c, backend, now=time.monotonic, sleep=time.sleep):
    r = c.report
    preload_gate()
    c.ready()
    before = c.get("gateway")
    require(binding(before) == CURRENT_BINDING and all_ready(before), "gateway_changed_since_canary")
    patch, expected = plan(before)
    baseline = {role: c.get(role) for role in ROLES}
    require(binding(baseline["frontend"]) == FRONT_BINDING, "frontend_changed_since_acceptance")
    c.save("BEFORE-DEPLOYMENTS.private.json", dict(gateway=before, **baseline))
    c.save("EXPECTED-SPEC.private.json", expected)
    r.update(before=binding(before), unrelated_before={k: binding(v) for k, v in baseline.items()})
    c.nodes()
    configuration_gate(c, backend)
    pods = active_pods(before, c.inventory(), OLD)
    service = c.call("gateway-service", ["get", "service", "gateway", "-o", "json"])
    c.endpoints(service, pods)
    r["preflight_observations"] = []
    for pod in selected_two(pods):
        value = c.remote(pod, dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=False))
        check_runtime(value, False)
        r["preflight_observations"].append(dict(pod=pod["metadata"]["name"], uid=pod["metadata"]["uid"], observation=value))
        print(json.dumps(dict(phase="GLM_STREAM_RUNTIME_PREFLIGHT", accepted=len(r["preflight_observations"]), total=2)), flush=True)
    require(all(binding(c.get(role)) == binding(v) for role, v in baseline.items()), "unrelated_changed_before_patch")
    d = c.get("gateway")
    require(binding(d) == CURRENT_BINDING and all_ready(d), "gateway_changed_before_patch")
    patch, expected = plan(d)
    c.nodes()
    configuration_gate(c, backend)
    require(c.call("gateway-service-before-patch", ["get", "service", "gateway", "-o", "json"]) == service, "gateway_service_changed_before_patch")
    c.helper.write_new(c.parent, ONCE, canonical(dict(operation=r["operation"], directory=r["directory"], before=binding(d), expected_spec_sha256=pin(canonical(expected))["sha256"], intent="one-image-only-glm-stream-completion")))
    r.update(once_claimed=True)
    c.save("PREPATCH-INTENT.private.json", dict(operation=r["operation"], before=binding(d), expected_spec_sha256=pin(canonical(expected))["sha256"]))
    r.update(production_workload_mutation=None, gateway_restarted=None)
    acknowledged = c.patch(patch)
    require(acknowledged["metadata"]["uid"] == UID and acknowledged["spec"] == expected, "patch_acknowledgement_changed")
    r.update(production_workload_mutation=True)
    c.save("PATCH-ACKNOWLEDGEMENT.private.json", acknowledged)
    deadline, progress = now() + 4200, now()
    while True:
        current = c.get("gateway")
        require(current["metadata"]["uid"] == UID and current["spec"] == expected and current["metadata"]["generation"] == acknowledged["metadata"]["generation"], "gateway_changed_after_patch")
        if all_ready(current):
            pods = active_pods(current, c.inventory(), NEW)
            c.endpoints(service, pods)
            break
        require(now() < deadline, "rollout_deadline_review_required")
        if now() >= progress:
            s = current.get("status", {})
            print(json.dumps(dict(phase="GATEWAY_GLM_STREAM_ROLLOUT", desired=70, updated=s.get("updatedReplicas", 0), ready=s.get("readyReplicas", 0))), flush=True)
            progress = now() + 20
        sleep(10)
    r["runtime_observations"] = []
    for i, pod in enumerate(pods):
        value = c.remote(pod, dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=True))
        check_runtime(value, True)
        r["runtime_observations"].append(dict(pod=pod["metadata"]["name"], uid=pod["metadata"]["uid"], node=pod["spec"]["nodeName"], observation=value))
        r["runtime_accepted"] = i + 1
        if (i + 1) % 10 == 0:
            print(json.dumps(dict(phase="GATEWAY_GLM_STREAM_RUNTIME_ACCEPTANCE", accepted=i + 1, total=70)), flush=True)
    final = c.get("gateway")
    require(binding(final) == binding(current) and all_ready(final), "gateway_changed_during_acceptance")
    finalpods = active_pods(final, c.inventory(), NEW)

    def identities(rows):
        return {p["metadata"]["uid"]: [(s["name"], s.get("containerID"), s.get("imageID"), s.get("restartCount")) for s in p["status"]["containerStatuses"]] for p in rows}

    require(identities(finalpods) == identities(pods), "containers_changed_during_acceptance")
    c.endpoints(service, finalpods)
    require(c.call("final-gateway-service", ["get", "service", "gateway", "-o", "json"]) == service, "gateway_service_changed")
    c.nodes()
    configuration_gate(c, backend)
    require(all(binding(c.get(role)) == binding(v) for role, v in baseline.items()), "unrelated_deployment_changed")
    c.save("AFTER-GATEWAY.private.json", final)
    r.update(
        stage_passed=True,
        status="GATEWAY_GLM_STREAM_RUNTIME_ACCEPTED_BROWSER_PENDING",
        after=binding(final),
        runtime_accepted=70,
        stream_completion_runtime_accepted=70,
        authenticated_internal_http_accepted=70,
        glm_runtime_accepted=70,
        typed_uuid_read_accepted=70,
        frontend_preserved=True,
        configuration_preserved=True,
        unrelated_specs_preserved=True,
        gateway_other_spec_preserved=True,
        gateway_restarted=True,
        browser_acceptance_pending=True,
        image=NEW,
    )


def run():
    r = dict(
        schema="portal-gateway-glm-stream-activation.v1",
        status="INCOMPLETE",
        stage_passed=False,
        operation=uuid.uuid4().hex,
        commands=[],
        patch_requested=False,
        patch_request_count=0,
        patch_may_have_applied=False,
        automatic_retry=False,
        provider_requests=0,
        production_workload_mutation=False,
        business_state_write_requested=False,
        database_schema_changed=False,
        tasks_drained_operator_attested=False,
        source_pins=PINS,
        preloads=PRELOADS,
        field_acceptance=FIELD,
    )
    parent = directory = helper = None
    started = time.monotonic()
    try:
        require(os.name == "posix" and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode and socket.gethostname().split(".")[0] == "datax-01", "isolated_root_datax01_required")
        require(sys.argv[1:] == ["--activate-gateway-glm-stream-once", "--tasks-drained"], "explicit_idle_window_required")
        r["tasks_drained_operator_attested"] = True
        require(set(SOURCES) == set(PINS) == {"helper", "runtime"} and all(pin(v) == PINS[k] for k, v in SOURCES.items()), "embedded_source_pin")
        helper = types.ModuleType("_qualified_controller_helper")
        exec(compile(SOURCES["helper"], "<helper>", "exec"), helper.__dict__)
        parent = helper.private_parent()
        require(not os.path.lexists("/var/lib/" + ONCE), "activation_already_claimed_review_existing_report")
        require(os.fstatvfs(parent).f_bavail * os.fstatvfs(parent).f_frsize >= 671088640, "private_log_budget")
        read_canary(parent)
        backend = private_read(parent, BACKEND["folder"], "REPORT.json", BACKEND["bytes"], {k: BACKEND[k] for k in ("bytes", "sha256")})
        folder = "portal-gateway-glm-stream-activation-r1-" + r["operation"]
        os.mkdir(folder, 0o700, dir_fd=parent)
        os.fsync(parent)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        r["directory"] = "/var/lib/" + folder
        binary = shutil.which("kubectl", path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        require(binary in ("/usr/bin/kubectl", "/usr/local/bin/kubectl"), "trusted_kubectl_path")
        for path in (binary, os.path.dirname(binary)):
            info = os.lstat(path)
            require(info.st_uid == 0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode), "trusted_kubectl_permissions")
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        c = ActivationController(helper, directory, r, binary)
        c.parent = parent
        execute(c, backend)
    except BaseException as error:
        code = str(error)
        r.update(status="INCOMPLETE", stage_passed=False, error_class=type(error).__name__, error_code=code if isinstance(error, Refused) and re.fullmatch("[a-z0-9_-]{1,120}", code) else "DETAILS_WITHHELD")
    finally:
        r["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if directory is not None:
            try:
                raw = canonical(r) + b"\n"
                require(len(raw) <= 4194304, "report_size_limit")
                helper.write_new(directory, "REPORT.json", raw)
                os.fsync(directory)
                r.update(report_path=r["directory"] + "/REPORT.json", report_bytes=len(raw), report_sha256=pin(raw)["sha256"])
            except BaseException:
                r.update(status="INCOMPLETE", stage_passed=False, error_code="report_persistence_failed")
        for fd in (directory, parent):
            if fd is not None:
                os.close(fd)
    fields = (
        "schema",
        "status",
        "stage_passed",
        "operation",
        "error_class",
        "error_code",
        "elapsed_seconds",
        "before",
        "after",
        "runtime_accepted",
        "stream_completion_runtime_accepted",
        "authenticated_internal_http_accepted",
        "glm_runtime_accepted",
        "typed_uuid_read_accepted",
        "patch_requested",
        "patch_request_count",
        "patch_may_have_applied",
        "automatic_retry",
        "provider_requests",
        "production_workload_mutation",
        "business_state_write_requested",
        "database_schema_changed",
        "frontend_preserved",
        "configuration_preserved",
        "unrelated_specs_preserved",
        "gateway_other_spec_preserved",
        "gateway_restarted",
        "tasks_drained_operator_attested",
        "browser_acceptance_pending",
        "image",
        "report_path",
        "report_bytes",
        "report_sha256",
    )
    out = {k: r.get(k) for k in fields}
    out["exitcode"] = 0 if r["stage_passed"] else 1
    print(json.dumps(out, sort_keys=True), flush=True)
    return out["exitcode"]


if __name__ == "__main__":
    raise SystemExit(run())
