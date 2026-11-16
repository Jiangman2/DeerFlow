# Read-only proof of the webhook file-permission failure

The operator's `log(35).txt` establishes two actual paired sender failures in
chat `cd2ca7b0-139c-4576-ab5f-c67e9837db10`: `WEBHOOK_LOCAL_FAILURE` with
`PermissionError`. The original input and saved request contain the same
supported target hash. Build returned 2.1.7 `REPORT_ACCEPTED`, four file types,
and the following `present_files` call returned success. The run ended normally;
the evidence does not show Gateway interruption. Native schedule occurrences
are absent for this ordinary chat and cannot substitute for explicit group send.

Those receipts do not identify the failing file operation, prove no network
request occurred, or explain the exact ownership. The r2 inspector found two
workspace literals across all owning commands and intentionally skipped file
inspection instead of guessing. This new immutable tool narrows project
selection to the observed `send_webhook.py` commands. Multiple distinct sender
projects still stop file reads. All other inherited bounds, trusted-owner
storage resolution, read-only SQL, audit restrictions, raw-text/URL redaction,
and rolling-Gateway observation preservation remain in force.

Only six named roles get mode/owner metadata. UID/GID 1000 access flags are
explicitly labeled as POSIX-mode comparisons, not ACL/effective access tests.
The tool never invokes the sender, chmod, a task retry or a network delivery.
It does not patch the Skill or establish concurrency-rollout completion.

The archived 2.1.7 sender can be reproduced offline in an isolated Linux root
container by `reproduce_mixed_owner.py`: actual UID 1000 raises PermissionError
at the root-owned request chmod, the root-owned directory chmod, and an
unreadable root-owned published copy. Each case asserts zero mocked transport
calls. These are reproducible alternatives, not a claim that all three occurred
in production. The operator's metadata is required to select the concrete fix.

Windows: 24 passed, one Linux-only symlink case skipped. Linux/Python 3.10,
UID 1000: all 25 passed. Actual Python 3.7 sealed-resource compilation and
wrong-host zero-query refusal passed. No live send or production mutation is
part of these checks.

```bash
sudo python3 -I -B -u /home/cnooc/inspect-energy-webhook-permissions-20261116-r1.py --inspect-readonly
```

Return its JSON. Do not replay a prior installation or sender command.
