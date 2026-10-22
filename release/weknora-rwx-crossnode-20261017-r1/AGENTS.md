# Receipt-bound cross-node synthetic NFS test

This is a new stage after the operator-returned r2 NFS PASS receipt, not a
production storage switch. Keep all earlier tools and evidence immutable.
Create only two new Retain PV/PVC pairs, a deny-all test NetworkPolicy and two
nonroot test Pods. Do not run WeKnora, copy business data, change old mounts,
signal consumers, increase replicas or call providers. Existing application
specifications, process identities and storage bindings must remain unchanged.

Use the already preloaded candidate image with its entrypoint overridden; never
mount old claims, credentials or host paths. Verify admission, node identity,
image identity and exact claim bindings before executing synthetic I/O.
Keep root_squash, omit fsGroup, and never recursively chown the NFS filesystem.
Only remove checksum-verified synthetic files and UID-bound owned test objects.
Preserve new PV/PVC pairs. Unknown mutation outcomes require read-only status;
no automatic replay, rollback, state deletion or force termination.

Offline Linux synthetic qualification is mandatory. It does not establish real
NFS availability, performance, locking, durability on server failure or scale
approval. Small-file PASS permits migration planning, not a live mount switch.
