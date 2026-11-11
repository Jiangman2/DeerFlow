"""Verify archived V4.1 preparation offline; never execute deployment tools."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "deploy/enterprise/v41-glm-20261029"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def record(name):
    return json.loads((ARCHIVE / name).read_text(encoding="utf-8"))


def load_packager(name):
    spec = importlib.util.spec_from_file_location(name, ARCHIVE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def constant(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Missing packaged constant: " + name)


def verify_adapter(provider, prepared):
    """Bind the old receipt to its baseline and current bytes to declared deltas."""
    path = "backend/packages/harness/deerflow/models/cnooc_deepseek_v41.py"
    lock_path = "deploy/enterprise/production-source-lock-20261029.json"
    read = lambda name: (ROOT / name).read_bytes()
    normalized_digest = lambda raw: digest(raw.replace(b"\r\n", b"\n"))
    lock_raw = read(lock_path)
    lock = json.loads(lock_raw)
    historical = lock["source_sets"]["gateway"][path]
    require(provider["installed_adapter_sha256"] == prepared["adapter_sha256"]
            == historical, "Historical adapter receipt changed")

    # Reuse the same parent/hash/scope checks as the full baseline verifier.
    # The October 29 receipt must not be rewritten to describe later code.
    spec = importlib.util.spec_from_file_location(
        "v41_source_chain", Path(__file__).with_name("verify-release-source-20261023.py")
    )
    source_chain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_chain)
    expected = historical
    candidate_path = ROOT / source_chain.CANDIDATE
    candidate_raw = candidate_path.read_bytes() if candidate_path.is_file() else None
    prior = {}
    if candidate_raw is not None:
        prior = source_chain.candidate_overrides(
            lock, json.loads(candidate_raw), normalized_digest(lock_raw)
        )
        expected = prior.get(path, expected)
    incident_path = ROOT / source_chain.INCIDENT_DELTA
    if incident_path.is_file():
        require(candidate_raw is not None, "Incident adapter parent missing")
        incident = source_chain.incident_overrides(
            lock, prior, json.loads(incident_path.read_bytes()),
            normalized_digest(lock_raw), normalized_digest(candidate_raw),
        )
        expected = incident["gateway"].get(path, expected)
    require(normalized_digest(read(path)) == expected, "Installed adapter changed")


def verify():
    inventory = record("ARCHIVE.json")
    require(len(inventory["files"]) == 24, "Historical payload count changed")
    for name, expected in inventory["files"].items():
        require(Path(name).name == name and "\\" not in name, "Invalid archive path")
        path = ARCHIVE / name
        require(path.is_file() and not path.is_symlink(), "Invalid payload: " + name)
        raw = path.read_bytes()
        require(len(raw) == expected["bytes"] and digest(raw) == expected["sha256"],
                "Historical bytes changed: " + name)

    for filename, field in (
        ("VERIFICATION.json", "sources"),
        ("VERIFICATION-r2.json", "sources"),
        ("VERIFICATION-enable.json", "source_file_sha256"),
        ("VERIFICATION-split.json", "new_source_sha256"),
    ):
        for name, expected in record(filename)[field].items():
            require(digest((ARCHIVE / name).read_bytes()) == expected,
                    "Delivery input changed: " + name)

    packages = {}
    for module_name, receipt_name, phases in (
        ("package", "VERIFICATION.json", (None,)),
        ("package_r2", "VERIFICATION-r2.json", (None,)),
        ("package_enable", "VERIFICATION-enable.json", (None,)),
        ("package_split", "VERIFICATION-split.json", ("prepare", "rollout")),
    ):
        packager = load_packager(module_name)
        receipt = record(receipt_name)
        for phase in phases:
            payload = packager.packaged(phase) if phase else packager.packaged()
            expected = receipt["deliveries"][phase] if phase else receipt["delivery"]
            require(len(payload) == expected["bytes"] and digest(payload) == expected["sha256"],
                    "Packaged delivery mismatch: " + expected["filename"])
            ast.parse(payload.decode("utf-8"), feature_version=(3, 7))
            packages[phase or module_name] = payload

    provider = record("provider-acceptance-field-summary.json")
    prepared = record("preparation-field-summary.json")
    require(provider["status"] == "V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED",
            "Invalid provider acceptance state")
    require(provider["provider_requests"] == 7 and len(set(provider["checks"])) == 7
            and provider["all_cases_http_status"] == 200 and provider["all_cases_status"] == "PASS",
            "Incomplete provider checks")
    verify_adapter(provider, prepared)
    require(prepared["status"] == "V41_CONFIG_PREPARED_NOT_ACTIVATED"
            and prepared["preparation_accepted"] is True
            and prepared["patch_request_count"] == 0, "Invalid preparation state")
    for name in ("gateway_rollout_started", "patch_may_have_applied", "v41_enabled",
                 "workload_modified", "image_changed", "default_model_changed"):
        require(prepared[name] is False, "Preparation misrepresented: " + name)
    require(prepared["deployment_bindings"]["gateway"]["generation"] == 389,
            "Preparation baseline changed")
    require(provider["credential_source"] == prepared["credential_source"] == "GLM_5_3_FLASH_API_KEY",
            "Credential reference changed")
    require(provider["server_report"]["sha256"] == prepared["provider_receipt_sha256"],
            "Provider receipt binding changed")
    for phase in ("prepare", "rollout"):
        source = packages[phase].decode("utf-8")
        require(constant(source, "MODE") == phase, "Packaged phase changed")
        require(constant(source, "BUNDLE_ID") == prepared["bundle_id"], "Prepared bundle changed")
    return packages


def stage_tests(directory, packages):
    # New disposable directory only; never replace an operator delivery.
    directory.mkdir(parents=False, exist_ok=False)
    for name in ("test_split.py", "test_enable_linux37.py", "enable_host.py"):
        shutil.copyfile(ARCHIVE / name, directory / name)
    for name, payload in (
        ("prepare.packaged.py", packages["prepare"]),
        ("rollout.packaged.py", packages["rollout"]),
        ("activation.packaged.py", packages["package_enable"]),
    ):
        with (directory / name).open("xb") as handle:
            handle.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-tests", type=Path,
                        help="Stage synthetic host tests in a NEW disposable directory")
    args = parser.parse_args()
    packages = verify()
    if args.stage_tests:
        stage_tests(args.stage_tests, packages)
    print(json.dumps({
        "status": "V41_PREPARATION_ARCHIVE_VERIFIED_NOT_ACTIVATED",
        "historical_payloads": 24,
        "reconstructed_deliveries": len(packages),
        "provider_requests": 0,
        "production_commands": 0,
        "gateway_rollout_started": False,
        "synthetic_test_directory": str(args.stage_tests) if args.stage_tests else None,
    }))


if __name__ == "__main__":
    main()
