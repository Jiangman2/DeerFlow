# Enterprise DeepSeek-V4.1-Flash

This optional profile implements the supplied enterprise API document v1.2
(2026-10-14). It does not replace DeepSeek-V4-Flash or GLM and does not alter
the default model, WeKnora/OCR models, or isolated PPT provider.

- Configuration: `deploy/enterprise/deepseek-v4.1-flash.model.yaml`.
- Endpoint: `https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1/chat/completions`.
- Provider: candidate `deerflow.models.enterprise_deepseek_v41:EnterpriseDeepSeekV41Chat` (must be installed before activating the profile).
- Credential: Gateway-only `DEEPSEEK_V4_1_FLASH_API_KEY`, supplied through a required Secret reference.
- Flash/thinking: explicit `extra_body.enable_thinking: false/true`.
- Images: single/multiple base64 images; qualify the actual endpoint before activation.
- Reasoning effort: unsupported by the supplied contract; do not advertise or send it.

The frontend lists server configuration through `/api/models`. Refresh the page
after a successful Gateway configuration rollout; the browser caches that list
for the session. No frontend rebuild is required for this addition.

## Preparation and acceptance

Package a standalone operator tool with the repository's Python dependencies:

```bash
python deploy/enterprise/package-deepseek-v41.py --output /chosen/path/prepare-deepseek-v41.py
```

Run the packaged tool on datax-01 using isolated system Python (3.7 supported):

```bash
sudo python3 -I -B -u /home/cnooc/prepare-deepseek-v41.py --prepare-deepseek-v41-once
```

Enter the key at the hidden prompt. Do not put it in command arguments, Git,
ConfigMaps, browser configuration, or exported reports. The tool executes the
installed model factory in a short-lived child of one ready Gateway Pod and
checks flash, thinking, tool calling, synthetic tool continuation, and two-image
understanding. It uses direct HTTP with TLS verification, no redirects, no
automatic retries, at most five requests, and bounded output/total request time.
Model replies and reasoning are checked in memory and not exported. It creates
a new immutable Secret only after successful acceptance and unchanged Gateway
and sampled-container identities. No workload or configuration is patched.

A once marker records the attempt. If interrupted or unsuccessful, retain its
report and inspect the outcome before preparing any retry; never delete the
marker to repeat an uncertain Secret creation. The Secret name and UID are in
the successful report. Preparation is not online activation or all-replica
acceptance.

Preparation records observed/desired/updated/ready/available replica counts
before the readiness gate. It still requires a fully observed, stable 70-replica
Gateway. Read-only readiness failures no longer consume the provider once
marker. An old attempt that already consumed a marker can be resumed only by a
package explicitly bound to that exact report's operation, byte size and SHA256
(`--resume-noop-receipt`). The server checks the original private report and
marker, requires all provider/Secret/workload activity flags to be false, retains
both, and atomically claims a separate marker before the first provider request.
Unknown or partially executed attempts cannot use this path. Wait for any other
operator's Gateway rollout to finish before running preparation.

An explicit `--resume-flash-503-receipt` package supports one narrowly scoped
provider retry: the exact pinned report must show HTTP 503 on the first synthetic
flash request, zero completed checks, all later stages unattempted, and no Secret
or workload mutation. It binds the preceding recovery marker and unchanged
profile, preserves both reports/markers, and claims a new exclusive marker.
This is not an automatic retry loop. Timeout/unknown outcomes, 401/402, successful
stages, later tool stages, or any possible Secret write refuse this recovery.
A separate `--resume-synthetic-tool-503-receipt` package is available only for an
explicit operator request after the three successful synthetic stages followed
by tool-result HTTP 503. It pins that report and its previous recovery marker,
requires zero business tools and no Secret writes, and reruns at most five
requests without automatic retry. It does not replay any business tool or claim
the provider is healthy. The supplied v1.2 document records transient deployment
503s, but the field status alone does not prove that explanation for this run.
Further attempts require an explicit decision after reviewing the latest receipt;
do not publish an unqualified selector entry or build an unattended retry loop.

For activation, bind the current successful Gateway/frontend reports, append the
profile to a new configuration copy, add only the new required Secret reference,
and preserve all existing configuration and list ordering. Check actual routing
to `aiapi.cnooc.com.cn` (including proxy bypass), compare the full Deployment
specification before a single patch, and use an operator-confirmed idle window.
Accept every ready Gateway replica and the authenticated model-list response;
then verify both modes in a new browser conversation. The profile and preparation
tool alone do not perform or authorize this rollout.

The document reports that `max_tokens` does not bound hidden reasoning. The
probe has an independent 180-second total deadline per request; no keepalive
extends it. The supplied contract describes 402 as an entitlement error. An
observed HTTP 503 establishes a failure somewhere on the inference HTTP path;
without response provenance it does not identify the model service, ingress or
another forwarding layer. Neither status is silently retried by the tool.

## October 21 incident evidence

The operator reported three HTTP-503 batches: r2 failed its first flash request;
r3 passed flash, thinking and synthetic tool calling before tool-result failure;
r4 again failed its first flash request. The pinned profiles and probe sources
are identical. See `docs/deepseek-v41-503-incident-20261021.txt` for the shareable
application-side record. It contains no credentials or business conversations.

`deploy/enterprise/collect-deepseek-v41-incident.py --collect-existing-readonly`
is a Python 3.7 controller for datax-01. It reads only the three exact private
reports, checks their size/hash, exports report-file timestamps in UTC and
Shanghai time, and queries the current identity/address of the original Pod.
It does not run another canary, access Secrets, read raw logs or write files.
Report mtime is not a request timestamp; current Pod addresses are not historical
egress/NAT proof. The original tool did not save upstream request IDs, response
headers or error bodies, so this collector cannot recover them. Local operation
UUIDs must never be presented as upstream request IDs. If correlation remains
impossible, a separately coordinated synthetic request needs timestamped,
redacted transport evidence instead of another blind retry.

### Explicit two-node transport comparison

The October 21 renewed investigation verified the delivered r4 SHA256
`3716be98c073b890ef33e64b3adfffbbd7d3b5361cde1d1379c2a1689350aa17`:
its HTTPX client already explicitly uses `trust_env=False`, TLS verification,
and disabled redirects. Missing `NO_PROXY` cannot explain those diagnostic
503s. This says nothing about the default SDK client's later production route;
qualify that configuration separately before enabling the selector entry.
The documented endpoint/model/thinking toggle match the profile. Synthetic
tool continuation uses the returned assistant call ID and matching tool result;
there is no evidence yet attributing the 503 to a malformed continuation.

`package-deepseek-v41-diagnosis.py` binds r4 and reuses its exact application
probe. The new standalone diagnostic chooses two ready nodes of one stable
Gateway Deployment, compares one direct nonstream HTTP request with the five
application streaming stages on each node (at most twelve requests total),
and never executes business tools or creates Secrets/configuration. Each stage
runs once with no automatic retries. A raw baseline failure does not prevent
the independent application comparison; application failure stops its later
dependent stages. Wait until the joint Gateway rollout has finished.

It records UTC request/header timestamps, client correlation IDs, allowlisted
upstream response IDs, TCP peer/TLS certificate fingerprint when available,
DNS observations, actual payload structure/hash, response status, closed error
categories and bounded error-body fingerprint. Neither request bodies, model
text/reasoning, credentials, cookies nor arbitrary response headers are exported.
Client correlation IDs are sent via X-Request-ID; they are not proof of an
upstream-assigned request ID or receipt at the model server. Peer/DNS addresses
are not egress NAT evidence. Header/body signatures aid correlation but cannot
alone identify which infrastructure hop generated a 503.

Successful acceptance additionally requires both full application sequences,
correct wire thinking flags/tool-result linkage/two-image count, and observed
SSE terminal plus `[DONE]` markers. This validator is diagnostic-only; it does
not silently modify the installed adapter. A green batch does not establish
long-term availability or constitute production activation. Readiness/binding
drift prevents acceptance. The separate once marker is claimed before requests;
retain the report after failure and do not delete it for repeated trials.

Run after copying the packaged file to datax-01:

```bash
sudo python3 -I -B -u /home/cnooc/diagnose-portal-deepseek-v41-transport-20261021-r1.py --diagnose-and-test-once
```

Enter the V4.1 key only at the hidden prompt. The resulting sanitized JSON can
be forwarded to model/gateway operators for correlation. Preparing the immutable
Secret, versioned ConfigMap, exact-host environment bypass if needed, full-spec
CAS rollout and authenticated selector/browser acceptance remains a separate
step after these results are reviewed; preserve the new Data QA/GLM release.


### Afternoon direct-path evidence and token-field candidate

The field r1 batch `ce9c7e15fa854b4da7d417a01e774f96` made six requests
on two nodes, preserving generation 382 and the joint Data QA/GLM image.
One direct raw request with documented `max_tokens` received HTTP 503; two
application streams completed with `stop` and `[DONE]`; three requests ended
at local 45/90-second deadlines without response headers. Do not count those
three cancellations as server HTTP 503s. Both successful streams used
`max_completion_tokens`, showing a parent serializer mismatch with the supplied
contract, but the raw failure rules it out as the sole cause. Correlation details
are in `docs/deepseek-v41-503-afternoon-20261021.txt`. No production model has
been added by these diagnostics.

The dedicated adapter normalizes only this model's token field after parent
serialization. It preserves thinking toggles, tool linkage and images. Generic
RobustChatOpenAI and GLM behavior are unchanged. Local success is not provider
qualification. Previously delivered preparation scripts contain the old profile
and must not be reused to publish this candidate.

`package-deepseek-v41-candidate-diagnosis.py` binds the exact r1 package, prior
private receipt and generation-382 specification. The r2 diagnostic loads the
candidate source only inside a temporary child, validates the installed factory
and base adapter hashes, then repeats the bounded two-node comparison. Each
request has a 180-second total deadline (restored to the original preparation
canary allowance); no retry is automatic. This is a diagnostic timeout, not a
production timeout change. All application wire payloads must actually contain
`max_tokens: 1000`, no `max_completion_tokens`, and the required mode/history
fields before acceptance. Normal SDK GeneratorExit after terminal + DONE is
labelled as stream cleanup. Missing DONE still fails. The script writes only its
own private marker/report; it does not create a Secret, change configuration,
restart Gateway or install code in a running worker. Keep the joint release intact.

```bash
sudo python3 -I -B -u /home/cnooc/diagnose-portal-deepseek-v41-transport-20261021-r2.py --diagnose-and-test-once
```

A full passing result is still needed before packaging the adapter image and
versioned model configuration for a separate rollout. Longer diagnostic waits
cannot establish a root cause or guarantee production latency.


### Candidate r2 field outcome (15:11-15:19 Shanghai time)

Operation `2c4cf8c607cd43568f185e34db653476` made ten direct requests: eight
functional passes, one real HTTP 503 after 155.337 seconds, and one HTTP-200
multi-image stream cancelled by the diagnostic output budget. No 180-second
request deadline fired. All emitted requests used the corrected `max_tokens`.
The two tool-call bodies are byte-identical (575 bytes, SHA256
`8facb73144635a3b1b6f93f6d2054341718119b6e9c8175421c9cd8f433a519c`),
yet one returned 503 and the other completed, followed by a successful tool
continuation. The synthetic body is preserved in
`deploy/enterprise/deepseek-v41-synthetic-tool-request.json`; its bytes were
reconstructed offline through the real SDK and matched both recorded hashes.
No credentials or business data are included. Node/time/path differences remain
possible; this is not proof of a particular failing infrastructure hop.

The image rejection counts serialized LangChain chunk JSON characters (512000)
or chunks (4096), not wire bytes or model tokens. The legacy probe did not
report the counters separately. Do not label that local cancellation as HTTP
503, upstream truncation, unsupported vision, or proof that max_tokens was
ignored. The image request used thinking mode; the supplied document says
max_tokens does not bound reasoning, but the current receipt does not prove
which content caused the limit. Do not relax limits merely to pass acceptance.
See `docs/deepseek-v41-503-followup-20261021-r2.txt` for exact timestamps and
upstream correlation IDs. Provider-path investigation and completed vision
acceptance remain required. Production generation 382 was unchanged and this
model is still not qualified for rollout.
