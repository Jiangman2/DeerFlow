# SkillHub registry authentication remediation — 2026-11-06

At the delivery recorded in `ACTIVATION-DELIVERY.json`, the candidate image was
preloaded successfully on datax-01 and the reviewed server activation tool was
provided to the operator. **Its production activation result has not yet been
received.** Neither this directory nor the local checks claim that production
SkillHub authentication or the Nginx browser entry has been accepted.

## Exact production program preserved

The starting OCI image and metadata were exported from the running SkillHub
server, then checked locally. The candidate changes exactly two entries in that
original installed JAR:

- `compat/ClawHubRegistrySecurityConfig.class`
- `integration/deerflow/DeerFlowRouteSecurityPolicyRegistry.class`

`JAR-PATCH.json` records the original and candidate hashes, 788 preserved entries,
201 unchanged dependency JARs, and 46 unchanged migration entries. The candidate
JAR SHA256 is `342d0e02914d790a1c01a845cce4047af8b634fd34ecef0b01a66d5425fe7e3f`.
The image retains all 14 baseline layers and the original runtime configuration;
one new layer replaces `/app/app.jar`. Its ARM64 manifest is
`sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68`.
See `COMPILED-SOURCE-BINDING.json`, `JAR-EXTRACTION.json`, and
`IMAGE-QUALIFICATION.json` for the source, binary, and image evidence.

## What was actually tested

`runtime/REPORT-native-r3.json` records **56 passing assertions**: 54 real HTTP
status checks and two synthetic ZIP byte-identity checks. The exact unchanged and
patched installed JARs ran on native Java 21 with disposable PostgreSQL and Redis,
synthetic users/tokens/content, no production credentials, and no host-published
ports. The unchanged JAR reproduced anonymous registry reads. The patched JAR
rejected anonymous and fabricated/revoked credentials on the tested web, CLI and
compatibility registry routes, while retaining health/login discovery, valid
session and bearer reads/downloads, token scopes, role checks, and CSRF controls.
The two downloads were synthetic fixtures, not production business acceptance.

These were **exact-JAR native Java tests**, not successful full ARM64 image
startup tests. `CLASS-LINK-QUALIFICATION.json` separately records successful
candidate class linking on ARM64 without application/Spring initialization;
its optional baseline comparison timed out. The exact ARM64 candidate also ran
the JAR hash command as UID 100/GID 101 with a read-only filesystem, no network,
and all capabilities dropped. Probe utilities were checked without starting the
application. See `ACTIVATION-TOOL-QUALIFICATION.json`. Earlier local startup or
harness failures remain archived under `runtime/`; they are not relabeled as
passed tests or as authentication failures.

## Production sequence and current boundary

1. **Preload accepted:** `PRELOAD-OPERATOR-SUMMARY.json` preserves the normalized
   operator stdout. The private full report was not downloaded; its fixed remote
   path, 62,575-byte length, SHA256
   `1d1e580d2c5846d5e9b16f6a449c782fcc93409ddd024d21d7a9fb7c1f2f144d`, and
   operation `796641e830e84b05bf098d1ba309300d` are bound into the activation tool
   and will be revalidated on the host. Preloading did not switch the service.
2. **Native server activation pending:** the tool permits one image-only CAS
   update of the fixed `skillhub-server` Deployment after fresh cache, node,
   baseline, and isolated hash-only Pod checks. Its existing single-replica
   `Recreate` strategy causes a brief SkillHub outage. Normal application startup
   uses the existing database and Redis; the tool requests no explicit SQL,
   repair, schema/configuration change, or automatic rollback. It preserves
   unrelated Deployment identities and verifies the current JAR, native
   anonymous/fabricated-bearer 401 responses, health 200, and service endpoints.
3. **Nginx edge update remains separate:** a later edge tool must bind a successful
   native server activation receipt before enabling any bearer bypass of the
   edge session check. Native token/session validation, scopes, and RBAC remain
   authoritative. Production browser/session acceptance is still pending.

`ACTIVATION-TOOL-QUALIFICATION.json` records 28 Windows passes (three POSIX-only
skips), 31 Linux passes, the Python 3.7 wrong-host refusal, and root/independent
review. `ACTIVATION-DELIVERY.json` seals the delivered 31,500-byte tool. It does not
supersede or modify the earlier preload `DELIVERY.json`.

## Evidence preservation

Build, export, preload, and earlier test records are chronological snapshots.
Their earlier pending flags describe the stage when each was written. Keep
original exports, receipts, failed-run records, and source inventories immutable;
record subsequent production results in new evidence files. Do not rerun an
already completed once-only production action from archived commands.

## Production native activation accepted — operator r2 receipt

The later operator attachment records successful r2 activation operation
`25f363cb9f254e00b9f9dd196b2ade97`. SkillHub server is generation 15, using the
candidate manifest and exact JAR identified above. Its current Pod is
`skillhub-server-54554d4cd8-n8xft` (UID
`d49ffe43-3f17-4147-b3b0-c2b13790be42`) on datax-01. The tool reports one image-only
CAS patch, stable endpoint/readback checks, preserved unrelated Deployments,
and removal of its own temporary image-check Pod. Neither pinned historical
Failed Pod was modified or deleted.

Production loopback checks returned health 200, anonymous labels/skills 401, and
fabricated-bearer labels/skills 401. This is native server runtime acceptance;
**the Nginx edge update and authenticated browser/business acceptance remain
pending.** It does not claim new production valid-token, role, or scope tests.
Normal application startup used its existing database/Redis configuration; no
explicit database queries, repair, or schema changes were requested by the tool.

`ACTIVATION-R2-OPERATOR-SUMMARY.json` records the original attachment SHA256
`d0ea773818bb8ed7c1f47ab61779b0d05ccf7b26421e0bd7369a58e4915abe82` and normalized
operator output. `ACTIVATION-R2-REPORT-RECONSTRUCTED.json` is reconstructed from
that output, not downloaded from the server: one Markdown image-reference wrapper
was removed, then the output-only report path/length/hash fields were removed and
the remaining JSON serialized with the tool's exact canonical format. The result
is exactly 7,130 bytes with SHA256
`29e159302c129098c28786ab4dd8267724d31ebe28e37f02293186b31d033c38`, matching the
operator's report seal. The edge controller must still independently re-read and
verify the actual fixed-path server receipt before making an edge change.

The earlier delivery and qualification files are unchanged chronological records.
The r1 preflight refusal and the operator-supplied three-Pod inventory are retained
separately; r2's narrow exception validates only the two observed Failed Pod
identities and their current non-running state/owner chain.
