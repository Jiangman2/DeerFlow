# OA timeout configuration and paused rollout — November 3

This change archives the executed timeout-only operation and the operator's
subsequent pause. It does not deploy application code or establish that all
Gateway processes now use 60 minutes. It is a follow-up to PR #26.

## Where the value changed

The operator ran
[`set-portal-oa-run-timeout-60m-20261103-r1.py`](../deploy/enterprise/set-portal-oa-run-timeout-60m-20261103-r1.py).
That tool patched the live Kubernetes `gateway` Deployment in namespace
`deer-flow`, at `spec.template.spec.containers[name=gateway].env`, adding or
replacing `OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS` with the string `3600`.

Neither `config.yaml` nor the application's default was changed.
[`oa_policy_flow_config.py`](../backend/app/gateway/oa_policy_flow_config.py)
still defaults to `30 * 60` seconds and reads the environment override. Gateway
loads this configuration during application startup and retains it on the OA
service. Existing processes do not acquire a new value through a separate
`kubectl exec` shell or a file edit.

The executed script is preserved byte-for-byte: 16,008 bytes, SHA256
`323b3f2055883ce599d163fb58c22363128da0a7509bddd4890d3ac5f7e2d9c0`.
The narrowly scoped `.gitattributes` rule prevents line-ending normalization
from changing that delivered artifact. Its synthetic tests do not contact
production Kubernetes, model providers, OA or the production database.

## Operator-observed state

The environment patch was accepted at Deployment generation 404. The operator
pressed Ctrl+C during observation and then explicitly requested that further
Gateway rolling/restarting stop while users were active. Ctrl+C did not undo
the accepted patch. The operator successfully paused the Deployment.

The subsequent readbacks are preserved in
[`oa-timeout-pause-field-20261103.json`](../deploy/enterprise/oa-timeout-pause-field-20261103.json):

| Resource | Observed state |
| --- | --- |
| Gateway Deployment | generation 405; `paused=true` |
| Deployment replica status | desired 70; updated 3; ready 70; available 70 |
| Old ReplicaSet `gateway-7d6dd5589b` | desired/current 68; ready 68 |
| New ReplicaSet `gateway-7887d6c656` | desired/current 3; ready 2 |

These commands ran sequentially, so their values are not an atomic snapshot.
Already requested creation/termination can finish after pause; ReplicaSet
self-healing also remains active. Both ReplicaSets use the existing Gateway 403
image, `sha256:ebcde83d011eb0bb5f2babae7ccb3a5e782839801da5b156f2c0a5c971f029f7`.
Generation 405 is a configuration/pause change, not a new application release.

Old processes retain their startup 30-minute setting; new Pods are configured
for 60 minutes. All runtime values were not verified. The initial rollout-wait
line reporting 70 updated/ready was controller status before reconciliation;
it was not a successful rollout receipt. Business continuity or OA delivery
acceptance cannot be inferred from the ready replica count alone.

## Handoff for a later operator-authorized release

- Preserve `OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS: "3600"` in the colleague's
  deployment manifest or release configuration. Committing this archive does
  not change their deployment source or the application's default.
- Keep Gateway paused until the operator authorizes the next maintenance
  window. A later release must account for the pause explicitly; changing an
  image while paused does not itself complete the rollout.
- After an authorized rollout, verify the final Deployment state and effective
  configuration across the new Gateway Pods before claiming global adoption.
- Do not replay the archived mutation script, delete its execution marker, or
  use its original generation-404 verification plan against paused generation
  405. Preserve the existing private plan/report under
  `/var/lib/portal-oa-run-timeout-60m-20261103-r1/`.

The longer limit provides time for the reported approximately 31-minute run,
but it cannot guarantee delivery for longer runs, other failures or an OA
client with a shorter deadline. It does not recover already failed jobs or
enable callbacks. Timeout reconciliation, monitoring improvements and the
separate single-job recovery are outside this PR. Database head remains 0022;
Frontend, PPT Runner and deferred Work DataLake authorization are unchanged.

## PR validation follow-up

The byte-preservation attribute triggered the V4.1 preparation archive workflow.
Its verifier compared the current adapter directly with the October 29 receipt,
although the existing candidate/incident source manifests already qualify the
later stream-guard changes. This produced `Installed adapter changed` before
the workflow reached its synthetic tests.

The verifier now binds that historical receipt to the October 29 source lock
and checks the current adapter through the existing source-delta validators.
Those validators still enforce source hashes, parent hashes and path scope;
the workflow also retains its complete production-baseline check. New tests
cover the original and qualified later versions, line-ending conversion and
rejection of changed sources, rewritten receipts and broken parent bindings.
Historical payloads, receipts, source manifests and application code are unchanged.
