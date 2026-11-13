# Oil and gas daily browser fix, November 12

The ordinary sandbox executes as UID/GID1000. The usable ARM64 headless shell
was installed under root's private cache, while the ordinary user found only
the system Chromium that had failed during page creation. Code2.0.1 skips
inaccessible discovery paths and supports a separately sealed browser runtime.
It verifies and copies that non-secret dependency into a fresh private local
temporary directory, so neither root cache traversal nor executable NFS is
required. The root directory permissions are not changed.

The business acquisition, financial calculations, historical data and renderer
source bytes are unchanged. The old2.0.0 package remains immutable. Prepared
2.0.0 projects remain compatible; browser errors preserve acquired sources and
stop rather than inducing model-driven source edits or GPU parameter probing.

Production qualification r4 passed47tests as actual UID/GID1000 on the fixed
ARM64 image. The full780x5250PNG took3.1seconds and exactly matches the previously
viewed fixed sample SHA256bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b.
This is synthetic fixed-sample acceptance, not acceptance of a current-day
business report or Work notification. No credential/provider request occurred.

The first qualification stopped on a256MiBfile bound; the measured executable
is298,683,784bytes, so the bound is384MiBwhile total runtime remains512MiB.
The third qualification exposed a separate test-harness path mapping defect:
tests were placed away from their inferred Skill sibling. The exact2failures
and4errors were reproduced in Python3.10/UID1000; mapping the fixture root to
the actual candidate produced47/47passes without modifying business code.
All earlier tools, markers and server receipts are retained.

The installation tool backs up the current105-filef880tree, stages a107-file
candidate and the independent11-file runtime on the shared volume, then verifies
the actual read-only mounts and exact PNG again as UID1000 before publication.
It does not modify Gateway, Provisioner, Secrets, existing sandboxes, historical
reports, or schedules. Gateway414 may finish its already-started rollout in
parallel; final source and conversation-link acceptance uses postcheckr3,
which understands the browser-fix installation receipt.

The operator accepted publication at2026-11-12 01:36Beijing time. The new
107-file tree is6362fa76e30f4c0850a3a6d5f9e2c2e5fd74cf841235b279ea868f00b8d4122b.
The real shared-volume UID1000 check produced the exact accepted PNG in3.108s;
both maintenance Pod and ConfigMap were absent after cleanup. See
FIELD-INSTALL-ACCEPTANCE.json for the operator-summary provenance and raw-server
receipt reference. Current-day conversation/Work acceptance and the existing
Gateway414 fleet postcheck remain pending. No schedule was created. Do not
rerun a claimed activation/installation or delete markers to retry.
