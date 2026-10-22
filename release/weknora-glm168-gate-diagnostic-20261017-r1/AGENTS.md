# Diagnose the failed168 review, never retry expansion

Bind the exact failed review RESULT SHA and immutable review/helper chain. Keep
the prior tool and evidence unchanged. This diagnostic must never call collect,
proposal, dry_run, apply, signal, restart, quarantine reset or a provider.

Report the historical memory estimate's actual operands, including container
peak and the old fixed limit, without changing that heuristic or treating
current cache as an explanation of historical peak. Distinguish cgroup total,
cache/anonymous counters, process RSS/HWM and kubelet working set; no sums across
incompatible scopes and no RSS-only safety approval.

The old fifth database gate's operands were not retained. Say NOT_RETAINED, not
that a new observation reconstructs the old failure. Use three short READ ONLY
SQL metadata snapshots with5s statement/1s lock timeouts. Show connection, idle
transaction and lock conditions separately. Do not export PID, query text,
arbitrary relation names, document data or credentials. Never kill blockers or
autovacuum, increase max_connections or change memory limits here.

Emit each diagnostic sample before assessing its gate. Return collection
completion separately from gate eligibility, always ready_to_expand=false.
Qualify the byte-exact delivery on Python3.7/3.12, isolated PostgreSQL16, and the
actual frozen helper loader without production calls. Preserve old exports.
