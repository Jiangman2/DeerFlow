"""Build-only failure boundaries; no systemd/production operations in tests."""

import ast
import copy
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

import build_offline as builder
import build_resources as resources
import bounded_runc
import prepare_oci as staging
from test_oci_base import layout
from verify_built_image import preserved
from verify_oci_base import verify

HERE = Path(__file__).parent


def archive_from(root, target):
    with tarfile.open(target, "w") as stream:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stream.add(
                    path, arcname=path.relative_to(root).as_posix(), recursive=False
                )
    return target


def test_real_tar_staging_preserves_graph_and_refuses_existing_destination(tmp_path):
    root, expected, *_ = layout(tmp_path)
    archive = archive_from(root, tmp_path / "base.tar")
    staged = staging.extract(archive, tmp_path / "staged")
    assert verify(staged, expected)["layers_verified"] == 1
    with pytest.raises(ValueError, match="exists"):
        staging.extract(archive, staged)
    assert verify(root, expected)["layers_verified"] == 1


def test_real_tar_stages_345_layer_base_and_verifies_candidate(tmp_path):
    import hashlib
    import subprocess

    # Opaque synthetic blobs exercise the archive/graph/controller contract;
    # this is not an ARM64 runtime or snapshotter acceptance test.
    base, expected, _, _, _, _ = layout(tmp_path / "baseline", layer_count=345)
    staged = staging.extract(
        archive_from(base, tmp_path / "base.tar"), tmp_path / "staged-base"
    )
    receipt = verify(staged, expected)
    assert receipt["layers_verified"] == 345
    candidate, _, _, manifest, _, _ = layout(tmp_path / "candidate", layer_count=350)
    archive = archive_from(candidate, tmp_path / "candidate.tar")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"containerimage.digest": manifest["digest"]}))
    base_receipt = tmp_path / "base-receipt.json"
    base_receipt.write_text(json.dumps(receipt))
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(HERE / "verify_built_image.py"),
            "--archive",
            str(archive),
            "--output",
            str(tmp_path / "verified-candidate"),
            "--image",
            expected["source_image"],
            "--metadata",
            str(metadata),
            "--base",
            str(staged),
            "--base-receipt",
            str(base_receipt),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    result = json.loads(result.stdout)
    assert result["layers_verified"] == 350
    assert result["base_layer_prefix"] == "PRESERVED"
    assert result["runtime_config"] == "PRESERVED"
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["ready_for_rollout"] is False


@pytest.mark.parametrize(
    "problem",
    [
        "parent",
        "absolute",
        "symlink",
        "hardlink",
        "fifo",
        "sparse",
        "oversize",
        "unknown",
        "duplicate",
        "checksum",
    ],
)
def test_archive_rejection_leaves_original_and_outside_untouched(tmp_path, problem):
    archive = tmp_path / "bad.tar"
    member = tarfile.TarInfo("oci-layout")
    data = b"{}"
    if problem == "parent":
        member.name = "../outside"
    elif problem == "absolute":
        member.name = "/tmp/outside"
    elif problem in ("symlink", "hardlink"):
        member.type = tarfile.SYMTYPE if problem == "symlink" else tarfile.LNKTYPE
        member.linkname = "../outside"
    elif problem == "fifo":
        member.type = tarfile.FIFOTYPE
    elif problem == "sparse":
        member.sparse = [(0, 1)]
        with pytest.raises(ValueError, match="sparse"):
            staging.member_name(member)
        return
    elif problem == "oversize":
        member.size = staging.META_LIMIT + 1
        with pytest.raises(ValueError, match="Oversized"):
            staging.member_name(member)
        return
    elif problem == "unknown":
        member.name = "Dockerfile"
    elif problem == "checksum":
        member.name = "blobs/sha256/" + "0" * 64
    member.size = len(data)
    with tarfile.open(archive, "w") as stream:
        stream.addfile(member, io.BytesIO(data))
        if problem == "duplicate":
            stream.addfile(member, io.BytesIO(data))
    original = archive.read_bytes()
    with pytest.raises(ValueError):
        staging.extract(archive, tmp_path / "staged")
    assert archive.read_bytes() == original
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("problem", ["config", "reordered", "same", "truncated"])
def test_candidate_runtime_and_exact_base_layers_cannot_be_changed(problem):
    base = {
        "config": {"User": "app", "Cmd": ["existing"], "Env": ["PRESERVE=true"]},
        "rootfs": {"diff_ids": ["one", "two"]},
    }
    candidate = copy.deepcopy(base)
    candidate["rootfs"]["diff_ids"].append("employee")
    preserved(base, candidate)
    if problem == "config":
        candidate["config"]["Cmd"] = ["other"]
    elif problem == "reordered":
        candidate["rootfs"]["diff_ids"] = ["two", "one", "employee"]
    elif problem == "same":
        candidate["rootfs"]["diff_ids"] = ["one", "two"]
    else:
        candidate["rootfs"]["diff_ids"] = ["employee"]
    with pytest.raises(ValueError):
        preserved(base, candidate)


def limits(tmp_path, version):
    relative = "employeesharingtest.slice"
    if version == "v2":
        roots = {"unified": tmp_path / "unified"}
        content = {
            "unified": {
                "cpu.max": "150000 100000",
                "memory.max": str(resources.MEMORY_BYTES),
                "pids.max": "2048",
                "io.max": "252:16 rbps=%s wbps=%s riops=max wiops=max"
                % (resources.READ_BPS, resources.WRITE_BPS),
            }
        }
    else:
        roots = {name: tmp_path / name for name in ("cpu", "memory", "pids", "blkio")}
        content = {
            "cpu": {"cpu.cfs_quota_us": "150000", "cpu.cfs_period_us": "100000"},
            "memory": {
                "memory.limit_in_bytes": str(resources.MEMORY_BYTES),
                "memory.use_hierarchy": "1",
            },
            "pids": {"pids.max": "2048"},
            "blkio": {
                "blkio.throttle.read_bps_device": "252:16 " + str(resources.READ_BPS),
                "blkio.throttle.write_bps_device": "252:16 " + str(resources.WRITE_BPS),
            },
        }
    for controller, entries in content.items():
        directory = roots[controller] / relative
        directory.mkdir(parents=True)
        for name, text in entries.items():
            (directory / name).write_text(text)
    return roots, "/" + relative, "252:16"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_kernel_bounds_are_required_not_only_systemd_flags(tmp_path, version):
    roots, cgroup, device = limits(tmp_path, version)
    assert resources.check_limits(roots, cgroup, device) == version
    cpu = roots.get("unified", roots.get("cpu")) / cgroup[1:]
    path = cpu / ("cpu.max" if version == "v2" else "cpu.cfs_quota_us")
    path.write_text("max 100000" if version == "v2" else "-1")
    with pytest.raises((ValueError, RuntimeError)):
        resources.check_limits(roots, cgroup, device)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("problem", ["wrong_device", "memory", "pids", "io", "missing"])
def test_partial_or_wrong_resource_enforcement_is_blocked(tmp_path, version, problem):
    roots, cgroup, device = limits(tmp_path, version)
    if problem == "wrong_device":
        device = "8:0"
    elif problem == "missing":
        roots.clear()
    else:
        controller = {"memory": "memory", "pids": "pids", "io": "blkio"}[problem]
        directory = roots.get("unified", roots.get(controller)) / cgroup[1:]
        filename = {
            "memory": "memory.max" if version == "v2" else "memory.limit_in_bytes",
            "pids": "pids.max",
            "io": "io.max" if version == "v2" else "blkio.throttle.write_bps_device",
        }[problem]
        (directory / filename).write_text("max" if problem != "io" else "")
    with pytest.raises((ValueError, RuntimeError)):
        resources.check_limits(roots, cgroup, device)


def test_v1_memory_must_cover_descendant_containers(tmp_path):
    roots, cgroup, device = limits(tmp_path, "v1")
    (roots["memory"] / cgroup[1:] / "memory.use_hierarchy").write_text("0")
    with pytest.raises(RuntimeError, match="children"):
        resources.check_limits(roots, cgroup, device)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_real_executor_must_be_in_every_limited_controller(tmp_path, version):
    roots, cgroup, _ = limits(tmp_path, version)
    primary = roots.get("unified", roots.get("cpu"))
    child = primary / cgroup[1:] / "buildkit/abc"
    child.mkdir(parents=True)
    (child / "cgroup.procs").write_text("42\n")
    proc = tmp_path / "proc"
    (proc / "42").mkdir(parents=True)
    mapping = (
        "0::%s/buildkit/abc\n" % cgroup
        if version == "v2"
        else "".join(
            "%d:%s:%s/buildkit/abc\n" % (i, name, cgroup)
            for i, name in enumerate(("cpu", "memory", "blkio", "pids"), 1)
        )
    )
    (proc / "42/cgroup").write_text(mapping)
    assert resources.executor_is_bounded(roots, cgroup, proc)
    (proc / "42/cgroup").write_text(
        mapping.replace(cgroup + "/buildkit/abc", "/wrong", 1)
    )
    assert not resources.executor_is_bounded(roots, cgroup, proc)


def test_hybrid_mounts_choose_real_v1_controllers():
    content = "cgroup2 /sys/fs/cgroup/unified cgroup2 rw 0 0\n" + "".join(
        "cgroup /sys/fs/cgroup/%s cgroup rw,%s 0 0\n" % (x, x)
        for x in ("cpu", "memory", "pids", "blkio")
    )
    assert "unified" not in resources.controller_roots(content)


def test_daemon_network_must_be_separate_loopback_only(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    (proc / "42/net").mkdir(parents=True)
    (proc / "42/net/dev").write_text("header\nheader\nlo: 0\n")
    monkeypatch.setattr(
        resources.os, "readlink", lambda value: "host" if "self" in value else "private"
    )
    resources.private_network(42, proc)
    (proc / "42/net/dev").write_text("header\nheader\nlo: 0\neth0: 0\n")
    with pytest.raises(RuntimeError, match="loopback"):
        resources.private_network(42, proc)
    (proc / "42/net/dev").write_text("header\nheader\nlo: 0\n")
    monkeypatch.setattr(resources.os, "readlink", lambda value: "same")
    with pytest.raises(RuntimeError, match="loopback"):
        resources.private_network(42, proc)


def cluster():
    node = {
        "status": {
            "conditions": [
                {"type": name, "status": value}
                for name, value in (
                    ("Ready", "True"),
                    ("MemoryPressure", "False"),
                    ("DiskPressure", "False"),
                    ("PIDPressure", "False"),
                )
            ]
        }
    }
    deployments, pods, expected = {"items": []}, {"items": []}, {}
    for name in builder.COMPONENTS:
        image = "deerflow.local/" + name + ":base"
        identity = "sha256:" + "a" * 64
        expected[name] = {"source_image": image, "source_image_id": identity}
        deployments["items"].append(
            {
                "metadata": {"name": name, "uid": name, "generation": 3},
                "spec": {
                    "replicas": 1,
                    "template": {
                        "spec": {"containers": [{"name": name, "image": image}]}
                    },
                },
                "status": {
                    "observedGeneration": 3,
                    "replicas": 1,
                    "updatedReplicas": 1,
                    "readyReplicas": 1,
                    "availableReplicas": 1,
                },
            }
        )
        pods["items"].append(
            {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "nodeName": "ecs-zdz-pro-0022" if name == "gateway" else "datax-01"
                },
                "status": {
                    "containerStatuses": [
                        {
                            "name": name,
                            "ready": True,
                            "imageID": "containerd://" + identity,
                        }
                    ]
                },
            }
        )
    return node, deployments, pods, expected, "datax-01"


@pytest.mark.parametrize(
    "problem",
    [
        "colocation",
        "terminating",
        "pressure",
        "missing_condition",
        "no_gateway",
        "unready",
        "replicas",
        "generation",
        "image",
        "image_id",
    ],
)
def test_cluster_guard_rejects_unsafe_or_changed_production(problem):
    args = cluster()
    assert len(builder.inspect_cluster(*args)) == 3
    node, deployments, pods, _, _ = args
    if problem in ("colocation", "terminating"):
        pods["items"][0]["spec"]["nodeName"] = "datax-01"
        if problem == "terminating":
            pods["items"][0]["metadata"]["deletionTimestamp"] = "now"
    elif problem == "pressure":
        node["status"]["conditions"][1]["status"] = "True"
    elif problem == "missing_condition":
        node["status"]["conditions"].pop()
    elif problem == "no_gateway":
        pods["items"].pop(0)
    elif problem == "unready":
        pods["items"][0]["status"]["containerStatuses"][0]["ready"] = False
    elif problem == "image_id":
        pods["items"][1]["status"]["containerStatuses"][0]["imageID"] = (
            "sha256:" + "b" * 64
        )
    elif problem == "image":
        deployments["items"][0]["spec"]["template"]["spec"]["containers"][0][
            "image"
        ] = "other"
    elif problem == "generation":
        deployments["items"][0]["status"]["observedGeneration"] = 2
    else:
        deployments["items"][0]["status"]["updatedReplicas"] = 0
    with pytest.raises(RuntimeError):
        builder.inspect_cluster(*args)


def test_build_arguments_use_only_offline_base_and_explicit_executor_limits():
    args = builder.build_arguments(
        "/usr/bin/buildctl",
        "unix:///opt/build/socket",
        Path("/opt/package"),
        "gateway",
        Path("/opt/base"),
        "sha256:" + "a" * 64,
        "/employeesharingtest.slice",
        stage="source-check",
    )
    assert "--oci-layout" in args
    assert (
        "context:employee-gateway-base=oci-layout://gateway_base@sha256:" + "a" * 64
        in args
    )
    assert "cgroup-parent=/employeesharingtest.slice" in args
    assert "force-network-mode=none" in args
    assert "target=source-check" in args
    assert "--output" not in args
    assert not any("http" in a or "push=true" in a for a in args)


def test_receipts_cannot_be_inferred_from_pass_text_or_duplicates():
    valid = b'{"audited_oci_base":"PASS", "ready_for_rollout":false}\n'
    assert (
        builder.json_receipt(
            b"Running unit\n" + valid + b"Finished\n", "audited_oci_base"
        )["ready_for_rollout"]
        is False
    )
    for bad in (b"audited_oci_base=PASS", valid + valid, b'{"other":"PASS"}'):
        with pytest.raises(RuntimeError):
            builder.json_receipt(bad, "audited_oci_base")


def test_build_order_checks_all_sources_before_candidates_and_never_imports(
    tmp_path, monkeypatch
):
    package, output, archives = [
        tmp_path / name for name in ("package", "output", "archives")
    ]
    for path in (package, output, archives):
        path.mkdir()
    (package / "CANDIDATE.json").write_text(json.dumps({"components": cluster()[3]}))
    (package / "PACKAGE.SHA256").write_text("fixture")
    controller = builder.Builder(package, archives, output)
    called = []

    def phase(name, arguments, **kwargs):
        called.append((name, arguments, kwargs))
        if name.startswith("candidate-"):
            destination = arguments[arguments.index("--output") + 1].split("dest=")[1]
            Path(destination).write_bytes(b"disposable fixture archive")
        return json.dumps(
            {
                "audited_oci_base": "PASS",
                "manifest_digest": "sha256:" + "a" * 64,
                "archive_sha256": "b" * 64,
                "ready_for_rollout": False,
            }
        ).encode()

    monkeypatch.setattr(controller, "phase", phase)
    guards = []
    monkeypatch.setattr(controller, "guard", lambda **kw: guards.append(kw))
    result = controller.build()
    assert guards == [{"force_source_hash": True}]
    names = [name for name, _, _ in called]
    assert names == [
        "stage-gateway",
        "stage-frontend",
        "stage-provisioner",
        "resource-canary",
        "source-gateway",
        "source-frontend",
        "source-provisioner",
        "candidate-gateway",
        "verify-gateway",
        "candidate-frontend",
        "verify-frontend",
        "candidate-provisioner",
        "verify-provisioner",
    ]
    assert called[3][2]["canary"] is True
    assert result["ready_for_rollout"] is False and result["images_imported"] is False
    assert all(
        "kubectl" not in a and "ctr" not in a
        for _, args, _ in called
        for a in map(str, args)
    )


def test_source_check_failure_prevents_any_candidate_build(tmp_path, monkeypatch):
    package, output = tmp_path / "package", tmp_path / "output"
    package.mkdir()
    output.mkdir()
    (package / "CANDIDATE.json").write_text(json.dumps({"components": cluster()[3]}))
    controller = builder.Builder(package, tmp_path, output)
    names = []

    def fail(name, arguments, **kwargs):
        names.append(name)
        if name == "source-frontend":
            raise RuntimeError("fixture anchor mismatch")
        return b'{"audited_oci_base":"PASS", "manifest_digest":"sha256:fixture"}'

    monkeypatch.setattr(controller, "phase", fail)
    with pytest.raises(RuntimeError, match="anchor"):
        controller.build()
    assert not any(name.startswith("candidate-") for name in names)


def test_residual_executor_keeps_slice_and_hold_for_resource_protection(
    tmp_path, monkeypatch
):
    package = tmp_path / "package"
    package.mkdir()
    (package / "CANDIDATE.json").write_text(json.dumps({"components": {}}))
    controller = builder.Builder(package, tmp_path, tmp_path)
    controller.token, controller.slice, controller.cgroup = (
        "employeesharingtest",
        "employeesharingtest.slice",
        "/employeesharingtest.slice",
    )
    roots, _, _ = limits(tmp_path, "v1")
    controller.roots = roots
    child = roots["cpu"] / controller.cgroup[1:] / "buildkit/a"
    child.mkdir(parents=True)
    (child / "cgroup.procs").write_text("42")
    controller.units = [
        "ownedhold.service",
        "owneddaemon.service",
        "ownedphase.service",
    ]
    calls = []
    monkeypatch.setattr(builder, "run", lambda args, *a: calls.append(args) or "")
    with pytest.raises(RuntimeError, match="retained"):
        controller.stop()
    assert calls == [
        ["systemctl", "stop", "ownedphase.service"],
        ["systemctl", "stop", "owneddaemon.service"],
    ]


@pytest.mark.parametrize(
    "name",
    [
        "build_offline.py",
        "build_resources.py",
        "bounded_runc.py",
        "prepare_oci.py",
        "verify_built_image.py",
        "verify_candidate.py",
        "verify_oci_base.py",
    ],
)
def test_enterprise_host_controls_have_python37_syntax(name):
    ast.parse((HERE / name).read_text(encoding="utf-8"), feature_version=(3, 7))


def test_linked_archive_and_destination_rejected(tmp_path):
    root, *_ = layout(tmp_path)
    archive = archive_from(root, tmp_path / "base.tar")
    linked = tmp_path / "linked.tar"
    try:
        linked.symlink_to(archive)
    except OSError:
        pytest.skip("Linux validates link rejection")
    with pytest.raises(ValueError):
        staging.extract(linked, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


@pytest.mark.skipif(
    os.name == "nt", reason="Linux validates Unix ownership and /opt path syntax"
)
def test_root_owned_path_validation_rejects_broad_or_unsafe_locations(tmp_path):
    for path in ("/", "/opt", "/opt/a/../b", "/opt/has a space", str(tmp_path)):
        with pytest.raises(ValueError):
            builder.opt_path(path)


def runtime_spec():
    settings = {"slice": "employeesharing" + "a" * 16 + ".slice", "device": "252:16"}
    spec = {
        "process": {
            "args": ["/bin/sh", "-c", "build"],
            "env": ["DO_NOT_EXPOSE=private"],
        },
        "linux": {
            "cgroupsPath": "/" + settings["slice"] + "/buildkit/abcdef",
            "resources": {"cpu": {"shares": 100}},
        },
    }
    return settings, spec


def test_leaf_runtime_limits_preserve_command_env_and_other_resources():
    settings, spec = runtime_spec()
    before = copy.deepcopy(spec)
    result = bounded_runc.amend(spec, settings)
    assert result["process"] == before["process"]
    assert result["linux"]["resources"]["cpu"] == before["linux"]["resources"]["cpu"]
    assert result["linux"]["resources"]["blockIO"] == {
        "throttleReadBpsDevice": [
            {"major": 252, "minor": 16, "rate": resources.READ_BPS // 3}
        ],
        "throttleWriteBpsDevice": [
            {"major": 252, "minor": 16, "rate": resources.WRITE_BPS // 3}
        ],
    }
    assert bounded_runc.amend(result, settings) == result


@pytest.mark.parametrize(
    "problem", ["production_slice", "escape", "nested", "conflicting_limit"]
)
def test_runtime_cannot_patch_nonowned_or_conflicting_specs(problem):
    settings, spec = runtime_spec()
    if problem == "production_slice":
        settings["slice"] = "system.slice"
    elif problem == "escape":
        spec["linux"]["cgroupsPath"] = "/system.slice/production.service"
    elif problem == "nested":
        spec["linux"]["cgroupsPath"] += "/../../other"
    else:
        spec["linux"]["resources"]["blockIO"] = {
            "throttleReadBpsDevice": [{"major": 8, "minor": 0, "rate": 999999999}]
        }
    with pytest.raises(ValueError):
        bounded_runc.amend(spec, settings)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_leaf_io_limits_require_actual_kernel_values(tmp_path, version):
    roots, cgroup, device = limits(tmp_path, version)
    # A budget only at the parent / at the full rate is NOT the smaller leaf budget.
    with pytest.raises(RuntimeError):
        resources.check_leaf_io(roots, cgroup, device)
    if version == "v2":
        (roots["unified"] / cgroup[1:] / "io.max").write_text(
            "252:16 rbps=%s wbps=%s"
            % (resources.READ_BPS // 3, resources.WRITE_BPS // 3)
        )
    else:
        for direction, ceiling in (
            ("read", resources.READ_BPS),
            ("write", resources.WRITE_BPS),
        ):
            (
                roots["blkio"]
                / cgroup[1:]
                / ("blkio.throttle.%s_bps_device" % direction)
            ).write_text("252:16 " + str(ceiling // 3))
    resources.check_leaf_io(roots, cgroup, device)


def test_runtime_wrapper_amends_only_new_build_config_before_real_exec(
    tmp_path, monkeypatch
):
    settings, spec = runtime_spec()
    settings.update(root=str(tmp_path / "buildkit"), runtime="/usr/bin/runc")
    bundle = Path(settings["root"]) / "runc/a"
    bundle.mkdir(parents=True)
    config = bundle / "config.json"
    config.write_text(json.dumps(spec))
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings))
    called = []
    monkeypatch.setattr(bounded_runc.os, "execv", lambda *args: called.append(args))
    bounded_runc.main(
        str(path), ["--log", "/tmp/log", "run", "--bundle", str(bundle), "abcdef"]
    )
    assert called[0][0] == "/usr/bin/runc"
    assert json.loads(config.read_text())["process"] == spec["process"]
    assert not (bundle / "employee-bounded-config.json").exists()
    called.clear()
    with pytest.raises(ValueError):
        bounded_runc.main(str(path), ["run", "--bundle", str(tmp_path), "abcdef"])
    assert not called


def test_cleanup_accepts_garbage_collected_units_but_not_active_failures(monkeypatch):
    def failed_stop(args, *unused, **kwargs):
        if args[1] == "stop":
            raise RuntimeError("unit unavailable")
        assert kwargs["allow_absent_unit"] is True
        return "not-found\n"

    monkeypatch.setattr(builder, "run", failed_stop)
    builder.stop_owned_unit("owned.service")
    monkeypatch.setattr(builder, "unit_absent", lambda unit: False)
    with pytest.raises(RuntimeError, match="unavailable"):
        builder.stop_owned_unit("owned.service")


def test_cli_build_requires_ack_before_platform_or_host_actions(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_offline.py",
            "--base-archives",
            "/opt/bases",
            "--output",
            "/opt/build/output",
        ],
    )
    monkeypatch.setattr(
        builder,
        "run",
        lambda *args, **kwargs: pytest.fail("no command may run before ACK"),
    )
    with pytest.raises(SystemExit) as result:
        builder.main()
    assert result.value.code == 2


@pytest.mark.skipif(os.name == "nt", reason="Linux exercises host read-only controller")
def test_check_only_starts_no_units_and_creates_no_files(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    package = tmp_path / "package"
    package.mkdir()
    (package / "CANDIDATE.json").write_text(json.dumps({"components": cluster()[3]}))
    output, archives = tmp_path / "new-output", tmp_path / "new-archives"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_offline.py",
            "--base-archives",
            str(archives),
            "--output",
            str(output),
            "--check-only",
        ],
    )
    monkeypatch.setattr(builder, "HERE", package)
    monkeypatch.setattr(builder, "verify_package", lambda p: None)
    monkeypatch.setattr(builder, "opt_path", lambda p: p)
    monkeypatch.setattr(builder.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(builder.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(builder.os, "geteuid", lambda: 0)
    monkeypatch.setattr(builder.shutil, "which", lambda tool: "/usr/bin/" + tool)
    original_stat = os.stat
    monkeypatch.setattr(
        builder.os,
        "stat",
        lambda p, *a, **kw: (
            SimpleNamespace(
                st_dev=2 if str(p) == "/opt" else 1, st_mode=0o40755, st_uid=0
            )
            if str(p) in ("/", "/opt")
            else original_stat(p, *a, **kw)
        ),
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "MemAvailable: 33554432 kB\n"
            if str(p) == "/proc/meminfo"
            else original_read(p, *a, **kw)
        ),
    )
    commands = []

    def only_ps(args, *a, **kw):
        assert args == ["ps", "-eo", "comm=,args="]
        commands.append(args)
        return ""

    monkeypatch.setattr(builder, "run", only_ps)
    monkeypatch.setattr(
        builder.Builder, "guard", lambda self: commands.append("read_cluster_guard")
    )
    monkeypatch.setattr(
        builder.os,
        "open",
        lambda *a, **kw: pytest.fail("check-only cannot create lock or files"),
    )
    builder.main()
    result = json.loads(capsys.readouterr().out)
    assert result["base_archives_missing"] == list(builder.COMPONENTS)
    assert result["resource_limits_live_verified"] is False
    assert commands == [["ps", "-eo", "comm=,args="], "read_cluster_guard"]
    assert not output.exists() and not archives.exists()
