# Scheduler concurrency 32 and lease-aware multi-instance mode

At 2026-11-16 09:21:55 Asia/Shanghai, the operator's read-only observation shows
Gateway 418 ready on all 70 replicas, a scheduler limit of 3, 3 executing
occurrences with live durable run leases, and 86 queued occurrences. Both named
daily tasks have zero launch attempts, no linked durable run, and no older or
active same-thread conflict. The interval task's next time is 10:05 and has not
passed; the cron's 09:00 occurrence is already durably queued. Neither date
display alone establishes that polling stopped. This is a historical snapshot,
not a claim that today's live queue remains at 86.

The operator first chose 16, then explicitly changed the final target to 32.
No 16-concurrency production rollout was submitted. This release changes only
`scheduler.max_concurrent_runs: 3 -> 32` and `scheduler.multi_instance: false ->
true` (inserting the latter if the previous false value is an omitted default).
Both step budgets remain 10000. All other YAML data and source/extension/model/
CA/secret-reference/volume/strategy fields are preserved.

The mode change is required by the observed 70-Pod topology: the field-matching
scheduler's single-instance startup calls the global restart sweep, which may
mark peer-owned running occurrences interrupted. The existing multi-instance
branch uses durable run lease reconciliation instead. The new qualifier
requires existing run-ownership heartbeats to be enabled; it does not turn them
on implicitly. Mode and startup behavior are verified against the exact observed
service source. Ordinary process shutdown can still interrupt work owned by a
Pod being replaced; this is not graceful draining or proof that every active
task will survive. The operator previously authorized cutover without waiting
for idle tasks.

The operator is derived from the field-qualified scheduler-budget r5 tool. It
binds Gateway UID/generation 418/spec hash, the current 15992-byte immutable
budget ConfigMap, and 17 current source hashes. It backs up the original
Deployment/ConfigMaps in a private root-owned release directory, creates one
immutable config clone, and issues one reference-only JSON Patch guarded by
UID, resourceVersion, generation and the complete original spec. No old map,
Skill, Nginx, Frontend, Provisioner, task or schedule is edited. No Secret value
is read, provider request made, or task/notification replayed by the operator.
Normal application startup and enabled background schedules may access their
normal database, Redis, providers and Work delivery.

The activation command returns promptly after patch submission. It does not
wait for the 70-Pod rolling update or claim final acceptance. The same tool's
read-only mode loads the saved plan/journal, reports progress, and after
convergence checks all 70 Pod source/config identities and Service endpoints.
It never reissues the activation patch or changes saved receipts. An interrupted
terminal is therefore not a request to rerun activation. No automatic retry or
rollback is provided.

```bash
sudo python3 -I -B -u /home/cnooc/activate-scheduler-concurrency-20261116-r2.py --activate-concurrency-once
sudo python3 -I -B -u /home/cnooc/activate-scheduler-concurrency-20261116-r2.py --verify-readonly
```

Validation: 52 Windows tests and the same 52 tests in the cached field-identical
ARM64 image. The complete qualifier and production side-effect audit pass
qualify/verify modes in that image with synthetic config and a local source
inventory; those fixtures do not substitute for actual production overlay
identity, which the operator independently enforces before and after rollout.
With the field-matching SQL repository, both local SQLite and isolated offline
PostgreSQL races preserve three existing executions and admit exactly 29 of 40
concurrent claimants, reaching 32 globally with 11 still queued. This verifies
budget fencing, not model/provider throughput or future business success.
Python 3.7 sealed entry and wrong-host guards are checked separately. Full
repository `make test` is unavailable in this Windows workspace.

The resource projection covers ten sandbox-pool nodes. It sums only regular
container requests and does not establish real usage or complete Kubernetes
scheduler capacity (init/restartable-sidecar/Pod-level/overhead resources are
not fully reconciled). No unrelated work or warm sandboxes are deleted to make
room. Current queued occurrences follow their existing timeout and execution
policies; the release does not promise immediate launch of all 86.

The unshipped r1 package is preserved. Use r2, which also supports a confirmed
three-slot limit omitted from YAML and supplied by the existing schema default.
Neither unshipped package has modified production.
