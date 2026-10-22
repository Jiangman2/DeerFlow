# Bounded business-file copy pilot

The operator approved at most5000 files and1GiB to a new private pilot directory
on the already tested internal NFS export. This is not full migration, a mount
switch, a worker change, or approval for replicas2. Keep older tools immutable.
Run only on datax-03, bind its exact successful source-audit receipt, and retain
the datax-01 inventory/cross-node hashes as operator-provided evidence, not fresh
cluster checks. No Kubernetes/SQL/provider clients belong in this tool.

Use a transient systemd unit with Restart=no and an exclusive durable state.
Mount only inside a verified private mount namespace. The source bind is RO;
the NFS export remains hard/NFSv4.1/root_squash. All business traversal/copy runs
as UID/GID1000 with cleared supplementary groups and no capabilities. No chmod,
chown, deletion, symlink following, rsync daemon, or source mutations. Copy only
stable ordinary files through descriptor-relative no-follow opens, with strict
byte/count caps, throttled payload I/O, SHA256 readback and change detection.
No source paths, content or raw exceptions in public evidence. A changed source
or partial operation stays unconfirmed. Status never replays or mounts anything.

Offline Linux fixtures must exercise real privilege drop and content copying;
namespace, systemd and NFS integration remain untested until operator receipts.
Sampled cached readback is not server-crash durability, ACL/xattr preservation,
cross-node readback, unconstrained throughput or a consistent live-data snapshot.
