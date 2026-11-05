# Root-space cleanup follow-up, 2026-10-28

Operator `log(30).txt` confirms 61/61 r2 relocation symlinks still resolve to
their recorded copies. Root availability is 67,938,152,448 bytes (~63.27 GiB),
Ready=True, DiskPressure=False. Dispatcher and SkillHub active Pods are ready.
This is not full platform acceptance: observability/alloy remains Pending with
ImagePullBackOff. Terminal failed Pods include historical evictions; their
presence does not justify deleting runtime storage or assuming current replicas
are down. Image filesystem metrics and `du` allocation have different accounting
scopes and their difference must not be presented as reclaimable capacity.

## r3 selected retained relocation

`relocate-portal-datax01-offline-archives-20261028-r3.py` binds the successful r2
report SHA256 `d4282c5256a00369054d59949b8b59788bb7b78c4833c7f59e6cd4d322317e10`.
It uses a new once directory `/opt/portal-offline-archives-20261028-r3` and retains
all original bytes with the same verified-copy/atomic-symlink method as r2.

The scope is 80 metadata-pinned old rotated logs (~0.981 GiB), each at least
30 days old by mtime and ctime, plus direct archive files in these three observed
historical build/import output directories:

- `/var/lib/portal-ppt-host-oci-bound-r1-output-bf5f67d679944a64861f8394e38f8584`
- `/var/lib/portal-ppt-provisioner-same-version-import-r1`
- `/var/lib/portal-ppt-refresh-tool-TeLN6c6D`

The first directory accounts for ~4.97 GiB. Directory size is a potential target,
not a promised saving. Only regular archive files at least 48 hours old qualify;
reports, source code, database files and arbitrary nested files are not selected.
Package metadata is pinned freshly before transfer. Selected rotated log names
must also match the fixed inventory allowlist and rotation suffix pattern.
Current logs, system packages, Ambari/Hadoop installations, etcd, user documents,
database backups, containerd and Kubernetes storage are excluded. No services
restart and no images are deleted. Copies retain owner, permissions, xattrs and
timestamps; hash validation and process/mount checks precede path replacement.

31 Linux Python 3.7 tests passed, including the inherited transfer/refusal tests,
rotated-log allowlisting, exclusion of containerd/arbitrary build directories,
and full copy/readback of a pinned log. Execution is pending. Original paths
depend on the off-root copies; retain r2/r3 directories permanently until a
separately reviewed restoration or retention decision is made.

## Build-cache ownership diagnosis

`inspect-portal-datax01-build-cache-20261028-r1.py` performs only local read-only
metadata queries. The inventory has 177 image references (169 distinct targets)
in `deerflow-buildkit`, versus 87 references (42 targets) in `k8s.io`; none is
automatically disposable. The preceding tabular container-list projection was
ambiguous (`-` image columns), so the next tool reads selected container info
fields and task states rather than treating that projection as idle proof.

The tool locates buildkitd processes and root-owned local Unix sockets, then
optionally runs `buildctl du --verbose`. It projects cache identity, size and
usage flags only: no build descriptions, credentials, environment or full OCI
process configuration are exported. No prune, image deletion, namespace
deletion, service restart, build or application request is made. Missing tooling
or an inaccessible socket is reported rather than bypassed with container exec.
Six Linux Python 3.7 projection tests passed. Cache reclaimability alone is not
authorization to remove retained release images or an active build's data.

Still outstanding: both new receipts; Alloy pull failure investigation; full
platform-health r2 result; independent database backup coverage verification.
