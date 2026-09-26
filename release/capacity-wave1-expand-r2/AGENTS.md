# Wave 1 frontend expansion

- Expand only the production frontend Deployment from one to two replicas,
  constrained to datax-02 and ecs-zdz-pro-0015. Never alter Gateway, runner,
  service, database, node cordon or image tag.
- Bind rollout to the exact successful isolated canary evidence, preload evidence,
  current frontend spec and protected workload stamps. Abort on drift.
- Use a RollingUpdate with maxSurge 1, maxUnavailable 0, and hard pod anti-affinity
  on app=frontend per node scoped to pod-template-hash using matchLabelKeys.
  Old/new revision coexistence is required to avoid a two-node rolling deadlock.
  Preserve the existing process, environment, probes,
  volumes and image. Use a UID/resourceVersion-preconditioned stdin JSON patch.
- After mutation, verify both ready endpoints and Next build/action key equality.
  If expansion fails, attempt an exact guarded restoration of the prior Deployment
  spec. Never force-delete Pods or assume a failed rollback succeeded.
- Poll the observed generation, replica counts, Pods and endpoints; do not use
  stale ProgressDeadlineExceeded conditions as proof that restoration failed.
  Show redacted progress and retain redacted failure diagnostics before rollback.
- Keep reports redacted: no raw Kubernetes manifests, env values, secrets,
  app logs or HTTP bodies in files or terminal. Preserve Python 3.6 compatibility
  and LF/no-BOM package checks. No local test counts as a production rollout.
