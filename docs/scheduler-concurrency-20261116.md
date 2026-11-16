# November 16 scheduler queue and 32-concurrency candidate

The operator's read-only snapshot at 2026-11-16 09:21:55 Asia/Shanghai confirms
Gateway 418 with all 70 replicas ready, a configured scheduler concurrency of 3,
three executing scheduled occurrences with unexpired durable run leases, and
86 queued occurrences. The two enabled tasks named 油气与金融市场日报 are the
same owner's interval and cron tasks. Both have zero launch attempts, no linked
run, and no active or older occurrence on the same thread. The interval's next
time at 10:05 is still in the future; the cron's 09:00 occurrence has been queued.
The evidence supports saturation of scheduler capacity, not a Skill/render
failure or a proof that every poller has stopped. Queue size is a snapshot.

The operator first selected 16, then explicitly selected 32. No 16-capacity
rollout was submitted. The configuration candidate changes only
`scheduler.max_concurrent_runs` from 3 to 32 and `scheduler.multi_instance` from
false to true. Both execution step budgets remain 10000.

The second change addresses a concrete compatibility problem with 70 Gateway
Pods: the observed service source's single-instance startup uses the global
restart sweep. It can mark a peer-owned scheduled run interrupted even while
its durable run lease is alive. Existing multi-instance mode performs
lease-aware reconciliation instead. Source-level execution tests reproduce
both startup paths; the release qualifier additionally requires already-enabled
run-ownership heartbeats. No application/repository code changes are shipped.
This prevents that global startup sweep; it is not graceful draining of tasks
owned by a Pod being replaced. The operator previously allowed direct cutover
without waiting for idle tasks.

The candidate derives from the accepted November 13 r5 deployment framework.
It binds the fresh Gateway 418 identity/spec, the existing immutable budget
ConfigMap and 17 current source files. It preserves colleague overlays, CA and
Secret references, image, volumes and rolling strategy. The new immutable map
is a copy of the existing one, with only those two scheduler settings changed.
One UID/resourceVersion/generation/full-spec-guarded JSON Patch changes its
reference. Old maps, Skills, schedules, historical runs and receipts are kept.
The operator itself neither dispatches/replays jobs nor sends Work messages;
existing background schedules and ordinary application startup retain their
normal behavior.

Activation returns after submission rather than blocking the terminal for the
entire 70-Pod rollout. The separate `--verify-readonly` mode reports progress
and, after convergence, checks all 70 config/source identities and Service
endpoints from the saved plan/journal. It never resubmits activation. Closing
the terminal is not a reason to rerun the activation command.

The offline tests cover configuration scope, omitted defaults, YAML duplicates,
CAS identity fences, Pod admission defaults, source closure, exact endpoints,
exclusive backups, journal recovery, bounded subprocess transport and both
lease-aware startup branches. Local SQLite and isolated PostgreSQL runs using
the field-matching SQL repository retain three existing executions and admit
29 of 40 competing claims, resulting in exactly 32 executing rows and 11 queued.
This tests budget fencing, not provider capacity or the real queued tasks.

The resource read covers ten eligible sandbox nodes and only regular-container
requests. It is not an exact Kubernetes admission calculation or usage reading.
No unrelated Pods or warm sandboxes are deleted. Existing queue timeouts and
ordering remain in effect; increasing capacity does not promise all 86 entries
will start immediately or that a provider will handle 32 simultaneous jobs.

The unshipped r1 package is preserved. The final r2 package and field observation
are archived in `release/scheduler-concurrency-20261116-r2/`; the source and
reproducible checks are in
`portable-skills/deployment/scheduler-concurrency-20261116-r1/`. Production
activation and subsequent business acceptance are pending. Historical release
source locks, inventories and operator receipts are not rewritten.
