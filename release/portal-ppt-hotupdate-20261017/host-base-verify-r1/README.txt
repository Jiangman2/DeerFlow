PREPARED FOR REVIEW. Not executed against the server archive. No build or rollout approval.

Purpose
Verify only the existing /opt/deerflow-build-artifacts/ppt-runtime-repair-r2/build1/agent-host.candidate.oci.tar.
Archive SHA256 ea8bfd699a29032e49fa5127d8dcdd59ad635c34bdce1cfeef006461a4a0bc65.
Manifest 126fabc46a5d4d3b9d50924722bf10e7a1003a4d1adbdf72ace8ae25e188f97e.
Config 09ea44f66e1d47269b10e05f11f933f9f08897a1e5a55222a138f0a1b1acfaea.
Historical candidate-images-r2-build1.json records 128 layers and 5,337,237,974
compressed layer bytes. It does not record the complete tar size; no size was
invented. The complete tar is limited to 6 GiB and must match the exact SHA.

Interface for the reviewed wrapper
python3 -I -B verify_host_base.py --verify --out /var/lib/portal-ppt-host-base-verify-r1/host-base-<16 lowercase hex digits>
The actual CLI requires Linux, root, and hostname exactly datax-01. It accepts no
archive path, service target, dependency prefix, or exception override. --out is
only a fresh child of the fixed base above. New directories are 0700 and the
exclusive REPORT.json is 0600. Existing output is never reused or overwritten.
The wrapper must stage and hash-verify these four files in a root-private tool
directory: verify_host_base.py, EXPECTATIONS.json, frozen_gateway_replay.py,
frozen_export_verifier.py. The wrapper may export only the newly written safe
REPORT.json. No private source/config bytes or archive are exported by this tool.

Result semantics
Exit code 2 is intentional, including a completed DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED
report with an empty errors array. It means stop and review, never automatically
rerun. Any errors or status INCOMPLETE remain failures. Source mismatches are
retained individually and return nonzero. ready_for_build and ready_for_rollout
are always false. No image/binding/overlay artifact is written.

Evidence and scope
EXPECTATIONS.json pins the historical build receipt, R7 source receipt and frozen
host-source-r1 SOURCE-PREPARATION. The 12 prospective changes are checked against
four historical raw-file hashes and eight required physical absences. Limited
historical capture did not prove those absences. The unchanged capability probe
is a thirteenth exact target. CRLF-containing historical vendor files use RAW
R7 hashes; normalized patch hashes are not accepted as physical base hashes.

The exact archive digest is checked before parsing. All compressed blob digests,
OCI metadata closure/platform, and all ordered layer diffIDs (including tar tail
padding) are verified. Only fixed source/hash observations and their ancestors
are replayed; no rootfs is extracted. OCI whiteouts erase lower layers before
same-layer updates. Links/special files are never followed; a link at a source
target or ancestor prevents source precondition qualification.

Historical exceptional header
R7-RESULT recorded one unrelated backslash name with SHA256
8b5bbd065df580d90e27aead4de0e496bc6c8ffe18e5b77a820d76b89a19275c.
Its old reader did not record its layer digest or type. This tool grants no
exception. For this one name only, a regular non-sparse/non-whiteout entry may
be diagnosed without extraction, only after both literal POSIX and slash-
substitution interpretations are disjoint from every source/dependency scope
and no selected ancestor is affected. It records layer digest, original type,
raw tar-name hash, historical normalized-name hash, size and safety booleans;
the original name/content is withheld. A second/unknown invalid header or any
overlap/link/special type stops immediately. Even a complete diagnostic remains
REVIEW_REQUIRED, so local review can decide the exact layer/type handling later.

Runtime dependency limits
The historical build recipe/Host source establishes /usr/bin/python3, Python
3.10-compatible Host source, Codex command usage, /opt/ms-playwright and the
public upstream provenance/Skill requirements files. Existing R7 source capture
did not capture installed distribution or browser paths. The tool therefore
observes only fixed candidates under Python 3.10 dist/site-packages for Flask,
Werkzeug and Playwright (small exact module/metadata leaves), standard Codex
paths, specific Chromium/headless-shell binary layouts, and public Skill locks.
These are candidate path observations, not asserted installed versions. Hashes,
sizes and file/link kinds only are exported; .env provenance content, METADATA,
package JSON, browser JSON, command arguments and image Env are not exported.
Unknown layouts (including other Python versions or headless chrome-linux64)
remain unobserved; no path is dynamically expanded. Dependency links are not
resolved. The report does NOT prove import resolution, shared libraries,
functional browser/CLI startup, provider/auth state or full dependency closure.

Bounds and operations
Python 3.7+ standard library; 1800-second monotonic checks plus POSIX wall alarm.
Inherited frozen limits: 8 GiB expanded per layer, 24 GiB all ordered layers,
400,000 headers and 32 MiB name metadata per layer, 64 KiB PAX/longname record.
Source files max 4 MiB; dependency hash candidates max 512 MiB each; selected
hash bytes max 4 GiB overall; max 128 dependency paths/512 replay state entries.
No source, Secret, ConfigMap, provider, database, model or website calls. No
subprocess, image execution/import, rootfs extraction, workload writes or retry.
Only new local private report storage is written. The frozen imported helper
modules have unrelated Gateway entrypoints; none of them are called. Only
their pinned archive/digest/parser primitives are reused by the fixed Host
entrypoint, and Gateway migrations are explicitly absent from the Host scope.

Local qualification
18 microfixture tests: Windows 17 passed + 1 POSIX-only skip; Linux 18 passed.
Linux used the already installed QA image config 1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630,
1 CPU, 512 MiB RAM/no extra swap, 64 PIDs, network none, read-only image/source,
64 MiB tmpfs. It ran only these stdlib fixtures, not the Host image. The raw
logs and QUALIFICATION.json record exact sources and result boundaries.
Host's earlier 175 unique source cases remain separately qualified and were
not rerun. Actual archive verification and independent review remain pending.
