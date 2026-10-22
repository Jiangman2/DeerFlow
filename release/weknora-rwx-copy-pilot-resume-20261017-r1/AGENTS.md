# Receipt-bound pilot startup recovery

This is a recovery of one failed, pre-entry pilot on datax-03, not a migration.
Keep the old script, state and failed systemd unit immutable. Bind the exact old
STARTED/script hashes and token, require the observed oneshot/zero-start-timeout
failure and no operation-entry markers, and scan process cgroups before a new
attempt. A changed or incomplete baseline blocks recovery without a retry.

Use a fresh exclusive state/unit, Type=exec, an explicit nonzero startup timeout,
Restart=no, and verified effective service properties before any business I/O.
Preserve the pinned pilot engine byte-for-byte. Its private mount namespace,
read-only source, root_squash/UID1000 path, 5000-file/1GiB budget, 10MiB/s combined
payload throttle and immutable source/partial-output contracts remain in force.
Load that trusted engine only after hash verification; rebind its operation state
only inside a new module instance. Never call its old start() entry point.

Status is read-only, and a launch acknowledgement is not copy acceptance. Retain
bounded stderr hashes and fixed diagnostic classes, never raw output/business
paths. Include source and exact-delivery tests; systemd/NFS integration is not
qualified by offline mocks. No cleanup, reset-failed, restart, signal, production
mount change, Kubernetes/SQL/provider client or full-copy fallback belongs here.
