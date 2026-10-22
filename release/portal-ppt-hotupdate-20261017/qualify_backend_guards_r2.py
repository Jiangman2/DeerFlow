"""One bounded local POSIX regression run for the reviewed inert placeholder guard."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time
import uuid

MERGE = Path(r"C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017")
OUT = MERGE / "backend-guards-r2"
IMAGE = "sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630"
EXPECTED_APPLY = "5762b486147cb4e434f873f005b746a3a6cd58ca26b967039478fd6e1ff1a613"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert sha(OUT / "apply_overlay.py") == EXPECTED_APPLY
    helper = MERGE / "frontend-build-r1/build_frontend.py"
    assert sha(helper) == "b2863f160268c41f1f9cf055455c1200c1991b578d95b9b7f1aaa6e3e5ced801"
    spec = importlib.util.spec_from_file_location("reviewed_build", helper)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    resources = build.host_preflight(build.helper("build_pool", build.POOL_HELPER_SHA))
    owner = uuid.uuid4().hex
    name = "portal-guard-r2-" + owner[:12]
    command = ["docker", "run", "--rm", "--name", name, "--label", "portal-guard-owner=" + owner,
               "--pull=never", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--cpus", "1", "--memory", "1g",
               "--memory-swap", "1g", "--pids-limit", "256", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
               "--mount", "type=bind,src=" + str(MERGE) + ",dst=/work,readonly",
               "--entrypoint", "/app/backend/.venv/bin/python", IMAGE, "-I", "-B", "-c",
               "import sys,unittest;sys.path.insert(0,'/work/backend-guards-r2');"
               "suite=unittest.defaultTestLoader.loadTestsFromName('test_inert_migration_guard');"
               "r=unittest.TextTestRunner(verbosity=2).run(suite);sys.exit(not r.wasSuccessful())"]
    report = {"schema": "portal-ppt-backend-guards-posix-qualification.v1", "command": command,
              "resources": resources, "inputs": {p.name: sha(p) for p in OUT.glob("*.py")},
              "status": "INCOMPLETE", "production_access": False, "network": "none",
              "image_build": False, "ready_for_rollout": False}
    destination = OUT / "INDEPENDENT-POSIX-QUALIFICATION.json"
    with (OUT / "INDEPENDENT-POSIX-STARTED.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    start = time.monotonic()
    try:
        process = subprocess.run(command, capture_output=True, timeout=180)
        report["exit_code"] = process.returncode
        stdout, stderr = process.stdout, process.stderr
    except subprocess.TimeoutExpired as exc:
        report["exit_code"] = 124
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
    finally:
        check = subprocess.run(["docker", "inspect", name, "--format",
                                '{{json .Name}} {{json (index .Config.Labels "portal-guard-owner")}}'],
                               capture_output=True, timeout=30)
        if check.returncode == 0:
            assert check.stdout.decode().strip() == json.dumps("/" + name) + " " + json.dumps(owner)
            cleanup = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            report["cleanup_complete"] = cleanup.returncode == 0
        else:
            report["cleanup_complete"] = check.returncode == 1 and b"No such" in check.stderr
    for label, raw in (("stdout", stdout), ("stderr", stderr)):
        assert len(raw) < 2 * 1024 * 1024
        with (OUT / ("INDEPENDENT-POSIX." + label + ".log")).open("xb") as stream:
            stream.write(raw)
        report[label + "_sha256"] = hashlib.sha256(raw).hexdigest()
    report["seconds"] = round(time.monotonic() - start, 3)
    report["inputs_stable"] = all(sha(OUT / name) == expected for name, expected in report["inputs"].items())
    if report["exit_code"] == 0 and report["cleanup_complete"] and report["inputs_stable"]:
        report["status"] = "PASS"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "exit_code": report["exit_code"], "report": str(destination)}))


if __name__ == "__main__":
    main()
