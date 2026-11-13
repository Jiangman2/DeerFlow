# November 12 retention configuration observation

The operator's 11:43 Beijing observation reads `replicas=6`,
`idle_timeout=600` and `startup_timeout=180`. It is a fresh configuration read,
not an inspection of every Gateway's loaded Provider instance. The observation
does not establish that an idle checker actually reclaimed a particular Pod.

The current AIO Provider source matches the earlier observed production hash
`477ad5e5afb0ab3178e75cff85290268f4c2b72a74b30d27a69bb6e2ec6c1be4`.
Its replica limit and activity maps belong to each process. Seventy Gateway
processes do not share a cluster-wide six-Sandbox hard admission limit.
The periodic idle checker visits tracked local entries. Startup reconciliation
adopts existing backends once; it does not continuously scan Kubernetes for
untracked resources.

Two source-level risks can leave a created resource outside later local cleanup:
async creation registers it only after readiness succeeds, and cancellation of
the readiness await can exit before registration; remote deletion errors can
also be logged without raising. The previously observed remote backend hash is
`a57007d2a4c2841a2e8e22382697b36b69ec5773b5234a81c7a03bceb796e2a6`.
These are reachable code paths, not proof that either caused the current Pod
backlog. Do not attribute all Pending Pods to either issue without task evidence.

Node 0015 contains five MinerU worker Pods, with ordinary-container requests
totalling 10 CPU and 32 GiB. These are document parsing services, not established
idle or orphaned workloads. Do not scale or delete them as a cleanup shortcut.
The older energy collector requests only 100m CPU and 128 MiB; removing it alone
does not establish a solution to the pool-wide backlog.

Any reclamation plan must reconcile trusted thread ownership, actual Pod and
storage identities, current run states and existing execution/creation locks.
Pending/running run rows remain active even if their worker lease has expired.
An `awaiting_human_input` stop is not completed business acceptance. Missing or
inconsistent evidence excludes a candidate. A plan is provisional and must be
revalidated immediately before any separately authorized mutation.

There is a further boundary: current non-run upload/conversion handlers and live
preview lookup do not consistently hold the run execution lease. A free lock and
terminal run metadata do not prove that no user is uploading, converting or
viewing that conversation. A plan must disclose this limitation and cannot be
called a guarantee of harmless eviction. No deletion, node-pool expansion,
request reduction, Gateway rollout or capacity restoration has been performed
by this observation.
