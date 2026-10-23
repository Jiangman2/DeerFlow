# Data QA progress frontend activation preparation

The user has since added GLM Token-wait summary changes to this release. Candidate
`15d132...` is superseded. The packager's `CANDIDATE_SUPERSEDED` gate blocks an
operator artifact even after a successful Gateway receipt arrives. Retain this
tested baseline for adapting the replacement image, exact runtime source pins,
and new matching preload evidence. Do not activate the old candidate.

This is a prepared activation template. No operator activation artifact is created
until the Gateway feature rollout returns its accepted report identity. Both
frontend r2 preload summaries are recorded in
`data-qa-progress-frontend-preloads-field-20261022-r2.json`.

The baseline is frontend UID `bb0b3f6e-31f7-4073-8b76-7650ea5b96ee`, generation
225, spec SHA256 `1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123`,
image manifest `ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5`.
The sole intended mutation replaces the frontend container image with
`deerflow.local/deer-flow-frontend@sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475`.

The two preload receipts remain operator-returned evidence. Their private reports
stay on their original nodes; activation does not SSH or require copying those
reports to datax-01. Packaging binds each complete available summary, its reported
byte count/SHA256, candidate manifest/config, operation, exact node UID, all four
import/CRI phases, capacity preflight, and old-reference preservation. Activation
freshly checks both Node UIDs, readiness, pressure, ARM64 platform, and image cache.

The forthcoming Gateway summary must report
`GATEWAY_OA_DATA_QA_RUNTIME_ACCEPTED_FRONTEND_AND_OA_PENDING`, all 70 accepted Pods,
Gateway generation 384, Runner generation 55, schema `0021_oa_result_sets`, and
preserved frontend/configuration. At execution, the script reads the root-owned
private Gateway `REPORT.json` on datax-01, verifies its exact size and SHA256 with
a 1 MiB bound, and validates the full acceptance record. Missing, changed, failed,
or incomplete evidence refuses execution before the frontend once claim.

Preparation command, only after recording the actual Gateway summary:

```powershell
python -I -B deploy/enterprise/package-data-qa-progress-frontend-activation.py `
  --backend-acceptance <actual-gateway-field-summary.json> `
  --prior-activator C:/Users/15285/Downloads/activate-portal-frontend-employee-roster-20261020-r2.py `
  --output <new-operator-activation.py>
```

The packager extracts the proven roster activator's functions through the AST
only after validating its complete SHA256. It retains the bounded process helper,
Pod ownership/current ReplicaSet, service-account mount normalization, runtime
image, EndpointSlice, and node checks. Its old rollout entrypoint and old receipt
gates are excluded.

The operator script runs on exact hostname `datax-01` as root with isolated
Python and disabled bytecode writes:

```bash
sudo python3 -I -B /home/cnooc/<new-operator-activation.py> --activate-frontend-data-qa-progress-once
```

It writes a unique private evidence directory and the persistent marker
`/var/lib/portal-frontend-data-qa-progress-activation-20261022-r1.once`.
Before the only PATCH, it fsyncs dispatch intent. The JSON Patch tests the UID,
resourceVersion, generation, and complete old spec, then replaces only the image.
An uncertain response permits readback only; it never permits another PATCH or
automatic rollback. Do not remove the marker or retry an incomplete attempt.

Acceptance requires both new Pods on the exact two nodes, current ReplicaSet
ownership, matching runtime image IDs, readiness and exact Service endpoints.
Each Pod independently verifies the candidate BUILD_ID, routes manifest, and two
compiled Data QA chunks; loopback HTTP must serve both chunks with exact bytes
and SHA256. HTTP does not follow redirects. The Pods/container IDs must remain
stable through final acceptance. Gateway/Runner images, readiness, endpoints,
read-only schema 0021 checks, all unrelated deployment specs, services, and
configuration object UID/resourceVersion metadata are preserved and rechecked.
No configuration values, raw credentials, business rows, provider calls, OA
callbacks, migration, or external HTTP requests are requested.

Success is
`FRONTEND_DATA_QA_PROGRESS_RUNTIME_ACCEPTED_BROWSER_PENDING`. Production browser,
SSO, real Data QA progress rendering, and OA business acceptance remain separate.

Offline qualification: 15 tests passed on Windows/Python, including a local Node
VM execution of the runtime probe with synthetic filesystem/HTTP responses. Tests
cover evidence refusal, exact CAS, durable dispatch ordering, uncertain PATCH
readback, node drift, full acceptance flow, configuration drift, and refusal to
package the superseded candidate even with a backend acceptance receipt. The host
template and generated in-memory package parse as Python 3.7. No cluster or
Docker operations were used for this preparation.
