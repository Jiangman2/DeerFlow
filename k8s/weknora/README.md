# WeKnora local Kubernetes deployment

This manifest runs WeKnora inside the same `deer-flow` namespace as DeerFlow so
Gateway can call it through the stable in-cluster URL:

```text
http://weknora-app:8080
```

Create the runtime secret before applying the Kustomize bundle:

```powershell
kubectl -n deer-flow create secret generic weknora-secret `
  --from-literal=DB_PASSWORD='<change-me>' `
  --from-literal=REDIS_PASSWORD='<change-me>' `
  --from-literal=TENANT_AES_KEY='<32-byte-key>' `
  --from-literal=SYSTEM_AES_KEY='<32-byte-key>' `
  --from-literal=LLM_API_KEY='<dashscope-or-compatible-key>' `
  --from-literal=EMBEDDING_API_KEY='<dashscope-or-compatible-key>' `
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k k8s/weknora
```

Configure Gateway with:

```text
WEKNORA_API_URL=http://weknora-app:8080
WEKNORA_SSO_SECRET=<stable-random-secret>
WEKNORA_KNOWLEDGE_BASE_IDS=<knowledge-base-id>
```

When DeerFlow Gateway can reach the WeKnora PostgreSQL database, the SSO bridge
also keeps the DeerFlow default WeKnora models present as platform built-ins
(`is_builtin=true`). Those rows are visible to every WeKnora tenant, including
tenants created after the deployment. The local PVC stack wires these values
from `weknora-secret`:

```text
WEKNORA_DB_HOST=weknora-postgres
WEKNORA_DB_PORT=5432
WEKNORA_DB_USER=postgres
WEKNORA_DB_NAME=WeKnora
WEKNORA_DB_CONNECT_TIMEOUT_SECONDS=3
WEKNORA_DB_PASSWORD=<weknora-secret.DB_PASSWORD>
LLM_MODEL_NAME=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_PROVIDER=aliyun
LLM_API_KEY=<weknora-secret.LLM_API_KEY>
EMBEDDING_MODEL_NAME=text-embedding-v4
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_PROVIDER=aliyun
EMBEDDING_API_KEY=<weknora-secret.EMBEDDING_API_KEY>
```

For an already-running server database that has an empty `models` table, seed
the same platform defaults once with `seed-default-models.sql`, then restart or
re-enter through DeerFlow SSO:

```bash
LLM_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.LLM_API_KEY}' | base64 -d)
EMBEDDING_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.EMBEDDING_API_KEY}' | base64 -d)

kubectl -n deer-flow exec -i weknora-postgres-0 -- env \
  LLM_API_KEY="$LLM_API_KEY" \
  EMBEDDING_API_KEY="$EMBEDDING_API_KEY" \
  sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d WeKnora \
    -v summary_model_id="deerflow-qwen-plus" \
    -v embedding_model_id="deerflow-dashscope-embedding" \
    -v builtin_tenant_id="10000" \
    -v llm_model_name="qwen-plus" \
    -v llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" \
    -v llm_provider="aliyun" \
    -v llm_api_key="$LLM_API_KEY" \
    -v embedding_model_name="text-embedding-v4" \
    -v embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" \
    -v embedding_provider="aliyun" \
    -v embedding_api_key="$EMBEDDING_API_KEY" \
    -v embedding_dimension="1024" \
    -v embedding_truncate_prompt_tokens="0"' < k8s/weknora/seed-default-models.sql
```

DeerFlow user-facing requests do not use a shared WeKnora tenant API key.
Gateway provisions/logs in the matching WeKnora user and forwards requests with
that user's bearer token, so the native WeKnora UI and DeerFlow Knowledge page
show the same personal, joined, team, and public knowledge bases. `WEKNORA_API_KEY`
can still be configured as an optional fallback for background tool calls that
do not have a DeerFlow browser user.

Local test endpoints:

- WeKnora API: `http://localhost:32080`
- WeKnora UI: `http://localhost:32090`

If the local Kubernetes environment does not expose NodePorts on the host,
use port-forwarding for the native UI instead:

```powershell
kubectl -n deer-flow port-forward svc/weknora-frontend 8090:80 --address 127.0.0.1
```

Then configure DeerFlow's browser-facing URL as
`WEKNORA_FRONTEND_URL=http://127.0.0.1:8090`.

The bundled WeKnora UI deployment mounts `weknora-ui-patch` over the small
auth API chunk in the upstream UI bundle. That patch makes WeKnora's login-page
auto-setup call DeerFlow's `/api/weknora/sso-session` first, using the current
DeerFlow browser cookie, and falls back to WeKnora's native auto-setup if the
DeerFlow SSO request is unavailable. Keep the host name aligned between
DeerFlow and WeKnora, for example `127.0.0.1:2026` and `127.0.0.1:8090`, so
the DeerFlow cookie is sent.

The API container uses a TCP readiness probe because this WeKnora image protects
its HTTP health endpoints with authentication. A 401 from `/health` is therefore
not a container-readiness failure.

For production, replace the local NodePorts with an Ingress or LoadBalancer and
rotate all secrets.
