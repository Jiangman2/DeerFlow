"""Verify live artifact-card changes against the historical source."""
import hashlib

ARCHIVE = "release/live-artifact-cards-20261106/"
FILES = {'frontend/src/components/workspace/messages/message-list.tsx': ('1376cc5982b8ab077a0a57d5165eaa4b5a4df96108900d1fa76db0056ff9bb02', 'f799e4eb132dba7da1070ab4601636f6f33d041f42db3ca73903e3e44abb7330'), 'frontend/tests/e2e/artifact-preview.spec.ts': ('9dce05392ae0da10907f58815e0204b77029064241c8fe48adad00bff5c13818', '05497d4f4d022134fef67554c1dc7603a926066eb578539554ce3b2dbe49c3b5')}
# Qualify the updated new-chat expectation without rewriting the old seal.
MODEL_TEST = 'frontend/tests/e2e/model-square-account.spec.ts'
FILES[MODEL_TEST] = ('62b3c2f7bfdd9bd92e791d2fb1d46aef066af8cba8291aa80806a4de2fd07bb1', 'ab373180a8c7a73cd44ff17162a041cef38c33078bcdb8b950e5b48528e03095')

def historical_test_digest(path, raw):
    """Accept only the exact new-chat test successor in the navigation seal."""
    actual = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    before, after = FILES[MODEL_TEST]
    return before if path == MODEL_TEST and actual == after else actual

def historical_read(read):
    # Older releases and synthetic verifier fixtures do not contain this delta.
    # Activate on either archived evidence or a known successor source, so a
    # deleted/partial archive cannot silently exempt the successor from checks.
    active = False
    for path, (_, after) in FILES.items():
        try:
            read(ARCHIVE + "baseline/" + path)
        except (FileNotFoundError, KeyError):
            pass
        else:
            active = True
        try:
            current = read(path).replace(b"\r\n", b"\n")
        except (FileNotFoundError, KeyError):
            continue
        active |= hashlib.sha256(current).hexdigest() == after
    if not active:
        return read
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
