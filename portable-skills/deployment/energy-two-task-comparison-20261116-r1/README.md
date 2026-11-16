# Two named November 16 scheduled report outcomes

The operator reports one successful and one unsuccessful scheduled task and
provides these exact chats, without assigning success by link order:

- `9214a4fc-4cff-4599-9855-ea0af1d1ee27`
- `945b81c9-225f-4a12-a033-19d13da92903`

The authenticated internal pages cannot be read through the available web
tool. This inspector uses the established root-on-datax-01 read-only route.
It compares each chat's bounded durable runs, scheduled occurrences and parent
metadata, first/last execution events plus explicit errors, primitive workflow
checkpoint summary, current notification formatting, owner-bound Work connection
metadata and outbox state. No success/failure cause is inferred before that
field observation.

Execution selection is at most two recent runs and a detailed timeline for
the latest run as of its repeatable-read snapshot. Event selection is the first
80 and last 80 selected-kind events plus the first 8 explicit errors, with row
and byte bounds and explicit incomplete-coverage markers. The latest checkpoint
is a thread snapshot, not an assertion that it belongs to a particular run.
Only primitive JSON/msgpack workflow state is decoded; pickle and application
object loading are prohibited.

The tool preserves receipt error codes/classes, stage/version/date/count facts
and four-artifact extensions. It distinguishes graph/recovery stops, news
shortage, browser failures, a global Gateway restart sweep and expired durable
run ownership. Tool proposals are never reported as proof that a command ran.
Raw model messages, hidden reasoning, commands, credentials, webhook URLs and
article/report bodies are not exported.

Notification inspection uses the same two field-qualified Gateway 418 formatter
sources (the subsequent concurrency release changes only configuration), with
AST extraction of exact pure functions. It performs no sender calls. A formatter
source mismatch leaves notification evidence incomplete while retaining the
execution diagnosis. It reads only scalar owner-bound target/binding presence
and outbox status; notification ciphertext is never selected or decrypted.
Formatting replay is not actual delivery evidence. Execution and notification
queries use separately labeled read-only snapshots.

The host selects the newest ready owned Gateway Pod and records deployment
replica progress and that Pod's fresh scheduler config. This is not an inspection
of the loaded scheduler object or all-Pod rollout acceptance. If a rollout changes
the Pod/deployment during the postcheck, durable database evidence is retained
and `stable_readback=false` is reported. Only seven fixed read/exec commands
are supported. The child process permits network connections solely to the
configured database, read-only transactions and bounded SELECT/SET LOCAL work.
No task is retried, no Pod created or deleted, no workload/config changed and no
provider or Work message sent.

30 execution/privacy/formatting/rollout-boundary checks pass locally. The sealed
entry and child parse on Python3.7; the real entry guard is tested separately.
These synthetic checks do not establish the requested tasks' production cause.
The deployment state of the separate 32-concurrency release remains unconfirmed
until its own runtime receipt is supplied; this inspector never reactivates it.

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-two-results-20261116-r1.py --inspect-readonly
```
