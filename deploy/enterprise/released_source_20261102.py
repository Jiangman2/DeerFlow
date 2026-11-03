"""Verify the additive November 2 released-source seal without production I/O."""

import ast
import hashlib
import json
from pathlib import PurePosixPath

ARCHIVE = "release/nov02-released-fixes-source-20261103/"
SOURCE = ARCHIVE + "SOURCE.json"
DELTA = "deploy/enterprise/released-source-delta-20261102.json"
PARENT_DELTA = "deploy/enterprise/released-source-delta-20261101.json"
BASE_COMMIT = "97bd146de0fb41264eb991c15f098d0e0de395db"
CI_ARCHIVE = "release/pr26-ci-qualification-20261103/"
CI_MANIFEST = CI_ARCHIVE + "SOURCE.json"
CI_MANIFEST_SHA256 = "08d28a9b6f9863f074d3138be1b8c21059efc0b922040a4712626d9e25115f1d"
CI_FOLLOWUP = CI_ARCHIVE + "FOLLOWUP.json"
CI_FOLLOWUP_SHA256 = "cafa45d3ad15f2f53cb5275c3769b213b879ef4c02f4672110661b3e8f9569f8"
FORBIDDEN = (
    "backend/app/gateway/auth/data_lake_work.py",
    "backend/packages/harness/deerflow/persistence/data_lake_auth/work_grant.py",
    "backend/packages/harness/deerflow/persistence/migrations/versions/0023_data_lake_work_grants.py",
)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def safe_path(path):
    value = PurePosixPath(path)
    assert not value.is_absolute() and ".." not in value.parts and "\\" not in path, "NOV02_UNSAFE_PATH"
    assert value.as_posix() == path, "NOV02_NONCANONICAL_PATH"


def ci_qualification(read):
    """Qualify later test/formatter corrections without changing release evidence."""
    try:
        raw = read(CI_MANIFEST)
    except (FileNotFoundError, KeyError):
        return {}
    assert digest(raw) == CI_MANIFEST_SHA256, "PR26_CI_SEAL_CHANGED"
    qualification = json.loads(raw)
    assert qualification["schema"] == "pr26-ci-qualification.v1", "PR26_CI_SCHEMA"
    assert qualification["base_commit"] == "5ec9fa2059f846d6b13646f64aa457d8eec10230", "PR26_CI_BASE_CHANGED"
    for path, row in qualification["changes"].items():
        safe_path(path)
        assert row["baseline_path"] == CI_ARCHIVE + "baseline/" + path, "PR26_CI_BASELINE_PATH"
        assert digest(read(row["baseline_path"])) == row["base_sha256"], "PR26_CI_BASELINE_CHANGED"
        if row["kind"] == "prettier_only":
            assert path in ("frontend/src/app/workspace/admin/dashboard/page.tsx", "frontend/src/styles/cnooc-concept.css"), "PR26_CI_FORMAT_SCOPE"
        else:
            assert row["kind"] == "test_correction" and path.startswith("frontend/tests/"), "PR26_CI_TEST_SCOPE"
    changes = qualification["changes"]
    try:
        followup_raw = read(CI_FOLLOWUP)
    except (FileNotFoundError, KeyError):
        followup_raw = None
    if followup_raw is not None:
        assert digest(followup_raw) == CI_FOLLOWUP_SHA256, "PR26_CI_FOLLOWUP_SEAL_CHANGED"
        followup = json.loads(followup_raw)
        assert followup["schema"] == "pr26-ci-test-followup.v1", "PR26_CI_FOLLOWUP_SCHEMA"
        assert followup["base_commit"] == "2d5fcb05d7175ec265caddbde6be616afce3c4f0", "PR26_CI_FOLLOWUP_BASE_CHANGED"
        assert followup["parent_manifest_sha256"] == CI_MANIFEST_SHA256, "PR26_CI_FOLLOWUP_PARENT_CHANGED"
        for path, row in followup["changes"].items():
            safe_path(path)
            assert row["kind"] == "test_correction" and path.startswith(("backend/tests/", "frontend/tests/")), "PR26_CI_FOLLOWUP_SCOPE"
            assert row["baseline_path"] == CI_ARCHIVE + "followup-baseline/" + path, "PR26_CI_FOLLOWUP_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], "PR26_CI_FOLLOWUP_BASELINE_CHANGED"
            if path in changes:
                assert row["base_sha256"] == changes[path]["current_sha256"], "PR26_CI_FOLLOWUP_CHAIN_CHANGED"
                changes[path] = {**changes[path], "current_sha256": row["current_sha256"]}
            else:
                changes[path] = row
    for path, row in changes.items():
        assert digest(read(path)) == row["current_sha256"], (path, "PR26_CI_CURRENT_CHANGED")
    return changes


def ci_baseline_read(read, qualification):
    return lambda path: read(qualification[path]["baseline_path"] if path in qualification else path)


def load(read, *, check_current=True):
    read = ci_baseline_read(read, ci_qualification(read))
    delta = json.loads(read(DELTA))
    assert delta["schema"] == "released-source-delta.v2", "NOV02_DELTA_SCHEMA"
    assert delta["work_datalake_deferred"] is True and delta["database_migration"] is False, "NOV02_DELTA_FEATURE_SCOPE"
    assert delta["base_commit"] == BASE_COMMIT, "NOV02_PARENT_COMMIT_CHANGED"
    assert delta["parent_delta"] == PARENT_DELTA, "NOV02_PARENT_PATH_CHANGED"
    assert digest(read(PARENT_DELTA)) == delta["parent_delta_sha256"], "NOV02_PARENT_DELTA_CHANGED"
    assert delta["source_manifest"] == SOURCE, "NOV02_SOURCE_PATH_CHANGED"
    assert digest(read(SOURCE)) == delta["source_manifest_sha256"], "NOV02_SOURCE_SEAL_CHANGED"
    manifest = json.loads(read(SOURCE))
    assert manifest["schema"] == "nov02-released-source.v1", "NOV02_SOURCE_SCHEMA"
    assert manifest["gateway_generation"] == 403 and manifest["frontend_generation"] == 239, "NOV02_RELEASE_GENERATIONS_CHANGED"
    assert manifest["base_commit"] == BASE_COMMIT, "NOV02_SOURCE_PARENT_CHANGED"
    assert manifest["work_datalake_deferred"] is True and manifest["schema_migration_requested"] is False, "NOV02_SCOPE_CHANGED"
    sources, tests = manifest["sources"], manifest["tests"]
    assert len(sources) == 41, "NOV02_SOURCE_COUNT_CHANGED"
    assert sum(name.startswith("backend/") for name in sources) == 22, "NOV02_BACKEND_SCOPE_CHANGED"
    assert sum(name.startswith("frontend/src/") for name in sources) == 19, "NOV02_FRONTEND_SCOPE_CHANGED"
    assert not set(sources) & set(FORBIDDEN), "NOV02_DEFERRED_SOURCE"
    for path, row in {**sources, **tests}.items():
        safe_path(path)
        if path in sources:
            assert path.startswith(("backend/app/", "backend/packages/harness/", "frontend/src/")), "NOV02_RUNTIME_PATH"
            assert row["published_source"] == ARCHIVE + "published/" + path, "NOV02_PUBLISHED_PATH"
            published = read(row["published_source"])
            assert hashlib.sha256(published).hexdigest() == row["deployed_sha256"], "NOV02_PUBLISHED_SOURCE_CHANGED"
            if check_current:
                current = read(path)
                if row.get("normalization"):
                    assert row["normalization"] == "python_layout_ast_equal" and path.endswith(".py"), "NOV02_NORMALIZATION_SCOPE"
                    assert ast.dump(ast.parse(published), include_attributes=False) == ast.dump(ast.parse(current), include_attributes=False), "NOV02_NORMALIZATION_LOGIC_CHANGED"
                else:
                    assert current.replace(b"\r\n", b"\n") == published.replace(b"\r\n", b"\n"), "NOV02_RUNTIME_LOGIC_CHANGED"
        else:
            assert path.startswith(("backend/tests/", "frontend/tests/")), "NOV02_TEST_SCOPE"
        if row["base_sha256"] is not None:
            assert row["parent_source"] == ARCHIVE + "parent/" + path, "NOV02_PARENT_SOURCE_PATH"
            assert digest(read(row["parent_source"])) == row["base_sha256"], "NOV02_PARENT_SOURCE_CHANGED"
        else:
            assert row.get("parent_source") is None, "NOV02_UNEXPECTED_PARENT_SOURCE"
        if check_current:
            assert digest(read(path)) == row["source_sha256"], (path, "NOV02_CURRENT_SOURCE_CHANGED")
    expected = {"gateway": {}, "frontend": {}}
    for path, row in sources.items():
        component = "gateway" if path.startswith("backend/") else "frontend"
        expected[component][path] = {"base_sha256": row["base_sha256"], "released_sha256": row["source_sha256"], "kind": "released-runtime"}
    for path, row in tests.items():
        if path.startswith("frontend/tests/"):
            expected["frontend"][path] = {"base_sha256": row["base_sha256"], "released_sha256": row["source_sha256"], "kind": "regression-test"}
    assert delta["changes"] == expected, "NOV02_DELTA_SCOPE_CHANGED"
    for path, expected_digest in manifest["evidence"].items():
        safe_path(path)
        assert path.startswith(ARCHIVE + "evidence/"), "NOV02_EVIDENCE_PATH"
        assert digest(read(path)) == expected_digest, (path, "NOV02_EVIDENCE_CHANGED")
    # Reconstruct the exact shipped backend overlay union from its three sealed packages.
    backend = {}
    for package in manifest["gateway_packages"]:
        assert package in manifest["evidence"], "NOV02_GATEWAY_PACKAGE_UNSEALED"
        for image_path, record in json.loads(read(package))["qualification"]["source_files"].items():
            assert image_path.startswith("app/backend/"), "NOV02_GATEWAY_SOURCE_PREFIX"
            backend[image_path.removeprefix("app/")] = record["sha256"]
    assert backend == {p: r["deployed_sha256"] for p, r in sources.items() if p.startswith("backend/")}, "NOV02_GATEWAY_PACKAGE_SOURCE_MISMATCH"
    inventory_path = manifest["frontend_inventory"]
    assert inventory_path in manifest["evidence"], "NOV02_FRONTEND_INVENTORY_UNSEALED"
    inventory = {row["path"]: row["sha256"] for row in json.loads(read(inventory_path))}
    assert len(inventory) == 979, "NOV02_FRONTEND_INVENTORY_SCOPE"
    for path, row in sources.items():
        if path.startswith("frontend/"):
            assert row["deployed_sha256"] == inventory[path], "NOV02_FRONTEND_SOURCE_MISMATCH"
    protected_paths = {
        "frontend/src/core/auth/AuthProvider.tsx",
        "frontend/src/core/data-lake/auth.ts",
        "frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx",
        "frontend/src/core/settings/store.ts",
    }
    assert set(manifest["protected_sources"]) == protected_paths, "NOV02_PROTECTED_SOURCE_SCOPE"
    for path, expected_digest in manifest["protected_sources"].items():
        assert inventory[path] == expected_digest, "NOV02_PROTECTED_SOURCE_INVENTORY_CHANGED"
        assert digest(read(path)) == expected_digest, (path, "NOV02_PROTECTED_SOURCE_CHANGED")
    receipt_path = ARCHIVE + "evidence/deploy/enterprise/usage-heatmap-frontend-runtime-accepted-field-20261102-r2.json"
    browser_path = ARCHIVE + "evidence/deploy/enterprise/monitor-http-heatmap-browser-acceptance-field-20261103.json"
    assert receipt_path in manifest["evidence"] and browser_path in manifest["evidence"], "NOV02_ACCEPTANCE_NOT_SEALED"
    activation = json.loads(read(receipt_path))["activation"]
    assert activation["status"] == "FRONTEND_USAGE_HEATMAP_RUNTIME_ACCEPTED_BROWSER_PENDING", "NOV02_RUNTIME_ACCEPTANCE_CHANGED"
    assert activation["after"]["frontend"]["generation"] == 239 and activation["gateway_accepted_binding"]["generation"] == 403, "NOV02_RUNTIME_GENERATIONS_CHANGED"
    assert activation["cache_manifest"] == "sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1", "NOV02_RUNTIME_IMAGE_CHANGED"
    assert activation["report_sha256"] == "e51535c28660d87ffb8ff563d62899cb2f85fa2a2acbe89c1e63e1bb06710c92", "NOV02_RUNTIME_RECEIPT_CHANGED"
    assert activation["gateway_completed_receipt"]["sha256"] == "032605e8607dbff5a0e066d95ca537fc8ec6450bc058f253afd973e6093cd234", "NOV02_GATEWAY_RECEIPT_CHANGED"
    assert activation["schema_migration_requested"] is False and activation["work_datalake_activated"] is False, "NOV02_RUNTIME_SCOPE_CHANGED"
    browser = json.loads(read(browser_path))
    assert browser["reply"] == "热力图和监控均正常" and browser["browser_acceptance_pending"] is False, "NOV02_BROWSER_ACCEPTANCE_CHANGED"
    assert browser["individual_http_status_independently_captured"] is False and browser["provider_request_tests_claimed"] is False and browser["oa_business_request_tests_claimed"] is False, "NOV02_EVIDENCE_SCOPE_OVERCLAIMED"
    return delta, manifest


def overrides(lock, prior, incident, released, read):
    delta, manifest = load(read)
    result = {"gateway": {}, "frontend": {}}
    for component, changes in delta["changes"].items():
        for name, change in changes.items():
            if component == "gateway":
                expected = prior.get(name, lock.get("format_normalizations", {}).get(name, {}).get("formatted_sha256", lock["source_sets"][component].get(name)))
            else:
                expected = lock.get("line_ending_normalizations", {}).get(name, {}).get("lf_sha256", lock.get("qualification_test_overrides", {}).get(name, {}).get("current_sha256", lock["source_sets"][component].get(name)))
            expected = released[component].get(name, incident[component].get(name, expected))
            # Historical source inventories omit some newer synthetic fixtures.
            if name in manifest["tests"] and expected is None:
                expected = manifest["tests"][name]["base_sha256"]
            assert change["base_sha256"] == expected, (name, "NOV02_PARENT_CHAIN_MISMATCH")
            result[component][name] = change["released_sha256"]
    for path, row in ci_qualification(read).items():
        if path.startswith("frontend/"):
            result["frontend"][path] = row["current_sha256"]
    return result


def historical_read(read):
    """Verify the new seal, then expose the immutable PR25 bytes to the older audit."""
    _, manifest = load(read)
    read = ci_baseline_read(read, ci_qualification(read))
    parents = {p: row["parent_source"] for p, row in {**manifest["sources"], **manifest["tests"]}.items() if row.get("parent_source")}
    return lambda path: read(parents.get(path, path))
