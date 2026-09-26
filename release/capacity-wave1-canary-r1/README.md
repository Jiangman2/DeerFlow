# Wave 1: isolated frontend canary on ecs-zdz-pro-0015

This is a small validation package, not a frontend rollout or a Multiworker release.
The already-preloaded production frontend image is reused. No dependency install,
build, image pull/import, node uncordon, node-label change or production patch occurs.

## Preconditions

- Run as root on datax-01, using /etc/kubernetes/admin.conf.
- Supply the cluster report and the successful 0015 preload report from preparation
  R1. Both must be at most six hours old. The 20260924T104413Z cluster report expires
  at 2026-09-24 16:44 UTC / 2026-09-25 00:44 Beijing time.
- The actual frontend Deployment, running Pod, Service, image digest, Next build and
  action-key fingerprints must still match. Gateway and PPT runner must be unchanged.
- 0015 must remain cordoned, healthy, with only the reviewed system DaemonSet Pods.
- Its container runtime remains on /mnt/data/containerd. Preload is not proof of
  successful unpack/start; this canary tests that missing part.

## Run

Transfer these small files to datax-01:/home/cnooc/:

- deerflow-capacity-wave1-canary-20260924-r1.tar.gz and its .sha256
- wave1-preload-0015-20260924T105418Z.json (from 0015:/home/cnooc/)

Keep the existing wave1-cluster-20260924T104413Z.json. Validate the outer checksum,
extract to a new directory, validate PACKAGE.SHA256, then run:

```bash
sudo python3 -B /home/cnooc/deerflow-capacity-wave1-canary-20260924-r1/canary-wave1.py \
  --plan /home/cnooc/wave1-cluster-20260924T104413Z.json \
  --preload /home/cnooc/wave1-preload-0015-20260924T105418Z.json \
  --ack FRONTEND_CANARY_ONLY
```

Do not use a simultaneous production rollout or another canary. Kubernetes
administrators should avoid concurrent changes during the bounded test. A host-local
flock prevents duplicate invocations of this tool on datax-01.

## Exactly what changes

One temporary frontend Pod and one temporary ClusterIP Service with a random
cap-w1-fe-* name are created in deer-flow. Neither uses app=frontend or an existing
Service selector. No Ingress, NodePort or public route is created.

The Pod uses the current frontend command, environment references and read-only
ConfigMap/Secret references, but never reads Secret API objects or logs values.
It has 100m/256Mi requests, 1 CPU/1Gi memory/512Mi ephemeral-storage limits,
restartPolicy Never, a 900-second active deadline, no service-account-token mount,
and no host ports. It does not use a PVC or access thread storage.

The scheduler selects only 0015 using nodeSelector plus the narrow
node.kubernetes.io/unschedulable:NoSchedule toleration. The node stays cordoned;
resource-fit scheduling still applies. Image unpack is performed by the runtime
on its data disk, not by a build process. CPU/I/O for runtime unpack is NOT governed
by the Pod's application CPU limit; only one small canary is started here.

The read-only probes check:

1. Real Pod readiness, image digest and identical Next build/action-key fingerprints.
2. Loopback icon and compiled JavaScript, with bounded response lengths and hashes.
3. 0015 -> production frontend Service, plus Gateway's existing health endpoint.
4. Production frontend -> the temporary 0015 Service (cross-node DNS and routing).

Probes run tiny bounded Node scripts in the new Pod and the existing frontend Pod.
They do not invoke models, submit runs, log in users, fetch authenticated pages, or
print response bodies/credentials. They do not test full browser sign-in or load.
ConfigMap/Secret *values* are not snapshotted; avoid editing those during this test.

## Cleanup / failure

The tool attempts normal-grace cleanup on success, error or Ctrl+C. Only its exact
names and labels are eligible, and API UID preconditions prevent deleting a same-name
replacement. No force deletion, prune, runtime restart or production rollback runs.
Successful cleanup removes only the temporary validation Pod and Service; evidence
and all image archives are retained. The Pod carries no persistent user files.

Evidence is written to /opt/deerflow-hotfix-evidence/capacity-wave1-canary-<time>-<id>/.
If the API is unavailable or SSH is lost, the Pod active deadline limits its running
time but does not delete the Pod/Service objects. Send the output for review. The
same package can finish cleanup using the exact evidence directory printed earlier:

```bash
sudo python3 -B ./canary-wave1.py --cleanup /opt/deerflow-hotfix-evidence/capacity-wave1-canary-<exact-directory>
```

Do not remove cordons, change image policy, reinstall packages or repeat creates to
work around a failed check. A PASS report confirms this one-node canary only, not
2000 concurrent tasks, browser acceptance, storage HA, or Gateway multiworker safety.
After review, frontend 1 -> 2 requires a separate guarded expansion change.

## Verification and references

Local Python unit tests cover guards, dry-run/defaulting, resource isolation,
production drift, UID-precondition cleanup, uncertain creates and cleanup on failure.
Node HTTP tests cover hashes/types, redirects, body bounds and deadlines. Delivered
archive validation checks inner/outer hashes, LF/no-BOM and Python 3.6 syntax.
No live production execution or Linux/containerd integration is claimed locally.

- Kubernetes v1.36.2 scheduler handling of cordoned nodes:
  https://github.com/kubernetes/kubernetes/blob/v1.36.2/pkg/scheduler/framework/plugins/nodeunschedulable/node_unschedulable.go
- https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/
- kubectl v0.36.2 raw DELETE supports a DeleteOptions stdin body with UID preconditions:
  https://github.com/kubernetes/kubectl/blob/v0.36.2/pkg/cmd/delete/delete.go
- https://nextjs.org/docs/app/guides/self-hosting
