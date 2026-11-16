# November 16 scheduler queue and concurrency inspection

The operator showed two enabled tasks named `油气与金融市场日报`, one interval and
one repeated schedule, both with zero launches. Both manual triggers reportedly
remain queued. The operator selected a future global concurrency ceiling of 16.
The production value is unknown: 3 is only the repository default. The prior
10,000 graph-step budget is a separate setting.

This script reads one ready Gateway Pod owned by the expected Deployment UID.
It reads current scheduler configuration in an isolated Python process, validates
the live configuration model accepts 16 without changing that instance, and uses
one read-only repeatable-read PostgreSQL transaction with 4-second statement
limits. It returns active queue counts, at most 64 executing occurrence summaries,
and up to three occurrence summaries for each of the two exact same-owner enabled
title matches. If the name matches are ambiguous, no task-specific details are
read. No prompts, messages, webhook values, user IDs, task/thread IDs or raw errors
are exported. General executing rows use hashed identities and scalar metadata.

The tool distinguishes exhausted capacity, terminal runs retaining scheduler
slots, expired launch/run leases, overlapping thread activity and attempts that
were requeued. Expiry is an observation, not permission to clean up. Fresh config
is not an inspection of the live scheduler object's captured fields or poll task.
One Pod cannot prove all 70 pollers healthy. An inconclusive result is explicit.

Optional resource reads return the Provisioner's explicitly configured CPU,
memory and node-pool values, and node regular-container request projections.
They do not establish actual usage or Kubernetes scheduler admission capacity;
init containers, restartable sidecars, Pod-level resources and overhead are not
fully reconciled. Missing resource keys are not replaced with assumed defaults.

No Secret API reads, application startup, provider requests, task dispatch/retry,
database changes, workload changes or Work messages are supported. The script
does not update concurrency; that requires the operator's current configuration
and queue observation before preparing the separately authorized 16-limit change.

14 offline privacy/scope/evidence tests pass on Windows and Linux/Python3.10.
Python3.7 sealed-entry and wrong-host guards pass. These do not replace production
observation. The host entry is restricted to root on datax-01; no installer or
archived activation command should be rerun for this inspection.

```bash
sudo python3 -I -B -u /home/cnooc/inspect-scheduler-queue-20261116-r1.py --inspect-readonly
```
