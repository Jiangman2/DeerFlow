"""Verify the exact new-chat model successor before projecting its parent.

This is committed-source provenance, not a deployment or business-call receipt.
"""
import hashlib
import json

ARCHIVE = "release/new-chat-model-source-20261105/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "7df54c7cf094ed6892fa10e376b7a189827de57b6d2e840402c3a8d0d60baebe"
BASE_COMMIT = "d63a01c9b293bad64d99055918ac3d16ffb973eb"
SOURCE_COMMIT = "459b44eae00e671466c90fc20462dee018b6a0c0"
RUNTIME = frozenset((
    "frontend/src/app/workspace/chats/[thread_id]/page.tsx",
    "frontend/src/components/workspace/chats/use-thread-chat.ts",
    "frontend/src/components/workspace/command-palette.tsx",
    "frontend/src/components/workspace/workspace-nav-chat-list.tsx",
    "frontend/src/core/settings/hooks.ts",
    "frontend/src/core/settings/store.ts",
))
TESTS = frozenset((
    "frontend/tests/e2e/new-chat-default-model.spec.ts",
    "frontend/tests/unit/core/settings/new-chat-model.dom.test.tsx",
))
GUIDE = "frontend/AGENTS.md"
PATHS = RUNTIME | TESTS | {GUIDE}


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    if isinstance(read, HistoricalReader):
        actual = qualify(read.original_read)
        assert actual == read.qualification, "NEW_CHAT_SOURCE_READER_CHANGED"
        return actual
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        for path in RUNTIME:
            try:
                read(ARCHIVE + "baseline/" + path)
            except (FileNotFoundError, KeyError):
                continue
            raise AssertionError("NEW_CHAT_SOURCE_MANIFEST_REQUIRED")
        return {}
    assert digest(raw) == MANIFEST_SHA256, "NEW_CHAT_SOURCE_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert set(manifest) == {
        "schema", "status", "base_commit", "source_commit",
        "production_acceptance_claimed", "runtime_modified_by_registration", "changes",
    }, "NEW_CHAT_SOURCE_SCHEMA"
    assert manifest["schema"] == "new-chat-model-source.v1", "NEW_CHAT_SOURCE_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT and manifest["source_commit"] == SOURCE_COMMIT, "NEW_CHAT_SOURCE_COMMITS"
    assert manifest["status"] == "COMMITTED_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE", "NEW_CHAT_SOURCE_STATUS"
    assert manifest["production_acceptance_claimed"] is False and manifest["runtime_modified_by_registration"] is False, "NEW_CHAT_SOURCE_OVERCLAIM"
    assert set(manifest["changes"]) == PATHS, "NEW_CHAT_SOURCE_SCOPE"
    for path, row in manifest["changes"].items():
        assert set(row) == {"kind", "current_sha256", "base_sha256", "baseline_path"}, "NEW_CHAT_SOURCE_ROW_SCHEMA"
        kind = "runtime-source" if path in RUNTIME else "regression-test" if path in TESTS else "module-guide"
        assert row["kind"] == kind, "NEW_CHAT_SOURCE_KIND"
        assert digest(read(path)) == row["current_sha256"], (path, "NEW_CHAT_SOURCE_CURRENT_CHANGED")
        if path in TESTS:
            assert row["baseline_path"] is None and row["base_sha256"] is None, "NEW_CHAT_SOURCE_NEW_TEST_PARENT"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + path, "NEW_CHAT_SOURCE_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], (path, "NEW_CHAT_SOURCE_BASELINE_CHANGED")
    return manifest


class HistoricalReader:
    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "NEW_CHAT_SOURCE_READER_CHANGED"
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
    return {"source_commit": SOURCE_COMMIT, "runtime_sources_verified": len(RUNTIME),
            "tests_verified": len(TESTS), "production_acceptance_claimed": False}
