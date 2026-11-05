# Current production state reconciliation

The operator requested that PR #13 archive the current deployed state, including
changes made by other operators after earlier hot updates. Historical rollout
receipts are provenance, not authority to restore an older configuration.

## Collect the current metadata

Copy `inspect-current-deployment-archive.py` to the control-plane host and run:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-portal-current-deployment-archive-20261021-r1.py --inspect-current-readonly
```

The delivered filename differs from the source filename only. The collector
uses Kubernetes GET operations in `deer-flow`, with a bounded process output,
query count and total duration. It does not run a container command, read Secret
data, call a provider, inspect business files, patch or restart any service.

The output records selected Deployment/StatefulSet/DaemonSet identities,
replicas and status, image references, actual running image IDs grouped by node,
resources, numeric capacity environment declarations, scheduling, mounts and
ConfigMap/Secret references. Literal environment values outside the numeric
capacity allowlist, command arguments and ConfigMap contents are withheld.
Referenced ConfigMaps are represented by content hashes and key names only.
Service selectors/ports/affinity and ingress host/spec identities are included.

Workload UID/generation/spec and ConfigMap UID/content are checked again at the
end. `CHANGED_DURING_COLLECTION` is a drift finding, not a baseline to apply.
This is not an atomic whole-cluster snapshot: Service, ingress and Pod data are
single observations. Status changes are not treated as workload spec changes.

## Reconcile before editing deployment defaults

1. Bind the returned current workload identity, generation and image digest to
   the PR baseline. Do not select a configuration simply because its local file
   is newer or because an old script says it once passed.
2. Compare the returned ConfigMap hashes against local rendered source content.
   A matching name is insufficient. For an unmatched entry, obtain a targeted,
   reviewed source/configuration projection; do not export whole Secrets or
   raw configuration that may contain credentials.
3. Match image-contained source and mounted patches to their source locks.
   An image digest alone identifies the artifact but does not recover its source.
   Keep an explicit gap if source is not available.
4. Reconcile values that are owned by databases or runtime settings separately.
   In particular, WeKnora worker environment declarations do not establish the
   effective model concurrency limit. Do not write database overrides merely
   to make them resemble a YAML template.
5. Commit the confirmed reproducible configuration, source/patches and sanitized
   provenance. Preserve historical receipts as history. Separate credentials,
   generated archives and mutable business state from source control.

The collector does not declare release readiness. No old mutator or deployment
bundle should be replayed as part of this archive task.

## Local verification

`test-current-deployment-archive.py` checks output privacy, capacity/image
projection, workload and ConfigMap drift, nested references, replacement UID
detection and rejection of Secret queries. All fixtures are synthetic.
The Python 3.7 syntax check is not a Python 3.7 runtime acceptance claim.

## Historical evidence already establishes drift

The primary workspace's
`release/weknora-xlsx-current-layout-review-20261019-r1/AGENTS.md` describes two
Apps with 24 GiB each and enrichment 32 each, plus one DocReader with 16 GiB.
Earlier switch/recovery scripts describe a single App with 34/36 GiB and other
concurrency values. These records demonstrate why the earlier layout cannot be
used as today's desired state. The new collector's returned observations are
still required; this historical note is not fresh server evidence.

The operator subsequently returned the 2026-10-21 16:46 observation. See
`current-production-observation-20261021.json` and
`../../docs/pr13-current-production-reconciliation-20261021.md`. In particular,
the live enrichment declaration is 64 per App, not the historical 32 above.
