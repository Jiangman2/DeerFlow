# November 12 sandbox scheduling incident

The fixed-event observation at 10:58 Beijing confirmed that the first daily
command failed during Sandbox initialization. The preserved error is the
provider's `failed to become ready within 180s` exception, wrapped by
`Unexpected error initializing sandbox`. No daily CLI JSON or shell exit code
was returned. The earlier journal interval was 182.375 seconds. The subsequent
three model-proposed Bash calls had no matching execution receipts; the host
then reached its bounded recovery-contract stop.

Two retained Pod events for `sandbox-4afd412d` reported `FailedScheduling`.
The operator's direct event query reported zero available nodes out of 18,
with CPU/memory shortages, an unschedulable node, node-affinity/selector
mismatches and untolerated taints. Preemption could not place the Pod. These
observations establish a scheduling failure before the Skill command body,
not another browser-runtime or market-data failure. The current Pod's absence
alone was not used to infer this cause. See `FIELD-OBSERVATION-20261112.json`;
this is a summary of pasted operator output, not a downloaded raw receipt.

Do not treat reserved resource requests as real-time usage. The scheduler can
reject a new Pod even while measured usage is low; see the official
[resource scheduling rules](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).
The next observation must bind the actual Provisioner node pool and resource
configuration to node allocatable resources and native `kubectl describe`
allocation summaries, including other workloads. Repository defaults are not
the live Provisioner source. No capacity change has been applied by this probe.

Preserve existing work and execution leases. Pod age, phase or low CPU alone
does not establish safe eviction. The previous operator request to skip an
idle-task gate for a Skill cutover is not evidence that arbitrary active
Sandbox Pods can be destroyed. Prefer validated capacity in the existing
eligible pool, or a separately qualified worker, before considering reclamation.
Do not remove node restrictions or lower requests simply to force admission.
