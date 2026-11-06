"""Seal the six-file composer candidate before projecting its immutable parent.

No production acceptance is inferred from source registration or local tests.
"""
import hashlib
import json

ARCHIVE = "release/history-composer-autosize-source-20261106/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "f7d56bd203f490e0ca1abb3bc9890d76033205387e53a64bf7e793f8229b1bac"
BASE_COMMIT = "47a4fb82b2fae402c7fc95e6e9e3e93ad6187d50"
INPUT = "frontend/src/components/workspace/input-box.tsx"
CSS = "frontend/src/styles/cnooc-concept.css"
HOOK = "frontend/src/hooks/use-textarea-autosize.ts"
EXISTING_TEST = "frontend/tests/e2e/composer-submit-layout.spec.ts"
NEW_TEST = "frontend/tests/e2e/history-composer-autosize.spec.ts"
SIDECAR_TEST = "frontend/tests/e2e/sidecar-chat.spec.ts"
RUNTIME = frozenset((INPUT, CSS, HOOK))
EXISTING = frozenset((INPUT, CSS, EXISTING_TEST, SIDECAR_TEST))
NEW = frozenset((HOOK, NEW_TEST))
PATHS = EXISTING | NEW


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        actual = qualify(read.original_read)
        assert actual == read.qualification, "HISTORY_COMPOSER_READER_CHANGED"
        return actual
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        for path in (*NEW, *(ARCHIVE + "baseline/" + p for p in EXISTING)):
            try:
                read(path)
            except (FileNotFoundError, KeyError):
                continue
            raise AssertionError("HISTORY_COMPOSER_MANIFEST_REQUIRED")
        return {}
    assert digest(raw) == MANIFEST_SHA256, "HISTORY_COMPOSER_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {"schema", "status", "base_commit", "production_acceptance_claimed", "runtime_modified_by_registration", "changes"}, "HISTORY_COMPOSER_SCHEMA"
    assert manifest["schema"] == "history-composer-autosize-source.v1", "HISTORY_COMPOSER_SCHEMA"
    assert manifest["status"] == "NOT_DEPLOYED", "HISTORY_COMPOSER_STATUS"
    assert manifest["base_commit"] == BASE_COMMIT, "HISTORY_COMPOSER_PARENT"
    assert manifest["production_acceptance_claimed"] is False and manifest["runtime_modified_by_registration"] is False, "HISTORY_COMPOSER_OVERCLAIM"
    assert set(manifest["changes"]) == PATHS, "HISTORY_COMPOSER_SCOPE"
    for path, row in manifest["changes"].items():
        assert set(row) == {"kind", "base_sha256", "baseline_path", "current_sha256"}, "HISTORY_COMPOSER_ROW_SCHEMA"
        assert row["kind"] == ("runtime-source" if path in RUNTIME else "regression-test"), "HISTORY_COMPOSER_KIND"
        assert digest(read(path)) == row["current_sha256"], (path, "HISTORY_COMPOSER_CURRENT_CHANGED")
        if path in NEW:
            assert row["base_sha256"] is None and row["baseline_path"] is None, "HISTORY_COMPOSER_NEW_PARENT"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + path, "HISTORY_COMPOSER_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], (path, "HISTORY_COMPOSER_BASELINE_CHANGED")
    return manifest


class HistoricalReader:
    """Project the parent only after checking every candidate and baseline byte."""

    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "HISTORY_COMPOSER_READER_CHANGED"
        self.original_read = read
        self.qualification = verified

    def __call__(self, path):
        # These additions did not exist in the parent. Hiding the archive also
        # makes projection composable through older lambda-based readers: they
        # cannot requalify already-projected bytes as a fresh candidate.
        if path.startswith(ARCHIVE) or path in NEW:
            raise KeyError(path)
        row = self.qualification["changes"].get(path)
        return self.original_read(row["baseline_path"] if row else path)


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    qualification = qualify(read)
    return HistoricalReader(read, qualification) if qualification else read


def summary(qualification):
    if not qualification:
        return None
    return {"status": "NOT_DEPLOYED", "base_commit": BASE_COMMIT,
            "runtime_sources_verified": len(RUNTIME), "tests_verified": len(PATHS - RUNTIME),
            "frontend_runtime_paths": sorted(RUNTIME), "production_acceptance_claimed": False}
