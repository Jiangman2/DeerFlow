"""Qualify the one-file Frontend 242 successor before historical projection.

This offline registration binds source to the preserved build evidence. Production
and browser acceptance remain separate operator records, not a new observation.
"""
import hashlib
import json

ARCHIVE = "release/sidechat-frontend-source-20261106/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "d9424ca0fe008a0a29b54877820aea1d9717a76c130c3eed7084563d7d4a9a08"
BASE_COMMIT = "b95b481f95380065e688d97b995b3603f8adf68a"
RUNTIME = "frontend/src/components/workspace/sidecar/sidecar-panel.tsx"
TEST = "frontend/tests/e2e/sidecar-composer-layout.spec.ts"
PATHS = frozenset((RUNTIME, TEST))
IMAGE = "sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3"
BASE_IMAGE = "sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d"
BUILD_ARCHIVE = "release/sidechat-skillhub-security-20261105-r1/"
EVIDENCE_PATHS = tuple(BUILD_ARCHIVE + name for name in (
    "frontend-SOURCE-CHANGE.json", "frontend-SOURCE.json", "frontend-RESULT.json",
))


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        actual = qualify(read.original_read)
        assert actual == read.qualification, "SIDECHAT_SOURCE_READER_CHANGED"
        return actual
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        try:
            read(ARCHIVE + "baseline/" + RUNTIME)
        except (FileNotFoundError, KeyError):
            return {}
        raise AssertionError("SIDECHAT_SOURCE_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "SIDECHAT_SOURCE_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {
        "schema", "status", "base_commit", "runtime_modified_by_registration",
        "fresh_production_acceptance_claimed", "frontend_image", "changes",
        "build_evidence_sha256_lf",
    }, "SIDECHAT_SOURCE_SCHEMA"
    assert manifest["schema"] == "sidechat-frontend-source.v1", "SIDECHAT_SOURCE_SCHEMA"
    assert manifest["status"] == "SOURCE_REGISTRATION_NOT_FRESH_DEPLOYMENT_ACCEPTANCE", "SIDECHAT_SOURCE_STATUS"
    assert manifest["base_commit"] == BASE_COMMIT, "SIDECHAT_SOURCE_PARENT"
    assert manifest["runtime_modified_by_registration"] is False and manifest["fresh_production_acceptance_claimed"] is False, "SIDECHAT_SOURCE_OVERCLAIM"
    assert manifest["frontend_image"] == IMAGE, "SIDECHAT_SOURCE_IMAGE"
    assert set(manifest["changes"]) == PATHS, "SIDECHAT_SOURCE_SCOPE"
    for path, row in manifest["changes"].items():
        assert set(row) == {"kind", "base_sha256", "baseline_path", "current_sha256"}, "SIDECHAT_SOURCE_ROW_SCHEMA"
        assert row["kind"] == ("runtime-source" if path == RUNTIME else "regression-test"), "SIDECHAT_SOURCE_KIND"
        assert digest(read(path)) == row["current_sha256"], (path, "SIDECHAT_SOURCE_CURRENT_CHANGED")
        if path == TEST:
            assert row["baseline_path"] is None and row["base_sha256"] is None, "SIDECHAT_SOURCE_NEW_TEST_PARENT"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + RUNTIME, "SIDECHAT_SOURCE_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], "SIDECHAT_SOURCE_BASELINE_CHANGED"
    assert set(manifest["build_evidence_sha256_lf"]) == set(EVIDENCE_PATHS), "SIDECHAT_SOURCE_EVIDENCE_SCOPE"
    for path, expected in manifest["build_evidence_sha256_lf"].items():
        assert digest(read(path)) == expected, (path, "SIDECHAT_SOURCE_BUILD_EVIDENCE_CHANGED")
    delta, inventory, result = (json.loads(read(path)) for path in EVIDENCE_PATHS)
    runtime = manifest["changes"][RUNTIME]
    assert delta["schema"] == "portal-sidechat-source-delta.v1" and delta["base_image"] == BASE_IMAGE, "SIDECHAT_SOURCE_BUILD_BASE"
    assert delta["changed"] == [{"path": RUNTIME, "before_sha256": runtime["base_sha256"], "after_sha256": runtime["current_sha256"]}], "SIDECHAT_SOURCE_BUILD_DELTA"
    assert delta["preserved_files"] == 981 and delta["production_writes"] == 0, "SIDECHAT_SOURCE_BUILD_SCOPE"
    assert len(inventory) == 982 and len({row["path"] for row in inventory}) == 982, "SIDECHAT_SOURCE_BUILD_INVENTORY"
    assert next(row["sha256"] for row in inventory if row["path"] == RUNTIME) == runtime["current_sha256"], "SIDECHAT_SOURCE_BUILD_BINDING"
    assert result["git_commit"] == BASE_COMMIT and result["image"]["id"] == IMAGE, "SIDECHAT_SOURCE_BUILD_RESULT"
    assert result["source"]["files"] == 982 and result["platform"] == "linux/arm64", "SIDECHAT_SOURCE_BUILD_RESULT"
    return manifest


class HistoricalReader:
    """Only the verified component file is replaced; the new test did not exist."""

    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "SIDECHAT_SOURCE_READER_CHANGED"
        self.original_read = read
        self.qualification = verified

    def __call__(self, path):
        row = self.qualification["changes"].get(path)
        if row is None:
            return self.original_read(path)
        if row["baseline_path"] is None:
            raise KeyError(path)
        return self.original_read(row["baseline_path"])


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    qualification = qualify(read)
    return HistoricalReader(read, qualification) if qualification else read


def summary(qualification):
    if not qualification:
        return None
    return {"status": qualification["status"], "runtime_sources_verified": 1,
            "tests_verified": 1, "frontend_image": IMAGE,
            "fresh_production_acceptance_claimed": False}
