"""No production calls. Effective-source replay and read-only drift checks."""

import ast
import base64
import copy
import json
from pathlib import Path

import pytest

import build_offline as builder
import effective_sources as effective
from apply_image_delta import apply, sha
from test_source_overlays import audit as _audit_fixture, fake_reader

audit = _audit_fixture
HERE = Path(__file__).parent


@pytest.fixture
def prepared(audit, monkeypatch):
    deployments, pods, cm, calls = fake_reader(audit, monkeypatch)
    report = audit.collect()
    binding = report["workloads"]["gateway"]["source_bindings"][0]
    old = base64.b64decode(binding["base64"])
    target = old + b"EMPLOYEE_BOOTSTRAP = True\n"
    components = {
        name: {
            "source_image": state["image"],
            "source_image_id": "sha256:" + "a" * 64,
            "component": name,
            "ready_for_rollout": False,
            "entries": [],
            "anchors": {},
        }
        for name, state in report["workloads"].items()
        if name != "ppt-master-runner"
    }
    components["gateway"].update(
        {
            "schema": 1,
            "scope": "employee-sharing-image-candidate",
            "entries": [
                {
                    "path": "app/gateway/app.py",
                    "source_sha256": sha(old),
                    "target_sha256": sha(target),
                    "content_base64": base64.b64encode(target).decode(),
                }
            ],
        }
    )
    evidence = effective.from_report(json.dumps(report).encode(), components)
    calls.clear()
    return evidence, components, deployments, pods, cm, calls, audit


def test_disposable_image_replays_runtime_bytes_before_employee_delta(
    prepared, tmp_path
):
    evidence, components, *_ = prepared
    app = tmp_path / "app/gateway/app.py"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"# original OCI source differs from mounted production\n")
    before = app.read_bytes()
    unchanged = tmp_path / "app/gateway/unrelated.py"
    unchanged.write_bytes(b"UNCHANGED = True\n")
    receipt = effective.apply_sources(tmp_path, evidence, components)
    assert app.read_bytes() == before
    assert receipt["files"][0]["image_sha256_before"] == sha(before)
    effective.apply_sources(tmp_path, evidence, components, check_only=False)
    assert sha(app.read_bytes()) == components["gateway"]["entries"][0]["source_sha256"]
    apply(tmp_path, components["gateway"], check_only=False)
    assert sha(app.read_bytes()) == components["gateway"]["entries"][0]["target_sha256"]
    assert unchanged.read_bytes() == b"UNCHANGED = True\n"
    assert not receipt["ready_for_rollout"]


@pytest.mark.parametrize(
    "problem",
    [
        "payload",
        "traversal",
        "duplicate",
        "wrong_image",
        "extra_component",
        "ambiguous_cm",
        "timestamp",
    ],
)
def test_bad_evidence_stops_before_any_image_write(prepared, tmp_path, problem):
    evidence, components, *_ = prepared
    original = copy.deepcopy(evidence)
    report = evidence["report"]
    bindings = report["workloads"]["gateway"]["source_bindings"]
    if problem == "payload":
        bindings[0]["base64"] = base64.b64encode(b"unreviewed").decode()
    elif problem == "traversal":
        bindings[0]["mount_path"] = "/app/backend/app/../outside.py"
    elif problem == "duplicate":
        bindings.append(copy.deepcopy(bindings[0]))
    elif problem == "wrong_image":
        report["workloads"]["frontend"]["image"] = "different"
    elif problem == "extra_component":
        report["workloads"]["provisioner"]["source_bindings"] = bindings
    elif problem == "ambiguous_cm":
        report["workloads"]["ppt-master-runner"]["source_bindings"][0][
            "configmap_immutable"
        ] = True
    else:
        report["collected_at"] = "2026-09-24T10:00:00"
    with pytest.raises((ValueError, KeyError)):
        effective.apply_sources(tmp_path, evidence, components, check_only=False)
    assert not list(tmp_path.iterdir())
    effective.validate(original, components)


def test_linked_image_target_rejected(prepared, tmp_path):
    evidence, components, *_ = prepared
    outside = tmp_path / "untouched.py"
    outside.write_bytes(b"ORIGINAL = 1\n")
    app = tmp_path / "app/gateway/app.py"
    app.parent.mkdir(parents=True)
    try:
        app.symlink_to(outside)
    except OSError:
        pytest.skip("requires symlink permission; Linux validation still required")
    with pytest.raises(ValueError, match="Unsafe"):
        effective.apply_sources(tmp_path, evidence, components, check_only=False)
    assert outside.read_bytes() == b"ORIGINAL = 1\n"


def test_later_invalid_python_overlay_prevents_earlier_replacement(prepared, tmp_path):
    evidence, components, *_ = prepared
    rows = evidence["report"]["workloads"]["gateway"]["source_bindings"]
    extra = copy.deepcopy(rows[0])
    bad = b"def invalid syntax!!!\n"
    extra.update(
        {
            "mount_path": "/app/backend/app/z.py",
            "sub_path": "z.py",
            "configmap_key": "z.py",
            "bytes": len(bad),
            "base64": base64.b64encode(bad).decode(),
            "sha256": sha(bad),
        }
    )
    rows.append(extra)
    app = tmp_path / "app/gateway/app.py"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"ORIGINAL = 1\n")
    with pytest.raises(SyntaxError):
        effective.apply_sources(tmp_path, evidence, components, check_only=False)
    assert app.read_bytes() == b"ORIGINAL = 1\n"
    assert not (tmp_path / "app/z.py").exists()


def setup_guard(prepared):
    evidence, components, deployments, pods, cm, calls, audit = prepared
    guard = effective.LiveGuard(evidence, components)
    guard.audit = audit
    return (
        guard,
        {"items": list(deployments.values())},
        {"items": pods},
        cm,
        calls,
        audit.read,
    )


def test_live_guard_hashes_once_per_container_and_again_at_completion(prepared):
    guard, deployments, pods, cm, calls, read = setup_guard(prepared)
    before = copy.deepcopy((deployments, pods, cm))
    allowed = guard.check(deployments, pods, read)
    assert len([a for a in calls if a[0] == "exec"]) == 2
    calls.clear()
    guard.check(deployments, pods, read)
    assert not any(a[0] == "exec" for a in calls)
    assert any(a[:2] == ["get", "configmap"] for a in calls)
    calls.clear()
    guard.check(deployments, pods, read, force_hash=True)
    assert len([a for a in calls if a[0] == "exec"]) == 2
    assert (deployments, pods, cm) == before
    assert "NEVER_EXPORT" not in json.dumps(allowed)
    assert all(a[0] in ("get", "exec") for a in calls)
    assert not any("secret" in a[:2] for a in calls)
    protected = {"gateway": {"/app/backend/app/gateway/app.py"}}
    builder.reject_source_overlays(deployments, pods, protected, allowed)
    with pytest.raises(RuntimeError):
        builder.reject_source_overlays(deployments, pods, protected)


@pytest.mark.parametrize(
    "problem",
    [
        "cm_uid",
        "cm_version",
        "cm_bytes",
        "unready",
        "terminating",
        "pod_mount",
        "deployment_env",
        "image",
        "new_mount",
        "new_pod_count",
    ],
)
def test_metadata_drift_blocks_even_after_first_success(prepared, problem):
    guard, deployments, pods, cm, calls, read = setup_guard(prepared)
    guard.check(deployments, pods, read)
    gw = next(p for p in pods["items"] if p["metadata"]["labels"]["app"] == "gateway")
    if problem == "cm_uid":
        cm["metadata"]["uid"] = "recreated"
    elif problem == "cm_version":
        cm["metadata"]["resourceVersion"] = "5"
    elif problem == "cm_bytes":
        cm["data"]["python-key"] += "# newer even with spoofed same RV\n"
    elif problem == "unready":
        gw["status"]["containerStatuses"][0]["ready"] = False
    elif problem == "terminating":
        gw["metadata"]["deletionTimestamp"] = "now"
    elif problem == "pod_mount":
        gw["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
    elif problem == "deployment_env":
        deployments["items"][0]["spec"]["template"]["spec"]["containers"][0][
            "env"
        ].clear()
    elif problem == "image":
        gw["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "f" * 64
    elif problem == "new_mount":
        gw["spec"]["containers"][0]["volumeMounts"].append(
            {"mountPath": "/app/backend/app", "name": "other"}
        )
    else:
        pods["items"].append(copy.deepcopy(gw))
    with pytest.raises(ValueError) as error:
        guard.check(deployments, pods, read)
    assert "NEVER_EXPORT" not in str(error.value)


def test_new_container_requires_new_hash_and_stale_subpath_fails(prepared):
    guard, deployments, pods, _, calls, read = setup_guard(prepared)
    guard.check(deployments, pods, read)
    gw = next(p for p in pods["items"] if p["metadata"]["labels"]["app"] == "gateway")
    gw["status"]["containerStatuses"][0]["containerID"] += "-new"
    calls.clear()

    def stale(args):
        result = read(args)
        if args[0] == "exec":
            next(iter(result.values()))["sha256"] = "0" * 64
        return result

    with pytest.raises(ValueError, match="actual mounted"):
        guard.check(deployments, pods, stale)
    assert any(a[0] == "exec" for a in calls)


@pytest.mark.parametrize(
    "later_change", [None, "replicas", "image", "unready", "environment", "pod_count"]
)
def test_reviewed_frontend_scale_refresh_retains_all_drift_checks(
    prepared, later_change
):
    evidence, components, deployments, pods, _, _, audit = prepared
    previous = copy.deepcopy(evidence)
    frontend = deployments["frontend"]
    frontend["spec"]["replicas"] = 2
    frontend["spec"]["template"]["metadata"] = {
        "annotations": {"reviewed-topology": "two-replicas"}
    }
    first = next(p for p in pods if p["metadata"]["labels"]["app"] == "frontend")
    second = copy.deepcopy(first)
    second["metadata"].update(name="frontend-second", uid="frontend-second-uid")
    second["status"]["containerStatuses"][0]["containerID"] += "-second"
    pods.append(second)
    live_deployments = {"items": list(deployments.values())}
    live_pods = {"items": pods}

    old_guard = effective.LiveGuard(evidence, components)
    old_guard.audit = audit
    with pytest.raises(ValueError, match="audited source deployment changed: frontend"):
        old_guard.check(live_deployments, live_pods, audit.read)

    refreshed = effective.from_report(json.dumps(audit.collect()).encode(), components)
    new_guard = effective.LiveGuard(refreshed, components)
    new_guard.audit = audit
    new_guard.check(live_deployments, live_pods, audit.read)
    assert evidence == previous
    assert len(refreshed["report"]["workloads"]["frontend"]["instances"]) == 2
    assert not refreshed["ready_for_rollout"]
    for name in ("gateway", "provisioner", "ppt-master-runner"):
        assert (
            refreshed["report"]["workloads"][name]
            == evidence["report"]["workloads"][name]
        )

    if later_change is None:
        return
    if later_change == "replicas":
        frontend["spec"]["replicas"] = 3
    elif later_change == "image":
        second["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "f" * 64
    elif later_change == "unready":
        second["status"]["containerStatuses"][0]["ready"] = False
    elif later_change == "environment":
        frontend["spec"]["template"]["spec"]["containers"][0]["env"].clear()
    else:
        pods.pop()
    with pytest.raises(ValueError):
        new_guard.check(live_deployments, live_pods, audit.read)


def test_fixed_reader_detects_concurrent_pod_restart(prepared):
    guard, deployments, pods, _, _, read = setup_guard(prepared)

    def restarting(args):
        result = read(args)
        if args[:2] == ["get", "pod"]:
            result["status"]["containerStatuses"][0]["restartCount"] += 1
        return result

    with pytest.raises(ValueError, match="runtime changed"):
        guard.check(deployments, pods, restarting)
    assert not guard.verified_instances


def test_configmap_change_during_guard_is_rejected(prepared):
    guard, deployments, pods, _, _, read = setup_guard(prepared)
    count = 0

    def changing(args):
        nonlocal count
        result = read(args)
        if args[:2] == ["get", "configmap"]:
            count += 1
            if count == 2:
                result["metadata"]["resourceVersion"] = "5"
        return result

    with pytest.raises(ValueError, match="during guard"):
        guard.check(deployments, pods, changing)


def test_slow_guard_stops_before_more_queries(prepared, monkeypatch):
    guard, deployments, pods, _, calls, read = setup_guard(prepared)
    times = iter((0, 31))
    monkeypatch.setattr(effective.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="read budget"):
        guard.check(deployments, pods, read)
    assert not calls


def test_builder_uses_audited_guard_before_permitting_any_mount(
    prepared, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    evidence, components, deployments, pods, _, calls, audit = prepared
    (tmp_path / "CANDIDATE.json").write_text(json.dumps({"components": components}))
    (tmp_path / "source-overlays.json").write_text(json.dumps(evidence))
    for name, value in components.items():
        (tmp_path / (name + "-delta.json")).write_text(json.dumps(value))
    controller = builder.Builder(tmp_path, tmp_path, tmp_path / "unused")
    monkeypatch.setattr(builder, "verify_package", lambda p: None)
    monkeypatch.setattr(effective, "auditor", lambda: audit)
    monkeypatch.setattr(builder, "inspect_cluster", lambda *a: {"healthy": True})
    monkeypatch.setattr(
        builder.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=200 * builder.GIB, used=100, total=1000),
    )
    real_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "MemAvailable: 33554432 kB\n"
            if str(p).replace("\\", "/") == "/proc/meminfo"
            else real_read(p, *a, **kw)
        ),
    )

    def kube(args):
        if "deployment" in args:
            assert "ppt-master-runner" in args
            return {"items": list(deployments.values())}
        if "pods" in args:
            assert "app in (gateway,frontend,provisioner,ppt-master-runner)" in args
            return {"items": pods}
        return {}

    monkeypatch.setattr(builder, "kube", kube)
    controller.guard()
    assert len([a for a in calls if a[0] == "exec"]) == 2
    assert controller.baseline == {"healthy": True}
    assert not controller.output.exists()
    calls.clear()
    controller.guard()
    assert not any(a[0] == "exec" for a in calls)
    # Replacing the entire overlay evidence cannot reset the guard's baseline.
    changed = copy.deepcopy(evidence)
    changed["report"]["workloads"]["gateway"]["deployment_uid"] = "other"
    (tmp_path / "source-overlays.json").write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match="package changed"):
        controller.guard()


@pytest.mark.parametrize("name", ["effective_sources.py", "source_overlay_plan.py"])
def test_new_host_helpers_support_python37_syntax(name):
    ast.parse((HERE / name).read_text(encoding="utf-8"), feature_version=(3, 7))


def test_reported_production_overlay_replays_all_fifteen_gateway_files(tmp_path):
    path = HERE / "audited-source-overlays-20260924.json"
    if not path.exists():
        pytest.skip("internal source report not present")
    raw = path.read_bytes()
    assert (
        sha(raw) == "41aa33d91d030118caf160023f947c204e40e877fc5df9ec2bed9c7028a821ec"
    )
    components = {
        name: json.loads(
            (
                HERE / "candidate-inputs-20260924-v3" / (name + "-delta.json")
            ).read_bytes()
        )
        for name in ("gateway", "frontend", "provisioner")
    }
    document = effective.from_report(raw, components)
    report = document["report"]
    assert len(report["workloads"]["gateway"]["source_bindings"]) == 15
    assert len(report["workloads"]["ppt-master-runner"]["source_bindings"]) == 4
    receipt = effective.apply_sources(tmp_path, document, components, check_only=False)
    assert len(receipt["files"]) == 15
    assert len(list(tmp_path.rglob("*.py"))) == 15
    for binding in report["workloads"]["gateway"]["source_bindings"]:
        relative = binding["mount_path"][len("/app/backend/") :]
        assert sha((tmp_path / relative).read_bytes()) == binding["sha256"]
    # Runner's private lease/deps patches never enter the Gateway image.
    assert not (tmp_path / "app/gateway/deps.py").exists()
    assert not (
        tmp_path
        / "packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py"
    ).exists()
