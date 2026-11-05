# Combined frontend activation

The operator has supplied accepted Gateway and both new frontend preload
summaries. This package binds the exact private Gateway report (302,782 bytes,
SHA256 `9748159776fc431cdbaedb21da8c96eda68534532bf5a97581d03a8dd51669b0`),
the two frontend cache receipts and the four compiled-source pins. It re-reads
and validates the full Gateway report on datax-01 before any update.

Operator artifact:
`C:/Users/15285/Downloads/activate-portal-frontend-data-qa-token-20261022-r1.py`

- Bytes: 169823.
- SHA256: `0f45c2678a40c0812f656b667b9a7350e606e8260db919ff84acffaa6c4a74db`.
- Target image: `deerflow.local/deer-flow-frontend@sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5`.
- Expected Frontend baseline: UID `bb0b3f6e-31f7-4073-8b76-7650ea5b96ee`, generation 225, spec SHA256 `1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123`.
- Preserved Gateway: generation 384 from the accepted report; Runner: generation 55.
- Schema: `0021_oa_result_sets`; OA v2 stays disabled.

Copy the script to datax-01 `/home/cnooc/`, then execute once:

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-frontend-data-qa-token-20261022-r1.py --activate-frontend-data-qa-token-once
```

It performs one image-only compare-and-swap patch with the full previous spec,
UID, generation and resource version as preconditions. A durable dispatch
record precedes the patch; uncertain responses never cause a second patch.
Preflight rechecks node UIDs, cache visibility, backend readiness, compatible
schema, service bindings and configuration metadata. Runtime acceptance checks
both frontend Pods, source hashes, HTTP-delivered JavaScript bytes and service
endpoints, then verifies backend and configuration preservation.

The exact package passed 22 offline tests, including failed receipt rejection,
old preloads rejection, source/HTTP verification, timeout uncertainty handling,
CAS scope and before/after preservation. Nine additional Linux Python 3.7
checks loaded the packaged host and embedded helpers in isolated mode and
validated actual receipt bindings and rejection cases. These tests made no
production requests or writes.

Expected successful status:
`FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING`.
Expected frontend generation: 226. After success, verify Data QA progress and
the GLM reply-footer waiting/final-usage display in the browser. Upstream GLM
reports final summary usage; the frontend does not invent incremental counts.

Preserve the report under `/var/lib/portal-frontend-data-qa-token-activation-<operation>/`.
Do not rerun a failed or uncertain activation or remove the once marker. A
failure after dispatch requires inspecting the actual state first. This
activation neither replays the database migration nor enables OA v2; receiver
and business acceptance of OA v2 remain outstanding.

## Operator-reported activation accepted

Received 2026-10-23, operation `a1ac5b2855ba4d71965dba198878b697`:
Frontend generation 226, spec SHA256
`ecf0297bd8609f104a6b549d484d84b7d658c71b4a0b100a5c66149e821fb3a3`.
Both `frontend-676fdb5959-nnrmm` (datax-02) and
`frontend-676fdb5959-sjks5` (ecs-zdz-pro-0015) passed immutable-source and
HTTP static-asset verification. The local root response was HTTP 307; both
JavaScript assets were independently checked for HTTP 200 and exact bytes.
This is runtime acceptance, not authenticated browser acceptance.

Gateway 384, Runner 55, Nginx 116 and Provisioner 85 retain their preflight
spec hashes. Configuration metadata and schema 0021 are preserved. Exactly
one frontend patch was dispatched; no model request or migration was made.

The server report at
`/var/lib/portal-frontend-data-qa-token-activation-a1ac5b2855ba4d71965dba198878b697/REPORT.json`
is 96,421 bytes, SHA256
`dfd628dbd45b03859196593343399137ac3395f117df98921623b8ac0a3e1899`.
The pasted summary is preserved in
`data-qa-token-frontend-activation-field-20261023-r1.json`, explicitly marked
as operator-supplied evidence. The local agent has not fetched that private
report or performed production browser testing. Do not rerun this activation.
