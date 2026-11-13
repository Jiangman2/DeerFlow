# Scheduled-run budget 300 → 10000

This operator targets the verified Gateway 415 deployment only. It copies the
existing configuration verbatim into a new immutable ConfigMap and replaces
only the scheduler's numeric recursion limit. The global maximum stays 10000.
The candidate configuration is exactly two bytes longer (300 → 10000). Revision
r5 refuses Gateway 416 or any drift from its fixed Gateway 415 source. Do not
execute the superseded r1, r2, r3 or r4 tools alongside this revision.

The November 13 baseline is Gateway 415 with its newly observed image and full
deployment-spec hash. The four core sources, six result-link overlays, two
upload overlays and OpenSSL file are pinned to that same fresh observation.
Reading a ConfigMap with the same historical name does not establish unchanged
content: all 13 derived file hashes must match before the budget patch.

Revision r5 recognizes only two observed Kubernetes admission projections while
checking each Pod. Declared init containers may retain their exact mount order
or append the same standard read-only ServiceAccount mount used by the main
container; their image, command, environment and other fields must match fully.
The single additional projected volume must contain exactly one token projection
at path `token`, and ServiceAccount automount must not be disabled. An already
occupied ServiceAccount mount path cannot receive a second mount.

For topology spread constraints, an explicitly declared `pod-template-hash`
matchLabelKeys entry may produce exactly one appended `In` selector expression.
Its value must match this Pod's label and the owning ReplicaSet's metadata,
template and selector labels. Existing selectors may not already contain that
key; all other constraint fields and expression order remain exact. This uses
each current Pod's labels and generated volume name, never an earlier Pod's
random identifiers. Other differences still fail closed. Failure reports may
include only the schema field and JSON path, never compared values or commands.

Revision r5 replaces only the aggregate Pod-list transport with a fixed four-column
stdout projection (name, UID, phase and deletion timestamp), bounded to 256 KiB
and 512 rows. Each selected Pod is still read in full and bound to its listed
UID; final fleet verification repeats full reads of all 70 Pods. This CLI
projection does not claim that the Kubernetes client receives a metadata-only
API response. Single-Pod output remains bounded to 4 MiB, qualification output
to 64 KiB and command stderr to 128 KiB. An output-limit refusal reports only
the fixed command kind, stream, limit and observed byte count.
The existing image, source overlays, CA references, 70 replicas and rollout
strategy remain in the full-spec compare-and-swap boundary.

The new limit applies to future scheduled runs across the platform. It does not
change already captured run configuration, replay a task, send a test Work
message or prove any report/business result. Existing background schedules can
continue normally. Starting replacement Gateways can connect to existing
database/Redis services and reconnect Work channels; ongoing tasks may be
interrupted during the existing rolling strategy.

Before a patch, an existing Gateway runs strict duplicate-key/alias-safe YAML
qualification, checks exact configuration bytes and hashes 13 fixed files:
four current core files, six result-link overlays, two upload overlays and the
OpenSSL compatibility file. It imports no application/model/provider modules.
Only the operator's private root-owned backup directory stores configuration
body content. No Secret API values, configuration body or raw errors are
printed.

After deployment convergence, the same qualification runs in all 70 current
Pods, with ownership, template, container identity and service endpoints checked
before acceptance. CA Secret content is deliberately not read; its deployment
references and mounts are preserved by the exact full-spec comparison.

Activation uses `--activate-scheduler-budget-once`. On interruption or an
uncertain submission, preserve its control directory and use the same sealed
script with `--verify-readonly`; this mode never creates, patches, waits for a
rollout, or changes its original journals. It reports ongoing rollouts directly.
No automatic retry or rollback is implemented.

Do not execute the unsealed source. Independent review precedes
`seal_scheduler_budget.py --reviewed`. The sealer creates a new release directory
and refuses to overwrite it. Archived release commands and receipts remain
unchanged.
