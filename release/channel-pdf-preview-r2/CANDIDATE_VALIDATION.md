# R2 preloaded candidate validation (separate helper)

`validate-candidates.py` does not modify or rebuild the delivered R2 package. Run
it as root on datax-01 with `--run` after all four preload results pass. It uses
only Python standard library and kubectl; it installs no dependencies.

It creates one uniquely scoped NetworkPolicy and four sequential temporary Pods:
Gateway on ECS, Frontend on datax-01/02/03. Pods have no business volumes,
production Secret/ConfigMap references, service account token or production
Service labels. All images use `Never` pull policy. A policy requests deny-all
ingress/egress for only these Pods; CNI enforcement is not independently tested.
Each Pod is bounded at 500m CPU, 1Gi memory, 256Mi ephemeral writable storage and
900 seconds. Containerd image unpack is outside these Pod resource limits, so
first launch may still cause disk I/O. Execution is sequential, with node
Ready/pressure and production Deployment health checks.

Gateway runs the existing six PDF fixtures and provider display/connect text
assertions, not the application lifespan or a second channel worker. Frontend
verifies eight target source hashes, the supplied icon and build output, starts
Next with sanitized environment on loopback, and checks the icon and one real
built JavaScript resource over HTTP. This is not rendered-browser acceptance,
authenticated API validation, or a full production Gateway startup test.

Runtime image IDs must match the exact candidate manifest or config digest
recorded in the successful R2 build. Production Deployment UID/spec hashes are
checked before and after each test; no raw production env is saved. Logs, Pod
status and related events go to a unique root-only evidence directory under
`/opt/deerflow-hotfix-evidence/`. Only owned temporary objects are deleted, after
checking name, label and UID. Failure stops the sequence and never rolls out.
An abrupt SSH/session termination may prevent cleanup; report the evidence
directory and `cpdf-r2-...` names before removing any leftover test objects.

Local verification: unit tests cover manifests, native probe syntax, receipt and
image identity checks, service isolation, cleanup ownership, pressure guards,
credential-safe baseline snapshots and failure evidence. Native ARM64 execution
is performed by the user on the four nodes, not claimed by local unit tests.
