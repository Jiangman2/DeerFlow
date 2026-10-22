# Existing NFS verification path, reselected by operator

The user selected fast cross-node replicas through the existing NFS node after
considering datax-03 exports. This is a new package, not reactivation of the
withdrawn r1 draft. Preserve prior deliveries and production file/mount state.

First stage adds only two empty directories and four exact host/path exports
on ecs-zdz-pro-0000, plus one dedicated persistent exports.d file. No global
export reload, no service restart, no copy, no chmod/chown of existing paths,
no mount, no Kubernetes call, no workload/queue/model change. Clients are the
operator-confirmed datax-01/.2 and datax-03/.18 addresses only. Keep root_squash.

Preflight, exclusive once-only operation state, durable intent before mutation,
read-back, and unchanged unrelated exports/config are mandatory. On partial or
uncertain outcomes only status is allowed; no automatic replay or rollback.
Status never writes, grants rollout approval, or treats unreadable state as absent.

Use offline Linux tests with exact redirected fixture paths and mocked exportfs.
Local tests are not NFS/cross-node acceptance. The next controlled PV/PVC and
small synthetic UID-1000 I/O test requires the returned NFS-stage receipt.
Never copy the 250 GiB source or attach empty volumes to production here.
