# Exact-chat explicit webhook diagnosis

This inspector is scoped to operator-supplied chat
`cd2ca7b0-139c-4576-ab5f-c67e9837db10`. It does not replay a task, send a
message, instantiate a Sandbox, or update Kubernetes/application resources.
Do not substitute an older fixed-chat inspector.

The sealed transport retains the previous read-only Kubernetes command
allowlist, validates ready Gateway Pod ownership, and preserves the durable
observation if the concurrent Gateway rollout changes its sample. It does
not establish completion of the concurrency rollout.

The child retains bounded, parameter-bound, repeatable-read execution and
native Work notification observations. Explicit group webhook delivery is
separate from native Work private-chat binding. New controlled `WEBHOOK_*`
status/error codes are retained, and paired tool results are distinguished
from model proposals. A partial event window cannot prove absence of a send.

Additional observations compare hashes/policy flags for the bounded original
run input and current schedule prompt. The latter is labeled as a current
snapshot, not evidence of what an earlier run originally received. Only a
single literal workspace established by owning commands can select project
files. The user-data root is derived from the trusted `threads_meta.user_id`
record, never from a browser or model-supplied owner path.

Reads are limited to the named project RESULT, private request JSON, bounded
sender journals, and the four accepted published output copies. Symlinks,
hardlinks, oversized files and ambiguous projects are rejected. A missing
project or recycled Sandbox is reported without creating one. Webhook token
strings are examined in memory solely for hash/policy comparison and are
not exported. Raw report/news bodies, prompt text, commands and private paths
are not returned. No Secret API is queried. Configuration may resolve the
configured database credentials in the isolated read-only process.

Run the standalone sealed tool once on datax-01:

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-webhook-thread-20261116-r2.py --inspect-readonly
```

Return its JSON. It contains no send/retry mode. Production findings remain
pending until that operator result is available.
