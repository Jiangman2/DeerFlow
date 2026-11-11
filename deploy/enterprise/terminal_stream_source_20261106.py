"""Qualify the undeployed stream reconciliation delta without rewriting releases."""
import hashlib
import json

ARCHIVE = "release/terminal-stream-reconciliation-20261106/"
MANIFEST = ARCHIVE + "SOURCE.json"
CLIENT = "frontend/src/core/api/api-client.ts"
HELPER = "frontend/src/core/api/reconcile-run-stream.ts"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def historical_read(read):
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        # Old/synthetic source trees have neither the helper nor this archive.
        try:
            read(HELPER)
        except (FileNotFoundError, KeyError):
            return read
        raise AssertionError("TERMINAL_STREAM_MANIFEST_MISSING") from None
    manifest = json.loads(raw)
    assert manifest["status"] == "NOT_DEPLOYED"
    assert set(manifest["files"]) == {
        CLIENT, HELPER,
        "frontend/tests/unit/core/api/reconcile-run-stream.test.ts",
        "frontend/tests/e2e/terminal-stream-reconciliation.spec.ts",
    }, "TERMINAL_STREAM_SCOPE_CHANGED"
    originals = {}
    for path, row in manifest["files"].items():
        current = read(path)
        expected = row["sha256"]
        if path == CLIENT:
            baseline = read(ARCHIVE + "baseline/" + path)
            assert digest(baseline) == row["base_sha256"], "TERMINAL_STREAM_BASELINE_CHANGED"
            assert digest(current) in (expected, row["base_sha256"]), "TERMINAL_STREAM_SOURCE_CHANGED"
            originals[path] = baseline
        else:
            assert digest(current) == expected, (path, "TERMINAL_STREAM_SOURCE_CHANGED")
    return lambda path: originals[path] if path in originals else read(path)
