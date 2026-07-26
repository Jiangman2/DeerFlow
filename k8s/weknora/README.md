# WeKnora local Kubernetes deployment

This manifest runs WeKnora inside the same `deer-flow` namespace as DeerFlow so
Gateway can call it through the stable in-cluster URL:

```text
http://weknora-app:8080
```

The WeKnora application image is pinned by digest to the v0.7.0 release
baseline used by this integration. Do not replace it with the mutable `latest`
tag. The enterprise overlay intentionally substitutes its ARM64 image rebuilt
from the same baseline with the required proxy compatibility patch.

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
from `weknora-secret`. The built-in set contains:

- chat: `qwen-plus`
- chat (Bailian): `deepseek-v4-pro`
- chat (enterprise default, CNOOC Haineng): `DeepSeek-V4-Flash`
- embedding: `text-embedding-v4`
- rerank: `qwen3-rerank`
- vision: `qwen3-vl-plus`
- speech recognition: `qwen3-asr-flash`

The six Bailian-backed entries reuse the existing `LLM_API_KEY`;
`EMBEDDING_API_KEY` remains supported for deployments that intentionally use a
different embedding credential. The Haineng entry reads
`DeepSeek_V4_API_KEY` from the existing `deer-flow-env` runtime Secret and is
omitted by Gateway's automatic seed path when that key is absent. Keep
`model.ai.cnooc` in `NO_PROXY`/`no_proxy` so the internal endpoint is never sent
through an outbound Internet proxy.

For the enterprise baseline, apply
`enterprise-model-feature-defaults.sql` after the built-in models exist. It
performs a transactional, credential-gated normalization of existing
knowledge bases and custom agents, then installs database guards for future
objects. New knowledge bases start with internal `DeepSeek-V4-Flash`,
`text-embedding-v4`, `qwen3-vl-plus`, and `qwen3-asr-flash`; new agents start
with internal DeepSeek, `qwen3-rerank`, image upload, attachment image
understanding, and audio upload enabled. The insert guards establish initial
values only; users may still edit or disable object-level settings afterward.

The WeKnora application also uses the existing stable `SYSTEM_AES_KEY` as its
`JWT_SECRET`. This keeps SSO access and refresh tokens valid across application
pod restarts without adding another local secret to maintain.

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
DeepSeek_V4_API_KEY=<deer-flow-env.DeepSeek_V4_API_KEY>
EMBEDDING_MODEL_NAME=text-embedding-v4
EMBEDDING_BASE_URL=http://gateway:8001/api/weknora/bailian/v1
EMBEDDING_PROVIDER=generic
EMBEDDING_API_KEY=<weknora-secret.EMBEDDING_API_KEY>
```

The built-in embedding model calls the internal Gateway adapter at
`http://gateway:8001/api/weknora/bailian/v1/embeddings`. Gateway validates the
same embedding key and forwards the unchanged OpenAI-compatible JSON request
through its managed outbound TLS path. It does not persist document text.

WeKnora 0.7.0 sends speech recognition requests as OpenAI multipart
`/audio/transcriptions`, while Bailian `qwen3-asr-flash` accepts an
OpenAI-compatible chat payload containing `input_audio`. The built-in ASR model
therefore points to
`http://gateway:8001/api/weknora/bailian/v1`. Gateway validates the same Bailian
key, converts the request without persisting the uploaded audio, and calls
Bailian. Keep the bare `gateway` Service name in the WeKnora App's `NO_PROXY`
and `no_proxy` values in proxy-enabled clusters.

For an already-running server database that is missing any of these built-ins,
seed the platform defaults once with `seed-default-models.sql`, then restart or
re-enter through DeerFlow SSO:

```bash
LLM_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.LLM_API_KEY}' | base64 -d)
EMBEDDING_API_KEY=$(kubectl -n deer-flow get secret weknora-secret -o jsonpath='{.data.EMBEDDING_API_KEY}' | base64 -d)
CNOOC_DEEPSEEK_API_KEY=$(kubectl -n deer-flow get secret deer-flow-env -o jsonpath='{.data.DeepSeek_V4_API_KEY}' | base64 -d)

kubectl -n deer-flow exec -i weknora-postgres-0 -- env \
  LLM_API_KEY="$LLM_API_KEY" \
  EMBEDDING_API_KEY="$EMBEDDING_API_KEY" \
  CNOOC_DEEPSEEK_API_KEY="$CNOOC_DEEPSEEK_API_KEY" \
  sh -lc 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U postgres -d WeKnora \
    -v summary_model_id="deerflow-qwen-plus" \
    -v embedding_model_id="deerflow-dashscope-embedding" \
    -v builtin_tenant_id="10000" \
    -v llm_model_name="qwen-plus" \
    -v llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" \
    -v llm_provider="aliyun" \
    -v llm_api_key="$LLM_API_KEY" \
    -v cnooc_deepseek_api_key="$CNOOC_DEEPSEEK_API_KEY" \
    -v embedding_model_name="text-embedding-v4" \
    -v embedding_base_url="http://gateway:8001/api/weknora/bailian/v1" \
    -v embedding_provider="generic" \
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

The `k8s/local-pvc` DeerFlow frontend opens the native UI at
`http://127.0.0.1:32090/platform/knowledge-bases`. It also keeps the same-origin
`http://127.0.0.1:2026/platform/knowledge-bases` proxy as a compatibility
fallback. That proxy uses the stable `weknora-frontend` Kubernetes service DNS
name; do not replace it with a ClusterIP, because ClusterIPs change when the
local Kubernetes cluster is recreated.

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

The SSO bootstrap only persists the WeKnora token, user, tenant, and membership
metadata returned by DeerFlow. It does not intercept browser storage, override
tenant switching, or change WeKnora routes. On the unified port `2026`,
`/api/v1/auth/me` remains DeerFlow's endpoint, so the patched WeKnora auth chunk
uses `/api/weknora-native/auth/me`. Nginx proxies that exact path to WeKnora's
native `/api/v1/auth/me`, preserving the response shape needed by the user menu,
member management, and API settings. A DeerFlow `system_role` of `admin` maps
to WeKnora `is_system_admin` and `can_access_all_tenants`; normal and
OAuth-provisioned DeerFlow users remain regular WeKnora users.

WeKnora tenant workspaces and organizations are separate concepts. Registration
creates a default tenant workspace and owner membership. A system administrator
can create and switch tenant workspaces. Organizations are shared collaboration
spaces with their own admin/editor/viewer membership and knowledge-base sharing
rules.

The same UI patch embeds a branding layer in the mounted `index.html`. It
changes user-visible `WeKnora` product labels to `知识库` at runtime (including
content rendered after navigation) and aligns the native UI's primary blue,
active, focus, and page-background colors with the DeerFlow landing/workspace
palette. Internal API paths, storage keys, container names, and integration
identifiers deliberately remain unchanged.

Do not mount `config.js` or `/etc/nginx/conf.d/default.conf` read-only: the
upstream UI image regenerates both files in `/docker-entrypoint.sh` on every
start. The image builds the proxy configuration from `APP_HOST`, `APP_PORT`,
and `APP_SCHEME`; the patched authentication chunk has the DeerFlow SSO URL as
its fallback. The frontend can therefore restart normally without either
generated-file mount.

For production, replace the local NodePorts with an Ingress or LoadBalancer and
rotate all secrets.
