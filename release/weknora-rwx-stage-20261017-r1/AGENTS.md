# Additive WeKnora storage preparation

WITHDRAWN DRAFT: user chose to keep data on datax-03 before qualification or
delivery. Both create functions are disabled. Do not enable, package, or run
this proposal without a newly reviewed direction. No server writes occurred.

This package creates only the exact new NFS paths and exact dedicated exports
in stage_nfs.py. It never changes existing home/skills exports, reloads all
exports, mounts a volume, copies business data, invokes Kubernetes, or restarts
anything. Root-squashed AUTH_SYS exports are restricted to the two reviewed
node IPs; AUTH_SYS is not encryption or an application authorization boundary.

Read-only preflight precedes a durable exclusive operation marker. Every
mutation has a write-ahead receipt. An interrupted or uncertain mutation is
never replayed; status only reconciles observed files/exports and never writes.
Preserve old evidence and partial resources for reviewed recovery. No chmod
or chown of an existing directory, no deletion, no recursive filesystem action.

All process arguments are explicit, deadlines/output bounded, stderr never
printed. Local synthetic tests must not call exportfs, NFS, Kubernetes or model
services. Production execution and cross-node I/O remain separate acceptance.
PV/PVC creation and synthetic two-node testing follow the NFS receipt; neither
this stage nor RWX metadata authorizes migration or replicas.
