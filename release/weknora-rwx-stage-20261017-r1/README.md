# WeKnora dedicated shared storage — NFS creation stage

**WITHDRAWN DRAFT — NOT QUALIFIED OR DELIVERED.** The operator subsequently
requested retaining the knowledge files on datax-03's 3 TB disk. Do not run this
ecs-zdz-pro-0000 proposal. Its create entry point is disabled. No production
commands were run. Reassess datax-03 storage sharing before any export change.

Scope approved on 2026-10-17: new dedicated NFS exports, then new PV/PVC and
small synthetic cross-node tests. This draft describes only the NFS stage.
It is not a migration, a production mount switch, or replica expansion.

## Exact changes

Run only on `ecs-zdz-pro-0000` (NFS server `192.168.111.189`).

- New parent `/mnt/data/weknora-rwx-r1`, root:root, 0755.
- New children `files` and `docreader`, UID:GID 1000:1000, 0770.
- One new `/etc/exports.d/weknora-rwx-20261017-r1.exports` configuration.
- Four targeted additive exports: two paths × two clients.
- Clients: datax-01 `192.168.111.2`, datax-03 `192.168.111.18`, verified by
  operator Kubernetes node inventory. A future third node requires new review.
- Options: `rw,sync,root_squash,no_all_squash,no_subtree_check,secure,sec=sys`.
- New root-private evidence `/var/lib/weknora-rwx-nfs-20261017-r1`.

No `exportfs -ra`, `-a`, `-u`, `-f`, NFS restart, image operation, model request,
database operation, Kubernetes operation, old-export edit, or source-file copy.
The production paths under `/opt/deerflow-storage` are never accessed.

The parent is outside `/mnt/data/deerflow-rwx`; no permission change to that
existing 0750 directory is necessary. Do not use `chmod 777` or turn off
root_squash when a later client test fails. AUTH_SYS trusts permitted client
hosts' numeric UIDs; this test does not qualify hostile-host isolation or TLS.

## Execution

Upload the delivered `weknora-rwx-nfs-stage-20261017-r1.py` to
`/home/cnooc/` on the NFS server. Copy the complete `RUN-NFS.txt` block. It
installs a hash-checked private copy, prints a status command, and creates once.

Preflight checks the exact server address, mountpoint/XFS, >=600 GiB free and
>=1 million available inodes, path safety, required tools, existing exports
and configuration. This reserve accommodates the observed ~250 GiB source plus
growth; it is a planning threshold, not a reservation or capacity guarantee.
If `/etc/exports.d` is absent the tool creates only that exact directory.
An overlapping ancestor export, unexpected existing target, nonstandard export
table, changed config, or insufficient reserve stops before new exports.

`--check-only` is read-only and does not acquire locks or write evidence.
`--status` is read-only, does not replay mutations, and reports current partial
state plus the original receipt. Neither status nor check grants a PASS.
After interruption/failure **do not rerun create, delete the state, unexport,
or alter permissions**. Send the status output for exact recovery review.

On PASS send the final JSON and exit codes. The next stage will create separate
Retain PV/PVC and UID-1000 probes on datax-01/datax-03, preserving the current
production RWO claims. Do not attach these empty exports to production now.
If a third-party export/config changes during this operation, the tool stops
and retains all evidence; it does not try to undo the other administrator.

## Not yet qualified

Cross-node NFS mounts, actual kubelet source-IP routing, UID-1000 I/O, NFSv4.1
access, atomic rename/read visibility, throughput, capacity reservations/quotas,
250 GiB copy consistency, read-only DocReader sharing, startup recursive chown,
and dual-App behavior. NFS remains a single storage failure domain.

Reference semantics: https://www.man7.org/linux/man-pages/man8/exportfs.8.html
and https://www.man7.org/linux/man-pages/man5/exports.5.html .

## Local qualification

Use package.py in an offline Linux container. Synthetic tests redirect all
paths into a disposable test directory and emulate exportfs responses; no
actual NFS/exportfs/server access is performed. Source and delivery are frozen
after packaging. Do not modify the earlier read-only replica-inventory tool.
