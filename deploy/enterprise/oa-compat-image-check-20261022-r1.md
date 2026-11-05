# Installed compatibility image qualification

Effective preloads are now Gateway 5/5 and Runner 10/10. The datax-01 continuation
receipt is archived separately; all effective node UIDs and image/config digests
were reconciled with the build profiles. These are operator-pasted summaries,
not independent reads of all private reports. No compatibility rollout or
production migration has been performed by these tools.

`package-oa-compat-image-check.py` embeds the reviewed field baseline and build
pins into `verify-oa-compat-images.py`, together with `oa-compat-image-check.py`.
The output is `verify-portal-oa-compat-images-20261022-r1.py` in Downloads.

The host verifies both unchanged, ready deployments before creating Jobs. One
bounded Job per role runs on a pinned preloaded node (Gateway 0018, Runner
datax-01), using the exact digest and Never pull policy. Node identity, readiness,
taints and resulting Pod ownership/image IDs are checked. Required NoSchedule
tolerations are derived from the pinned node; NoExecute taints fail the check.
Jobs receive no production volumes, ConfigMaps, Secrets or service-account token.
They run only Python checks, not the service entrypoint. The root filesystem is
read-only, /tmp is a bounded emptyDir, retries are disabled, execution deadline is
180 seconds, and TTL cleanup is 600 seconds after completion.

The child disables Python socket connections and uses only temporary SQLite.
It verifies installed source digests and the retained 0020 migration head,
acceptance of the complete 0021 additive shape without changing its revision or
legacy row, rejection of malformed expansion and unknown successors, and normal
0020 startup. No code is injected into the running production consumers.
This is synthetic startup qualification, not production PostgreSQL migration or
OA callback/business acceptance. A later rollout requires its own checks.

Validation: nine Linux Python 3.7 host tests passed, including a complete mocked
two-Job flow, forbidden resource/mount checks, taint handling, pinned images,
deadlines, node/deployment drift and Pod ownership. Both installed-code child
variants passed all five checks in local ARM64 containers with no network,
read-only rootfs and temporary scratch space. Those local containers used the
reviewed payload mounted over retained images, so field execution of the actual
preloaded images remains required. Host/container tests made no cluster changes.

The runner uses a fresh exclusive control directory and retains JSON phase/Pod
diagnostics. A partial or uncertain Job creation is not retried automatically.
The script does not patch deployments or change the production schema.
