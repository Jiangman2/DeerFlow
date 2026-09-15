# Group OA policy-to-JointJS integration

The OA-developer-facing Chinese handoff specification is
[`oa-policy-flow-oa-handoff.zh-CN.md`](oa-policy-flow-oa-handoff.zh-CN.md).
This document additionally covers DeerFlow-internal configuration, recovery,
release, and server verification.

This integration accepts a policy document and OA business fields, runs the
configured user-owned DeerFlow agent asynchronously, validates the single JSON
artifact it presents, and exposes status/result polling. It can optionally
deliver the artifact plus the original business fields to a fixed OA callback.

Production API base URL:

```text
https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs
```

The deployment is pinned to the server-verified target:

```text
OA_POLICY_FLOW_OWNER_USER_ID=d1792c77-c297-439d-843f-81ea66095d1a
OA_POLICY_FLOW_AGENT_NAME=制度文生图智能体
```

Do not expose DeerFlow's internal thread, run, sandbox, or artifact APIs to OA.
The three routes below are the complete OA contract.

## 1. Submit a job

```http
POST /api/v1/integrations/oa/policy-flow/jobs
Authorization: Bearer <OA_POLICY_FLOW_INGEST_KEY>
Idempotency-Key: <stable key for this logical submission>
Content-Type: multipart/form-data
```

Multipart fields:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `document` | file | yes | One `.pdf`, `.doc`, or `.docx` policy document, at most 10 MiB |
| `request_id` | string | yes | OA's business request ID, 1-128 characters |
| `key_fields` | JSON object encoded as a form string | yes | OA fields to echo in the callback, at most 64 KiB |

Example:

```bash
curl --fail-with-body \
  -X POST \
  'https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs' \
  -H 'Authorization: Bearer REPLACE_WITH_INGEST_KEY' \
  -H 'Idempotency-Key: policy:ZD-2026-001:v3' \
  -F 'request_id=ZD-2026-001' \
  -F 'key_fields={"oaDocumentId":"ZD-2026-001","title":"采购管理制度","version":"V3.0","organizationCode":"10001"}' \
  -F 'document=@采购管理制度.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document'
```

The response is `202 Accepted`:

```json
{
  "job_id": "54a4dd5b-bc52-42f6-a5ed-559e7162fc15",
  "request_id": "ZD-2026-001",
  "status": "queued",
  "callback_status": "not_ready",
  "created_at": "2026-09-02T01:20:00+00:00",
  "updated_at": "2026-09-02T01:20:00+00:00",
  "completed_at": null,
  "delivered_at": null,
  "error_code": null,
  "error_detail": null,
  "callback_error": null,
  "result_filename": null,
  "result_sha256": null
}
```

The submit request does not wait for the agent. OA should use a 60-second HTTP
timeout for this upload/acceptance call, then poll the
status route. DeerFlow gives the agent run 1,800 seconds (30 minutes) by
default; after that it interrupts the run, marks the job `failed` with
`error_code=agent_run_timeout`. OA should use a 40-minute overall business wait
window. Callback-enabled deployments may additionally wait for the normal
failure callback.

`Idempotency-Key` is mandatory. Replaying the same key with the same document,
request ID, and key fields returns the original job. Reusing it with different
content returns `409 Conflict`.

## 2. Query status

```http
GET /api/v1/integrations/oa/policy-flow/jobs/{job_id}
Authorization: Bearer <OA_POLICY_FLOW_INGEST_KEY>
```

Job `status` values are `queued`, `launching`, `running`, `succeeded`, and
`failed`. Callback status values are `not_ready`, `disabled`, `pending`,
`delivered`, and `exhausted`. In polling-only mode, a terminal job reports
`callback_status=disabled`. A succeeded job remains succeeded if callback delivery is
temporarily pending or ultimately exhausted.

## 3. Download the accepted result

```http
GET /api/v1/integrations/oa/policy-flow/jobs/{job_id}/result
Authorization: Bearer <OA_POLICY_FLOW_INGEST_KEY>
```

This returns `application/json` only after `status=succeeded`. It is the primary
result path in polling-only mode and the recovery path if a callback is delayed
or missed. Before completion the
route returns `409`; a result removed from storage returns `410`.

## OA callback contract

This section applies only when `OA_POLICY_FLOW_CALLBACK_ENABLED=true`.

The callback URL is server-owned configuration. A submission cannot override
it, which prevents request-driven SSRF. DeerFlow sends `multipart/form-data`:

- `manifest`: a compact JSON object containing `job_id`, `request_id`,
  `status`, the original `key_fields`, result filename/SHA-256, completion
  timestamp, and failure information.
- `result_file`: the accepted JointJS JSON file on success; omitted on failure.

Both directions transfer files as normal multipart binary parts. No Base64
encoding is used, so a 10 MiB document is not inflated inside a JSON body.

Headers:

```http
Authorization: Bearer <OA callback token>        # when configured
Idempotency-Key: oa-policy-flow-callback:<job_id>
X-OA-Callback-Timestamp: <Unix seconds>
X-OA-Callback-Signature: sha256=<hex HMAC>        # when configured
```

The HMAC input is the UTF-8 string:

```text
<timestamp>.<canonical manifest JSON>.<result SHA-256 or empty string>
```

OA must verify timestamp freshness and the HMAC with a constant-time
comparison, handle `job_id` idempotently, persist the file before replying, and
return `2xx` only after durable acceptance. DeerFlow retries transport errors,
HTTP `408`, `409`, `425`, `429`, and `5xx` with bounded exponential backoff.
Other `4xx` responses are terminal protocol errors.

## Runtime configuration

Store every credential in the `deer-flow-env` Secret, never a ConfigMap or
source-controlled manifest:

```dotenv
OA_POLICY_FLOW_ENABLED=true
OA_POLICY_FLOW_INGEST_KEY=<openssl rand -hex 32>
OA_POLICY_FLOW_OWNER_USER_ID=d1792c77-c297-439d-843f-81ea66095d1a
OA_POLICY_FLOW_AGENT_NAME=制度文生图智能体
OA_POLICY_FLOW_CALLBACK_ENABLED=false
```

Polling-only mode requires no callback URL or callback credential. To enable
optional callback delivery, set:

```dotenv
OA_POLICY_FLOW_CALLBACK_ENABLED=true
OA_POLICY_FLOW_CALLBACK_URL=https://oa.example.cnooc/path/to/callback
OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN=<OA-provided token>
OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET=<openssl rand -hex 32>
```

Optional controls:

```dotenv
OA_POLICY_FLOW_CALLBACK_CA_BUNDLE=/path/to/internal-ca.pem
OA_POLICY_FLOW_CALLBACK_ALLOW_HTTP=false
OA_POLICY_FLOW_POLL_SECONDS=2
OA_POLICY_FLOW_RUN_POLL_SECONDS=5
OA_POLICY_FLOW_LEASE_SECONDS=90
OA_POLICY_FLOW_MAX_CONCURRENT_JOBS=2
OA_POLICY_FLOW_MAX_PROCESS_ATTEMPTS=5
OA_POLICY_FLOW_MAX_CALLBACK_ATTEMPTS=8
OA_POLICY_FLOW_CALLBACK_TIMEOUT_SECONDS=30
OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS=1800
OA_POLICY_FLOW_MAX_DOCUMENT_BYTES=10485760
OA_POLICY_FLOW_MAX_RESULT_BYTES=10485760
```

HTTPS is mandatory by default. Set the HTTP allow flag only for a reviewed
internal test callback. The callback client does not inherit proxy environment
variables and keeps CA-chain and hostname verification enabled.

The Gateway fails startup when the integration is enabled with incomplete core
configuration, a missing/disabled owner, missing agent, or non-SQL persistence.
Callback URL, authentication, CA, and TLS settings are validated only when
callback delivery is enabled. After changing the Secret, restart Gateway.

## Processing and recovery guarantees

- Upload bytes pass the same staged extension/PDF checks and atomic replacement
  rules as browser uploads.
- Office/PDF input is converted through the configured enterprise parser with
  the local conversion path as fallback before the agent starts.
- The exact prompt begins with `请解析这份制度，按固化格式输出JointJS JSON流程数据。`
  and treats document contents/key fields as untrusted data.
- The target agent runs as its real owner, so its personal agent definition,
  tools, skills, memory, sandbox, and file bucket resolve correctly.
- Completion requires exactly one `.json` artifact delivered by
  `present_files`. DeerFlow independently bounds its size, decodes UTF-8, parses
  JSON, and records its SHA-256 before making it available for download or callback.
- Jobs, retry state, callback state, and leases live in PostgreSQL. A restarted
  or competing Gateway worker recovers due work without blindly launching a
  second conversation. A crash between run creation and job update is repaired
  by finding the run's persisted `oa_policy_flow_job_id` metadata.
- OA can always query status and pull the accepted result. Polling-only mode
  schedules no callback work; callback-enabled mode preserves recovery when
  callback delivery is exhausted.
- Document parsing is outside the 30-minute agent-run budget. Each callback
  attempt has its own 30-second network timeout; neither timeout keeps the
  original submit connection open.

## Rollout and smoke test

1. Apply the database migration and deploy the Gateway image containing this
   feature with `OA_POLICY_FLOW_ENABLED=false`.
2. Confirm `GET /health` and the existing browser chat path.
3. Agree the delivery mode. For polling-only integration, set
   `OA_POLICY_FLOW_CALLBACK_ENABLED=false`; otherwise obtain OA's test callback
   URL and credentials. Update `deer-flow-env` and enable the integration.
4. Restart Gateway and require a successful readiness rollout. A bad target or
   incomplete config intentionally prevents startup.
5. Submit one small real DOCX fixture first, then a representative DOC/PDF.
   Verify the job reaches `succeeded`, the downloaded SHA-256 matches the status
   response, and the JSON opens in the OA JointJS renderer. When callbacks are
   enabled, also verify the callback copy has the same SHA-256.
6. Replay the same idempotency key and verify that no second run is created.
7. In callback-enabled mode, stop the callback temporarily, confirm retry/poll
   fallback, restore it, and confirm delivery.
8. Rotate test credentials before production handoff.

Do not enable production until OA confirms polling behavior, key-field schema,
authentication, timeout, and network allowlist requirements. Callback URL and
callback authentication material are required only when callback delivery is enabled.

## Server-side verification commands

This feature changes only the Gateway image and its SQL schema. It does not
require a Frontend, Nginx, Skill, Provisioner, or Sandbox image change. Use the
environment's existing immutable-image release process, initially keeping
`OA_POLICY_FLOW_ENABLED=false`, and then run:

```bash
kubectl -n deer-flow rollout status deployment/gateway --timeout=300s

GW_POD=$(kubectl -n deer-flow get pod -l app=gateway \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n deer-flow exec -c gateway "$GW_POD" -- sh -lc \
  'cd /app/backend && PYTHONPATH=. uv run --no-sync python -c "from app.gateway.oa_policy_flow_service import OAPolicyFlowService; print(\"oa_module=ok\")"'

kubectl -n deer-flow exec postgres-0 -- sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'

kubectl -n deer-flow exec postgres-0 -- sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select to_regclass('"'"'public.oa_policy_flow_jobs'"'"')"'
```

Expected values are `oa_module=ok`, migration revision
`0014_oa_policy_flow_jobs`, and table name `oa_policy_flow_jobs`.

After OA's test callback settings are stored in `deer-flow-env`, enable the
integration and restart Gateway. Verify Secret key names without printing any
values:

```bash
for key in \
  OA_POLICY_FLOW_ENABLED \
  OA_POLICY_FLOW_INGEST_KEY \
  OA_POLICY_FLOW_OWNER_USER_ID \
  OA_POLICY_FLOW_AGENT_NAME \
  OA_POLICY_FLOW_CALLBACK_URL \
  OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS
do
  kubectl -n deer-flow get secret deer-flow-env \
    -o "jsonpath={.data.$key}" | grep -q . || {
      echo "missing_secret_key=$key"
      exit 1
    }
done

CALLBACK_AUTH_PRESENT=false
for key in \
  OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN \
  OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET
do
  if kubectl -n deer-flow get secret deer-flow-env \
    -o "jsonpath={.data.$key}" | grep -q .
  then
    CALLBACK_AUTH_PRESENT=true
  fi
done
[ "$CALLBACK_AUTH_PRESENT" = true ] || {
  echo 'missing_callback_auth=configure Bearer token, HMAC secret, or both'
  exit 1
}

kubectl -n deer-flow rollout restart deployment/gateway
kubectl -n deer-flow rollout status deployment/gateway --timeout=300s
kubectl -n deer-flow logs deployment/gateway -c gateway --since=10m | \
  grep -E 'OA policy-flow service started|Failed to load configuration|policy-flow integration'
```

The log must contain the service-started line and no configuration failure.
Use a small real DOCX for the first end-to-end request:

```bash
export OA_INGEST_KEY="$(kubectl -n deer-flow get secret deer-flow-env \
  -o jsonpath='{.data.OA_POLICY_FLOW_INGEST_KEY}' | base64 -d)"

curl --fail-with-body \
  --connect-timeout 10 \
  --max-time 60 \
  -X POST \
  'https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs' \
  -H "Authorization: Bearer $OA_INGEST_KEY" \
  -H 'Idempotency-Key: smoke:policy:001' \
  -F 'request_id=SMOKE-POLICY-001' \
  -F 'key_fields={"oaDocumentId":"SMOKE-POLICY-001","environment":"test"}' \
  -F 'document=@./测试制度.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' \
  | tee /tmp/oa-policy-submit.json

JOB_ID=$(jq -r '.job_id' /tmp/oa-policy-submit.json)
for attempt in $(seq 1 160)
do
  STATUS_JSON=$(curl --fail-with-body \
    --connect-timeout 10 \
    --max-time 30 \
    -H "Authorization: Bearer $OA_INGEST_KEY" \
    "https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs/$JOB_ID") || exit 1
  JOB_STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.status')
  CALLBACK_STATUS=$(printf '%s' "$STATUS_JSON" | jq -r '.callback_status')
  echo "job_status=$JOB_STATUS callback_status=$CALLBACK_STATUS"
  if { [ "$JOB_STATUS" = succeeded ] || [ "$JOB_STATUS" = failed ]; } && \
     { [ "$CALLBACK_STATUS" = delivered ] || [ "$CALLBACK_STATUS" = exhausted ]; }
  then
    break
  fi
  sleep 15
done

[ "$JOB_STATUS" = succeeded ] && [ "$CALLBACK_STATUS" = delivered ] || {
  printf '%s\n' "$STATUS_JSON" | jq .
  unset OA_INGEST_KEY
  exit 1
}

curl --fail-with-body \
  --connect-timeout 10 \
  --max-time 60 \
  -H "Authorization: Bearer $OA_INGEST_KEY" \
  "https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs/$JOB_ID/result" \
  -o /tmp/oa-policy-result.json
sha256sum /tmp/oa-policy-result.json

unset OA_INGEST_KEY
```

Do not put real credentials directly into command history or send them through
chat. Remove `/tmp/oa-policy-submit.json` after the smoke-test record is no
longer needed. For final acceptance, require `status=succeeded`,
`callback_status=delivered`, matching SHA-256 values at OA and the result pull
route, and successful loading by OA's JointJS renderer.
