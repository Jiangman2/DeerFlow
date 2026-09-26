"""Single-Gateway build packaging and fail-closed boundaries; no cluster writes."""

import ast
import importlib.util
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DOWNLOADS = Path.home() / "Downloads"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    if os.environ.get("PPT_RELAY_TEST_BUILD2") == "1":
        pack = load(HERE / "package_build2.py", "ppt_build_pack")
        archive = pack.package(
            DOWNLOADS / "deerflow-ppt-relay-tool-arguments-20260924-r1-build1.tar.gz",
            tmp_path,
        )
    else:
        pack = load(HERE / "package_build.py", "ppt_build_pack")
        archive = pack.package(
            DOWNLOADS
            / "deerflow-ppt-relay-tool-arguments-20260924-r1-candidate.tar.gz",
            DOWNLOADS / "ppt-relay-runtime-20260924T103137Z.json",
            tmp_path,
        )
    with tarfile.open(archive) as bundle:
        bundle.extractall(tmp_path, filter="data")
    root = tmp_path / pack.NAME
    monkeypatch.syspath_prepend(str(root))
    # This suite is run separately from Employee infra tests: same generic
    # module basenames, independently frozen source and a different unit prefix.
    return root, load(root / "build-candidate.py", "ppt_build_controller")


def cluster(module, baseline):
    node = {
        "status": {
            "conditions": [
                {"type": k, "status": v}
                for k, v in (
                    ("Ready", "True"),
                    ("DiskPressure", "False"),
                    ("MemoryPressure", "False"),
                    ("PIDPressure", "False"),
                )
            ]
        }
    }
    deployments = {}
    for name, saved in baseline["deployments"].items():
        spec = {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {"name": n, "image": image}
                        for n, image in saved["images"].items()
                    ]
                }
            },
        }
        d = {
            "metadata": {"uid": saved["uid"], "generation": 1},
            "spec": spec,
            "status": {
                "observedGeneration": 1,
                "replicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
            },
        }
        deployments[name] = d
        baseline["deployments"][name] = module.audit.summary(d)
    pod = {
        "metadata": {"name": "gateway-one", "uid": "one"},
        "spec": {
            "nodeName": "ecs-zdz-pro-0022",
            "containers": [{"name": "gateway", "image": baseline["source_image"]}],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "gateway",
                    "ready": True,
                    "containerID": "running",
                    "imageID": baseline["source_image_id"],
                }
            ],
        },
    }
    return node, deployments, [pod]


def test_frozen_package_is_offline_narrow_and_python37_host_compatible(frozen):
    root, module = frozen
    module.verify_package(root)
    files = {p.name for p in root.iterdir() if p.is_file()}
    assert not {"app.py", "agents.py", "schema.sql", "build_offline.py"} & files
    docker = (root / "Dockerfile.gateway").read_text()
    assert "FROM ppt-relay-gateway-base AS candidate" in docker
    assert "RUN --network=none" in docker
    assert not any(word in docker for word in ("apt-get", "pip install", "# syntax="))
    for path in root.glob("*.py"):
        if path.name not in ("codex_responses_adapter.py", "smoke.py"):
            ast.parse(path.read_text(), feature_version=(3, 7))
    baseline = json.loads((root / "BASELINE.json").read_text())
    assert baseline["source_image_id"].startswith("sha256:a18f04d6")
    assert baseline["production_rollout_ready"] is False
    resource = (root / "isolated_buildkit.py").read_text()
    assert "LiveGuard" not in resource and "employee" not in resource
    assert "PrivateNetwork=yes" in resource and "max-parallelism = 1" in resource
    assert "cgroup-parent=" in resource and "executor_is_bounded" in resource
    assert "io" in (root / "build_resources.py").read_text()


@pytest.mark.parametrize(
    "problem",
    [
        "node",
        "terminating",
        "no_pods",
        "pressure",
        "missing_condition",
        "unready",
        "spec",
        "image_id",
        "source_mount",
        "generation",
    ],
)
def test_cluster_guard_blocks_unsafe_baseline(frozen, problem):
    root, module = frozen
    baseline = json.loads((root / "BASELINE.json").read_text())
    node, deployments, pods = cluster(module, baseline)
    module.inspect_cluster(node, deployments, pods, baseline)
    if problem == "node":
        pods[0]["spec"]["nodeName"] = "datax-01"
    elif problem == "terminating":
        pods[0]["metadata"]["deletionTimestamp"] = "now"
    elif problem == "no_pods":
        pods.clear()
    elif problem == "pressure":
        node["status"]["conditions"][1]["status"] = "True"
    elif problem == "missing_condition":
        node["status"]["conditions"].pop()
    elif problem == "unready":
        pods[0]["status"]["containerStatuses"][0]["ready"] = False
    elif problem == "spec":
        deployments["provisioner"]["spec"]["replicas"] = 2
    elif problem == "image_id":
        pods[0]["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "f" * 64
    elif problem == "source_mount":
        pods[0]["spec"]["containers"][0]["volumeMounts"] = [{"mountPath": "/app"}]
    else:
        deployments["gateway"]["status"]["observedGeneration"] = 0
    with pytest.raises(RuntimeError):
        module.inspect_cluster(node, deployments, pods, baseline)


def test_package_tamper_fails_before_any_build(frozen):
    root, module = frozen
    (root / "smoke.py").write_text("raise RuntimeError('not audited')\n")
    with pytest.raises(ValueError, match="checksum"):
        module.verify_package(root)


def test_offline_single_image_phases_and_no_import(frozen, tmp_path, monkeypatch):
    root, module = frozen
    out = tmp_path / "output"
    out.mkdir()
    b = module.Builder(root, tmp_path / "base.tar", out)
    calls = []

    def phase(label, argv, **kw):
        calls.append((label, list(map(str, argv)), kw))
        if label == "candidate-gateway":
            (out / "gateway.candidate.oci.tar.partial").write_bytes(b"fixture")
        return json.dumps(
            {
                "audited_oci_base": "PASS",
                "manifest_digest": "sha256:" + "a" * 64,
                "archive_sha256": "b" * 64,
            }
        ).encode()

    monkeypatch.setattr(b, "phase", phase)
    monkeypatch.setattr(b, "guard", lambda **kw: None)
    result = b.build()
    assert [c[0] for c in calls] == [
        "stage-gateway",
        "resource-canary",
        "candidate-gateway",
        "verify-gateway",
    ]
    assert calls[1][2] == {"canary": True}
    for _, argv, _ in calls:
        assert not any(x in ("ctr", "kubectl", "pip", "curl") for x in argv)
    assert "force-network-mode=none" in calls[2][1]
    assert result["ready_for_rollout"] is False and result["images_imported"] is False


def test_no_live_side_effects_before_build_ack(frozen, monkeypatch):
    _, module = frozen
    monkeypatch.setattr(
        sys, "argv", ["build-candidate.py", "--output", "/opt/build/new"]
    )
    monkeypatch.setattr(
        module, "run", lambda *a, **kw: pytest.fail("no calls before ACK")
    )
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2


def resource_fixture(tmp_path, module, version):
    cgroup = "/pptrelay" + "a" * 16 + ".slice"
    import build_resources as res

    if version == "v2":
        contents = {
            "unified": {
                "cpu.max": "150000 100000",
                "memory.max": str(res.MEMORY_BYTES),
                "pids.max": "2048",
                "io.max": "252:16 rbps=%s wbps=%s" % (res.READ_BPS, res.WRITE_BPS),
            }
        }
    else:
        contents = {
            "cpu": {"cpu.cfs_quota_us": "150000", "cpu.cfs_period_us": "100000"},
            "memory": {
                "memory.limit_in_bytes": str(res.MEMORY_BYTES),
                "memory.use_hierarchy": "1",
            },
            "pids": {"pids.max": "2048"},
            "blkio": {
                "blkio.throttle.read_bps_device": "252:16 " + str(res.READ_BPS),
                "blkio.throttle.write_bps_device": "252:16 " + str(res.WRITE_BPS),
            },
        }
    roots = {}
    for kind, entries in contents.items():
        roots[kind] = tmp_path / kind
        directory = roots[kind] / cgroup[1:]
        directory.mkdir(parents=True)
        for name, value in entries.items():
            (directory / name).write_text(value)
    return res, roots, cgroup


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_actual_kernel_limits_not_just_service_flags(frozen, tmp_path, version):
    _, module = frozen
    res, roots, cgroup = resource_fixture(tmp_path, module, version)
    assert res.check_limits(roots, cgroup, "252:16") == version
    with pytest.raises((ValueError, RuntimeError)):
        res.check_limits(roots, cgroup, "8:0")
    directory = roots.get("unified", roots.get("cpu")) / cgroup[1:]
    (directory / ("cpu.max" if version == "v2" else "cpu.cfs_quota_us")).write_text(
        "max 100000" if version == "v2" else "-1"
    )
    with pytest.raises((ValueError, RuntimeError)):
        res.check_limits(roots, cgroup, "252:16")


def test_resource_wrapper_cannot_modify_production_cgroups(frozen):
    import bounded_runc

    settings = {"slice": "pptrelay" + "a" * 16 + ".slice", "device": "252:16"}
    spec = {
        "process": {"args": ["safe"], "env": ["KEEP=yes"]},
        "linux": {
            "cgroupsPath": "/" + settings["slice"] + "/buildkit/xyz",
            "resources": {},
        },
    }
    result = bounded_runc.amend(spec, settings)
    assert result["process"] == {"args": ["safe"], "env": ["KEEP=yes"]}
    assert (
        result["linux"]["resources"]["blockIO"]["throttleReadBpsDevice"][0]["rate"] > 0
    )
    spec["linux"]["cgroupsPath"] = "/system.slice/containerd.service"
    with pytest.raises(ValueError):
        bounded_runc.amend(spec, settings)


def test_cleanup_only_owned_units_keeps_limits_for_residual_executor(
    frozen, tmp_path, monkeypatch
):
    root, module = frozen
    import isolated_buildkit as infra

    _, roots, cgroup = resource_fixture(tmp_path, module, "v1")
    b = module.Builder(root, tmp_path / "base.tar", tmp_path / "output")
    b.roots, b.cgroup = roots, cgroup
    b.units = ["owned-hold", "owned-daemon", "owned-phase"]
    child = roots["cpu"] / cgroup[1:] / "buildkit/abc"
    child.mkdir(parents=True)
    (child / "cgroup.procs").write_text("123")
    calls = []
    monkeypatch.setattr(infra, "stop_owned_unit", lambda name: calls.append(name))
    with pytest.raises(RuntimeError, match="retained"):
        b.stop()
    assert calls == ["owned-phase", "owned-daemon"]


def test_live_resource_canary_failure_stops_before_candidate(
    frozen, tmp_path, monkeypatch
):
    root, module = frozen
    out = tmp_path / "output"
    out.mkdir()
    b = module.Builder(root, tmp_path / "base.tar", out)
    calls = []

    def phase(label, args, **kwargs):
        calls.append(label)
        if label == "resource-canary":
            raise RuntimeError("No real RUN inside limits")
        return b'{"audited_oci_base":"PASS", "manifest_digest":"sha256:fixture"}'

    monkeypatch.setattr(b, "phase", phase)
    with pytest.raises(RuntimeError, match="No real RUN"):
        b.build()
    assert calls == ["stage-gateway", "resource-canary"]


def test_check_only_does_not_create_output_lock_or_service(
    frozen, tmp_path, monkeypatch, capsys
):
    from types import SimpleNamespace

    root, module = frozen
    output = tmp_path / "new-output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-candidate.py",
            "--output",
            str(output),
            "--base-archive",
            str(tmp_path / "missing.tar"),
            "--check-only",
        ],
    )
    monkeypatch.setattr(module, "HERE", root)
    monkeypatch.setattr(module, "opt_path", lambda p: p)
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(module.socket, "gethostname", lambda: "datax-01")
    monkeypatch.setattr(module.os, "name", "posix")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda n: "/usr/bin/" + n)
    original_stat = module.os.stat

    def fake_stat(p, *a, **kw):
        if str(p) in ("/", "/opt"):
            return SimpleNamespace(st_dev=1 if str(p) == "/" else 2)
        return original_stat(p, *a, **kw)

    monkeypatch.setattr(module.os, "stat", fake_stat)
    original_read = Path.read_text

    def fake_read(p, *a, **kw):
        if p.as_posix() == "/proc/meminfo":
            return "MemAvailable: 33554432 kB\n"
        return original_read(p, *a, **kw)

    monkeypatch.setattr(Path, "read_text", fake_read)
    # Avoid pathlib choosing PosixPath on Windows after patching os.name.
    monkeypatch.setattr(module, "Path", type(tmp_path))
    calls = []

    def only_ps(argv, *a, **kw):
        assert argv == ["ps", "-eo", "comm=,args="]
        calls.append("ps")
        return ""

    monkeypatch.setattr(module, "run", only_ps)
    monkeypatch.setattr(
        module.Builder, "guard", lambda self, **kw: calls.append("readonly_guard")
    )
    monkeypatch.setattr(
        module.os,
        "open",
        lambda *a, **kw: pytest.fail("check-only may not create files"),
    )
    module.main()
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert not result["ready_for_build"] and not result["base_archive_present"]
    assert not result["ready_for_rollout"]
    assert calls == ["ps", "readonly_guard"] and not output.exists()
