# Scheduled-run budget 300 → 10000

This operator targets the verified Gateway 414 deployment only. It copies the
existing configuration verbatim into a new immutable ConfigMap and replaces
only the scheduler's numeric recursion limit. The global maximum stays 10000.
The candidate configuration is exactly two bytes longer (300 → 10000). Revision
r3 refuses Gateway 415 or any drift from its fixed Gateway 414 source. Do not
execute the superseded 1000-budget r1 tool or the superseded r2 tool alongside this revision.

Revision r3 replaces only the aggregate Pod-list transport with a fixed four-column
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
