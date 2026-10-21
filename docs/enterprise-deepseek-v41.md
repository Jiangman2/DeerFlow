# Enterprise DeepSeek-V4.1-Flash

This optional profile implements the supplied enterprise API document v1.2
(2026-10-14). It does not replace DeepSeek-V4-Flash or GLM and does not alter
the default model, WeKnora/OCR models, or isolated PPT provider.

- Configuration: `deploy/enterprise/deepseek-v4.1-flash.model.yaml`.
- Endpoint: `https://aiapi.cnooc.com.cn/DeepSeek-V4.1-Flash/FCV-41/v1/chat/completions`.
- Provider: existing `deerflow.models.robust_openai:RobustChatOpenAI`.
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
