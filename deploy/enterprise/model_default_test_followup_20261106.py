"""Qualify a test-only V4.1 default correction before historical projection."""
import hashlib
import json

ARCHIVE = "release/model-default-test-followup-20261106/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "b559c88de46651197b29c0622878b509e3fd5225449fcabf317bebc059e42bc5"
BASE_COMMIT = "89b988706d2c6b1e91d7257d6b6540a5ad2fe5fd"
TEST = "frontend/tests/e2e/model-square-account.spec.ts"
BASELINE = ARCHIVE + "baseline/" + TEST


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        verified = qualify(read.original_read)
        assert verified == read.qualification, "MODEL_DEFAULT_TEST_READER_CHANGED"
        return verified
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        try:
            read(BASELINE)
        except (FileNotFoundError, KeyError):
            return {}
        raise AssertionError("MODEL_DEFAULT_TEST_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "MODEL_DEFAULT_TEST_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {
        "schema", "status", "base_commit", "runtime_modified",
        "fresh_production_acceptance_claimed", "reason", "changes",
    }, "MODEL_DEFAULT_TEST_SCHEMA"
    assert manifest["schema"] == "model-default-test-followup.v1", "MODEL_DEFAULT_TEST_SCHEMA"
    assert manifest["status"] == "TEST_ALIGNMENT_NOT_RUNTIME_CHANGE", "MODEL_DEFAULT_TEST_STATUS"
    assert manifest["base_commit"] == BASE_COMMIT, "MODEL_DEFAULT_TEST_PARENT"
    assert manifest["runtime_modified"] is False and manifest["fresh_production_acceptance_claimed"] is False, "MODEL_DEFAULT_TEST_OVERCLAIM"
    assert set(manifest["changes"]) == {TEST}, "MODEL_DEFAULT_TEST_SCOPE"
    row = manifest["changes"][TEST]
    assert set(row) == {"kind", "baseline_path", "base_sha256", "current_sha256"}, "MODEL_DEFAULT_TEST_ROW_SCHEMA"
    assert row["kind"] == "regression-test" and row["baseline_path"] == BASELINE, "MODEL_DEFAULT_TEST_SCOPE"
    assert digest(read(BASELINE)) == row["base_sha256"], "MODEL_DEFAULT_TEST_BASELINE_CHANGED"
    # Keep the historical verifier's current-source drift classification.
    assert digest(read(TEST)) == row["current_sha256"], (TEST, "DASHBOARD_NAVIGATION_CURRENT_CHANGED", "MODEL_DEFAULT_TEST_CURRENT_CHANGED")
    return manifest


class HistoricalReader:
    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "MODEL_DEFAULT_TEST_READER_CHANGED"
        self.original_read = read
        self.qualification = verified

    def __call__(self, path):
        return self.original_read(BASELINE if path == TEST else path)


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    qualification = qualify(read)
    return HistoricalReader(read, qualification) if qualification else read
