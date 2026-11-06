"""Verify live artifact-card changes against the historical source."""
import hashlib

ARCHIVE = "release/live-artifact-cards-20261106/"
FILES = {'frontend/src/components/workspace/messages/message-list.tsx': ('1376cc5982b8ab077a0a57d5165eaa4b5a4df96108900d1fa76db0056ff9bb02', 'f799e4eb132dba7da1070ab4601636f6f33d041f42db3ca73903e3e44abb7330'), 'frontend/tests/e2e/artifact-preview.spec.ts': ('9dce05392ae0da10907f58815e0204b77029064241c8fe48adad00bff5c13818', '05497d4f4d022134fef67554c1dc7603a926066eb578539554ce3b2dbe49c3b5')}
def historical_read(read):
    originals = {}
    for path, (before, after) in FILES.items():
        current = read(path).replace(b"\r\n", b"\n")
        if hashlib.sha256(current).hexdigest() == before:
            originals[path] = current
            continue
        assert hashlib.sha256(current).hexdigest() == after, (path, "LIVE_ARTIFACT_SOURCE_CHANGED")
        baseline = read(ARCHIVE + "baseline/" + path).replace(b"\r\n", b"\n")
        assert hashlib.sha256(baseline).hexdigest() == before, (path, "LIVE_ARTIFACT_BASELINE_CHANGED")
        originals[path] = baseline
    return lambda path: originals[path] if path in originals else read(path)
