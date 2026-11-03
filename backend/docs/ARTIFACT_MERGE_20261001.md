# Artifact reconciliation in the isolated enterprise merge

Scope: unfinished merge branch `fix/upload_test_env-enterprise-merge-20260930`,
HEAD `33688ae5`, incoming `523f1f0f`, after the backend batch-two repair.
No final merge commit or deployment is performed by this patch.

## Findings and changes

The next full-suite diagnostic stopped after 20 failures, with 300 passes, in
36.83 seconds. All 20 failures were in the artifact router test module. This
did not reproduce or identify the earlier wait near 8%.

The incoming artifact router overwrote the test environment's existing XML
active-content protection. Restore `text/xml`, `application/xml`, `text/xsl`,
and arbitrary `+xml` subtype detection in both ordinary-file and Skill-archive
delivery. Normalize case, whitespace, and MIME parameters before checking.
Active artifacts remain hardened attachments; tests are not removed or skipped.

The two Live Preview failures were stale test fixtures: one tried to load
`config.yaml` after the incoming router added server-owned AIO credentials;
the other asserted the former two-argument Runner forwarding call. Supply an
explicit fake key, assert direct transport and server-owned routing headers,
and include the exact port in the forwarding assertion. Add fail-closed tests
for a missing server key and browser attempts to supply credentials/port headers.
Do not weaken the preview runtime to avoid test configuration.

The preview port/runtime-profile allowlist, owner checks, direct transport,
CSRF injection, cookie stripping, size limits, Runner forwarding, PDF snapshot
validation/rendering, and streamed audio/video behavior remain unchanged.
Every pre-merge test remains present, all original artifact security assertions
remain intact, and the incoming media-streaming regression remains unchanged.

## Verification

Local dependency-light tests exercise the actual extracted router functions
with synthetic transport/dependency boundaries, not the full ASGI framework.
Patch verification checks pre-image/index hashes, backups, protected-file drift,
and forward/reverse application in a disposable Git fixture. Run the complete
artifact tests plus blocking-I/O/file-security regressions in the server's
dependency-complete environment, then continue full-suite diagnostics. Passing
this batch alone does not authorize final merge/deployment.
